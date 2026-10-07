"""Driving a Managed Agents session and rendering its event stream.

Implements the client-side rules from design 001 §7.2 that are easy to get
wrong: stream before send, and never break on `session.status_idle` alone.
"""

from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

import anthropic
import httpx2
from anthropic import Anthropic

from .config import Config
from .state import State

log = logging.getLogger(__name__)

#: A connection that dropped, as opposed to the API refusing. The SDK raises a
#: bare httpx2 error when the socket closes mid-stream -- "peer closed
#: connection without sending complete message" -- not an APIConnectionError.
DROPPED = (httpx2.TransportError, anthropic.APIConnectionError)

#: Reconnect attempts per turn, with backoff doubling from 1s to a 16s cap.
STREAM_RECONNECTS = 5


def budget_param(usd: float) -> dict:
    """Session spend cap. `amount` is minor units as an integer string."""
    return {"type": "limit", "max_list_cost": {"amount": str(int(round(usd * 100))), "currency": "USD"}}


def console_url(session_id: str, workspace_id: str | None) -> str:
    # `default` is only right when the API key belongs to the Default
    # workspace; a wrong value lands on "Session not found" (003 §2.2).
    return f"https://platform.claude.com/workspaces/{workspace_id or 'default'}/sessions/{session_id}"


