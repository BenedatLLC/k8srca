"""Session resolution and turn execution (design 001 §7.1, §7.2).

Everything here runs against fakes for Slack and the Anthropic control plane.
What is being tested is the orchestrator's own bookkeeping, and the property
that matters most in a shared channel: two investigations running at once must
not leak into each other's Slack thread.
"""

from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from k8srca.config import AgentConfig, Config, McpServer, ModelConfig, SandboxConfig
from k8srca.settings import SlackSettings
from k8srca.slack import app as app_mod
from k8srca.slack.app import Orchestrator, event_key
from k8srca.slack.sessions import SessionStore
from k8srca.state import AgentState, State

from fakes import FakeSlack


# -- events -------------------------------------------------------------
def msg(text):
    return SimpleNamespace(type="agent.message",
                           content=[SimpleNamespace(type="text", text=text)])


def tool(name):
    return SimpleNamespace(type="agent.tool_use", name=name)


def idle(reason="end_turn"):
    return SimpleNamespace(type="session.status_idle",
                           stop_reason=SimpleNamespace(type=reason))


def terminated():
    return SimpleNamespace(type="session.status_terminated")


def error(text):
    return SimpleNamespace(type="session.error", error=text)


# -- fakes --------------------------------------------------------------
class FakeStream:
    def __init__(self, sessions, session_id):
        self.sessions = sessions
        self.session_id = session_id

    def __enter__(self):
        with self.sessions.lock:
            self.sessions.open_streams += 1
            self.sessions.peak_streams = max(self.sessions.peak_streams,
                                             self.sessions.open_streams)
        return self

    def __exit__(self, *exc):
        with self.sessions.lock:
            self.sessions.open_streams -= 1
        return False

    def __iter__(self):
        yield from self.sessions.script(self.session_id)


class FakeEvents:
    def __init__(self, sessions):
        self.sessions = sessions

    def stream(self, session_id):
        return FakeStream(self.sessions, session_id)

    def send(self, session_id, events):
        with self.sessions.lock:
            self.sessions.sent.append((session_id, events))


class FakeSessions:
    def __init__(self, script=None, status="active"):
        self.lock = threading.Lock()
        self.created: list[tuple[str, dict]] = []
        self.retrieved: list[str] = []
        self.sent: list[tuple[str, list]] = []
        self.open_streams = 0
        self.peak_streams = 0
        self.status = status
        self.retrieve_raises: Exception | None = None
        self.script = script or (lambda sid: [msg("done"), idle()])
        self._n = itertools.count()
        self.events = FakeEvents(self)

    def create(self, **kw):
        with self.lock:
            sid = f"sess_{next(self._n)}"
            self.created.append((sid, kw))
        return SimpleNamespace(id=sid)

    def retrieve(self, session_id):
        with self.lock:
            self.retrieved.append(session_id)
        if self.retrieve_raises:
            raise self.retrieve_raises
        return SimpleNamespace(id=session_id, status=self.status)


class FakeAnthropic:
    def __init__(self, sessions):
        self.beta = SimpleNamespace(sessions=sessions)


# -- fixtures -----------------------------------------------------------
def config(**overrides):
    base = dict(
        mcp=[McpServer(name="k8stools", url="http://k8stools:8000/mcp", prefix="k8s_",
                       groups={"triage": ["a"], "full": "*"})],
        agents={"coord": AgentConfig(role="coordinator", model=ModelConfig(id="claude-sonnet-5"),
                                     system_prompt=Path("nope.md"))},
        sandbox=SandboxConfig(image="img"),
    )
    base.update(overrides)
    return Config(**base)


def state():
    return State(environment_id="env_1", config_rev="abc1234",
                 agents={"coord": AgentState(id="agt_1", version=3, manifest="m",
                                             model="claude-sonnet-5")})


@pytest.fixture
def sessions():
    return FakeSessions()


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.fixture
def orch(monkeypatch, sessions, store):
    monkeypatch.setattr(app_mod.anthropic, "Anthropic", lambda *a, **k: FakeAnthropic(sessions))
    return Orchestrator(config(), state(),
                        SlackSettings(socket_mode_token="xapp-x", bot_token="xoxb-x"),
                        store)


