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
