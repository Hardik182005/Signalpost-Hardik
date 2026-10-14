"""The submission packager and review queue: portable paths, safe extraction, no invented values."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "signalpost"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package = load("package_submission")
review = load("build_review_queue")
evaluation = load("write_evaluation_report")
batch = load("run_batch")


def envelope(**overrides) -> dict:
    base = {
        "organisation_number": "998645417",
        "run": {"run_id": "r1", "terminal_status": "partial"},
        "identity": {"names": ["REGIONPLANKONTORET AS"], "website_verdict": None},
        "operations": {"terminal_reason": "No company website could be verified"},
        "synthesis": {"summary": "A summary."},
        "claims": [
            {"claim_key": "998645417:revenue", "field": "revenue", "family": "financials", "value": 0,
             "availability": "available", "currency": "NOK", "unit": "NOK", "reporting_period": "2025",
             "confidence": 1.0, "evidence_ids": ["ev-1"]},
            {"claim_key": "998645417:employees", "field": "employees", "family": "legal_identity",
             "value": None, "availability": "not_available", "reason": "not reported"},
        ],
        "evidence": [{"id": "ev-1", "snapshot_id": "snap-1", "source_url": "https://data.brreg.no/x",
                      "source_class": "official_accounts", "retrieved_at": "2026-09-14T09:00:00Z",
                      "content_sha256": "ab" * 32, "locator": {"kind": "json_pointer", "value": "/a"}}],
        "snapshots": [{"id": "snap-1", "content_sha256": "ab" * 32, "source_class": "official_accounts",
                       "stored_path": "snapshots-submission\\ab\\" + "ab" * 32 + ".bin"}],
    }
    base.update(overrides)
    return base


class TestPortablePaths:
    def test_packaged_body_gets_a_forward_slash_package_path(self):
        record = envelope()
        record["snapshots"][0]["body_in_package"] = True
        package.normalise_envelope(record, "evidence/snapshots")
        assert record["snapshots"][0]["stored_path"] == "evidence/snapshots/ab/" + "ab" * 32 + ".bin"

    def test_body_left_out_of_the_package_has_no_path_to_dangle(self):
        record = envelope()
        record["snapshots"][0]["body_in_package"] = False
        package.normalise_envelope(record, "evidence/snapshots")
        assert record["snapshots"][0]["stored_path"] is None
        assert record["snapshots"][0]["content_sha256"] == "ab" * 32


class TestProfileView:
    def test_view_copies_claims_without_adding_values(self):
        view = package.profile_view(envelope())
        assert view["fields"]["revenue"]["value"] == 0  # a reported zero stays zero
        assert view["fields"]["employees"] == {"availability": "not_available", "reason": "not reported"}
        assert view["terminal_status"] == "partial"


class TestSafeMembers:
    @pytest.mark.parametrize("name", ["../evil.txt", "/etc/passwd", "C:/Windows/x", "a/../../b"])
    def test_traversal_and_absolute_names_are_refused(self, name):
        assert not package.safe_member(name)

    def test_ordinary_nested_name_is_allowed(self):
        assert package.safe_member("signalpost-submission/data/envelopes.jsonl")


class TestReviewQueue:
    def test_abstentions_and_misses_are_sampled_not_only_publications(self):
        ambiguous = envelope(identity={"names": ["X AS"], "website_verdict": {
            "candidate": "https://x.no/", "decision": "ambiguous", "confidence": 0.4, "reason": "weak"}})
        strata = {stratum for stratum, _ in review.candidates(ambiguous)}
        assert "website_withheld_ambiguous" in strata
        assert "website_none_found" in {stratum for stratum, _ in review.candidates(envelope())}

    def test_financial_item_keeps_currency_unit_period_and_evidence(self):
        items = [item for stratum, item in review.candidates(envelope()) if stratum == "financial_revenue"]
        assert items[0]["currency"] == "NOK" and items[0]["reporting_period"] == "2025"
        assert items[0]["evidence"][0]["source_url"] == "https://data.brreg.no/x"

    def test_ranking_is_deterministic(self):
        assert review.rank("seed", "k") == review.rank("seed", "k") != review.rank("other", "k")


class TestEvaluationReport:
    def write(self, tmp_path, name, data):
        path = tmp_path / name
        path.write_text(__import__("json").dumps(data), encoding="utf-8")
        return str(path)

    def config(self, tmp_path, resources=None):
        run = {
            "results": {"started_at": "a", "completed_at": "b", "envelopes": 2, "verified_websites": 1,
                        "terminal_states": {"completed": 1, "partial": 1},
                        "seconds_per_company": {"median": 1, "p90": 2, "max": 3}, "budget_exhausted_after": None,
                        "changes_total": 0, "changes_material": 0},
            "verification": {"envelopes": 2, "expected": 2, "exactly_one_per_input": True, "duplicates": [],
                             "missing_count": 0, "unexpected_count": 0, "checked_at": "c"},
            "budget": {"seconds_elapsed": 9.0, "requests_used": 12, "requests_per_company": 6.0,
                       "spend_usd": 0.0, "by_purpose": {"registry_entity": 2}},
            "config": {"concurrency": 2, "previous_snapshot": None},
        }
        integrity = {"totals": {"envelopes": 2, "claims": 5, "claims_available": 4, "evidence": 4, "snapshots": 3},
                     "snapshot_bodies_rehashed": 3, "problems": {}, "ok": True, "input_sha256": "d",
                     "checked_at": "e", "network_requests": 0, "companies_with_available_family": {"people": 0},
                     "available_claims_by_family": {}, "claim_availability": {"available": 4}}
        summary = {"items": 0, "envelopes": 2, "seed": "s", "input_sha256": "f", "selection": "g",
                   "population_by_stratum": {}, "drawn_by_stratum": {}, "review_protocol": "h",
                   "review_status": "unreviewed", "reviewed_items": 0, "metrics": "unverified"}
        report = self.write(tmp_path, "run.json", run)
        limits = {"requests": 2000, "seconds": 2700, "spend_usd": 10, "vcpu": 8, "memory_gib": 16, "disk_gib": 10}
        return {
            "generated_for": "test", "environment": {}, "tests": {"command": "t", "commit": "c", "result": "r"},
            "corpus": {"label": "corpus", "command": "x", "commit": "c", "report": report,
                       "integrity": self.write(tmp_path, "i.json", integrity),
                       "idempotency": self.write(tmp_path, "d.json", {"false_changes": 0, "claims_replayed": 5,
                                                                      "idempotent": True})},
            "live": {"label": "live", "command": "y", "commit": "c", "report": report, "limits": limits,
                     "resources": resources and self.write(tmp_path, "res.json", resources)},
            "review": {"summary": self.write(tmp_path, "rq.json", summary)},
        }

    def test_unmeasured_resources_are_unavailable_not_zero(self, tmp_path):
        text = evaluation.render(self.config(tmp_path))
        assert "unavailable (not measured)" in text
        assert "| Peak memory |" not in text

    def test_zero_coverage_is_shown_and_no_accuracy_is_claimed(self, tmp_path):
        text = evaluation.render(self.config(tmp_path))
        assert "| people | 0/2 (0.0%) | 0 |" in text
        assert "**Unavailable.**" in text and "`not_determined`" in text

    def test_measured_resources_are_reported_with_their_source(self, tmp_path):
        resources = {"peak_memory_mib": 300.5, "peak_memory_source": "job object", "mean_cpu_cores_used": 0.4,
                     "cpu_user_seconds": 1, "cpu_system_seconds": 1, "host": {"logical_cpus": 8},
                     "disk_mib_total": 12.0}
        text = evaluation.render(self.config(tmp_path, resources))
        assert "| Peak memory | 300.5 MiB (job object) | 16 GiB |" in text


class TestRunBatch:
    def test_starter_input_forms_are_accepted_in_order(self, tmp_path):
        jsonl = tmp_path / "batch.jsonl"
        jsonl.write_text('{"organisation_number": "998 645 417"}\n\n{"organisation_number": 979498047}\n', encoding="utf-8")
        listing = tmp_path / "batch.json"
        listing.write_text('{"organisation_numbers": ["979498047", "998645417"]}', encoding="utf-8")
        text = tmp_path / "batch.txt"
        text.write_text("# header\n998645417\n", encoding="utf-8")
        assert batch.read_organisations(jsonl) == ["998645417", "979498047"]
        assert batch.read_organisations(listing) == ["979498047", "998645417"]
        assert batch.read_organisations(text) == ["998645417"]

    @pytest.mark.parametrize("body", ['{"organisation_number": "12345"}\n', '{"name": "no number"}\n',
                                      '{"organisation_number": "998645417"}\n{"organisation_number": "998645417"}\n'])
    def test_malformed_or_duplicate_rows_refuse_the_batch(self, tmp_path, body):
        path = tmp_path / "batch.jsonl"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(batch.BatchError):
            batch.read_organisations(path)

    def test_count_mismatch_is_refused_before_any_run(self, tmp_path, capsys):
        path = tmp_path / "batch.jsonl"
        path.write_text('{"organisation_number": "998645417"}\n', encoding="utf-8")
        code = batch.main(["--organisations", str(path), "--output", str(tmp_path / "out.jsonl"), "--expected-count", "100"])
        assert code == 2 and "expected 100" in capsys.readouterr().err
        assert not (tmp_path / "out-inputs.txt").exists()

    def test_limit_cannot_silently_truncate_the_batch(self, tmp_path):
        path = tmp_path / "batch.jsonl"
        path.write_text('{"organisation_number": "998645417"}\n', encoding="utf-8")
        assert batch.main(["--organisations", str(path), "--output", str(tmp_path / "o.jsonl"), "--limit", "1"]) == 2


class TestRelink:
    def test_links_follow_documents_to_their_packaged_locations(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "runner.py").write_text("", encoding="utf-8")
        locations = {"EVAL.md": "docs/EVAL.md", "docs/SECURITY.md": "docs/SECURITY.md",
                     "SUBMISSION_CHECKLIST.md": "SUBMISSION_CHECKLIST.md"}
        text = "[a](../EVAL.md#runs) [b](../SUBMISSION_CHECKLIST.md) [c](../src/runner.py) [d](https://x.no) [e](missing.md)"
        out = package.relink(text, "docs/SECURITY.md", "docs/SECURITY.md", locations, tmp_path)
        assert out == "[a](EVAL.md#runs) [b](../SUBMISSION_CHECKLIST.md) [c](../source/src/runner.py) [d](https://x.no) [e](missing.md)"

    def test_top_level_document_links_into_docs(self, tmp_path):
        locations = {"docs/SECURITY.md": "docs/SECURITY.md", "SUBMISSION_README.md": "SUBMISSION_README.md"}
        out = package.relink("[s](docs/SECURITY.md)", "SUBMISSION_README.md", "SUBMISSION_README.md", locations, tmp_path)
        assert out == "[s](docs/SECURITY.md)"
