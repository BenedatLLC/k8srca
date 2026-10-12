"""Scenario and truth schemas (design 004 §2, §6.3)."""

import json
import shutil
from pathlib import Path

import pytest

from k8srca.scenario.model import ScenarioDir, StaleTruthError, Truth, discover

CAPTURE = Path("tests/fixtures/capture.json")


def write_scenario(root: Path, *, sid="demo", truth_captured_at=None, scenario_id=None):
    d = root / sid
    d.mkdir(parents=True)
    shutil.copy(CAPTURE, d / "k8s.json")
    captured_at = truth_captured_at or json.loads(CAPTURE.read_text())["captured_at"]
    (d / "scenario.yaml").write_text(
        f"id: {scenario_id or sid}\n"
        "question: why is it broken?\n"
        "sources:\n  k8stools:\n    state: k8s.json\n    clock: frozen\n"
        "requires_tools: [get_replicaset_summaries]\n"
    )
    (d / "truth.yaml").write_text(
        f"id: {sid}\n"
        f"capture:\n  captured_at: '{captured_at}'\n"
        "cause:\n  summary: the memory limit equals the request\n"
        "  must_identify: [memory limit]\n"
        "rivals:\n  - id: memory-leak\n    disposition: weakened\n"
    )
    return d


class TestLoading:
    def test_a_scenario_round_trips(self, tmp_path):
        sd = ScenarioDir(write_scenario(tmp_path))
        assert sd.scenario.question.startswith("why")
        assert sd.scenario.sources.k8stools.clock == "frozen"
        assert sd.truth.rivals[0].disposition == "weakened"

    def test_clock_defaults_to_frozen(self, tmp_path):
        """Repeated runs must be identical; an advancing clock is opt-in."""
        d = write_scenario(tmp_path)
        (d / "scenario.yaml").write_text(
            "id: demo\nquestion: q\nsources:\n  k8stools:\n    state: k8s.json\n")
        assert ScenarioDir(d).scenario.sources.k8stools.clock == "frozen"

    def test_mismatched_ids_are_rejected(self, tmp_path):
        d = write_scenario(tmp_path, scenario_id="other")
        with pytest.raises(ValueError, match="id"):
            ScenarioDir(d)

    def test_discover_finds_scenarios_by_id(self, tmp_path):
        write_scenario(tmp_path, sid="b-second")
        write_scenario(tmp_path, sid="a-first")
        assert [s.scenario.id for s in discover(tmp_path)] == ["a-first", "b-second"]

    def test_discover_on_a_missing_root_is_empty(self, tmp_path):
        assert discover(tmp_path / "nope") == []


class TestTruthFreshness:
    """004 §6.3 -- truth is versioned with the capture."""

    def test_a_matching_capture_is_current(self, tmp_path):
        ScenarioDir(write_scenario(tmp_path)).check_truth_is_current()

    def test_a_re_recorded_capture_is_refused(self, tmp_path):
        d = write_scenario(tmp_path, truth_captured_at="2020-01-01T00:00:00+00:00")
        with pytest.raises(StaleTruthError, match="Re-review truth.yaml"):
            ScenarioDir(d).check_truth_is_current()

    def test_the_guard_survives_a_clone(self, tmp_path):
        """mtimes do not survive git; captured_at does.

        A clone sets every file's mtime to checkout time, so an mtime-based
        guard would pass on exactly the machine that did not write the truth.
        """
        d = write_scenario(tmp_path)
        for f in d.iterdir():
            import os
            os.utime(f, (0, 0))          # as a fresh checkout would
        ScenarioDir(d).check_truth_is_current()

        cap = json.loads((d / "k8s.json").read_text())
        cap["captured_at"] = "2030-01-01T00:00:00+00:00"
        (d / "k8s.json").write_text(json.dumps(cap))
        import os
        os.utime(d / "k8s.json", (0, 0))  # re-recorded, but mtime says otherwise
        with pytest.raises(StaleTruthError):
            ScenarioDir(d).check_truth_is_current()


class TestTruthValidation:
    def test_duplicate_rival_ids_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate rival"):
            Truth.model_validate({
                "id": "x", "capture": {"captured_at": "t"},
                "cause": {"summary": "s"},
                "rivals": [{"id": "a", "disposition": "refuted"},
                           {"id": "a", "disposition": "weakened"}],
            })

    def test_an_unknown_disposition_is_rejected(self):
        """'worth checking later' is not a disposition (004 §6.2)."""
        with pytest.raises(ValueError):
            Truth.model_validate({
                "id": "x", "capture": {"captured_at": "t"},
                "cause": {"summary": "s"},
                "rivals": [{"id": "a", "disposition": "worth_checking_later"}],
            })


    def test_also_accept_takes_only_real_dispositions(self):
        """The escape hatch for a boundary rival is not a way back to 'later'."""
        with pytest.raises(ValueError):
            Truth.model_validate({
                "id": "x", "capture": {"captured_at": "t"},
                "cause": {"summary": "s"},
                "rivals": [{"id": "a", "disposition": "weakened",
                            "also_accept": ["worth_checking_later"]}],
            })

    def test_also_accept_defaults_to_nothing(self):
        t = Truth.model_validate({
            "id": "x", "capture": {"captured_at": "t"},
            "cause": {"summary": "s"},
            "rivals": [{"id": "a", "disposition": "weakened"}],
        })
        assert t.rivals[0].also_accept == []


