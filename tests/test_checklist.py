"""Checklist grading (010 §7): the files, the scoring, agreement. No model."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from k8srca.scenario import checklist as cl
from k8srca.scenario.model import discover

ROOT = Path(__file__).parent / "scenarios"
RECORDED = ["checkout-gc-pressure", "itbench-33-invalid-node-selector",
            "jvm-oom-on-startup", "jvm-oom-with-metrics"]


def _checklist() -> cl.Checklist:
    return cl.Checklist(id="s", items=[
        cl.Item(id="cause", expect="present", group="cause", statement="it is X"),
        cl.Item(id="trap", expect="absent", group="traps", statement="it is Y",
                tempting=["Y did it"]),
    ])


def _grade(**says) -> cl.ChecklistGrade:
    return cl.ChecklistGrade(items=[cl.ItemVerdict(id=k, says=v, quote="")
                                    for k, v in says.items()])


@pytest.mark.parametrize("sid", RECORDED)
def test_every_recorded_scenario_has_a_checklist(sid):
    sd = {s.scenario.id: s for s in discover(ROOT)}[sid]
    checklist = sd.checklist()
    assert checklist is not None and checklist.items
    # Each group the score reports has at least one item, so none reads 0/0.
    assert {i.group for i in checklist.items} == set(cl.GROUPS)
    for item in checklist.items:
        if item.expect == "present":
            assert not item.tempting, f"{item.id}: tempting is for absent items"


def test_duplicate_ids_are_refused(tmp_path):
    path = tmp_path / "checklist.yaml"
    path.write_text("id: s\nitems:\n"
                    "  - {id: a, expect: present, group: cause, statement: x}\n"
                    "  - {id: a, expect: absent, group: traps, statement: y}\n")
    with pytest.raises(ValueError, match="duplicate"):
        cl.Checklist.load(path)


def test_the_grader_is_not_told_which_way_an_item_goes():
    request = cl.build_request(_checklist(), "why?", "an answer")
    reference = request["system"][1]["text"]
    assert "expect" not in reference and "group" not in reference
    assert "Y did it" in reference
    # The checklist is the cached prefix; the answer is not in it.
    assert "an answer" not in reference
    assert "an answer" in request["messages"][0]["content"]


def test_cited_facts_go_with_the_answer():
    request = cl.build_request(_checklist(), "why?", "an answer [1]", ["[1] ad: OOMKilled"])
    assert "[1] ad: OOMKilled" in request["messages"][0]["content"]


def test_met_and_score():
    checklist = _checklist()
    s = cl.score(checklist, _grade(cause="yes", trap="no"))
    assert (s["met"], s["listed"], s["missed"]) == (2, 2, [])
    s = cl.score(checklist, _grade(cause="unclear", trap="yes"))
    assert (s["met"], s["missed"]) == (0, ["cause", "trap"])
    assert s["groups"] == {"cause": [0, 1], "traps": [0, 1]}


def test_agreement_is_the_share_matching_each_answers_majority():
    checklist = _checklist()
    gradings = [
        [_grade(cause="yes", trap="no")] * 3,
        [_grade(cause="yes", trap="no"), _grade(cause="yes", trap="no"),
         _grade(cause="no", trap="no")],
    ]
    agree = cl.agreement(gradings, checklist)
    assert agree["trap"] == {"agreement": 1.0, "gradings": 6, "splits": []}
    assert agree["cause"]["agreement"] == pytest.approx(5 / 6)
    assert agree["cause"]["splits"] == [1]


def test_a_skipped_item_comes_back_unclear():
    parsed = cl.ChecklistGrade(items=[cl.ItemVerdict(id="cause", says="yes", quote="X")])
    client = SimpleNamespace(messages=SimpleNamespace(
        parse=lambda **_: SimpleNamespace(parsed_output=parsed)))
    graded = cl.grade(_checklist(), "why?", "an answer", client=client)
    assert [(v.id, v.says) for v in graded.items] == [("cause", "yes"), ("trap", "unclear")]


def test_apparatus_digest_moves_with_the_prompt(monkeypatch):
    before = cl.apparatus_digest()
    monkeypatch.setattr(cl, "SYSTEM", cl.SYSTEM + " ")
    assert cl.apparatus_digest() != before


def test_the_rubric_grader_does_not_read_the_checklist():
    """The checklist lives beside truth.yaml, so the rubric grader's
    reference, which carries the whole truth, is unchanged by it."""
    from k8srca.scenario.grader import build_request

    sd = {s.scenario.id: s for s in discover(ROOT)}["itbench-33-invalid-node-selector"]
    reference = build_request(sd, "answer", [])["system"][1]["text"]
    assert "no-named-author" not in reference
    assert json.loads(reference.removeprefix("<reference>\n").removesuffix("\n</reference>"))["truth"]


def test_runs_aggregate_on_the_checklist():
    """`scenario run` grades on the checklist and the rubric is opt-in (010 §7):
    the batch table reads checklist scores, and a run record keeps them."""
    from k8srca.scenario import report as rp
    from k8srca.scenario.runner import Run

    def run(met, cause):
        r = Run(scenario_id="s", answer="a", tool_calls=["t"] * 4, usd=0.2)
        r.checklist = {"score": {"met": met, "listed": 5, "missed": [],
                                 "groups": {"cause": [cause, 2], "traps": [met - cause, 3]}},
                       "verdicts": [], "checklist_sha": "x", "grader": "y"}
        return r

    aggs = rp.aggregate([run(5, 2), run(3, 1)])
    assert aggs["s"].checklist == [5, 3] and aggs["s"].cause_items == [3, 4]
    assert not aggs["s"].dimension  # no rubric grade, no rubric columns
    row = rp.checklist_table(aggs)[2]
    assert "4.0/5" in row and "3/4" in row
    assert rp.run_record(run(5, 2), 1)["checklist"]["score"]["met"] == 5


class TestAgreementBar:
    def _agree(self, **per_item):
        return {item: {"agreement": a, "gradings": n, "splits": splits}
                for item, (a, n, splits) in per_item.items()}

    def test_unanimous_gradings_pass(self):
        assert cl.below_bar(self._agree(a=(1.0, 9, []), b=(1.0, 9, []))) == []

    def test_one_split_answer_on_an_item_passes(self):
        """8 of 9 on one item is 17 of 18 overall: 94%, below the bar; on a
        longer checklist one split is allowed."""
        agree = self._agree(**{f"i{n}": (1.0, 9, []) for n in range(9)},
                            split=(8 / 9, 9, [0]))
        assert cl.below_bar(agree) == []

    def test_an_item_split_on_two_answers_fails(self):
        agree = self._agree(**{f"i{n}": (1.0, 9, []) for n in range(30)},
                            split=(7 / 9, 9, [0, 2]))
        assert cl.below_bar(agree) == ["split split on 2 answers"]

    def test_overall_below_97_fails(self):
        assert cl.below_bar(self._agree(a=(8 / 9, 9, [0]), b=(8 / 9, 9, [1])))[0] == "88.9% overall"


class TestChecklistBaseline:
    """004 step 1 (§10.2, §10.3): the baseline is read on the checklist, and its
    gate is the diagnosis."""

    def _run(self, met, diagnosis=True, sha="x", traps=(2, 2)):
        from k8srca.scenario.runner import Run

        r = Run(scenario_id="s", answer="a", tool_calls=["t"] * 4, usd=0.2)
        r.capture_captured_at, r.skill_digest, r.truth_digest = "c", "k", "t"
        r.checklist = {"score": {"met": met, "listed": 9, "missed": [], "diagnosis": diagnosis,
                                 "groups": {"cause": [2 if diagnosis else 1, 2],
                                            "traps": list(traps)}},
                       "verdicts": [], "checklist_sha": sha, "grader": cl.apparatus_digest()}
        return r

    def _baseline(self, tmp_path, runs):
        from k8srca.scenario import report as rp

        path = tmp_path / "baseline.json"
        rp.save_baseline(rp.aggregate(runs), path)
        return rp.load_baseline(path)

    def test_a_run_saves_its_checklist_results_and_pins(self, tmp_path):
        from k8srca.scenario import report as rp

        b = self._baseline(tmp_path, [self._run(9), self._run(8, diagnosis=False)])
        entry = rp.scenarios(b)["s"]
        assert entry["checklist"] == [9, 8] and entry["checklist_listed"] == 9
        assert entry["diagnosis_runs"] == [1, 2]
        assert entry["groups"] == {"cause": [3, 4], "traps": [4, 4]}
        assert entry["checklist_sha"] == "x"
        assert b["checklist_grader"] == cl.apparatus_digest()
        assert rp.on_checklist(b) and rp.checklist_comparable(b)

    def test_a_rubric_baseline_is_not_read_as_a_checklist_one(self):
        from k8srca.scenario import report as rp

        old = {"grader": "g", "scenarios": {"s": {"dimension": {"cause": [3, 3]}}}}
        assert not rp.checklist_comparable(old) and not rp.on_checklist(old)
        aggs = rp.aggregate([self._run(9)])
        assert "baseline predates the checklist" in rp.checklist_table(aggs, old)[0]

    def test_the_same_results_are_no_move(self, tmp_path):
        from k8srca.scenario import report as rp

        runs = [self._run(9), self._run(8), self._run(9)]
        prior = rp.scenarios(self._baseline(tmp_path, runs))["s"]
        assert rp.checklist_delta(rp.aggregate(runs)["s"], prior) == "="

    def test_a_drop_within_the_spread_is_noise(self, tmp_path):
        from k8srca.scenario import report as rp

        prior = rp.scenarios(self._baseline(
            tmp_path, [self._run(9), self._run(7), self._run(9)]))["s"]
        now = rp.aggregate([self._run(8), self._run(7), self._run(8)])["s"]
        assert rp.checklist_delta(now, prior) == "noise"

    def test_a_drop_beyond_the_spread_is_a_regression(self, tmp_path):
        from k8srca.scenario import report as rp

        prior = rp.scenarios(self._baseline(
            tmp_path, [self._run(9), self._run(8), self._run(9)]))["s"]
        now = rp.aggregate([self._run(7), self._run(7), self._run(8)])["s"]
        assert rp.checklist_delta(now, prior) == "REGRESSION"

    def test_a_lost_diagnosis_is_a_regression_whatever_the_score(self, tmp_path):
        from k8srca.scenario import report as rp

        prior = rp.scenarios(self._baseline(tmp_path, [self._run(9)] * 3))["s"]
        now = rp.aggregate([self._run(9), self._run(9), self._run(9, diagnosis=False)])["s"]
        assert rp.checklist_delta(now, prior) == "REGRESSION"

    def test_a_missed_supporting_item_is_not_a_regression(self, tmp_path):
        """004 §10.3: the strict reading failed runs that named the cause."""
        from k8srca.scenario import report as rp

        prior = rp.scenarios(self._baseline(
            tmp_path, [self._run(9), self._run(8, traps=(1, 2)), self._run(9)]))["s"]
        now = rp.aggregate([self._run(8, traps=(1, 2)), self._run(9), self._run(9)])["s"]
        assert rp.checklist_delta(now, prior) == "="

    def test_an_unmarked_checklist_has_no_diagnosis_cell(self):
        from k8srca.scenario import report as rp

        aggs = rp.aggregate([self._run(9, diagnosis=None)])
        assert aggs["s"].diagnosis_runs == [0, 0]
        assert rp.checklist_table(aggs)[2].split()[1] == "-"

    def test_a_rewritten_checklist_is_reported_not_diffed(self, tmp_path):
        from k8srca.scenario import report as rp

        prior = rp.scenarios(self._baseline(tmp_path, [self._run(9)]))["s"]
        now = rp.aggregate([self._run(9, sha="y")])["s"]
        assert rp.checklist_delta(now, prior) == "checklist rewritten"

    def test_a_re_recorded_capture_is_still_refused(self, tmp_path):
        from k8srca.scenario import report as rp

        prior = rp.scenarios(self._baseline(tmp_path, [self._run(9)]))["s"]
        run = self._run(9)
        run.capture_captured_at = "c2"
        assert rp.checklist_delta(rp.aggregate([run])["s"], prior) == "capture re-recorded"

    def test_the_table_has_the_columns_of_004_10_3(self, tmp_path):
        from k8srca.scenario import report as rp

        lines = rp.checklist_table(rp.aggregate([self._run(9), self._run(8, diagnosis=False)]))
        assert lines[0].split()[:9] == ["scenario", "diagnosis", "checklist", "cause",
                                        "ruled", "out", "traps", "gaps", "next"]
        assert lines[2].split()[:5] == ["s", "1/2", "8.5/9", "3/4", "-"]

    def test_moved_cells_show_both_values(self, tmp_path):
        from k8srca.scenario import report as rp

        base = self._baseline(tmp_path, [self._run(9)] * 3)
        lines = rp.checklist_table(rp.aggregate(
            [self._run(9), self._run(9), self._run(9, diagnosis=False)]), base)
        assert "vs baseline" in lines[0]
        assert "3/3→2/3" in lines[2] and lines[2].rstrip().endswith("REGRESSION")

    def test_compare_has_before_after_and_moves(self, tmp_path):
        from k8srca.scenario import report as rp

        before = self._baseline(tmp_path, [self._run(9), self._run(7)])
        after = self._baseline(tmp_path, [self._run(6, diagnosis=False), self._run(6)])
        lines = rp.compare(before, after)
        assert "diagnosis" in lines[0]
        assert any(line.split()[:4] == ["s", "before", "2/2", "8.0/9"] for line in lines)
        assert any(line.split()[:3] == ["after", "1/2", "6.0/9"] for line in lines)
        assert next(line for line in lines if line.lstrip().startswith("Δ")).split()[1:3] == [
            "-1", "-2.0"]

    def test_compare_refuses_another_checklist(self, tmp_path):
        from k8srca.scenario import report as rp

        before = self._baseline(tmp_path, [self._run(9)])
        after = self._baseline(tmp_path, [self._run(9, sha="y")])
        assert any("not comparable: checklist differ" in line for line in rp.compare(before, after))
        after["checklist_grader"] = "other"
        assert rp.compare(before, after)[0].startswith("not comparable: graded by different "
                                                       "checklist graders")


class TestDiagnosis:
    def _checklist(self, mark=True):
        return cl.Checklist(id="s", items=[
            cl.Item(id="dx", expect="present", group="cause", statement="it is X",
                    diagnosis=mark),
            cl.Item(id="why", expect="present", group="cause", statement="because Z"),
        ])

    def test_the_diagnosis_is_met_without_the_supporting_item(self):
        score = cl.score(self._checklist(), _grade(dx="yes", why="no"))
        assert score["diagnosis"] is True and score["met"] == 1

    def test_a_missed_diagnosis_fails_it(self):
        assert cl.score(self._checklist(), _grade(dx="unclear", why="yes"))["diagnosis"] is False

    def test_an_unmarked_checklist_has_none(self):
        assert cl.score(self._checklist(mark=False), _grade(dx="yes", why="yes"))["diagnosis"] is None

    def test_the_grader_is_not_told_the_mark(self):
        reference = cl.build_request(self._checklist(), "why?", "a")["system"][1]["text"]
        assert "diagnosis" not in reference
        assert cl.grader_view(self._checklist()) == cl.grader_view(self._checklist(mark=False))

    def test_an_absent_item_cannot_be_the_diagnosis(self, tmp_path):
        path = tmp_path / "checklist.yaml"
        path.write_text("id: s\nitems:\n"
                        "  - {id: a, expect: absent, group: cause, statement: x, diagnosis: true}\n")
        with pytest.raises(ValueError, match="diagnosis"):
            cl.Checklist.load(path)

    @pytest.mark.parametrize("sd", [s for s in discover(ROOT) if s.checklist() is not None],
                             ids=lambda s: s.scenario.id)
    def test_every_checklist_marks_its_diagnosis(self, sd):
        assert any(i.diagnosis for i in sd.checklist().items)


class TestRescore:
    """`scenario rescore`: saved verdicts scored on the marked checklist, no grader."""

    SID = "liveness-probe-wrong-port"

    def _record(self, says, view=True):
        from k8srca.scenario import runner

        sd = {s.scenario.id: s for s in discover(ROOT)}[self.SID]
        checklist = sd.checklist()
        verdicts = [{"id": i.id, "says": says.get(i.id, "yes" if i.expect == "present" else "no"),
                     "quote": ""} for i in checklist.items]
        saved = {"score": {}, "verdicts": verdicts, "checklist_sha": "old", "grader": "g"}
        if view:
            saved["grader_view"] = cl.grader_view(checklist)
        return sd, runner, {"scenario_id": self.SID, "answer": "an answer", "tool_calls": [],
                            "usd": 0.1, "checklist": saved}

    def test_a_supporting_miss_keeps_the_diagnosis(self):
        sd, runner, record = self._record({"cause-unavailable": "no"})
        run = runner.rescore(sd, record)
        assert run.checklist["score"]["diagnosis"] is True
        assert run.checklist["checklist_sha"] == cl.checklist_sha(sd.checklist_path)

    def test_a_missed_diagnosis_is_kept(self):
        sd, runner, record = self._record({"cause-probe-wrong-port": "no"})
        assert runner.rescore(sd, record).checklist["score"]["diagnosis"] is False

    def test_a_changed_graded_text_is_refused(self):
        sd, runner, record = self._record({})
        record["checklist"]["grader_view"] = "something-else"
        with pytest.raises(runner.RescoreRefused, match="graded text changed"):
            runner.rescore(sd, record)

    def test_an_older_record_uses_the_view_it_is_given(self):
        sd, runner, record = self._record({}, view=False)
        with pytest.raises(runner.RescoreRefused):
            runner.rescore(sd, record)
        assert runner.rescore(sd, record, cl.grader_view(sd.checklist())).checklist


class TestBaselineCommand:
    """`scenario baseline` on a checklist-graded run needs the agreement check."""

    SID = "nothing-is-wrong"

    def _setup(self, tmp_path, monkeypatch, says="yes"):
        from k8srca import cli
        from k8srca.scenario import report as rp

        sd = {s.scenario.id: s for s in discover(ROOT)}[self.SID]
        checklist, sha = sd.checklist(), cl.checklist_sha(sd.checklist_path)
        last = {"grader": "g", "checklist_grader": cl.apparatus_digest(), "taken_at": "now",
                "scenarios": {self.SID: {"runs": 3, "checklist": [8, 8, 8], "checklist_sha": sha,
                                         "cause_runs": [3, 3], "dimension": {}}}}
        monkeypatch.setattr(cli, "LAST_RUN_PATH", tmp_path / "last-run.json")
        monkeypatch.setattr(cli, "BASELINE_PATH", tmp_path / "baseline.json")
        rp.promote(last, tmp_path / "last-run.json")

        def grading(flip):
            return {"items": [{"id": i.id, "says": ("no" if flip else says), "quote": ""}
                              for i in checklist.items]}
        detail = tmp_path / "agreement.jsonl"
        detail.write_text("".join(json.dumps({
            "scenario_id": self.SID, "session_id": f"s{n}", "checklist_sha": sha,
            "grader": cl.apparatus_digest(),
            "gradings": [grading(says == "split" and g == 0) for g in range(3)]}) + "\n"
            for n in range(3)))
        return cli, detail

    def _invoke(self, cli, *args):
        from typer.testing import CliRunner

        return CliRunner().invoke(cli.app, ["scenario", "baseline", "--root", str(ROOT), *args])

    def test_without_the_agreement_check_it_refuses(self, tmp_path, monkeypatch):
        cli, _ = self._setup(tmp_path, monkeypatch)
        r = self._invoke(cli)
        assert r.exit_code == 1 and not (tmp_path / "baseline.json").exists()

    def test_with_agreement_it_promotes(self, tmp_path, monkeypatch):
        cli, detail = self._setup(tmp_path, monkeypatch)
        r = self._invoke(cli, "--agreement", str(detail))
        assert r.exit_code == 0, r.output
        assert json.loads((tmp_path / "baseline.json").read_text())["scenarios"][self.SID]

    def test_a_checklist_below_the_bar_is_refused(self, tmp_path, monkeypatch):
        cli, detail = self._setup(tmp_path, monkeypatch, says="split")
        r = self._invoke(cli, "--agreement", str(detail))
        assert r.exit_code == 1 and not (tmp_path / "baseline.json").exists()
