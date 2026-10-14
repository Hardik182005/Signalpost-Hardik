"""Durable run output: uniqueness, resumption and crash tolerance.

The evaluator counts terminal envelopes and requires exactly one per input, so a
duplicate fails qualification as surely as a missing company does. A batch run is also
long enough to be interrupted, which makes resumption and partial-line tolerance part
of correctness rather than convenience.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from signalpost.store import (
    RunStore,
    completed_numbers,
    load_previous,
    read_envelopes,
    verify,
    write_json,
)


def envelope(org: str, *, state: str = "completed") -> dict:
    return {
        "organisation_number": org,
        "run": {"terminal_status": state, "run_id": "t"},
        "claims": [],
        "evidence": [],
        "changes": [],
        "errors": [],
        "operations": {},
    }


class TestUniqueness:
    def test_a_second_envelope_for_one_company_is_refused(self, tmp_path: Path) -> None:
        """Two envelopes for one input is a qualification failure, not a warning."""
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            assert store.write(envelope("918432639")) is True
            assert store.write(envelope("918432639")) is False
            assert store.written == 1
        assert len(list(read_envelopes(path))) == 1

    def test_an_envelope_without_an_organisation_number_is_rejected(self, tmp_path: Path) -> None:
        with RunStore(tmp_path / "run.jsonl") as store:
            with pytest.raises(ValueError):
                store.write({"run": {"terminal_status": "completed"}})

    def test_concurrent_writers_cannot_duplicate_a_company(self, tmp_path: Path) -> None:
        """The pipeline runs companies concurrently; uniqueness is enforced under lock."""
        import threading

        path = tmp_path / "run.jsonl"
        results: list[bool] = []
        barrier = threading.Barrier(8)

        with RunStore(path) as store:
            def attempt() -> None:
                barrier.wait()
                results.append(store.write(envelope("918432639")))

            threads = [threading.Thread(target=attempt) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert results.count(True) == 1
        assert len(list(read_envelopes(path))) == 1


class TestResumption:
    def test_a_re_run_reads_back_what_the_first_attempt_finished(self, tmp_path: Path) -> None:
        """Re-doing finished companies would re-spend the locked request budget."""
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639"))
            store.write(envelope("986270027"))

        assert completed_numbers(path) == {"918432639", "986270027"}

        with RunStore(path) as resumed:
            assert resumed.has("918432639")
            assert not resumed.has("979498047")
            assert resumed.written == 2
            assert resumed.write(envelope("979498047")) is True

        assert completed_numbers(path) == {"918432639", "986270027", "979498047"}

    def test_a_missing_file_resumes_from_empty(self, tmp_path: Path) -> None:
        assert completed_numbers(tmp_path / "absent.jsonl") == set()
        assert list(read_envelopes(tmp_path / "absent.jsonl")) == []


class TestCrashTolerance:
    def test_a_truncated_final_line_does_not_lose_the_intact_ones(self, tmp_path: Path) -> None:
        """A run killed mid-write leaves a partial last line; everything before is good."""
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639"))
            store.write(envelope("986270027"))
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write('{"organisation_number": "9794980')  # killed mid-write

        assert completed_numbers(path) == {"918432639", "986270027"}
        assert len(list(read_envelopes(path))) == 2

    def test_blank_lines_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639"))
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n\n")
        assert len(list(read_envelopes(path))) == 1

    def test_json_is_written_atomically(self, tmp_path: Path) -> None:
        """A crash mid-write must not leave a half-written report behind."""
        path = tmp_path / "reports" / "verification.json"
        write_json(path, {"ok": True})
        write_json(path, {"ok": False, "envelopes": 2})
        assert path.read_text(encoding="utf-8").strip().endswith("}")
        assert not list(path.parent.glob("*.tmp"))


class TestVerification:
    def test_a_clean_run_verifies_against_its_input_list(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        expected = ["918432639", "986270027", "979498047"]
        with RunStore(path) as store:
            for org in expected:
                store.write(envelope(org))

        report = verify(path, expected=expected)
        assert report["ok"]
        assert report["exactly_one_per_input"]
        assert report["envelopes"] == report["unique_companies"] == 3
        assert report["missing_count"] == report["unexpected_count"] == 0

    def test_a_missing_company_fails_the_one_per_input_gate(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639"))

        report = verify(path, expected=["918432639", "986270027"])
        assert not report["ok"]
        assert not report["exactly_one_per_input"]
        assert report["missing"] == ["986270027"]

    def test_a_company_not_asked_for_is_reported(self, tmp_path: Path) -> None:
        """Publishing an envelope for an input that was never given is also a defect."""
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639"))
            store.write(envelope("986270027"))

        report = verify(path, expected=["918432639"])
        assert report["unexpected"] == ["986270027"]
        assert not report["ok"]

    def test_an_invalid_terminal_state_is_reported(self, tmp_path: Path) -> None:
        """Only the five documented terminal states may appear."""
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639", state="in_progress"))

        report = verify(path)
        assert not report["ok"]
        assert report["invalid_terminal_states"]

    def test_a_missing_required_section_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        broken = envelope("918432639")
        del broken["evidence"]
        with RunStore(path) as store:
            store.write(broken)

        report = verify(path)
        assert not report["ok"]
        assert "evidence" in report["envelopes_missing_required_keys"][0]

    def test_terminal_states_are_counted_for_the_operator(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639", state="completed"))
            store.write(envelope("986270027", state="partial"))
            store.write(envelope("979498047", state="partial"))

        assert verify(path)["terminal_states"] == {"completed": 1, "partial": 2}


class TestPreviousRunIndex:
    def test_envelopes_are_indexed_by_company_for_refresh(self, tmp_path: Path) -> None:
        path = tmp_path / "run.jsonl"
        with RunStore(path) as store:
            store.write(envelope("918432639"))
            store.write(envelope("986270027"))

        previous = load_previous(path)
        assert set(previous) == {"918432639", "986270027"}
        assert previous["918432639"]["run"]["terminal_status"] == "completed"


class TestWhichOutputMayBeResumed:
    """Only an interrupted attempt at the same run is resumed; any other file is set aside."""

    def write(self, path: Path, *orgs: str) -> None:
        with RunStore(path) as store:
            for org in orgs:
                store.write(envelope(org))

    def test_an_interrupted_attempt_at_the_same_run_is_resumed(self, tmp_path: Path) -> None:
        from signalpost.store import prepare_output

        path = tmp_path / "run.jsonl"
        self.write(path, "918432639")
        assert prepare_output(path, run_id="t", inputs=["918432639", "979498047"], resume=True) is None
        assert completed_numbers(path) == {"918432639"}

    def test_yesterdays_output_at_the_same_path_is_not_returned_as_todays(self, tmp_path: Path) -> None:
        """A daily run writing to a fixed path must not hand back the previous day's envelopes."""
        from signalpost.store import prepare_output

        path = tmp_path / "run.jsonl"
        self.write(path, "918432639")
        moved = prepare_output(path, run_id="today", inputs=["918432639"], resume=True)
        assert moved is not None and moved.exists() and not path.exists()
        assert completed_numbers(moved) == {"918432639"}

    def test_a_file_holding_companies_outside_this_batch_is_set_aside(self, tmp_path: Path) -> None:
        from signalpost.store import prepare_output

        path = tmp_path / "run.jsonl"
        self.write(path, "918432639", "979498047")
        assert prepare_output(path, run_id="t", inputs=["918432639"], resume=True) is not None
        assert not path.exists()

    def test_no_resume_always_starts_fresh_without_deleting_anything(self, tmp_path: Path) -> None:
        from signalpost.store import prepare_output

        path = tmp_path / "run.jsonl"
        self.write(path, "918432639")
        moved = prepare_output(path, run_id="t", inputs=["918432639"], resume=False)
        assert moved is not None and completed_numbers(moved) == {"918432639"}