# -- resolve ------------------------------------------------------------
class TestResolve:
    def test_unknown_thread_needs_a_new_session(self, orch):
        assert orch.resolve("C1", "111.1") == ("", True)

    def test_live_session_is_reused(self, orch, store, sessions):
        store.put("C1", "111.1", "sess_a", 3)
        assert orch.resolve("C1", "111.1") == ("sess_a", False)
        assert sessions.retrieved == ["sess_a"]

    def test_terminated_upstream_starts_a_fresh_one(self, orch, store, sessions):
        store.put("C1", "111.1", "sess_a", 3)
        sessions.status = "terminated"
        assert orch.resolve("C1", "111.1") == ("", True)

    def test_unreachable_session_starts_a_fresh_one(self, orch, store, sessions):
        # Deleted, expired, or the control plane is down -- all the same to us.
        store.put("C1", "111.1", "sess_a", 3)
        sessions.retrieve_raises = RuntimeError("404 not found")
        assert orch.resolve("C1", "111.1") == ("", True)

    def test_terminated_locally_does_not_ask_upstream(self, orch, store, sessions):
        store.put("C1", "111.1", "sess_a", 3)
        store.touch("C1", "111.1", "terminated")
        assert orch.resolve("C1", "111.1") == ("", True)
        assert sessions.retrieved == []

    def test_stale_thread_does_not_ask_upstream(self, orch, store, sessions):
        # Past the idle TTL the session is gone on Anthropic's side anyway;
        # spending a round trip to be told so is waste.
        store.put("C1", "111.1", "sess_a", 3)
        with store._conn() as c:
            c.execute("UPDATE threads SET last_activity=?", (time.time() - 10_000 * 60,))
            c.commit()
        assert orch.resolve("C1", "111.1") == ("", True)
        assert sessions.retrieved == []


# -- a single turn ------------------------------------------------------
class TestFirstTurn:
    def test_creates_a_session_and_answers(self, orch, sessions):
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "why is ad crashing?", "U1")
        assert len(sessions.created) == 1
        assert slack.final_text("C1", "111.1") == "done"

    def test_records_the_thread(self, orch, store, sessions):
        orch.handle(FakeSlack(), "C1", "111.1", "111.1", "q", "U1")
        row = store.get("C1", "111.1")
        assert row.session_id == sessions.created[0][0] and row.status == "active"

    def test_carries_slack_provenance_in_metadata(self, orch, sessions):
        # Metadata is set at creation and cannot be backfilled (003 §2.2).
        orch.handle(FakeSlack(), "C1", "111.1", "111.1", "q", "U7")
        meta = sessions.created[0][1]["metadata"]
        assert meta["slack_channel"] == "C1"
        assert meta["slack_thread_ts"] == "111.1"
        assert meta["slack_user"] == "U7"
        assert meta["trigger"] == "user_question"
        assert meta["config_rev"] == "abc1234"

    def test_does_not_send_a_duplicate_first_message(self, orch, sessions):
        # initial_events already carries the question; sending it again would
        # ask it twice.
        orch.handle(FakeSlack(), "C1", "111.1", "111.1", "q", "U1")
        assert sessions.sent == []

    def test_acks_the_users_message(self, orch):
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "222.2", "q", "U1")
        assert slack.reactions == [{"channel": "C1", "timestamp": "222.2", "name": "eyes"}]

    def test_answer_replaces_the_placeholder_rather_than_adding_a_message(self, orch):
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "q", "U1")
        assert len(slack.posts) == 1  # one placeholder, edited into the answer
        assert slack.posts[0]["ts"] == slack.updates[-1]["ts"]


class TestFollowUp:
    def test_reuses_the_session(self, orch, sessions):
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "first", "U1")
        orch.handle(slack, "C1", "111.1", "111.2", "second", "U1")
        assert len(sessions.created) == 1

    def test_sends_the_follow_up_text(self, orch, sessions):
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "first", "U1")
        orch.handle(slack, "C1", "111.1", "111.2", "and the logs?", "U1")
        sid, events = sessions.sent[0]
        assert sid == sessions.created[0][0]
        assert events[0]["content"][0]["text"] == "and the logs?"

    def test_a_different_thread_gets_its_own_session(self, orch, sessions):
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "q", "U1")
        orch.handle(slack, "C1", "222.2", "222.2", "q", "U1")
        assert len({sid for sid, _ in sessions.created}) == 2


