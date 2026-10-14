"""FastAPI application: routes, server-side validation and downloads.

The interface is server-rendered. Filtering, sorting, pagination and every
validation rule live here rather than in the browser, so a bookmarked or hand-edited
URL is checked the same way a click is, and the page works with JavaScript disabled.
The small amount of client script that exists only adds conveniences on top of links
and forms that already work without it.
"""

from __future__ import annotations

import csv
import io
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Iterator, Sequence
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import AGENT_NAME, __version__
from ..identity import valid_org_number
from ..models import AVAILABILITY_STATES, FIELD_FAMILIES, TERMINAL_STATES
from . import changes as changes_mod
from . import data as data_mod
from . import formatting
from .data import (
    AVAILABILITY_HELP,
    AVAILABILITY_LABELS,
    FAMILY_LABELS,
    MAX_COMPARE,
    MIN_COMPARE,
    NOT_RECORDED,
    PAGE_SIZES,
    SORT_KEYS,
    TERMINAL_LABELS,
    BaselineResolver,
    CompanyQuery,
    Dataset,
    EvidenceIndex,
    QueryError,
    claim_csv_rows,
    company_csv_rows,
    evidence_csv_rows,
    field_label,
    load_dataset,
    load_reports,
)
from .jobs import MAX_UPLOAD_BYTES, MAX_UPLOAD_RECORDS, JobManager, ResearchDisabled, parse_batch
from .profile import build_comparison, build_profile, compare_counts

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"

MAX_INSPECTOR_ROWS = 2000
INSPECTOR_PAGE_SIZE = 50


@dataclass(slots=True)
class AppConfig:
    """How this server was started. Shown verbatim in the runs area."""

    input_path: Path
    reports_dir: Path | None = None
    baseline_path: Path | None = None
    scratch_dir: Path = Path("out/ui-scratch")
    enable_research: bool = False
    research_offline: bool = False
    max_records: int | None = None

    @property
    def resolved_reports_dir(self) -> Path:
        return self.reports_dir or self.input_path.parent


