"""Rendering a Managed Agents turn into a Slack thread (design 001 §7.2, §7.5).

The shape of a turn in Slack:

    👀                       reaction on the user's message, immediately
    "🔍 investigating…"      one placeholder message, edited as work proceeds
    <the answer>             the placeholder becomes the answer

One edited message rather than a stream of new ones. Slack rate limits make
token streaming hostile, and a thread full of progress notes is unreadable
during an incident.

**Intermediate assistant messages are status, not output.** A delegating turn
emits several `agent.message` events -- the coordinator narrating while it
waits on a subagent ("I'll wait for its report"). Those are the model talking
to itself; posting each one would fill the thread with noise. Only the final
message of a turn is the answer, so intermediates are shown in the placeholder
and then replaced.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("k8srca.slack.relay")

SLACK_LIMIT = 3800  # hard limit is 4000; leave room for continuation markers

# Tool name -> what a human should read while it runs.
TOOL_LABELS = {
    "k8s_get_pod_summaries": "listing pods",
    "k8s_get_pod_container_statuses": "checking container status",
    "k8s_get_pod_events": "reading pod events",
    "k8s_get_pod_spec": "reading the pod spec",
    "k8s_get_events": "reading cluster events",
    "k8s_get_node_summaries": "checking nodes",
    "k8s_get_deployment_summaries": "checking deployments",
    "k8s_get_logs_for_pod_and_container": "reading container logs",
    "bash": "running a check",
    "read": "reading a file",
    "grep": "searching",
}


def describe_tool(name: str) -> str:
    if name in TOOL_LABELS:
        return TOOL_LABELS[name]
    return f"running {name.removeprefix('k8s_').replace('_', ' ')}"


def chunk(text: str, limit: int = SLACK_LIMIT) -> list[str]:
    """Split for Slack, preferring paragraph then line boundaries."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = limit
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        parts.append(remaining)
    return parts


def to_mrkdwn(text: str) -> str:
    """Convert the Markdown the model writes into Slack's mrkdwn.

    Slack uses *bold* not **bold**, has no heading syntax, and renders
    unconverted markup literally.
    """
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.MULTILINE)  # headings -> bold
    text = re.sub(r"(?<!\*)\*\*(?!\*)(.+?)(?<!\*)\*\*(?!\*)", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)
    return text


@dataclass
class TurnRender:
    """Accumulates a turn; the final assistant message is the answer."""

    messages: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    delegations: int = 0
    errors: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    terminated: bool = False

    @property
    def answer(self) -> str | None:
        return self.messages[-1] if self.messages else None

    @property
    def status_line(self) -> str:
        bits = []
        if self.tools:
            bits.append(describe_tool(self.tools[-1]))
        if self.delegations:
            bits.append(f"delegated {self.delegations} check(s)")
        return " · ".join(bits) or "thinking"


class SlackTurn:
    """Drives one turn and keeps a single placeholder message up to date."""

    def __init__(self, slack, channel: str, thread_ts: str, console_url: str | None = None):
        self.slack = slack
        self.channel = channel
        self.thread_ts = thread_ts
        self.console_url = console_url
        self.placeholder_ts: str | None = None
        self._last_status = ""

    # -- lifecycle -------------------------------------------------------
    def ack(self, message_ts: str) -> None:
        try:
            self.slack.reactions_add(channel=self.channel, timestamp=message_ts, name="eyes")
        except Exception as exc:  # noqa: BLE001 - reactions:write may not be granted
            log.debug("reaction skipped: %s", exc)

    def start(self, text: str = "🔍 investigating…") -> None:
        resp = self.slack.chat_postMessage(channel=self.channel, thread_ts=self.thread_ts, text=text)
        self.placeholder_ts = resp["ts"]

    def status(self, text: str) -> None:
        """Edit the placeholder. Cheap, and keeps the thread to one message."""
        if not self.placeholder_ts or text == self._last_status:
            return
        self._last_status = text
        try:
            self.slack.chat_update(channel=self.channel, ts=self.placeholder_ts,
                                   text=f"🔍 {text}…")
        except Exception as exc:  # noqa: BLE001 - a failed status must not fail the turn
            log.debug("status update failed: %s", exc)

    def deliver(self, text: str) -> None:
        """Replace the placeholder with the answer, continuing in replies."""
        parts = chunk(to_mrkdwn(text))
        if self.placeholder_ts:
            self.slack.chat_update(channel=self.channel, ts=self.placeholder_ts, text=parts[0])
        else:
            self.slack.chat_postMessage(channel=self.channel, thread_ts=self.thread_ts, text=parts[0])
        for part in parts[1:]:
            self.slack.chat_postMessage(channel=self.channel, thread_ts=self.thread_ts, text=part)

    def fail(self, text: str) -> None:
        self.deliver(f":warning: {text}")


def consume(stream, turn: SlackTurn, on_progress: Callable[[str], None] | None = None) -> TurnRender:
    """Consume one turn's events, updating Slack as it goes.

    Break rules from 001 §7.2: terminated always ends it; idle ends it only
    when the stop reason is not `requires_action`, which is transient.
    """
    render = TurnRender()

    def progress() -> None:
        turn.status(render.status_line)
        if on_progress:
            on_progress(render.status_line)

    for event in stream:
        etype = getattr(event, "type", "")

        if etype == "agent.message":
            text = "".join(b.text for b in (getattr(event, "content", None) or [])
                           if getattr(b, "type", None) == "text")
            if text.strip():
                render.messages.append(text)
                # Not delivered yet: a later message may supersede it.
                turn.status(_summarize(text))
        elif etype in ("agent.tool_use", "agent.custom_tool_use", "agent.mcp_tool_use"):
            render.tools.append(getattr(event, "name", "?"))
            progress()
        elif etype == "session.thread_created":
            render.delegations += 1
            progress()
        elif etype == "session.error":
            render.errors.append(str(getattr(event, "error", event)))
        elif etype == "session.status_terminated":
            render.terminated = True
            break
        elif etype == "session.status_idle":
            reason = getattr(getattr(event, "stop_reason", None), "type", None)
            render.stop_reason = reason
            if reason == "requires_action":
                continue
            break
    return render


def _summarize(text: str, limit: int = 90) -> str:
    """A one-line gist of an intermediate message, for the placeholder."""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    # Strip markup, then re-strip: removing a leading "## " leaves a space.
    first = re.sub(r"[*_`#]", "", first).strip()
    return first[:limit] + ("…" if len(first) > limit else "")
