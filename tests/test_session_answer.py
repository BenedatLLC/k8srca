"""One definition of a turn's answer, for Slack and the scenario runner (#15).

The scenario runner once joined every message of a turn as the answer, so the
agent's narration ("Let me start the investigation...") was graded and checked
with it, while Slack posted only the last message. Both now read
`session.final_answer`; these tests hold the two to it.
"""

from __future__ import annotations

import pytest

from k8srca.session import Turn, final_answer
from k8srca.slack.relay import TurnRender

NARRATED = ["Let me start the investigation.", "Now I'll wait for the investigator.",
            "**Finding**: OOMKilled."]


@pytest.mark.parametrize("cls", [Turn, TurnRender])
def test_a_complete_turn_answers_with_its_last_message(cls):
    assert cls(messages=NARRATED, stop_reason="end_turn").answer == "**Finding**: OOMKilled."
    assert cls(messages=NARRATED, terminated=True).answer == "**Finding**: OOMKilled."


@pytest.mark.parametrize("cls", [Turn, TurnRender])
@pytest.mark.parametrize("stop", [None, "requires_action"])
def test_a_turn_that_is_not_complete_has_no_answer(cls, stop):
    assert cls(messages=NARRATED, stop_reason=stop).answer is None


@pytest.mark.parametrize("cls", [Turn, TurnRender])
def test_a_turn_with_no_messages_has_no_answer(cls):
    assert cls(stop_reason="end_turn").answer is None


def test_the_transcript_keeps_the_narration():
    t = Turn(messages=NARRATED, stop_reason="end_turn")
    assert t.transcript == "\n\n".join(NARRATED)


def test_final_answer():
    assert final_answer(["a", "b"], True) == "b"
    assert final_answer(["a", "b"], False) is None
    assert final_answer([], True) is None
