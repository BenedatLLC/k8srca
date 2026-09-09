"""Where a session's wall clock went (design 003 §2.3(d)).

The Anthropic console reports per-tool call counts and durations; it does not
decompose a turn's *latency*, which is what you need to answer "why did that
take two minutes". Reconstructed from event timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TOOL_USE = ("agent.tool_use", "agent.custom_tool_use", "agent.mcp_tool_use")
TOOL_RESULT = ("user.tool_result", "user.custom_tool_result", "agent.tool_result",
               "agent.mcp_tool_result")


@dataclass
class Timeline:
    wall: float = 0.0
    model_seconds: float = 0.0
    tool_seconds: float = 0.0
    model_requests: list[float] = field(default_factory=list)
    tool_calls: list[tuple[float, float]] = field(default_factory=list)  # (start, duration)
    delegations: int = 0
    first_tool_wait: float = 0.0   # session start -> first tool result

    @property
    def unaccounted(self) -> float:
        return max(0.0, self.wall - self.model_seconds - self.tool_seconds)


def build(events: list[Any]) -> Timeline:
    """Decompose a session's events into model time, tool time, and the rest.

    Tool pairing is approximate: results carry no reference to their call, so
    calls are matched to results in order. That is right for sequential calls
    and smears durations across a parallel batch, so treat individual tool
    durations as indicative and the totals as sound.
    """
    evs = sorted([e for e in events if getattr(e, "processed_at", None)],
                 key=lambda e: e.processed_at)
    tl = Timeline()
    if not evs:
        return tl
    t0 = evs[0].processed_at
    rel = lambda e: (e.processed_at - t0).total_seconds()  # noqa: E731
    tl.wall = rel(evs[-1])

    open_span = None
    pending: list[Any] = []
    for e in evs:
        if e.type == "span.model_request_start":
            open_span = e
        elif e.type == "span.model_request_end" and open_span is not None:
            d = rel(e) - rel(open_span)
            tl.model_seconds += d
            tl.model_requests.append(d)
            open_span = None
        elif e.type in TOOL_USE:
            pending.append(e)
        elif e.type in TOOL_RESULT and pending:
            start = pending.pop(0)
            d = rel(e) - rel(start)
            tl.tool_seconds += d
            tl.tool_calls.append((rel(start), d))
            if not tl.first_tool_wait:
                tl.first_tool_wait = rel(e)
        elif e.type == "session.thread_created":
            tl.delegations += 1
    return tl


def render(tl: Timeline, cost_cents: int | None = None) -> str:
    if not tl.wall:
        return "no timed events"
    pct = lambda v: f"{v / tl.wall * 100:4.1f}%"  # noqa: E731
    lines = [
        f"wall clock        {tl.wall:7.1f}s",
        f"  model inference {tl.model_seconds:7.1f}s  {pct(tl.model_seconds)}  "
        f"{len(tl.model_requests)} requests",
        f"  tool execution  {tl.tool_seconds:7.1f}s  {pct(tl.tool_seconds)}  "
        f"{len(tl.tool_calls)} calls",
        f"  unaccounted     {tl.unaccounted:7.1f}s  {pct(tl.unaccounted)}  "
        f"(queueing, sandbox start, idle gaps)",
    ]
    if tl.delegations:
        lines.append(f"  delegations     {tl.delegations}")
    if cost_cents is not None:
        lines.append(f"cost              ${cost_cents / 100:.2f}")
    if tl.tool_calls:
        slowest = sorted(tl.tool_calls, key=lambda c: -c[1])[:3]
        lines.append("slowest tool calls (start@ / duration):")
        lines += [f"    {s:6.1f}s  {d:5.1f}s" for s, d in slowest]
    # A long first-tool wait is almost never the tool: it is the sandbox not
    # being available yet, which usually means the poller was busy.
    if tl.first_tool_wait > 15:
        lines.append(
            f"NOTE: {tl.first_tool_wait:.0f}s before the first tool result. That is sandbox\n"
            f"      availability, not tool latency -- check whether the poller was\n"
            f"      already occupied (K8SRCA_MAX_CONCURRENT_SESSIONS).")
    return "\n".join(lines)
