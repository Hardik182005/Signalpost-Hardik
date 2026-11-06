#!/usr/bin/env python3
"""Build and verify the November 2026 revision ZIP.

    uv run python scripts/signalpost/package_update.py --config packaging/nov2026.json

A narrow adaptation of `package_submission.py` for a revision that no longer has to
ship the 1,000-company dataset: it reuses that script's source export (`git archive`
of one commit, links and unsafe paths refused), its research-code consistency guard,
its secret patterns and its checksum convention, and changes only the payload layout.

The ZIP is a delivery convenience, not an organiser-mandated format.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from package_submission import (  # noqa: E402
    ROOT,
    SECRET_PATTERNS,
    TEXT_SUFFIXES,
    export_source,
    git,
    pipeline_changes,
    read_jsonl,
    safe_member,
    sha256_file,
)

TOP = "submission"
PLACEHOLDER = re.compile(r"<[^<>\n]{3,60}>")


def copy(source: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / source, target)


def build(config: dict, workdir: Path) -> Path:
    commit = git("rev-parse", "HEAD")
    run_commit = git("rev-parse", config["run_commit"])
    changed = pipeline_changes(run_commit, commit)
    if changed:
        raise SystemExit(f"smoke run used {run_commit[:7]} but research code changed since: {changed}")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise SystemExit(f"tracked files differ from HEAD; commit first:\n{dirty}")

    created = datetime.now(timezone.utc).replace(microsecond=0)
    payload = workdir / TOP
    payload.mkdir(parents=True)
    source_files = export_source(commit, payload / "source")

    for section in ("smoke100", "verification", "website", "disclosures", "email", "top"):
        for name, source in config["files"].get(section, {}).items():
            copy(source, (payload if section == "top" else payload / section) / name)

    smoke = payload / "smoke100"
    envelopes = list(read_jsonl(smoke / "envelopes.jsonl"))
    inputs = [line.strip() for line in (smoke / "inputs.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    report = json.loads((smoke / "run-report.json").read_text(encoding="utf-8"))
    replay = json.loads((smoke / "refresh-validation.json").read_text(encoding="utf-8"))
    terminal: dict[str, int] = {}
    for envelope in envelopes:
        status = envelope["run"]["terminal_status"]
        terminal[status] = terminal.get(status, 0) + 1

    manifest = {
        "schema_version": "signalpost.update_manifest/1",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "agent_repository": config["repository_url"],
        "research_commit": commit,
        "smoke_run_commit": run_commit,
        "source_files": source_files,
        "smoke_test": {
            "run_id": envelopes[0]["run"]["run_id"] if envelopes else None,
            "unique_inputs": len(set(inputs)),
            "envelopes": len(envelopes),
            "terminal_states": terminal,
            "run_report": report,
            "refresh_validation_passed": replay.get("passed"),
        },
        **config["manifest"],
    }
    (payload / "SUBMISSION_MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                                      encoding="utf-8")

    lines = [f"{sha256_file(p)}  {p.relative_to(payload).as_posix()}"
             for p in sorted(payload.rglob("*")) if p.is_file()]
    (payload / "CHECKSUMS.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    zip_path = dist / f"Signalpost_Hardik_Update_{created:%Y%m%d}_{commit[:7]}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in payload.rglob("*") if p.is_file()):
            archive.write(path, (Path(TOP) / path.relative_to(payload)).as_posix())
    zip_path.with_name(zip_path.name + ".sha256").write_text(f"{sha256_file(zip_path)}  {zip_path.name}\n",
                                                             encoding="utf-8")
    return zip_path


def verify(zip_path: Path) -> dict:
    results: dict = {"zip": zip_path.name, "checks": {}, "problems": [], "notes": []}
    expected = zip_path.with_name(zip_path.name + ".sha256").read_text(encoding="utf-8").split()[0]
    results["checks"]["external_checksum_matches"] = sha256_file(zip_path) == expected
    with tempfile.TemporaryDirectory(prefix="signalpost-verify-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(zip_path) as archive:
            bad = [i.filename for i in archive.infolist()
                   if not safe_member(i.filename) or not i.filename.startswith(TOP + "/")
                   or (i.external_attr >> 16) & 0o170000 == 0o120000]
            results["checks"]["safe_members_only"] = not bad
            if bad:
                results["problems"].append(f"unsafe members: {bad[:5]}")
                return results
            archive.extractall(root)
        payload = root / TOP

        listed, mismatches = set(), 0
        for line in (payload / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            listed.add(relative)
            mismatches += sha256_file(payload / relative) != digest
        actual = {p.relative_to(payload).as_posix() for p in payload.rglob("*") if p.is_file()}
        results["checks"]["internal_checksums_match"] = mismatches == 0
        results["checks"]["every_file_listed"] = actual - listed == {"CHECKSUMS.sha256"}

        parsed = 0
        for path in payload.rglob("*"):
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
                parsed += 1
            elif path.suffix == ".jsonl":
                sum(1 for _ in read_jsonl(path))
                parsed += 1
        results["checks"]["json_files_parsed"] = parsed

        manifest = json.loads((payload / "SUBMISSION_MANIFEST.json").read_text(encoding="utf-8"))
        results["checks"]["research_commit_is_full_hash"] = bool(re.fullmatch(r"[0-9a-f]{40}", manifest["research_commit"]))
        results["checks"]["zip_name_matches_commit"] = manifest["research_commit"][:7] in zip_path.name
        inputs = [l.strip() for l in (payload / "smoke100/inputs.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
        envelopes = list(read_jsonl(payload / "smoke100/envelopes.jsonl"))
        orgs = [e["organisation_number"] for e in envelopes]
        results["checks"]["one_envelope_per_input"] = len(orgs) == len(set(orgs)) and set(orgs) == set(inputs)
        unresolved = 0
        for envelope in envelopes:
            evidence = {e["id"] for e in envelope.get("evidence") or []}
            snapshots = {s["id"] for s in envelope.get("snapshots") or []}
            for claim in envelope.get("claims") or []:
                unresolved += sum(1 for i in claim.get("evidence_ids") or [] if i not in evidence)
            unresolved += sum(1 for e in envelope.get("evidence") or [] if e.get("snapshot_id") and e["snapshot_id"] not in snapshots)
        results["checks"]["evidence_references_resolve"] = unresolved == 0

        secrets, local = [], []
        markers = {str(ROOT), ROOT.as_posix(), str(Path.home()), Path.home().as_posix()}
        for path in payload.rglob("*"):
            if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name.startswith(".env")):
                text = path.read_text(encoding="utf-8", errors="ignore")
                secrets += [f"{n} in {path.relative_to(payload).as_posix()}" for n, p in SECRET_PATTERNS.items() if p.search(text)]
                if any(m in text or m in text.replace("\\\\", "\\") for m in markers):
                    local.append(path.relative_to(payload).as_posix())
        forbidden = [p.relative_to(payload).as_posix() for p in payload.rglob("*")
                     if p.name in {".env", ".git", ".venv", "node_modules", "__pycache__"}]
        results["checks"]["no_secret_patterns"] = not secrets
        results["checks"]["no_builder_local_paths"] = not local
        results["checks"]["no_forbidden_paths"] = not forbidden
        results["problems"] += secrets + [f"local path in {p}" for p in local[:10]] + [f"forbidden: {p}" for p in forbidden]

        for draft in sorted((payload / "email").glob("*.md")):
            text = draft.read_text(encoding="utf-8")
            left = sorted(set(PLACEHOLDER.findall(text)) - {"<br>"})
            if left:
                results["notes"].append(f"{draft.name} still has placeholders to fill before sending: {left}")
    results["ok"] = all(v is True for v in results["checks"].values() if isinstance(v, bool)) and not results["problems"]
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.verify_only:
        zip_path = args.verify_only
    else:
        with tempfile.TemporaryDirectory(prefix="signalpost-build-") as temp:
            zip_path = build(config, Path(temp))
        print(f"built {zip_path.relative_to(ROOT).as_posix()} ({zip_path.stat().st_size / 1e6:.1f} MB)")
    results = verify(zip_path)
    results["verified_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    (zip_path.with_name(zip_path.stem + "-verification.json")).write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