class AppState:
    """Everything loaded once at startup. The envelope file is read once per process."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.dataset: Dataset = load_dataset(config.input_path, max_records=config.max_records)
        self.reports = load_reports(config.resolved_reports_dir)
        self.baselines = BaselineResolver(
            self.reports,
            explicit=config.baseline_path,
            root=config.resolved_reports_dir,
        )
        self.jobs = JobManager(
            scratch_dir=config.scratch_dir,
            enabled=config.enable_research,
            offline=config.research_offline,
        )

    def baseline_for(self, envelope: dict[str, Any]) -> Dataset | None:
        run_id = (envelope.get("refresh") or {}).get("compared_against")
        return self.baselines.for_run(str(run_id) if run_id else None)

    @property
    def current_report(self) -> Any:
        """The report whose output is the loaded file, if one exists."""
        wanted = self.dataset.path.name
        for report in self.reports:
            if Path(report.output_path).name == wanted:
                return report
        # A copied or renamed output file no longer matches the path its report names,
        # but its envelopes still carry the run id, which is the stronger link anyway.
        run_ids = set(self.dataset.run_ids)
        if len(run_ids) == 1:
            for report in self.reports:
                if report.run_id in run_ids:
                    return report
        return None


STREAM_CHUNK_CHARS = 64 * 1024


def _query_string(pairs: Sequence[tuple[str, str]]) -> str:
    encoded = urlencode([(key, value) for key, value in pairs if value != ""])
    return f"?{encoded}" if encoded else ""


def create_app(config: AppConfig) -> FastAPI:
    state = AppState(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            state.jobs.shutdown()

    app = FastAPI(
        title=f"{AGENT_NAME} — run explorer",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.signalpost = state

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    env = templates.env
    # Untrusted text (company names, page excerpts, error strings) flows straight into
    # these templates, so autoescaping is a correctness requirement, not a default.
    env.autoescape = True
    env.globals.update(
        agent_name=AGENT_NAME,
        version=__version__,
        availability_labels=AVAILABILITY_LABELS,
        availability_help=AVAILABILITY_HELP,
        availability_states=sorted(AVAILABILITY_STATES),
        terminal_labels=TERMINAL_LABELS,
        terminal_states=sorted(TERMINAL_STATES),
        family_labels=FAMILY_LABELS,
        families=sorted(FIELD_FAMILIES),
        sort_keys=SORT_KEYS,
        page_sizes=PAGE_SIZES,
        not_recorded=NOT_RECORDED,
        field_label=field_label,
        query_string=_query_string,
        max_upload_bytes=MAX_UPLOAD_BYTES,
        max_upload_records=MAX_UPLOAD_RECORDS,
    )
    env.filters.update(
        safe_url=formatting.safe_url,
        url_host=formatting.url_host,
        timestamp=formatting.format_timestamp,
        period=formatting.format_period,
        amount=formatting.format_amount,
        integer=formatting.format_int,
        seconds=formatting.format_seconds,
        filesize=formatting.format_bytes,
        shorthash=formatting.short_hash,
        truncate_text=formatting.truncate,
        scalar=formatting.format_scalar,
        pretty_json=lambda value: json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
    )

    def page(request: Request, name: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
        payload = {
            "dataset": state.dataset,
            "config": state.config,
            "research_enabled": state.jobs.enabled,
            "nav": context.get("nav", ""),
            **context,
        }
        return templates.TemplateResponse(request, name, payload, status_code=status_code)

    def wants_json(request: Request) -> bool:
        return request.url.path.startswith("/api/")

    def error_page(request: Request, message: str, status_code: int = 400, title: str = "Request rejected") -> Any:
        if wants_json(request):
            return JSONResponse({"error": message}, status_code=status_code)
        return page(
            request,
            "error.html",
            {"message": message, "title": title, "status_code": status_code},
            status_code=status_code,
        )

    @app.exception_handler(QueryError)
    async def _query_error(request: Request, exc: QueryError) -> Any:  # noqa: ANN401
        return error_page(request, str(exc), 400)

    # -- explorer -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Any:
        return RedirectResponse(url="/companies", status_code=307)

    @app.get("/companies", response_class=HTMLResponse)
    def companies(request: Request) -> Any:
        try:
            query = CompanyQuery.parse(request.query_params)
        except QueryError as error:
            return error_page(request, str(error), 400, "Invalid filter")
        results = data_mod.search(state.dataset, query)
        return page(
            request,
            "explorer.html",
            {
                "nav": "companies",
                "query": query,
                "results": results,
                "cities": state.dataset.cities,
                "statuses": state.dataset.statuses,
                "report": state.current_report,
            },
        )

    @app.get("/companies/{org}", response_class=HTMLResponse)
    def company(request: Request, org: str) -> Any:
        envelope = state.dataset.envelope(org)
        if envelope is None:
            return error_page(
                request,
                f"No company {org!r} in {state.dataset.label}. "
                "Only organisation numbers present in the loaded run can be opened.",
                404,
                "Company not in this run",
            )
        profile = build_profile(envelope)
        return page(
            request,
            "company.html",
            {"nav": "companies", "profile": profile, "back": _query_string(request.query_params.multi_items())},
        )

    @app.get("/compare", response_class=HTMLResponse)
    def compare(request: Request) -> Any:
        wanted = [value.strip() for value in request.query_params.getlist("org") if value.strip()]
        unique: list[str] = []
        for org in wanted:
            if org not in unique:
                unique.append(org)
        if not unique:
            return page(request, "compare.html", {"nav": "companies", "profiles": [], "lines": [], "counts": []})
        if len(unique) < MIN_COMPARE:
            return error_page(request, f"Select at least {MIN_COMPARE} companies to compare.", 400, "Nothing to compare")
        if len(unique) > MAX_COMPARE:
            return error_page(
                request, f"Compare at most {MAX_COMPARE} companies at a time; {len(unique)} were requested.", 400,
                "Too many companies",
            )
        missing = [org for org in unique if state.dataset.envelope(org) is None]
        if missing:
            return error_page(
                request,
                f"Not in this run: {', '.join(missing)}. Only researched companies can be compared.",
                404,
                "Company not in this run",
            )
        profiles = [build_profile(state.dataset.envelope(org)) for org in unique]
        return page(
            request,
            "compare.html",
            {
                "nav": "companies",
                "profiles": profiles,
                "lines": build_comparison(profiles),
                "counts": compare_counts(profiles),
            },
        )

    # -- changes --------------------------------------------------------------

    @app.get("/changes", response_class=HTMLResponse)
    def changes_index(request: Request) -> Any:
        baseline = state.baselines.for_run(next(iter(state.dataset.compared_against()), None))
        overview = changes_mod.build_overview(state.dataset, baseline=baseline)
        return page(
            request,
            "changes.html",
            {"nav": "changes", "overview": overview, "rows": state.dataset.changed_rows()},
        )

    @app.get("/changes/{org}", response_class=HTMLResponse)
    def changes_company(request: Request, org: str) -> Any:
        envelope = state.dataset.envelope(org)
        if envelope is None:
            return error_page(request, f"No company {org!r} in {state.dataset.label}.", 404, "Company not in this run")
        baseline = state.baseline_for(envelope)
        views = changes_mod.build_change_views(envelope, baseline=baseline)
        return page(
            request,
            "changes_company.html",
            {
                "nav": "changes",
                "row": state.dataset.row(org),
                "envelope": envelope,
                "views": views,
                "refresh": envelope.get("refresh") or {},
                "baseline": baseline,
            },
        )

    # -- runs and evidence ----------------------------------------------------

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request) -> Any:
        return page(
            request,
            "runs.html",
            {
                "nav": "runs",
                "reports": state.reports,
                "current": state.current_report,
                "jobs": state.jobs.recent(),
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str) -> Any:
        report = next((item for item in state.reports if item.run_id == run_id), None)
        if report is None:
            return error_page(request, f"No run report with run_id {run_id!r}.", 404, "Run report not found")
        return page(
            request,
            "run_detail.html",
            {"nav": "runs", "report": report, "is_current": report is state.current_report},
        )

    @app.get("/evidence", response_class=HTMLResponse)
    def evidence(request: Request) -> Any:
        params = request.query_params
        org = (params.get("org") or "").strip()
        field_name = (params.get("field") or "").strip()
        family = (params.get("family") or "").strip()
        availability = (params.get("availability") or "").strip()
        source = (params.get("source") or "").strip().casefold()
        try:
            page_number = max(1, int(params.get("page") or 1))
        except ValueError:
            return error_page(request, "page must be a whole number", 400, "Invalid filter")
        if family and family not in FIELD_FAMILIES:
            return error_page(request, f"Unknown field family: {family!r}", 400, "Invalid filter")
        if availability and availability not in AVAILABILITY_STATES:
            return error_page(request, f"Unknown availability state: {availability!r}", 400, "Invalid filter")
        if org and state.dataset.envelope(org) is None:
            return error_page(request, f"No company {org!r} in {state.dataset.label}.", 404, "Company not in this run")

        envelopes = [state.dataset.envelope(org)] if org else state.dataset.envelopes
        rows: list[dict[str, Any]] = []
        truncated = False
        for envelope in envelopes:
            if envelope is None:
                continue
            index = EvidenceIndex(envelope)
            for claim in envelope.get("claims") or []:
                if field_name and claim.get("field") != field_name:
                    continue
                if family and claim.get("family") != family:
                    continue
                if availability and claim.get("availability") != availability:
                    continue
                records = index.resolve(str(item) for item in (claim.get("evidence_ids") or []))
                if source and not any(source in (record.source_url or "").casefold() for record in records):
                    continue
                if len(rows) >= MAX_INSPECTOR_ROWS:
                    truncated = True
                    break
                rows.append(
                    {
                        "organisation_number": envelope.get("organisation_number"),
                        "claim": claim,
                        "evidence": records,
                        "unresolved": index.unresolved(
                            str(item) for item in (claim.get("evidence_ids") or [])
                        ),
                    }
                )
            if truncated:
                break

        total = len(rows)
        pages = max(1, (total + INSPECTOR_PAGE_SIZE - 1) // INSPECTOR_PAGE_SIZE)
        page_number = min(page_number, pages)
        start = (page_number - 1) * INSPECTOR_PAGE_SIZE
        return page(
            request,
            "evidence.html",
            {
                "nav": "runs",
                "rows": rows[start : start + INSPECTOR_PAGE_SIZE],
                "total": total,
                "page_number": page_number,
                "pages": pages,
                "truncated": truncated,
                "filters": {
                    "org": org,
                    "field": field_name,
                    "family": family,
                    "availability": availability,
                    "source": params.get("source") or "",
                },
                "fields": sorted({str(claim.get("field")) for env in state.dataset.envelopes for claim in (env.get("claims") or [])}),
            },
        )

    # -- research and batch ---------------------------------------------------

    @app.get("/research", response_class=HTMLResponse)
    def research_form(request: Request) -> Any:
        return page(
            request,
            "research.html",
            {"nav": "research", "jobs": state.jobs.recent(), "result": None, "batch": None},
        )

    @app.post("/research", response_class=HTMLResponse)
    def research_submit(request: Request, organisation_number: str = Form("")) -> Any:
        digits = "".join(char for char in organisation_number if char.isdigit())
        if not valid_org_number(digits):
            return page(
                request,
                "research.html",
                {
                    "nav": "research",
                    "jobs": state.jobs.recent(),
                    "result": {
                        "ok": False,
                        "message": f"{organisation_number.strip()!r} is not a valid Norwegian organisation "
                        "number. Nine digits with a MOD-11 control digit are required.",
                    },
                    "batch": None,
                },
                status_code=400,
            )
        existing = state.dataset.envelope(digits)
        if existing is not None and not state.jobs.enabled:
            return RedirectResponse(url=f"/companies/{digits}", status_code=303)
        try:
            job = state.jobs.submit([digits], kind="single")
        except ResearchDisabled as error:
            return page(
                request,
                "research.html",
                {
                    "nav": "research",
                    "jobs": state.jobs.recent(),
                    "result": {"ok": False, "message": str(error), "organisation_number": digits,
                               "in_dataset": existing is not None},
                    "batch": None,
                },
                status_code=403,
            )
        except (ValueError, RuntimeError) as error:
            return page(
                request,
                "research.html",
                {"nav": "research", "jobs": state.jobs.recent(),
                 "result": {"ok": False, "message": str(error)}, "batch": None},
                status_code=409,
            )
        return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)

    @app.post("/batch", response_class=HTMLResponse)
    async def batch_submit(request: Request, batch: UploadFile = File(...)) -> Any:
        raw = await batch.read(MAX_UPLOAD_BYTES + 1)
        parsed = parse_batch(raw)
        coverage = [
            {
                "organisation_number": org,
                "row": state.dataset.row(org),
            }
            for org in parsed.accepted
        ]
        status_code = 200 if parsed.ok else 400
        result = None
        if parsed.ok and state.jobs.enabled:
            try:
                job = state.jobs.submit(parsed.accepted, kind="batch")
            except (ValueError, RuntimeError, ResearchDisabled) as error:
                result = {"ok": False, "message": str(error)}
            else:
                return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
        return page(
            request,
            "research.html",
            {
                "nav": "research",
                "jobs": state.jobs.recent(),
                "result": result,
                "batch": parsed,
                "coverage": coverage,
                "filename": batch.filename or "upload",
            },
            status_code=status_code,
        )

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> Any:
        job = state.jobs.get(job_id)
        if job is None:
            return error_page(request, f"No job {job_id!r} on this server.", 404, "Job not found")
        results = load_dataset(job.output, label=job.id) if job.output.exists() else None
        return page(request, "job.html", {"nav": "research", "job": job, "results": results})

    # -- json api -------------------------------------------------------------

    @app.get("/api/dataset")
    def api_dataset() -> Any:
        dataset = state.dataset
        return {
            "path": str(dataset.path).replace("\\", "/"),
            "label": dataset.label,
            "companies": dataset.count,
            "load_error": dataset.load_error,
            "run_ids": dataset.run_ids,
            "total_changes": dataset.total_changes,
            "material_changes": dataset.total_material_changes,
            "research_enabled": state.jobs.enabled,
            "reports": [report.run_id for report in state.reports],
        }

    @app.get("/api/companies")
    def api_companies(request: Request) -> Any:
        try:
            query = CompanyQuery.parse(request.query_params)
        except QueryError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        results = data_mod.search(state.dataset, query)
        return {
            "total": results.total,
            "page": results.page,
            "pages": results.pages,
            "page_size": results.page_size,
            "items": [
                {
                    "organisation_number": row.organisation_number,
                    "name": row.name,
                    "terminal_status": row.terminal_status,
                    "city": row.city,
                    "industry_code": row.industry_code,
                    "industry_label": row.industry_label,
                    "employees": {"value": row.employees.value, "availability": row.employees.availability},
                    "revenue": {
                        "value": row.revenue.value,
                        "currency": row.revenue.currency,
                        "reporting_period": row.revenue.reporting_period,
                        "availability": row.revenue.availability,
                    },
                    "website": {"value": row.website.value, "availability": row.website.availability},
                    "claims_available": row.claims_available,
                    "claims_total": row.claims_total,
                    "evidence": row.evidence_count,
                    "changes_total": row.changes_total,
                    "last_checked": row.last_checked,
                }
                for row in results.items
            ],
        }

    @app.get("/api/companies/{org}")
    def api_company(org: str) -> Any:
        envelope = state.dataset.envelope(org)
        if envelope is None:
            return JSONResponse({"error": f"No company {org} in {state.dataset.label}"}, status_code=404)
        return envelope

    @app.get("/api/claim")
    def api_claim(org: str, key: str) -> Any:
        envelope = state.dataset.envelope(org)
        if envelope is None:
            return JSONResponse({"error": f"No company {org} in {state.dataset.label}"}, status_code=404)
        claim = next(
            (item for item in (envelope.get("claims") or []) if item.get("claim_key") == key), None
        )
        if claim is None:
            return JSONResponse({"error": f"No claim {key} for {org}"}, status_code=404)
        index = EvidenceIndex(envelope)
        records = index.resolve(str(item) for item in (claim.get("evidence_ids") or []))
        return {
            "claim": claim,
            "evidence": [
                {
                    "id": record.id,
                    "snapshot_id": record.snapshot_id,
                    "source_url": record.source_url,
                    "safe_link": formatting.safe_url(record.source_url),
                    "source_class": record.source_class,
                    "retrieved_at": record.retrieved_at,
                    "content_sha256": record.content_sha256,
                    "locator": {
                        "kind": record.locator_kind,
                        "value": record.locator_value,
                        "excerpt": record.locator_excerpt,
                    },
                    "http_status": record.status,
                    "final_url": record.final_url,
                    "content_type": record.content_type,
                    "redirect_chain": record.redirect_chain,
                }
                for record in records
            ],
            "unresolved_evidence_ids": index.unresolved(
                str(item) for item in (claim.get("evidence_ids") or [])
            ),
        }

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str) -> Any:
        job = state.jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": f"No job {job_id}"}, status_code=404)
        return job.to_dict()

    @app.post("/api/research")
    def api_research(payload: dict[str, Any]) -> Any:
        digits = "".join(char for char in str(payload.get("organisation_number") or "") if char.isdigit())
        if not valid_org_number(digits):
            return JSONResponse({"error": "Invalid Norwegian organisation number"}, status_code=400)
        try:
            job = state.jobs.submit([digits], kind="single")
        except ResearchDisabled as error:
            return JSONResponse({"error": str(error)}, status_code=403)
        except (ValueError, RuntimeError) as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        return JSONResponse(job.to_dict(), status_code=202)

    @app.get("/health")
    def health() -> Any:
        return {
            "status": "ok" if state.dataset.exists else "degraded",
            "companies": state.dataset.count,
            "load_error": state.dataset.load_error,
        }

    # -- downloads ------------------------------------------------------------

    def _selected_envelopes(request: Request) -> tuple[CompanyQuery, list[dict[str, Any]]]:
        query = CompanyQuery.parse(request.query_params)
        rows = data_mod.sort_rows(data_mod.filter_rows(state.dataset.rows, query), query)
        envelopes = [
            state.dataset.envelope(row.organisation_number)
            for row in rows
            if state.dataset.envelope(row.organisation_number) is not None
        ]
        return query, [envelope for envelope in envelopes if envelope is not None]

    def _csv_response(rows: Iterable[Sequence[Any]], filename: str) -> StreamingResponse:
        def generate() -> Iterator[str]:
            # Rows are written into a buffer and flushed in ~64 KB chunks. Yielding one
            # chunk per row costs a worker-thread hop per row inside Starlette, which made
            # the 40,000-row claims export of a 1,000-company run take over ten seconds.
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            for row in rows:
                writer.writerow(list(row))
                if buffer.tell() >= STREAM_CHUNK_CHARS:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate(0)
            if buffer.tell():
                yield buffer.getvalue()

        return StreamingResponse(
            generate(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/download/companies.csv")
    def download_companies(request: Request) -> Any:
        try:
            query = CompanyQuery.parse(request.query_params)
        except QueryError as error:
            return error_page(request, str(error), 400, "Invalid filter")
        rows = data_mod.sort_rows(data_mod.filter_rows(state.dataset.rows, query), query)
        return _csv_response(company_csv_rows(rows), "signalpost-companies.csv")

    @app.get("/download/claims.csv")
    def download_claims(request: Request) -> Any:
        try:
            _, envelopes = _selected_envelopes(request)
        except QueryError as error:
            return error_page(request, str(error), 400, "Invalid filter")
        return _csv_response(claim_csv_rows(envelopes), "signalpost-claims.csv")

    @app.get("/download/evidence.csv")
    def download_evidence(request: Request) -> Any:
        try:
            _, envelopes = _selected_envelopes(request)
        except QueryError as error:
            return error_page(request, str(error), 400, "Invalid filter")
        return _csv_response(evidence_csv_rows(envelopes), "signalpost-evidence.csv")

    @app.get("/download/envelopes.jsonl")
    def download_envelopes(request: Request) -> Any:
        try:
            _, envelopes = _selected_envelopes(request)
        except QueryError as error:
            return error_page(request, str(error), 400, "Invalid filter")

        def generate() -> Iterator[str]:
            chunk: list[str] = []
            size = 0
            for envelope in envelopes:
                line = json.dumps(envelope, ensure_ascii=False) + "\n"
                chunk.append(line)
                size += len(line)
                if size >= STREAM_CHUNK_CHARS:
                    yield "".join(chunk)
                    chunk, size = [], 0
            if chunk:
                yield "".join(chunk)

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="signalpost-envelopes.jsonl"'},
        )

    @app.get("/download/run/{run_id}.json")
    def download_run_report(request: Request, run_id: str) -> Any:
        report = next((item for item in state.reports if item.run_id == run_id), None)
        if report is None:
            return error_page(request, f"No run report with run_id {run_id!r}.", 404, "Run report not found")
        return JSONResponse(
            report.payload,
            headers={"Content-Disposition": f'attachment; filename="{report.path.name}"'},
        )

    return app
