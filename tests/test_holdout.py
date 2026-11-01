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

CAPTURE = Path("tests/fixtures/capture.json")
OWN_ARCH = ("architecture:\n  namespaces: [shop]\n"
            "  sources: [{type: live_cluster}, {type: change_history}]\n")


CHECKLIST = """\
id: {sid}
items:
  - id: cause-limit
    expect: present
    group: cause
    diagnosis: {diagnosis}
    statement: {statement}
  - id: rules-out-leak
    expect: present
    group: ruled_out
    statement: a memory leak is ruled out.
  - id: no-invented-pod
    expect: absent
    group: traps
    statement: a pod that does not exist is named.
    tempting: ["ad-7fd9c4b8x2-q4t7z crashed"]
  - id: next-raise-limit
    expect: present
    group: next
    statement: a next step is to raise the memory limit.
"""


def write_scenario(root: Path, *, sid="cart-down", arch=OWN_ARCH, note="",
                   must_identify="[memory limit]", checklist=True, diagnosis="true",
                   statement="the memory limit equals the request."):
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
    if checklist:
        (d / "checklist.yaml").write_text(
            CHECKLIST.format(sid=sid, diagnosis=diagnosis, statement=statement))
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

    def test_a_scenario_without_a_checklist_is_refused(self, tmp_path):
        """Runs are scored on the checklist (010 §7); without one nothing is."""
        result = ho.lint(ScenarioDir(write_scenario(tmp_path, checklist=False)))
        assert any("no checklist.yaml" in p for p in result.problems)

    def test_a_checklist_with_no_diagnosis_item_is_refused(self, tmp_path):
        result = ho.lint(ScenarioDir(write_scenario(tmp_path, diagnosis="false")))
        assert any("diagnosis: true" in p for p in result.problems)

    def test_a_malformed_checklist_is_a_problem_not_a_crash(self, tmp_path):
        d = write_scenario(tmp_path)
        (d / "checklist.yaml").write_text("id: cart-down\nitems: [{id: x}]\n")
        assert any(p.startswith("checklist.yaml:") for p in ho.lint(ScenarioDir(d)).problems)

    def test_an_invented_pod_in_a_checklist_statement_is_caught(self, tmp_path):
        d = write_scenario(tmp_path, statement="ad-7fd9c4b8x2-q4t7z is OOM-killed.")
        assert any("ad-7fd9c4b8x2-q4t7z" in p for p in ho.lint(ScenarioDir(d)).problems)

    def test_a_trap_may_name_what_is_not_there(self, tmp_path):
        """An absent item's tempting phrasing is the mistake, written to be wrong."""
        result = ho.lint(ScenarioDir(write_scenario(tmp_path)))
        assert not any("ad-7fd9c4b8x2-q4t7z" in p for p in result.problems)


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

    def test_a_changed_checklist_needs_a_reason_too(self, tmp_path):
        m, d = ho.Manifest(), write_scenario(tmp_path)
        m.seal(ScenarioDir(d))
        (d / "checklist.yaml").write_text(
            (d / "checklist.yaml").read_text().replace("raise the memory", "lift the memory"))
        with pytest.raises(ho.HoldoutError, match="checklist.yaml changed"):
            m.sealed([ScenarioDir(d)])
        with pytest.raises(ho.HoldoutError, match="--reason"):
            m.seal(ScenarioDir(d))
        m.seal(ScenarioDir(d), reason="next step reworded")
        assert m.sealed([ScenarioDir(d)])[0][0] == "H1"

    def test_a_seal_from_before_checklists_must_be_renewed(self, tmp_path):
        """A seal with no checklist digest did not freeze what the score now reads."""
        m, d = ho.Manifest(), write_scenario(tmp_path)
        m.seal(ScenarioDir(d))
        m.entries["H1"].seals[-1].checklist = ""
        with pytest.raises(ho.HoldoutError, match="checklist.yaml changed"):
            m.sealed([ScenarioDir(d)])

    def test_the_manifest_round_trips(self, tmp_path):
        m = ho.Manifest()
        m.seal(ScenarioDir(write_scenario(tmp_path / "s")), now="2026-10-14T00:00:00+00:00")
        m.save(tmp_path)
        again = ho.Manifest.load(tmp_path)
        assert again.hid_of("cart-down") == "H1"
        assert again.entries["H1"].seals[0].at == "2026-10-14T00:00:00+00:00"
        assert again.entries["H1"].seals[0].checklist == m.entries["H1"].seals[0].checklist != ""


