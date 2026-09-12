"""Fakes shared by more than one test module."""

from __future__ import annotations

import itertools
import threading
from pathlib import Path
from types import SimpleNamespace

from k8srca.config import AgentConfig, Config, McpServer, ModelConfig, SandboxConfig
from k8srca.state import AgentState, State


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

class FakeSlack:
    """Records what landed in each thread, so cross-talk is observable."""

    def __init__(self):
        self.lock = threading.Lock()
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.reactions: list[dict] = []
        self._ts = itertools.count(1)

    def chat_postMessage(self, channel, thread_ts, text):
        with self.lock:
            ts = f"ph.{next(self._ts)}"
            self.posts.append({"channel": channel, "thread_ts": thread_ts, "ts": ts, "text": text})
        return {"ts": ts}

    def chat_update(self, channel, ts, text):
        with self.lock:
            self.updates.append({"channel": channel, "ts": ts, "text": text})
        return {"ts": ts}

    def reactions_add(self, channel, timestamp, name):
        with self.lock:
            self.reactions.append({"channel": channel, "timestamp": timestamp, "name": name})

    # -- queries used by assertions -------------------------------------
    def thread_texts(self, channel, thread_ts) -> list[str]:
        """Everything a human would see in one thread, in order."""
        with self.lock:
            mine = {p["ts"] for p in self.posts
                    if p["channel"] == channel and p["thread_ts"] == thread_ts}
            out = [p["text"] for p in self.posts
                   if p["channel"] == channel and p["thread_ts"] == thread_ts]
            out += [u["text"] for u in self.updates if u["ts"] in mine]
        return out

    def final_text(self, channel, thread_ts) -> str:
        with self.lock:
            mine = [p["ts"] for p in self.posts
                    if p["channel"] == channel and p["thread_ts"] == thread_ts]
            updates = [u["text"] for u in self.updates if u["ts"] in set(mine)]
        return updates[-1] if updates else ""