def test_a_file_in_the_scenarios_root_is_not_mistaken_for_a_scenario():
    """The baseline lives beside the scenarios (tests/scenarios/baseline.json).

    discover() walks that directory, so a stray file there must be skipped
    rather than raising -- otherwise committing the baseline breaks every
    command that lists scenarios.
    """
    from k8srca.scenario.model import discover

    found = discover(Path("tests/scenarios"))
    assert found and all(s.scenario.id for s in found)


class TestArchitectureForms:
    """A scenario carries the legacy skill and the wiki, and runs with either:
    the before/after of kubewiki design §10 step 4."""

    def _skill(self, d: Path, *parts: str, data: str = "data.json") -> Path:
        skill = d.joinpath("skill", *parts, "cluster-architecture")
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: cluster-architecture\n---\n")
        (skill / data).write_text("{\"from\": \"" + "/".join(parts or ("legacy",)) + "\"}")
        return skill

    def _pin(self, d: Path, **digests: str) -> None:
        truth = d / "truth.yaml"
        text = truth.read_text()
        lines = "".join(f"    {k}: {v}\n" for k, v in digests.items())
        truth.write_text(text.replace("capture:\n", "capture:\n  skill_digests:\n" + lines, 1))

    def test_each_form_has_its_own_directory_named_for_the_skill(self, tmp_path):
        # The upload names the skill after its directory: both must be
        # `cluster-architecture`, or the agent finds it at another path.
        d = write_scenario(tmp_path)
        assert ScenarioDir(d).skill_dir == d / "skill" / "cluster-architecture"
        assert ScenarioDir(d, "wiki").skill_dir == d / "skill" / "wiki" / "cluster-architecture"

    def test_the_grader_reference_follows_the_form(self, tmp_path):
        d = write_scenario(tmp_path)
        self._skill(d, data="architecture.json")
        self._skill(d, "wiki", data="graph.json")
        assert ScenarioDir(d).architecture() == {"from": "legacy"}
        assert ScenarioDir(d, "wiki").architecture() == {"from": "wiki"}

    def test_an_unknown_form_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="form"):
            ScenarioDir(write_scenario(tmp_path), "snapshot")

    def test_a_wiki_the_truth_was_never_read_against_is_refused(self, tmp_path):
        # Truth written for the snapshot says nothing about what the wiki
        # states; running against it would grade the agent on the wrong world.
        d = write_scenario(tmp_path)
        self._skill(d, "wiki")
        with pytest.raises(StaleTruthError, match="skill_digests.wiki"):
            ScenarioDir(d, "wiki").check_truth_is_current()

    def test_a_pinned_wiki_is_current_and_a_changed_one_is_not(self, tmp_path):
        d = write_scenario(tmp_path)
        skill = self._skill(d, "wiki")
        self._pin(d, wiki=ScenarioDir(d, "wiki").skill_sha())
        ScenarioDir(d, "wiki").check_truth_is_current()
        (skill / "index.md").write_text("rebuilt")
        with pytest.raises(StaleTruthError, match="changed"):
            ScenarioDir(d, "wiki").check_truth_is_current()

    def test_the_legacy_pin_is_still_read_from_skill_digest(self, tmp_path):
        d = write_scenario(tmp_path)
        assert ScenarioDir(d).truth.capture.pinned("legacy") is None
        truth = Truth.model_validate({"id": "x", "capture": {"captured_at": "t",
                                                              "skill_digest": "abc"},
                                      "cause": {"summary": "s"}})
        assert truth.capture.pinned("legacy") == "abc"
        assert truth.capture.pinned("wiki") is None

    def test_discover_loads_the_requested_form(self, tmp_path):
        write_scenario(tmp_path)
        assert [s.arch for s in discover(tmp_path, "wiki")] == ["wiki"]
        assert [s.arch for s in discover(tmp_path)] == ["legacy"]

    def test_a_scenario_may_name_its_own_wiki_sources(self, tmp_path):
        d = write_scenario(tmp_path)
        with (d / "scenario.yaml").open("a") as f:
            f.write("architecture:\n  namespaces: [otel-demo]\n"
                    "  sources:\n    - type: live_cluster\n")
        own = ScenarioDir(d).scenario.architecture
        assert own.namespaces == ["otel-demo"] and own.sources[0].type == "live_cluster"
