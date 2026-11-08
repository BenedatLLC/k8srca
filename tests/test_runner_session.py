"""`run_session`: one session read through to its answer, with no scenario (014 §4.4).

It was cut out of `run_once` so a live exam can run the agent the way the suite
does. These tests pin what it does between the poller and the grader, with the
fakes the session tests use: nothing here reaches the network or a cluster.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from k8srca.config import FactsConfig, ReviewConfig
from k8srca.scenario import runner
from k8srca.scenario.runner import BillingExhausted, Run, run_session

from fakes import FakeAnthropic, FakeSessions, config, idle, msg, state, tool


def new_run() -> Run:
    return Run(scenario_id="p1", answer="")


def turns(*scripts):
    """A script that plays one list of events per stream opened."""
    it = iter(scripts)
    return lambda sid: next(it)


def go(sessions, cfg=None, **kw):
    run = new_run()
    kw.setdefault("poller_log", Path("/nonexistent/poller.log"))
    kw.setdefault("title", "t")
    out = run_session(FakeAnthropic(sessions), cfg or config(), state(), "why?", run, **kw)
    assert out is run
    return run


def test_reads_the_first_turn_into_the_run():
    s = FakeSessions(script=lambda sid: [msg("the cause"), tool("k8s_get"), idle()])
    run = go(s, title="exam:p1", metadata={"k": "v"})
    assert run.answer == "the cause"
    assert run.tool_calls == ["k8s_get"]
    assert run.session_id == "sess_0"
    assert run.usd == 0.0 and not run.invalid and not run.errors
    # the question, title and metadata reach the session; the agent is the coordinator's
    (sid, kw), = s.created
    assert kw["title"] == "exam:p1" and kw["metadata"] == {"k": "v"}
    assert kw["initial_events"][0]["content"][0]["text"] == "why?"
    assert kw["agent"]["id"] == "agt_1"


def test_a_billing_error_on_create_stops_the_suite(monkeypatch):
    s = FakeSessions()

    def boom(**kw):
        raise RuntimeError("Your credit balance is too low to access the API")

    s.create = boom
    with pytest.raises(BillingExhausted):
        go(s)


def test_another_error_on_create_is_not_swallowed():
    s = FakeSessions()
    s.create = lambda **kw: (_ for _ in ()).throw(ValueError("bad request"))
    with pytest.raises(ValueError):
        go(s)


def test_a_billing_error_inside_the_session_stops_the_suite():
    from fakes import error

    s = FakeSessions(script=lambda sid: [error("billing_error"), idle()])
    with pytest.raises(BillingExhausted):
        go(s)


def lost_turn(monkeypatch):
    """A first turn that never finished: `_consume_turn` marks the run invalid."""
    from k8srca.session import Turn

    def consume(client, session_id, stream, run, **where):
        run.invalid = "incomplete turn: stream lost"
        return Turn()

    monkeypatch.setattr(runner, "_consume_turn", consume)


def test_a_turn_that_never_finishes_is_invalid_and_the_session_interrupted(monkeypatch):
    lost_turn(monkeypatch)
    s = FakeSessions()
    run = go(s)
    assert run.invalid.startswith("incomplete turn")
    interrupts = [e for _, evs in s.sent for e in evs if e.get("type") == "user.interrupt"]
    assert interrupts, "an abandoned session must be stopped"
    assert s.retrieved == ["sess_0"], "the spend is still read"


def test_follow_up_is_sent_after_opening_its_stream_and_joined_to_the_answer():
    s = FakeSessions(script=turns([msg("first"), idle()], [msg("second"), idle()]))
    run = go(s, follow_up="and now?")
    assert run.answer == "first\n\nsecond"
    (sid, events), = s.sent
    assert events[0]["content"][0]["text"] == "and now?"


def test_no_follow_up_after_a_bad_first_turn(monkeypatch):
    lost_turn(monkeypatch)
    s = FakeSessions()
    go(s, follow_up="and now?")
    texts = [e["content"][0]["text"] for _, evs in s.sent for e in evs if "content" in e]
    assert "and now?" not in texts


def test_the_review_can_replace_the_answer(monkeypatch):
    review = SimpleNamespace(prompt="say it plainly", as_dict=lambda: {"flags": 1})
    monkeypatch.setattr(runner, "check", lambda *a, **k: review)
    s = FakeSessions(script=turns([msg("wordy"), idle()], [msg("plain"), idle()]))
    run = go(s, cfg=config(review=ReviewConfig(answer=True)))
    assert run.answer == "plain" and run.first_answer == "wordy"
    assert run.review == {"flags": 1}


def test_a_review_with_no_workspace_on_this_host_is_skipped(monkeypatch):
    monkeypatch.setattr(runner, "check", lambda *a, **k: None)
    s = FakeSessions(script=lambda sid: [msg("answer"), idle()])
    run = go(s, cfg=config(review=ReviewConfig(answer=True)))
    assert run.answer == "answer" and run.review is None


def test_no_review_when_every_check_is_off(monkeypatch):
    monkeypatch.setattr(runner, "check", lambda *a, **k: pytest.fail("review ran"))
    go(FakeSessions(), cfg=config(review=ReviewConfig(completeness=False)))


def test_facts_are_rendered_into_the_answer_and_the_raw_one_kept(monkeypatch):
    seen = []

    def render(answer, workspace):
        seen.append(workspace.name)
        return answer + " [1]"

    monkeypatch.setattr("k8srca.facts.render.render_workspace", render)
    s = FakeSessions(script=lambda sid: [msg("cause"), idle()])
    run = go(s, cfg=config(facts=FactsConfig(enabled=True)))
    assert run.answer == "cause [1]" and run.raw_answer == "cause"
    assert seen == ["sess_0"]


def test_an_empty_answer_is_not_rendered(monkeypatch):
    monkeypatch.setattr("k8srca.facts.render.render_workspace",
                        lambda *a: pytest.fail("rendered an empty answer"))
    s = FakeSessions(script=lambda sid: [idle()])
    run = go(s, cfg=config(facts=FactsConfig(enabled=True)))
    assert run.answer == ""


def test_the_spend_is_read_in_minor_units():
    s = FakeSessions(script=lambda sid: [msg("a"), idle()])
    s.retrieve = lambda session_id: SimpleNamespace(
        id=session_id, usage=SimpleNamespace(list_cost=SimpleNamespace(amount="25")))
    assert go(s).usd == pytest.approx(0.25)