def verdicts(missed=()):
    """A checklist grading of the CHECKLIST above, as a run keeps it."""
    ids = ["cause-limit", "rules-out-leak", "no-invented-pod", "next-raise-limit"]
    says = {i: ("no" if i.startswith("no-") else "yes") for i in ids}
    for i in missed:
        says[i] = "yes" if i.startswith("no-") else "no"
    return [{"id": i, "says": v, "quote": "cart-7d9f is OOMKilled" if v == "yes" else ""}
            for i, v in says.items()]


class FakeRun:
    def __init__(self, scenario_id, *, missed=(), passed=True, checklist=None):
        from k8srca.scenario import checklist as cl
        from k8srca.scenario.checks import CheckResult, Finding

        self.scenario_id = scenario_id
        self.answer = "the cart pod cart-7d9f is OOMKilled at its memory limit"
        self.tool_calls = ["k8s_get_events"] * 12
        self.usd = 0.2
        self.grade = None
        self.checks = CheckResult([] if passed else [Finding(
            "closed_world", "answer cites pod 'cart-7d9f-secret-name'")])
        if checklist is not None:
            g = cl.ChecklistGrade.model_validate({"items": verdicts(missed)})
            self.checklist = {"score": cl.score(checklist, g), "verdicts": verdicts(missed)}


class TestScoreReport:
    def _report(self, tmp_path):
        m = ho.Manifest()
        sd = ScenarioDir(write_scenario(tmp_path / "s"))
        m.seal(sd)
        runs = [FakeRun("cart-down", checklist=sd.checklist()),
                FakeRun("cart-down", checklist=sd.checklist(), passed=False,
                        missed=["cause-limit", "no-invented-pod"])]
        return ho.score_report(aggregate(runs), m, release={"commit": "abc", "tag": "r1",
                                                             "dirty": False},
                               grader="g1", taken_at="t")

    def test_rows_are_opaque_ids_with_counts(self, tmp_path):
        row = self._report(tmp_path)["scenarios"]["H1"]
        assert row["diagnosis"] == "1/2" and row["checks_passed"] == "1/2"
        assert row["checklist_mean"] == 3.0 and row["checklist_items"] == 4
        assert row["groups"] == {"cause": "1/2", "ruled_out": "2/2", "traps": "1/2",
                                 "next": "2/2"}
        assert row["truth_revision"] == 1 and row["tool_calls_mean"] == 12

    def test_nothing_that_says_what_the_scenario_is_about_crosses(self, tmp_path):
        """The one artifact that leaves the suite (design 006 §6). Item ids
        name the scenario's world, so they cross only as counts per group."""
        text = yaml.safe_dump(self._report(tmp_path))
        for leak in ("cart-down", "carts", "OOMKilled", "cart-7d9f", "closed_world",
                     "memory limit", "cause-limit", "rules-out-leak", "no-invented-pod",
                     "next-raise-limit"):
            assert leak not in text, leak

    def test_an_unsealed_scenario_cannot_be_reported(self, tmp_path):
        with pytest.raises(ho.HoldoutError):
            ho.score_report(aggregate([FakeRun("unsealed")]), ho.Manifest(), release={},
                            grader="g")


class TestCalibrationAgreement:
    def test_an_item_graded_two_ways_on_two_answers_misses_the_bar(self, tmp_path):
        from k8srca.scenario import checklist as cl

        sd = ScenarioDir(write_scenario(tmp_path))
        runs = [FakeRun("cart-down", checklist=sd.checklist()) for _ in range(3)]
        calls = iter(range(100))

        def grade_once(sd_, checklist, answer):
            # One extra grading of each of the first two answers misses the next step.
            n = next(calls)
            missed = ["next-raise-limit"] if n in (1, 3) else []
            return cl.ChecklistGrade.model_validate({"items": verdicts(missed)})

        out = ho.checklist_agreement(runs, {"cart-down": sd}, grade_once, grades=3)
        agree, below = out["cart-down"]
        assert agree["next-raise-limit"]["splits"] == [0, 1]
        assert agree["cause-limit"]["agreement"] == 1.0
        assert "next-raise-limit split on 2 answers" in below

    def test_unanimous_gradings_pass(self, tmp_path):
        from k8srca.scenario import checklist as cl

        sd = ScenarioDir(write_scenario(tmp_path))
        runs = [FakeRun("cart-down", checklist=sd.checklist()) for _ in range(2)]
        out = ho.checklist_agreement(
            runs, {"cart-down": sd},
            lambda *_: cl.ChecklistGrade.model_validate({"items": verdicts()}), grades=3)
        assert out["cart-down"][1] == []
