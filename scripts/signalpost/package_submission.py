#!/usr/bin/env python3
"""Build the submission ZIP from a frozen commit, then verify it from a fresh extraction.

    uv run python scripts/signalpost/package_submission.py --config packaging.json

Source comes from `git archive <commit>`, never from the working tree, so the packaged
code is exactly the commit named in the manifest. Run outputs are copied in from the
paths the config names. Every payload file is hashed into SHA256SUMS.txt; the ZIP's own
hash goes in a separate file beside it, so no checksum ever covers itself.

The ZIP is our delivery convenience. The organiser specifies what must be delivered
(code, profiles, manifest, run instructions, declarations), not this layout.
"""

from __future__ import annotations

import argparse
import email
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.message import EmailMessage
from email.utils import formatdate
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
TOP = "signalpost-submission"

# Patterns that would indicate a live credential in the payload. Placeholders in
# .env.example ("your-...") do not match.
SECRET_PATTERNS = {
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "openai_key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".toml", ".lock", ".eml", ".html",
                 ".css", ".js", ".cfg", ".ini", ".yml", ".yaml", ".example", ".csv"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not (path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", name))


def export_source(commit: str, destination: Path) -> int:
    """Extract `git archive` of the commit, refusing unsafe paths and links."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit], cwd=ROOT, check=True, capture_output=True
    ).stdout
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if not safe_member(member.name):
                raise SystemExit(f"unsafe path in git archive: {member.name}")
            if member.issym() or member.islnk():
                raise SystemExit(f"link in git archive refused: {member.name}")
            if member.isdir():
                continue
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            assert extracted is not None
            target.write_bytes(extracted.read())
            count += 1
    return count


def read_jsonl(path: Path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, records) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


def normalise_envelope(envelope: dict, snapshot_prefix: str | None) -> dict:
    """Make stored snapshot paths portable: forward slashes, package-relative.

    A body that is not in the package (rights-restricted, or a refresh file) gets no
    stored_path, so nothing points at a file that does not exist. Its URL and SHA-256
    stay on the snapshot, and data/source-manifest.jsonl lists it."""
    for snapshot in envelope.get("snapshots") or []:
        stored = snapshot.get("stored_path")
        if stored and snapshot_prefix and snapshot.get("body_in_package"):
            posix = stored.replace("\\", "/")
            name = posix.split("/", 1)[1] if "/" in posix else posix
            snapshot["stored_path"] = f"{snapshot_prefix}/{name}"
        else:
            snapshot["stored_path"] = None
    return envelope


PROFILE_CLAIM_FIELDS = ("value", "availability", "reason", "scope", "currency", "unit",
                        "reporting_period", "confidence", "evidence_ids")


def profile_view(envelope: dict) -> dict:
    """A compact per-company view derived only from the envelope's own claims.

    No value is added or changed; the envelope stays the audit record."""
    fields: dict[str, dict] = {}
    for claim in envelope.get("claims") or []:
        fields[claim["field"]] = {k: claim[k] for k in PROFILE_CLAIM_FIELDS if claim.get(k) is not None}
    return {
        "schema_version": "signalpost.profile_view/1",
        "organisation_number": envelope["organisation_number"],
        "run_id": envelope["run"]["run_id"],
        "terminal_status": envelope["run"]["terminal_status"],
        "terminal_reason": (envelope.get("operations") or {}).get("terminal_reason"),
        "summary": (envelope.get("synthesis") or {}).get("summary"),
        "fields": fields,
        "envelope_ref": "data/envelopes.jsonl",
    }


PLACEHOLDER = re.compile(r"\[USER INPUT:[^\]]*\]")
PIPELINE_PATHS = ["main.py", "src/signalpost", ":(exclude)src/signalpost/ui"]


def runtime_requirements(commit: str) -> str:
    """The locked runtime dependency set (no extras, no dev group) at a commit."""
    with tempfile.TemporaryDirectory(prefix="signalpost-lock-") as temp:
        for name in ("pyproject.toml", "uv.lock"):
            Path(temp, name).write_bytes(subprocess.run(
                ["git", "show", f"{commit}:{name}"], cwd=ROOT, check=True, capture_output=True).stdout)
        return subprocess.run(
            ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-header", "--no-emit-project"],
            cwd=temp, check=True, capture_output=True, text=True).stdout


def pipeline_changes(run_commit: str, source_commit: str) -> list[str]:
    """What could change research output between the commit a run used and the packaged one:
    research code, or the locked runtime dependencies. Optional extras (the UI) do not count."""
    changed = git("diff", "--name-only", run_commit, source_commit, "--", *PIPELINE_PATHS)
    problems = [line for line in changed.splitlines() if line.strip()]
    if runtime_requirements(run_commit) != runtime_requirements(source_commit):
        problems.append("locked runtime dependencies (uv export --no-dev, no extras)")
    return problems


def dig(data, dotted: str):
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def render_email(manifest: dict, config: dict, live: dict, refresh: dict, zip_name: str,
                 checksum_line: str) -> tuple[str, str]:
    personal = config["personal"]
    ready = all(manifest["readiness"].get(k) for k in (
        "implementation_complete", "local_validation_passed", "submission_artifacts_complete",
        "reviewer_access_verified", "email_ready"))
    states = manifest["terminal_states"]
    subject = f"Signalpost Round 1 submission — {manifest['agent_name']} — {personal['entrant']}"
    banner = "" if ready else (
        "DRAFT — NOT READY TO SEND\n"
        "Outstanding before sending (see SUBMISSION_CHECKLIST.md):\n"
        + "".join(f"  - {b}\n" for b in manifest["readiness_blockers"]) + "\n")
    lb = dig(live, "budget") or {}
    body = f"""{banner}To: submit@builderr.ai
Subject: {subject}

Hello Builderr team,

Please find our entry for the Signalpost company-research challenge.

Agent name: {manifest['agent_name']}
Entrant/team: {personal['entrant']}
Contact for results: {personal['contact']}
Repository: {manifest['repository_url']}
Exact source commit: {manifest['source_commit']}
Repository access: {manifest['access_status']}
Completed company profiles: {manifest['profiles']} researched profiles for {manifest['unique_inputs']} unique eligible organisation numbers, exactly one terminal envelope each ({states.get('completed', 0)} completed with a verified company website, {states.get('partial', 0)} partial with official-register research but no verified website, {states.get('failed', 0)} failed)
Organisation-number manifest URL: {config['manifest_url']}
Organisation-number manifest in package: signalpost-submission/data/organisation-numbers.txt (SHA-256 {manifest['organisation_number_manifest_sha256']})
Profiles and evidence: signalpost-submission/data/envelopes.jsonl (claims, evidence, snapshot references), data/profiles.jsonl (compact view), data/source-manifest.jsonl, evidence/snapshots/ (official-register bodies)

Setup instructions: signalpost-submission/SUBMISSION_README.md and source/README.md
Evaluator command (run from source/ after "uv sync --frozen"; the reviewer supplies the JSONL batch):
{manifest['evaluator_command']}

Models and APIs: {config['models_line']} Declarations: docs/MODELS_AND_APIS.md
Licences and source rights: docs/SOURCE_RIGHTS.md (official register under NLOD 2.0; company-website bodies are not redistributed)
Expected third-party cost per 100-company run: {config['cost_line']} Basis: docs/COSTS.md
Builderr source: source=unstop; utm_source=unstop; utm_medium=participant_email; utm_campaign=signalpost_2026
Measured representative 100-company run: {config['live_label']}: {dig(live, 'config.inputs')} companies, {lb.get('seconds_elapsed')} s wall clock, {lb.get('requests_used')} outbound requests, ${float(lb.get('spend_usd') or 0):.2f} third-party spend (reports/live-100-report.json)
Refresh support: --previous-snapshot <prior envelopes.jsonl> compares by stable claim key and writes changes into each envelope; measured on {config['refresh_label']}: {dig(refresh, 'results.changes_total')} changes, {dig(refresh, 'results.changes_material')} material (refresh/)

Submission package: {zip_name}
SHA-256: {checksum_line}

Validation: {config['validation_line']}
Known limitations: {config['limitations_line']} Full list: docs/LIMITATIONS.md

Please confirm receipt and let us know if any access or execution details are missing.

Regards,
{personal['name']}
{personal['contact']}
"""
    return subject, body


def write_email(directory: Path, stem: str, subject: str, body: str, personal: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.txt").write_text(body, encoding="utf-8", newline="\n")
    message = EmailMessage()
    message["To"] = "submit@builderr.ai"
    message["From"] = personal["from_header"]
    message["Subject"] = ("[DRAFT — NOT READY TO SEND] " + subject) if body.startswith("DRAFT") else subject
    message["Date"] = formatdate(usegmt=True)
    message["X-Unsent"] = "1"
    message.set_content(body)
    (directory / f"{stem}.eml").write_bytes(bytes(message))


def build(config: dict, workdir: Path) -> tuple[Path, dict]:
    commit = git("rev-parse", config.get("commit", "HEAD"))
    short = commit[:7]
    run_commits = {name: git("rev-parse", ref) for name, ref in config["run_commits"].items()}
    for name, run_commit in run_commits.items():
        changed = pipeline_changes(run_commit, commit)
        if changed:
            raise SystemExit(f"{name} ran on {run_commit[:7]} but research code changed since: {changed}")
    tracked_dirty = git("status", "--porcelain", "--untracked-files=no")
    if tracked_dirty and not config.get("allow_dirty_tracked"):
        raise SystemExit(f"tracked files differ from HEAD; commit first:\n{tracked_dirty}")

    created = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = created.strftime("%Y%m%d")
    payload = workdir / TOP
    payload.mkdir(parents=True)

    # -- source ------------------------------------------------------------------
    source_files = export_source(commit, payload / "source")

    # -- data --------------------------------------------------------------------
    data = config["data"]
    evidence_root = ROOT / config["evidence"]["snapshot_root"]
    allowed_classes = set(config["evidence"]["redistribute_source_classes"])
    (payload / "data").mkdir()
    shutil.copyfile(ROOT / data["entry_companies"], payload / "data/entry-companies.jsonl")
    shutil.copyfile(ROOT / data["organisation_numbers"], payload / "data/organisation-numbers.txt")
    shutil.copyfile(ROOT / data["selection"], payload / "data/selection.json")

    included_bodies: dict[str, str] = {}
    source_rows: dict[str, dict] = {}
    profile_count = 0
    terminal: dict[str, int] = {}

    def profiles():
        nonlocal profile_count
        for envelope in read_jsonl(ROOT / data["profiles"]):
            profile_count += 1
            state = envelope["run"]["terminal_status"]
            terminal[state] = terminal.get(state, 0) + 1
            for snapshot in envelope.get("snapshots") or []:
                digest = snapshot["content_sha256"]
                stored = (snapshot.get("stored_path") or "").replace("\\", "/")
                redistributable = snapshot.get("source_class") in allowed_classes
                if redistributable and stored and digest not in included_bodies:
                    body = evidence_root / stored.split("/", 1)[1]
                    if body.is_file():
                        target = payload / "evidence/snapshots" / digest[:2] / f"{digest}.bin"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(body, target)
                        included_bodies[digest] = target.relative_to(payload).as_posix()
                source_rows.setdefault(digest, {
                    "content_sha256": digest,
                    "source_class": snapshot.get("source_class"),
                    "final_url": snapshot.get("final_url"),
                    "status": snapshot.get("status"),
                    "content_type": snapshot.get("content_type"),
                    "retrieved_at": snapshot.get("retrieved_at"),
                    "body_in_package": None,
                    "organisation_numbers": [],
                })["organisation_numbers"].append(envelope["organisation_number"])
                snapshot["body_in_package"] = digest in included_bodies
            yield normalise_envelope(envelope, "evidence/snapshots")

    views = []

    def envelopes_and_views():
        for envelope in profiles():
            views.append(profile_view(envelope))
            yield envelope

    write_jsonl(payload / "data/envelopes.jsonl", envelopes_and_views())
    write_jsonl(payload / "data/profiles.jsonl", views)
    for digest, row in source_rows.items():
        row["body_in_package"] = included_bodies.get(digest)
    write_jsonl(payload / "data/source-manifest.jsonl", source_rows.values())

    # -- refresh -----------------------------------------------------------------
    refresh = config["refresh"]
    (payload / "refresh").mkdir()
    current = list(read_jsonl(ROOT / refresh["current"]))
    current_orgs = {e["organisation_number"] for e in current}
    previous = [e for e in read_jsonl(ROOT / refresh["previous"]) if e["organisation_number"] in current_orgs]
    write_jsonl(payload / "refresh/previous-snapshot.jsonl", (normalise_envelope(e, None) for e in previous))
    write_jsonl(payload / "refresh/current-snapshot.jsonl", (normalise_envelope(e, None) for e in current))
    changes = [
        {"organisation_number": e["organisation_number"], **change}
        for e in current for change in e.get("changes") or []
    ]
    write_jsonl(payload / "refresh/changes.jsonl", changes)
    refresh_report = json.loads((ROOT / refresh["report"]).read_text(encoding="utf-8"))
    refresh_report["package_note"] = refresh["label"]
    (payload / "refresh/refresh-report.json").write_text(
        json.dumps(refresh_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # -- reports and docs --------------------------------------------------------
    (payload / "reports").mkdir()
    for name, relative in config["reports"].items():
        shutil.copyfile(ROOT / relative, payload / "reports" / name)
    (payload / "docs").mkdir()
    for relative in config["docs"]:
        target = payload / "docs" / Path(relative).name
        if target.exists():
            raise SystemExit(f"two documents share the name {target.name}")
        shutil.copyfile(ROOT / relative, target)
    for relative in config["top_level"]:
        shutil.copyfile(ROOT / relative, payload / Path(relative).name)

    # -- manifest ----------------------------------------------------------------
    orgs = [l.strip() for l in (ROOT / data["organisation_numbers"]).read_text(encoding="utf-8").splitlines() if l.strip()]
    selection = json.loads((ROOT / data["selection"]).read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "signalpost.submission_manifest/1",
        "agent_name": config["agent_name"],
        "repository_url": config["repository_url"],
        "source_commit": commit,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "unique_inputs": len(set(orgs)),
        "profiles": profile_count,
        "envelopes": profile_count,
        "profiles_file_note": (
            "data/envelopes.jsonl is the full audit record (claims, evidence, snapshots). "
            "data/profiles.jsonl is a compact view derived from those claims with no added values."
        ),
        "terminal_states": terminal,
        "eligible_universe_sha256": selection["universe"]["sha256"],
        "organisation_number_manifest_sha256": sha256_file(ROOT / data["organisation_numbers"]),
        "selection_manifest_sha256": selection["manifest_sha256"],
        "selection_manifest_sha256_note": (
            "selection.json hashes the numbers joined by newlines without a trailing newline; "
            "organisation_number_manifest_sha256 is the file bytes, which end in a newline."
        ),
        "dependency_lock_sha256": sha256_file(payload / "source/uv.lock"),
        "dataset_hashes": {
            "data/envelopes.jsonl": sha256_file(payload / "data/envelopes.jsonl"),
            "data/profiles.jsonl": sha256_file(payload / "data/profiles.jsonl"),
            "data/source-manifest.jsonl": sha256_file(payload / "data/source-manifest.jsonl"),
            "data/entry-companies.jsonl": sha256_file(payload / "data/entry-companies.jsonl"),
            "data/organisation-numbers.txt": sha256_file(payload / "data/organisation-numbers.txt"),
        },
        "source_files": source_files,
        "run_commits": run_commits,
        "run_commits_note": (
            "Each run's commit differs from source_commit in no file under main.py or "
            "src/signalpost (UI excluded), and resolves the same locked runtime dependencies "
            "(uv export --frozen --no-dev, no extras); checked at packaging."
        ),
        "evidence_bodies_included": len(included_bodies),
        "evidence_bodies_referenced": len(source_rows),
        "evidence_redistributed_source_classes": sorted(allowed_classes),
        "evaluator_command": config["evaluator_command"],
        "required_environment_variables": [],
        "models_and_apis": config["models_and_apis"],
        "cost_estimate": config["cost_estimate"],
        "access_status": config["access_status"],
        "readiness": config["readiness"],
        "readiness_blockers": config["readiness_blockers"],
        "official_qualification": "not_determined",
    }
    (payload / "submission-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # -- email draft: generated before checksums, points at the external checksum file --
    zip_name = f"signalpost-submission-{stamp}-{short}.zip"
    live = json.loads((payload / "reports/live-100-report.json").read_text(encoding="utf-8"))
    subject, body = render_email(manifest, config, live, refresh_report, zip_name,
                                 f"see {zip_name}.sha256, delivered beside the ZIP")
    write_email(payload / "email", "submission-email", subject, body, config["personal"])

    # -- checksums: every payload file except the checksum file itself -----------
    lines = []
    for path in sorted(p for p in payload.rglob("*") if p.is_file()):
        relative = path.relative_to(payload).as_posix()
        lines.append(f"{sha256_file(path)}  {relative}")
    (payload / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- zip ---------------------------------------------------------------------
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    zip_path = dist / f"signalpost-submission-{stamp}-{short}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in payload.rglob("*") if p.is_file()):
            archive.write(path, (Path(TOP) / path.relative_to(payload)).as_posix())
    checksum_path = zip_path.with_name(zip_path.name + ".sha256")
    zip_sha = sha256_file(zip_path)
    checksum_path.write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    subject, body = render_email(manifest, config, live, refresh_report, zip_path.name, zip_sha)
    write_email(dist, zip_path.stem + "-email-final", subject, body, config["personal"])
    return zip_path, manifest


def verify(zip_path: Path, config: dict) -> dict:
    """Extract into a fresh directory and check everything a reviewer would."""
    results: dict = {"zip": zip_path.name, "checks": {}, "problems": []}
    expected = zip_path.with_name(zip_path.name + ".sha256").read_text(encoding="utf-8").split()[0]
    results["checks"]["external_checksum_matches"] = sha256_file(zip_path) == expected

    with tempfile.TemporaryDirectory(prefix="signalpost-verify-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                if not safe_member(info.filename) or not info.filename.startswith(TOP + "/"):
                    results["problems"].append(f"unsafe or misplaced member: {info.filename}")
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    results["problems"].append(f"symlink member: {info.filename}")
                    continue
            archive.extractall(root)
        payload = root / TOP

        mismatches = 0
        listed = set()
        for line in (payload / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            listed.add(relative)
            if sha256_file(payload / relative) != digest:
                mismatches += 1
        actual = {p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file()}
        unlisted = sorted(actual - listed - {"SHA256SUMS.txt"})
        results["checks"]["payload_checksums_match"] = mismatches == 0
        results["checks"]["every_payload_file_listed"] = not unlisted
        if unlisted:
            results["problems"].append(f"files missing from SHA256SUMS.txt: {unlisted[:10]}")

        parsed = 0
        for path in payload.rglob("*"):
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
                parsed += 1
            elif path.suffix == ".jsonl":
                for _ in read_jsonl(path):
                    pass
                parsed += 1
        results["checks"]["json_files_parsed"] = parsed

        manifest = json.loads((payload / "submission-manifest.json").read_text(encoding="utf-8"))
        orgs = [l.strip() for l in (payload / "data/organisation-numbers.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
        envelope_orgs = [e["organisation_number"] for e in read_jsonl(payload / "data/envelopes.jsonl")]
        profile_orgs = [e["organisation_number"] for e in read_jsonl(payload / "data/profiles.jsonl")]
        results["checks"]["envelopes_match_manifest_exactly"] = (
            len(envelope_orgs) == len(set(envelope_orgs)) and set(envelope_orgs) == set(orgs)
        )
        results["checks"]["profiles_match_envelopes_in_order"] = profile_orgs == envelope_orgs
        results["checks"]["manifest_counts_consistent"] = (
            manifest["profiles"] == len(envelope_orgs) and manifest["unique_inputs"] == len(set(orgs))
        )
        results["checks"]["selection_digest_matches_numbers"] = (
            sha256_bytes("\n".join(orgs).encode("utf-8")) == manifest["selection_manifest_sha256"]
            and sha256_file(payload / "data/organisation-numbers.txt") == manifest["organisation_number_manifest_sha256"]
        )
        results["checks"]["source_commit_is_full_hash"] = bool(re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"]))

        unresolved = dangling_bodies = absolute = 0
        for envelope in read_jsonl(payload / "data/envelopes.jsonl"):
            evidence = {e["id"] for e in envelope.get("evidence") or []}
            snapshots = {s["id"]: s for s in envelope.get("snapshots") or []}
            for claim in envelope.get("claims") or []:
                unresolved += sum(1 for i in claim.get("evidence_ids") or [] if i not in evidence)
            for record in envelope.get("evidence") or []:
                if record.get("snapshot_id") and record["snapshot_id"] not in snapshots:
                    unresolved += 1
            for snapshot in snapshots.values():
                stored = snapshot.get("stored_path")
                if stored and (stored.startswith("/") or "\\" in stored or re.match(r"^[A-Za-z]:", stored)):
                    absolute += 1
                if snapshot.get("body_in_package") and not (payload / stored).is_file():
                    dangling_bodies += 1
        results["checks"]["evidence_references_resolve"] = unresolved == 0
        results["checks"]["packaged_evidence_bodies_present"] = dangling_bodies == 0
        results["checks"]["no_machine_specific_paths"] = absolute == 0
        if unresolved:
            results["problems"].append(f"{unresolved} unresolved evidence references")

        hits = []
        for path in payload.rglob("*"):
            if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name.startswith(".env")):
                text = path.read_text(encoding="utf-8", errors="ignore")
                for name, pattern in SECRET_PATTERNS.items():
                    if pattern.search(text):
                        hits.append(f"{name} in {path.relative_to(payload).as_posix()}")
        forbidden = [p.relative_to(payload).as_posix() for p in payload.rglob("*")
                     if p.name in {".env", ".git", ".venv", "node_modules", "__pycache__"}]
        results["checks"]["no_secret_patterns"] = not hits
        results["checks"]["no_forbidden_paths"] = not forbidden
        results["problems"].extend(hits + [f"forbidden path: {f}" for f in forbidden])

        email_text = (payload / "email/submission-email.txt").read_text(encoding="utf-8")
        results["email_placeholders"] = sorted(set(PLACEHOLDER.findall(email_text)))
        results["email_labelled_draft"] = email_text.startswith("DRAFT — NOT READY TO SEND")
        results["checks"]["email_has_no_technical_placeholders"] = not re.search(
            r"\bNone\b|\{[a-z_]+\}|TODO|TBD", email_text)
        results["checks"]["email_eml_parses"] = bool(
            email.message_from_bytes((payload / "email/submission-email.eml").read_bytes())["To"])

        smoke = config.get("smoke_command")
        if smoke:
            completed = subprocess.run(smoke, cwd=payload / "source", shell=True, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=1800)
            results["checks"]["packaged_source_smoke_run_exit_zero"] = completed.returncode == 0
            results["smoke"] = {"command": smoke, "exit_code": completed.returncode,
                                "stdout_tail": completed.stdout[-1500:], "stderr_tail": completed.stderr[-1500:]}

    results["ok"] = all(v is True for v in results["checks"].values() if isinstance(v, bool)) and not results["problems"]
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--verify-only", type=Path, help="Verify an existing ZIP instead of building")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))

    if args.verify_only:
        zip_path = args.verify_only
    else:
        with tempfile.TemporaryDirectory(prefix="signalpost-build-") as temp:
            zip_path, manifest = build(config, Path(temp))
        print(f"built {zip_path.relative_to(ROOT).as_posix()} ({zip_path.stat().st_size / 1e6:.1f} MB)")

    results = verify(zip_path, config)
    results["verified_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report = ROOT / "dist" / (zip_path.stem + "-verification.json")
    report.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: results[k] for k in ("checks", "problems", "ok")}, indent=2))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
