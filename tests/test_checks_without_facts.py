"""The answer checks when the record holds no facts (#49).

An inventory question answered by a tool that derives no facts, or a follow-up
such as "what did you just tell me?", leaves a reply nothing to cite. The
checks asked it to cite anyway, and the agent invented `[F1]` markers, resent
its first answer in place of the follow-up's reply, or spent the turn trying
to record a fact. These tests hold the fix: ask only for what can exist, and
refer to the reply being checked. Only a record with no facts at all is
exempt: a follow-up in a session that has facts is checked as before.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from k8srca.config import FactsConfig, ReviewConfig, SandboxConfig
from k8srca.facts import Fact, FactLog
from k8srca.facts.render import MAX_WORDS
from k8srca.review import Review, answer_problems, check
from k8srca.settings import SlackSettings
from k8srca.slack import app as app_mod
from k8srca.slack.app import Orchestrator
from k8srca.slack.sessions import SessionStore

from fakes import FakeAnthropic, FakeSessions, FakeSlack, config, idle, msg, state

FACT = Fact("default/Deployment/ad/ad", "memory_limit", "300Mi",
            "ad: memory limit 300Mi, equal to its request", id="F7",
            source={"tool": "k8s_get_pod_spec", "n": 6})


def empty(tmp_path: Path) -> Path:
    """A session workspace whose fact record holds nothing."""
    (tmp_path / "evidence").mkdir(parents=True)
    (tmp_path / "evidence" / "facts.jsonl").write_text("")
    return tmp_path


# -- the checks ask only for what can exist ---------------------------------------

def test_no_facts_and_no_markers_is_not_flagged(tmp_path):
    ws = empty(tmp_path)
    assert answer_problems("The `default` namespace has the most running pods, with 27.",
                           ws) == []


def test_no_facts_does_not_ask_for_figures_to_be_cited(tmp_path):
    ws = empty(tmp_path)
    assert answer_problems("default has 27 pods, kube-system 9, dashboard 2.", ws) == []


def test_no_facts_still_flags_a_marker_and_says_to_remove_it(tmp_path):
    ws = empty(tmp_path)
    [problem] = answer_problems("default has 27 pods [F1].", ws)
    assert "F1 name no fact" in problem and "Remove those markers" in problem
    # Not an instruction it cannot follow: recording a fact is what it tried (#49).
    assert "kb_query.py facts" not in problem
    # Nor a ban: in an investigation whose tools derive no facts, recording one
    # can be the right move.
    assert "record a fact" not in problem


def test_no_facts_still_checks_the_length(tmp_path):
    ws = empty(tmp_path)
    [problem] = answer_problems("word " * (MAX_WORDS + 1), ws)
    assert f"limit is {MAX_WORDS}" in problem


def test_no_facts_raises_no_revision(tmp_path):
    ws = empty(tmp_path)
    cfg = SimpleNamespace(completeness=False, answer=True)
    assert check("I told you default has the most pods, 27.", ws, "s", cfg).prompt is None


def test_with_facts_in_the_record_the_checks_are_unchanged(tmp_path):
    FactLog(tmp_path).add([Fact(**{**FACT.as_dict(), "id": ""})], FACT.source)
    problems = answer_problems("no markers, 27 pods", tmp_path)
    assert any("cites no facts" in p for p in problems)
    assert any("27" in p for p in problems)


# -- the revision prompt refers to the reply being checked ------------------------

@pytest.mark.parametrize("review", [
    Review(opens=None, answer=["It cites no facts."]),
    Review(opens=0),
    Review(opens=None, light=[{"quote": "q", "kind": "no-marker", "why": "w"}]),
])
def test_the_prompt_never_asks_for_the_complete_answer(review):
    prompt = review.prompt
    assert "complete answer" not in prompt
    assert "your last reply" in prompt and "Reply with that reply again" in prompt
    assert "If it gives a most likely cause, keep it" in prompt


# -- in Slack: the whole path ------------------------------------------------------

def orchestrator(monkeypatch, tmp_path, replies):
    """An orchestrator with facts and the answer checks on, as in k8srca.yaml,
    whose session's workspace holds an empty fact record."""
    empty(tmp_path / "sess_0")
    it = iter(replies)
    sessions = FakeSessions(script=lambda sid: [msg(r) for r in next(it)] + [idle()])
    monkeypatch.setattr(app_mod.anthropic, "Anthropic", lambda *a, **k: FakeAnthropic(sessions))
    cfg = config(sandbox=SandboxConfig(image="img", workspaces=tmp_path),
                 facts=FactsConfig(enabled=True),
                 review=ReviewConfig(completeness=False, answer=True))
    orch = Orchestrator(cfg, state(), SlackSettings(socket_mode_token="x", bot_token="x"),
                        SessionStore(tmp_path / "sessions.db"))
    return orch, sessions


def test_a_reply_with_nothing_to_cite_is_posted_without_a_revision(monkeypatch, tmp_path):
    orch, sessions = orchestrator(monkeypatch, tmp_path,
                                  [["Five namespaces: default, kube-system, kube-public."]])
    slack = FakeSlack()
    orch.handle(slack, "C1", "111.1", "111.1", "which namespaces?", "U1")
    assert sessions.sent == [], "nothing to revise"
    assert slack.final_text("C1", "111.1") == "Five namespaces: default, kube-system, kube-public."


def test_a_marker_the_revision_leaves_unresolved_is_not_posted(monkeypatch, tmp_path):
    # Rendering drops a marker that names no fact, even with an empty record;
    # the claim stays.
    orch, sessions = orchestrator(monkeypatch, tmp_path, [
        ["default has the most running pods, 27 [F1]."],
        ["default has the most running pods, 27 [F1]."],
    ])
    slack = FakeSlack()
    orch.handle(slack, "C1", "111.1", "111.1", "most pods?", "U1")
    [(_, events)] = sessions.sent
    assert "Remove those markers" in events[0]["content"][0]["text"]
    assert slack.final_text("C1", "111.1") == "default has the most running pods, 27."
