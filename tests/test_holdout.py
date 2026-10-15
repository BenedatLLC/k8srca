"""The hold-out kit (design 006).

What matters is what it refuses: a suite inside this repo, a scenario that
would inherit our application's wiki sources, a truth that changed after it was
sealed -- and a score report that carries anything but scores.
"""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from k8srca.scenario import holdout as ho
from k8srca.scenario.model import ScenarioDir
from k8srca.scenario.report import aggregate
from k8srca.scenario.grader import Grade

CAPTURE = Path("tests/fixtures/capture.json")
OWN_ARCH = ("architecture:\n  namespaces: [shop]\n"
            "  sources: [{type: live_cluster}, {type: change_history}]\n")


def write_scenario(root: Path, *, sid="cart-down", arch=OWN_ARCH, note="",
                   must_identify="[memory limit]"):
    d = root / sid
    d.mkdir(parents=True)
    shutil.copy(CAPTURE, d / "k8s.json")
    captured_at = json.loads(CAPTURE.read_text())["captured_at"]
    (d / "scenario.yaml").write_text(
        f"id: {sid}\nquestion: why are carts failing?\n"
        "sources:\n  k8stools:\n    state: k8s.json\n" + arch)
    wiki = d / "skill" / "wiki" / "cluster-architecture"
    wiki.mkdir(parents=True)
    (wiki / "SKILL.md").write_text("---\nname: cluster-architecture\n---\n")
    (wiki / "graph.json").write_text("{}")
    from k8srca.kb.skills import bundle_digest

    (d / "truth.yaml").write_text(
        f"id: {sid}\ncapture:\n  captured_at: '{captured_at}'\n"
        f"  skill_digests:\n    wiki: {bundle_digest(wiki)}\n"
        f"cause:\n  summary: the memory limit equals the request\n"
        f"  must_identify: {must_identify}\n"
        f"rivals:\n  - id: memory-leak\n    disposition: weakened\n    note: \"{note}\"\n")
    return d


class TestWhereTheSuiteLives:
    def test_inside_this_repo_is_refused(self, tmp_path):
        with pytest.raises(ho.HoldoutError, match="inside this repository"):
            ho.check_suite_location(tmp_path / "repo" / "suite", tmp_path / "repo")

    def test_the_repo_itself_is_refused(self, tmp_path):
        with pytest.raises(ho.HoldoutError):
            ho.check_suite_location(tmp_path, tmp_path)

    def test_outside_is_fine(self, tmp_path):
        ho.check_suite_location(tmp_path / "suite", tmp_path / "repo")


class TestOwnArchitecture:
    def test_a_scenario_that_would_inherit_our_sources_is_refused(self, tmp_path):
        """Inherited, the wiki describes the OTel demo and nothing fails."""
        draft = ho.Draft.load(write_scenario(tmp_path, arch=""))
        with pytest.raises(ho.HoldoutError, match="architecture.sources"):
            ho.require_own_architecture(draft.scenario)

    def test_sources_without_namespaces_are_refused(self, tmp_path):
        arch = "architecture:\n  sources: [{type: live_cluster}]\n"
        draft = ho.Draft.load(write_scenario(tmp_path, arch=arch))
        with pytest.raises(ho.HoldoutError, match="namespaces"):
            ho.require_own_architecture(draft.scenario)

    def test_a_draft_loads_without_a_truth(self, tmp_path):
        d = write_scenario(tmp_path)
        (d / "truth.yaml").unlink()
        draft = ho.Draft.load(d)
        ho.require_own_architecture(draft.scenario)
        assert draft.capture_path == d / "k8s.json"


class TestLint:
    def test_a_sound_truth_passes(self, tmp_path):
        result = ho.lint(ScenarioDir(write_scenario(tmp_path)))
        assert result.passed, result.problems

    def test_an_invented_pod_in_a_truth_note_is_caught(self, tmp_path):
        d = write_scenario(tmp_path, note="ad-7fd9c4b8x2-q4t7z never started")
        result = ho.lint(ScenarioDir(d))
        assert any("ad-7fd9c4b8x2-q4t7z" in p for p in result.problems)

    def test_a_must_identify_term_nowhere_in_the_world_is_flagged(self, tmp_path):
        d = write_scenario(tmp_path, must_identify="[quota-exceeded-xyz]")
        result = ho.lint(ScenarioDir(d))
        assert result.passed            # a phrase need not appear verbatim
        assert any("quota-exceeded-xyz" in a for a in result.advisories)

    def test_a_stale_pin_is_a_problem(self, tmp_path):
        d = write_scenario(tmp_path)
        (d / "skill" / "wiki" / "cluster-architecture" / "graph.json").write_text('{"x": 1}')
        assert any("changed since" in p for p in ho.lint(ScenarioDir(d)).problems)

    def test_a_default_budget_is_only_advice(self, tmp_path):
        result = ho.lint(ScenarioDir(write_scenario(tmp_path)))
        assert result.passed and any("budget" in a for a in result.advisories)


