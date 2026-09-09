"""Driving a Managed Agents session and rendering its event stream.

Implements the client-side rules from design 001 §7.2 that are easy to get
wrong: stream before send, and never break on `session.status_idle` alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from anthropic import Anthropic

from .config import Config
from .state import State


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


def _text(event: Any) -> str:
    parts = []
    for block in getattr(event, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


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
