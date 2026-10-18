"""The MCP server: tools.py over streamable HTTP (design 009 §4).

It is a peer of k8stools. k8srca declares it with `provides: [prometheus]`,
and the knowledge base's `prometheus` actions resolve against these names.
Every tool reads; none takes a path, a method or a URL from its caller
(client.py).
"""

from __future__ import annotations

from datetime import datetime

from mcp.server.mcpserver import MCPServer

from .client import PromClient
from .tools import Tools

INSTRUCTIONS = """Read-only Prometheus. Results carry `notes`: what the series
cannot show (pod-level cgroups, scrape interval against short lives, the
retention edge, double scraping). Read them before concluding from a number.
Times are RFC 3339 or unix seconds; durations are PromQL's (15m, 7d)."""


def when(value: str | float | None) -> float | None:
    """RFC 3339 or unix seconds, to unix seconds."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def build(client: PromClient) -> MCPServer:
    t = Tools(client)
    server = MCPServer("promtools", instructions=INSTRUCTIONS)

    @server.tool()
    def query(promql: str, time: str | None = None) -> dict:
        """Evaluate a PromQL expression at one moment (default: now). A range
        selector such as `x[1h]` returns the raw samples in that range."""
        return t.query(promql, when(time))

    @server.tool()
    def query_range(promql: str, start: str, end: str, step: str = "1m") -> dict:
        """Evaluate a PromQL expression at every `step` from start to end. At
        most 1500 points per series: widen the step for long ranges."""
        return t.query_range(promql, when(start), when(end), step)

    @server.tool()
    def series(match: list[str], start: str | None = None, end: str | None = None) -> dict:
        """The series matching selectors, with their labels, without samples."""
        return t.series(match, when(start), when(end))

    @server.tool()
    def label_values(label: str, match: list[str] | None = None) -> dict:
        """The values a label takes, optionally among series matching selectors."""
        return t.label_values(label, match)

    @server.tool()
    def metadata(metric: str | None = None) -> dict:
        """Type and help text for a metric, or for all."""
        return t.metadata(metric)

    @server.tool()
    def targets() -> dict:
        """What is scraped, how often, and whether the last scrape worked."""
        return t.targets()

    @server.tool()
    def runtime_info() -> dict:
        """Retention, the oldest retained sample, and scrape intervals: the edges
        of what any query here can see."""
        return t.runtime_info()

    @server.tool()
    def memory_report(namespace: str, pod: str, window: str = "7d",
                      end: str | None = None) -> dict:
        """Working-set peak and median against the memory limit, per container or
        per pod, with the sample count and what the samples cannot show. `pod`
        is a regex, e.g. `ad-.*`."""
        return t.memory_report(namespace, pod, window, when(end))

    @server.tool()
    def restart_report(namespace: str, pod: str, container: str | None = None,
                       window: str = "7d", end: str | None = None) -> dict:
        """Restarts across the window: total, per-hour least and most, hours with
        none, whether the first restart falls inside the window (an observed
        onset), and how long each termination reason was the latest. `pod` is a
        regex."""
        return t.restart_report(namespace, pod, container, window, when(end))

    return server