class TestSeal:
    def test_the_first_seal_assigns_the_next_opaque_id(self, tmp_path):
        m = ho.Manifest()
        a = ScenarioDir(write_scenario(tmp_path, sid="a"))
        b = ScenarioDir(write_scenario(tmp_path, sid="b"))
        assert m.seal(a) == "H1" and m.seal(b) == "H2"

    def test_resealing_an_unchanged_truth_is_refused(self, tmp_path):
        m, sd = ho.Manifest(), ScenarioDir(write_scenario(tmp_path))
        m.seal(sd)
        with pytest.raises(ho.HoldoutError, match="already sealed"):
            m.seal(sd, reason="again")

    def test_a_changed_truth_needs_a_reason_and_counts_as_a_revision(self, tmp_path):
        m, d = ho.Manifest(), write_scenario(tmp_path)
        m.seal(ScenarioDir(d))
        (d / "truth.yaml").write_text((d / "truth.yaml").read_text() + "gaps: [{id: no-metrics}]\n")
        with pytest.raises(ho.HoldoutError, match="--reason"):
            m.seal(ScenarioDir(d))
        assert m.seal(ScenarioDir(d), reason="gap was missing") == "H1"
        assert len(m.entries["H1"].seals) == 2

    def test_scoring_refuses_a_truth_edited_after_sealing(self, tmp_path):
        m, d = ho.Manifest(), write_scenario(tmp_path)
        m.seal(ScenarioDir(d))
        (d / "truth.yaml").write_text((d / "truth.yaml").read_text() + "gaps: [{id: g}]\n")
        with pytest.raises(ho.HoldoutError, match="changed after it was sealed"):
            m.sealed([ScenarioDir(d)])

    def test_the_manifest_round_trips(self, tmp_path):
        m = ho.Manifest()
        m.seal(ScenarioDir(write_scenario(tmp_path / "s")), now="2026-10-14T00:00:00+00:00")
        m.save(tmp_path)
        again = ho.Manifest.load(tmp_path)
        assert again.hid_of("cart-down") == "H1"
        assert again.entries["H1"].seals[0].at == "2026-10-14T00:00:00+00:00"


class FakeRun:
    def __init__(self, scenario_id, grade, *, passed=True):
        from k8srca.scenario.checks import CheckResult, Finding

        self.scenario_id = scenario_id
        self.tool_calls = ["k8s_get_events"] * 12
        self.usd = 0.2
        self.grade = grade
        self.checks = CheckResult([] if passed else [Finding(
            "closed_world", "answer cites pod 'cart-7d9f-secret-name'")])


class TestScoreReport:
    def _report(self, tmp_path):
        m = ho.Manifest()
        m.seal(ScenarioDir(write_scenario(tmp_path / "s")))
        grade = Grade(cause_correct=True, cause_note="the cart pod cart-7d9f is OOMKilled",
                      evidence_supported=False, evidence_note="cites valkey password",
                      unsupported_claims=["valkey requires AUTH since 10:02"])
        runs = [FakeRun("cart-down", grade), FakeRun("cart-down", grade, passed=False)]
        return ho.score_report(aggregate(runs), m, release={"commit": "abc", "tag": "r1",
                                                             "dirty": False},
                               grader="g1", taken_at="t")

    def test_rows_are_opaque_ids_with_counts(self, tmp_path):
        row = self._report(tmp_path)["scenarios"]["H1"]
        assert row["cause"] == "2/2" and row["evidence"] == "0/2"
        assert row["unsupported_per_run"] == "1.0" and row["checks_passed"] == "1/2"
        assert row["truth_revision"] == 1 and row["tool_calls_mean"] == 12

    def test_nothing_that_says_what_the_scenario_is_about_crosses(self, tmp_path):
        """The one artifact that leaves the suite (design 006 §6)."""
        text = yaml.safe_dump(self._report(tmp_path))
        for leak in ("cart-down", "carts", "OOMKilled", "valkey", "cart-7d9f",
                     "closed_world", "memory limit"):
            assert leak not in text, leak

    def test_an_unsealed_scenario_cannot_be_reported(self, tmp_path):
        runs = [FakeRun("unsealed", Grade(cause_correct=True, cause_note="",
                                          evidence_supported=True, evidence_note=""))]
        with pytest.raises(ho.HoldoutError):
            ho.score_report(aggregate(runs), ho.Manifest(), release={}, grader="g")