# -- outcomes other than an answer --------------------------------------
class TestTurnOutcomes:
    def make(self, monkeypatch, store, script):
        sessions = FakeSessions(script=script)
        monkeypatch.setattr(app_mod.anthropic, "Anthropic", lambda *a, **k: FakeAnthropic(sessions))
        orch = Orchestrator(config(), state(),
                            SlackSettings(socket_mode_token="x", bot_token="x"), store)
        return orch, sessions

    def test_termination_is_recorded_so_the_next_turn_restarts(self, monkeypatch, store):
        orch, sessions = self.make(monkeypatch, store,
                                   lambda sid: [msg("last words"), terminated()])
        orch.handle(FakeSlack(), "C1", "111.1", "111.1", "q", "U1")
        assert store.get("C1", "111.1").status == "terminated"
        orch.handle(FakeSlack(), "C1", "111.1", "111.2", "again", "U1")
        assert len(sessions.created) == 2

    def test_budget_cap_explains_itself(self, monkeypatch, store):
        orch, _ = self.make(monkeypatch, store, lambda sid: [idle("budget_reached")])
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "q", "U1")
        assert "cost cap" in slack.final_text("C1", "111.1")

    def test_agent_error_is_surfaced(self, monkeypatch, store):
        orch, _ = self.make(monkeypatch, store, lambda sid: [error("tool exploded"), idle()])
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "q", "U1")
        assert "tool exploded" in slack.final_text("C1", "111.1")

    def test_silence_points_at_the_console(self, monkeypatch, store):
        orch, _ = self.make(monkeypatch, store, lambda sid: [tool("k8s_get_pod_summaries"), idle()])
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "q", "U1")
        assert "stopped without answering" in slack.final_text("C1", "111.1")

    def test_only_the_final_message_is_delivered(self, monkeypatch, store):
        # Narration while waiting on a subagent is status, not output (001 §7.5).
        orch, _ = self.make(monkeypatch, store,
                            lambda sid: [msg("I'll check the pods."), msg("*Finding* — OOMKilled."),
                                         idle()])
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "q", "U1")
        assert slack.final_text("C1", "111.1") == "*Finding* — OOMKilled."

    def test_a_crash_reports_into_the_thread_instead_of_hanging(self, monkeypatch, store):
        def boom(sid):
            raise RuntimeError("stream died")
            yield  # pragma: no cover

        orch, _ = self.make(monkeypatch, store, boom)
        slack = FakeSlack()
        orch.handle(slack, "C1", "111.1", "111.1", "q", "U1")  # must not raise
        assert "Something went wrong" in slack.final_text("C1", "111.1")


# -- concurrency --------------------------------------------------------
def slow_script(delay=0.3):
    def script(sid):
        yield msg(f"answer for {sid}")
        time.sleep(delay)
        yield idle()
    return script


class TestConcurrentThreads:
    @pytest.fixture
    def setup(self, monkeypatch, store):
        sessions = FakeSessions(script=slow_script())
        monkeypatch.setattr(app_mod.anthropic, "Anthropic", lambda *a, **k: FakeAnthropic(sessions))
        orch = Orchestrator(config(), state(),
                            SlackSettings(socket_mode_token="x", bot_token="x"), store)
        return orch, sessions

    def run_threads(self, orch, slack, threads):
        workers = [threading.Thread(target=orch.handle,
                                    args=(slack, ch, ts, ts, f"question {ts}", "U1"))
                   for ch, ts in threads]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=30)
        assert not any(w.is_alive() for w in workers)

    def test_three_threads_actually_overlap(self, setup):
        orch, sessions = setup
        self.run_threads(orch, FakeSlack(), [("C1", "1.1"), ("C1", "2.2"), ("C1", "3.3")])
        assert sessions.peak_streams == 3

    def test_each_thread_gets_its_own_session(self, setup):
        orch, sessions = setup
        self.run_threads(orch, FakeSlack(), [("C1", "1.1"), ("C1", "2.2"), ("C1", "3.3")])
        assert len({sid for sid, _ in sessions.created}) == 3

    def test_no_cross_talk_between_overlapping_threads(self, setup, store):
        # The regression this file exists for: each Slack thread must receive
        # the answer from the session it owns, and nobody else's.
        orch, _ = setup
        slack = FakeSlack()
        threads = [("C1", "1.1"), ("C1", "2.2"), ("C1", "3.3")]
        self.run_threads(orch, slack, threads)
        for ch, ts in threads:
            sid = store.get(ch, ts).session_id
            assert slack.final_text(ch, ts) == f"answer for {sid}"

    def test_each_thread_gets_exactly_one_placeholder(self, setup):
        orch, _ = setup
        slack = FakeSlack()
        threads = [("C1", "1.1"), ("C1", "2.2"), ("C1", "3.3")]
        self.run_threads(orch, slack, threads)
        for ch, ts in threads:
            assert len([p for p in slack.posts
                        if p["channel"] == ch and p["thread_ts"] == ts]) == 1

    def test_turns_in_one_thread_are_serialised(self, setup, store):
        # A session processes turns in order; two overlapping streams on one
        # thread would interleave output.
        orch, sessions = setup
        slack = FakeSlack()
        self.run_threads(orch, slack, [("C1", "1.1"), ("C1", "1.1")])
        assert sessions.peak_streams == 1
        assert len(sessions.created) == 1  # the second turn reused the first session

    def test_distinct_locks_per_thread(self, orch):
        assert orch.locks[("C1", "1.1")] is orch.locks[("C1", "1.1")]
        assert orch.locks[("C1", "1.1")] is not orch.locks[("C1", "2.2")]


# -- inbound event identity ---------------------------------------------
class TestEventKey:
    def test_prefers_client_msg_id(self):
        assert event_key({"client_msg_id": "c1", "event_ts": "e1", "ts": "t1"}) == "c1"

    def test_falls_back_to_event_ts(self):
        assert event_key({"event_ts": "e1", "ts": "t1"}) == "e1"

    def test_falls_back_to_ts(self):
        assert event_key({"ts": "t1"}) == "t1"

    def test_empty_when_nothing_identifies_it(self):
        assert event_key({}) == ""