def create(client: Anthropic, cfg: Config, state: State, prompt: str,
           title: str | None = None, metadata: dict[str, str] | None = None) -> Any:
    coordinator = state.agents[cfg.coordinator]
    return client.beta.sessions.create(
        agent={"type": "agent", "id": coordinator.id, "version": coordinator.version},
        environment_id=state.environment_id,
        title=title or prompt[:80],
        budget=budget_param(cfg.session.budget_usd),
        metadata=metadata or {},
        initial_events=[{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
    )


class StreamEnded(Exception):
    """The event stream closed cleanly with the turn unfinished: no terminal
    event (#2). Treated as a dropped connection, and raised once reconnecting
    has not recovered the turn."""


@dataclass
class Turn:
    """What a single turn produced, for callers that want the outcome."""

    messages: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    threads: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    terminated: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors and self.stop_reason in (None, "end_turn")

    @property
    def complete(self) -> bool:
        """The session finished the turn: terminated, or idle for a reason that
        is not waiting on tool results. A turn that is not complete ended for
        the reader, not for the agent, and its messages are not an answer."""
        return self.terminated or self.stop_reason in ("end_turn", "retries_exhausted",
                                                        "budget_reached")


def _text(event: Any) -> str:
    parts = []
    for block in getattr(event, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


def sent_event_id(sent: Any) -> str | None:
    """The id of the first event an `events.send` call accepted."""
    data = getattr(sent, "data", None) or []
    return getattr(data[0], "id", None) if data else None


def _missed(history: Iterable[Any], *, from_start: bool, after: str | None) -> list[Any]:
    """The part of a session's history that belongs to the current turn."""
    events = list(history)
    if from_start:
        return events
    ids = [getattr(e, "id", None) for e in events]
    if after is None or after not in ids:
        # Not processed yet, so the turn has produced nothing to miss. Never
        # fall back to earlier history: it holds the previous turn's final
        # idle, which would end this turn with the last turn's answer.
        return []
    return events[ids.index(after) + 1:]


def turn_events(client: Anthropic, session_id: str, stream: Iterable[Any], *,
                from_start: bool = False, after: str | None = None,
                reconnects: int = STREAM_RECONNECTS,
                sleep: Callable[[float], None] = time.sleep,
                abort: Callable[[], bool] = lambda: False) -> Iterator[Any]:
    """One turn's events, surviving a dropped connection.

    SSE has no replay: a stream reopened after a drop starts from "now", and
    everything emitted in between is gone. So on a drop this reopens the
    stream, reads the session history for the events that belong to this turn,
    and replays those before tailing the new stream, skipping every event id
    already delivered. A batch was lost to exactly this -- a scenario run's
    stream closed mid-turn, the session ran on unobserved, and the error
    killed the remaining runs.

    A stream that *ends* without the caller having stopped reading -- it
    stops on the turn's terminal event -- is treated the same way: the server
    closed it mid-turn. Taking that end as the end of the turn graded a
    "still waiting..." message as a scenario's answer (#2). Reconnects are
    counted while they make no progress; any new event restores the budget, so
    a long turn is not penalised for an occasional close.

    `abort` is asked before each reconnect: a caller that has given up on the
    session (a watchdog closed the stream) is not reconnected to it.

    `stream` is the caller's already-open stream (stream before send, 001
    §7.2). Which history is this turn's: everything, for a turn started by
    `initial_events` on a new session (`from_start`); otherwise only events
    after `after`, the id of the user.message this turn sent.
    """
    seen: set[str] = set()
    source: Iterator[Any] = iter(stream)
    owned: Any = None
    left, delay = reconnects, 1.0
    try:
        while True:
            try:
                for event in source:
                    eid = getattr(event, "id", None)
                    if eid is not None:
                        if eid in seen:
                            continue
                        seen.add(eid)
                    yield event
                    left, delay = reconnects, 1.0
                raise StreamEnded(f"the event stream for {session_id} ended "
                                  f"without a terminal event")
            except (*DROPPED, StreamEnded) as exc:
                dropped: BaseException = exc
                while True:
                    if left <= 0 or abort():
                        raise dropped
                    left -= 1
                    log.warning("stream dropped session=%s (%s); reconnecting, %d left",
                                session_id, dropped, left)
                    sleep(delay)
                    delay = min(delay * 2, 16.0)
                    if owned is not None:
                        owned.close()
                        owned = None
                    try:
                        # Stream first, then history, as for the first open:
                        # an event emitted between the two then appears in
                        # both, and `seen` drops the second copy, rather than
                        # in neither.
                        owned = client.beta.sessions.events.stream(session_id=session_id)
                        missed = _missed(
                            client.beta.sessions.events.list(session_id=session_id),
                            from_start=from_start, after=after)
                    except DROPPED as again:
                        dropped = again
                        continue
                    source = itertools.chain(missed, owned)
                    break
    finally:
        if owned is not None:
            owned.close()


def consume(stream: Iterator[Any], on_event: Callable[[str, str], None] | None = None) -> Turn:
    """Consume one turn's events.

    Breaks on `session.status_terminated`, or on `session.status_idle` whose
    stop reason is not `requires_action` -- idle alone is transient, emitted
    between parallel tool calls and while awaiting client-side results.
    """
    turn = Turn()

    def emit(kind: str, detail: str) -> None:
        if on_event:
            on_event(kind, detail)

    for event in stream:
        etype = getattr(event, "type", "")

        if etype == "agent.message":
            text = _text(event)
            if text:
                turn.messages.append(text)
                emit("message", text)
        elif etype in ("agent.tool_use", "agent.custom_tool_use", "agent.mcp_tool_use"):
            name = getattr(event, "name", "?")
            turn.tool_calls.append(name)
            emit("tool", name)
        elif etype == "session.thread_created":
            tid = getattr(event, "session_thread_id", None) or getattr(event, "id", "?")
            turn.threads.add(str(tid))
            emit("thread", f"delegated -> {getattr(event, 'agent_name', tid)}")
        elif etype == "session.error":
            detail = str(getattr(event, "error", event))
            turn.errors.append(detail)
            emit("error", detail)
        elif etype == "session.status_terminated":
            turn.terminated = True
            emit("status", "terminated")
            break
        elif etype == "session.status_idle":
            reason = getattr(getattr(event, "stop_reason", None), "type", None)
            turn.stop_reason = reason
            if reason == "requires_action":
                emit("status", "idle: requires_action")
                continue  # waiting on us, not done
            emit("status", f"idle: {reason}")
            break
    return turn
