"""Reproducible selection of the companies to research.

The evaluator draws its own inputs from the eligible universe. A corpus that
cherry-picked companies which are easy to research — the 10.9% that publish a website
in the registry, say — would score well locally and badly under evaluation, and would
misrepresent what the agent can do. So selection here is **stratified and
proportional**: the corpus mirrors the universe's industry mix, and within each
stratum membership is decided by a keyed hash of the organisation number.

That makes the sample deterministic and auditable. Given the same universe file and
the same seed, `select()` returns the same organisation numbers on any machine, and
`selection_manifest()` records the input checksum, the seed and the algorithm so a
reviewer can regenerate it and compare.

Nothing here decides whether a company is *interesting*. It decides only which
companies are *in scope*, before anything is known about them.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .identity import valid_org_number
from .models import utc_now

DEFAULT_SEED = "signalpost-round-1"
SELECTION_ALGORITHM = "stratified-proportional-sha256-v1"


@dataclass(slots=True)
class UniverseRow:
    """One eligible company as published by the organisers."""

    organisation_number: str
    name: str
    legal_form: str
    industry_code: str
    industry_label: str
    municipality: str | None = None
    municipality_number: str | None = None
    employees: int | None = None
    website: str | None = None
    latest_submitted_accounts: str | None = None

    @property
    def sector(self) -> str:
        """Two-digit NACE division — the stratification key."""
        return (self.industry_code or "00")[:2]

    def to_dict(self) -> dict[str, Any]:
        return {
            "organisation_number": self.organisation_number,
            "name": self.name,
            "legal_form": self.legal_form,
            "industry_code": self.industry_code,
            "industry_label": self.industry_label,
            "municipality": self.municipality,
            "employees": self.employees,
            "registry_website": self.website or None,
        }


def _open(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def read_universe(path: Path) -> Iterator[UniverseRow]:
    """Stream the eligible universe, skipping rows that fail basic validation.

    An organisation number that fails the MOD-11 check digit cannot be a real
    Norwegian entity, so it is dropped here rather than wasting a request later.
    """
    with _open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            org = str(record.get("organisation_number") or "").strip()
            if not valid_org_number(org):
                continue
            employees = record.get("employees")
            yield UniverseRow(
                organisation_number=org,
                name=str(record.get("name") or "").strip(),
                legal_form=str(record.get("legal_form") or "").strip(),
                industry_code=str(record.get("industry_code") or "").strip(),
                industry_label=str(record.get("industry_label") or "").strip(),
                municipality=(record.get("municipality") or None),
                municipality_number=(record.get("municipality_number") or None),
                employees=int(employees) if isinstance(employees, (int, float)) else None,
                website=(str(record.get("website")).strip() or None)
                if record.get("website")
                else None,
                latest_submitted_accounts=record.get("latest_submitted_accounts"),
            )


def rank_key(organisation_number: str, seed: str) -> str:
    """Deterministic, uniformly distributed ordering key.

    A hash rather than the organisation number itself: organisation numbers are issued
    sequentially, so sorting by them would bias the corpus towards older companies.
    """
    digest = hashlib.sha256(f"{seed}:{organisation_number}".encode("utf-8")).hexdigest()
    return digest


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class Selection:
    """The chosen corpus plus everything needed to reproduce it."""

    rows: list[UniverseRow] = field(default_factory=list)
    seed: str = DEFAULT_SEED
    universe_path: str = ""
    universe_sha256: str = ""
    universe_rows: int = 0
    strata: dict[str, int] = field(default_factory=dict)
    generated_at: str = field(default_factory=utc_now)

    @property
    def organisation_numbers(self) -> list[str]:
        return [row.organisation_number for row in self.rows]

    def manifest(self) -> dict[str, Any]:
        """The reproducibility record. Regenerating with these inputs must match."""
        digest = hashlib.sha256(
            "\n".join(self.organisation_numbers).encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": "signalpost.selection/1",
            "generated_at": self.generated_at,
            "algorithm": SELECTION_ALGORITHM,
            "seed": self.seed,
            "universe": {
                "path": self.universe_path,
                "sha256": self.universe_sha256,
                "eligible_rows": self.universe_rows,
            },
            "selected": len(self.rows),
            "manifest_sha256": digest,
            "strata": dict(sorted(self.strata.items())),
            "composition": self.composition(),
        }

    def composition(self) -> dict[str, Any]:
        """How the corpus is shaped — reported so it can be compared to the universe."""
        forms = Counter(row.legal_form for row in self.rows)
        municipalities = Counter(row.municipality or "unknown" for row in self.rows)
        with_site = sum(1 for row in self.rows if row.website)
        with_employees = sum(1 for row in self.rows if row.employees is not None)
        return {
            "legal_forms": dict(forms.most_common(12)),
            "distinct_sectors": len({row.sector for row in self.rows}),
            "distinct_municipalities": len(municipalities),
            "top_municipalities": dict(municipalities.most_common(8)),
            "with_registry_website": with_site,
            "with_registry_website_pct": round(with_site / max(len(self.rows), 1) * 100, 2),
            "with_employee_count": with_employees,
        }


def select(
    universe_path: Path,
    *,
    count: int = 1000,
    seed: str = DEFAULT_SEED,
    exclude: Iterable[str] = (),
) -> Selection:
    """Choose `count` companies, proportionally across industry sectors.

    Largest-remainder allocation: each sector gets its proportional share of the
    corpus, and the seats left over by rounding go to the sectors with the largest
    fractional remainders. This keeps the corpus's industry mix close to the
    universe's instead of over-weighting whichever sector happens to sort first.
    """
    excluded = {str(item) for item in exclude}
    by_sector: dict[str, list[UniverseRow]] = defaultdict(list)
    total = 0
    for row in read_universe(universe_path):
        if row.organisation_number in excluded:
            continue
        total += 1
        by_sector[row.sector].append(row)

    if total == 0:
        raise ValueError(f"No eligible rows found in {universe_path}")
    count = min(count, total)

    # Proportional allocation, largest remainder for the leftover seats.
    exact = {sector: len(rows) / total * count for sector, rows in by_sector.items()}
    allocation = {sector: int(value) for sector, value in exact.items()}
    remaining = count - sum(allocation.values())
    for sector, _ in sorted(
        exact.items(), key=lambda item: (item[1] - int(item[1]), item[0]), reverse=True
    ):
        if remaining <= 0:
            break
        allocation[sector] += 1
        remaining -= 1

    chosen: list[UniverseRow] = []
    for sector, quota in allocation.items():
        if quota <= 0:
            continue
        ordered = sorted(
            by_sector[sector], key=lambda row: rank_key(row.organisation_number, seed)
        )
        chosen.extend(ordered[:quota])

    # Final ordering is by the same hash, so the corpus itself has no sector clumping
    # and a partial run is still a representative sample.
    chosen.sort(key=lambda row: rank_key(row.organisation_number, seed))

    return Selection(
        rows=chosen,
        seed=seed,
        universe_path=str(universe_path).replace("\\", "/"),
        universe_sha256=file_checksum(universe_path),
        universe_rows=total,
        strata={sector: value for sector, value in sorted(allocation.items()) if value},
    )


def write_selection(selection: Selection, directory: Path) -> dict[str, Path]:
    """Write the corpus, the organisation-number manifest and the provenance record."""
    directory.mkdir(parents=True, exist_ok=True)
    corpus_path = directory / "corpus.jsonl"
    manifest_path = directory / "organisation-numbers.txt"
    provenance_path = directory / "selection.json"

    with open(corpus_path, "w", encoding="utf-8", newline="\n") as handle:
        for row in selection.rows:
            handle.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")

    # Plain text, one number per line: the format the submission form asks for.
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
        for org in selection.organisation_numbers:
            handle.write(org + "\n")

    with open(provenance_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(selection.manifest(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    return {"corpus": corpus_path, "manifest": manifest_path, "provenance": provenance_path}


def load_corpus(path: Path) -> list[UniverseRow]:
    """Read back a written corpus file."""
    rows: list[UniverseRow] = []
    with _open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            rows.append(
                UniverseRow(
                    organisation_number=record["organisation_number"],
                    name=record.get("name", ""),
                    legal_form=record.get("legal_form", ""),
                    industry_code=record.get("industry_code", ""),
                    industry_label=record.get("industry_label", ""),
                    municipality=record.get("municipality"),
                    employees=record.get("employees"),
                    website=record.get("registry_website"),
                )
            )
    return rows
