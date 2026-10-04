"""Assemble the architecture from every configured source."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .model import Architecture
from .sources import ArchSource, Server


async def build(sources: Sequence[ArchSource], servers: Sequence[Server],
                cache: Path | None = None) -> tuple[Architecture, list[str]]:
    """Run each source in order, merging into one Architecture.

    Order matters only for reporting; facts are kept per-source with
    provenance, so a later source never overwrites an earlier one. A live
    source names its server; one that names none uses the first.
    """
    arch = Architecture()
    report: list[str] = []
    by_name = {s.name: s for s in servers}

    def server_for(source: ArchSource) -> Server:
        if source.server is None:
            if not servers:
                raise ValueError(f"{source.type} needs an MCP server and none is configured")
            return servers[0]
        if source.server not in by_name:
            raise KeyError(f"no MCP server named {source.server!r}")
        return by_name[source.server]

    for source in (s for s in sources if s.enabled):
        recorded = len(arch.sources)
        pinned = _pinned(source)
        if pinned:
            from .fetch import resolve

            source = resolve(source) if cache is None else resolve(source, cache=cache)
            report.append(f"{source.type:<14} {pinned} -> {source.path}")
        if source.type == "live_cluster":
            from .live import collect

            server = server_for(source)
            before = len(arch.services)
            await collect(server, source.namespaces, arch)
            report.append(f"live_cluster   {server.name}: {len(arch.services) - before} services "
                          f"from {', '.join(source.namespaces)}")
        elif source.type == "chart_repo":
            from .charts import collect_charts

            n = collect_charts(source, arch)
            report.append(f"chart_repo     {source.path}: {n} declaration(s)")
        elif source.type == "change_history":
            from .history import collect_history

            server = server_for(source)
            n = await collect_history(source, arch, server)
            report.append(f"change_history {server.name}: {n} workload(s) with revision history")
        elif source.type == "docs":
            from .docs import collect_docs

            n = collect_docs(source, arch)
            report.append(f"docs           {source.path or source.url}: {n} note(s)")
        # A pinned source is recorded by what was pinned (chart version, docs
        # commit), not only by the cache directory it was read from.
        if pinned:
            for entry in arch.sources[recorded:]:
                entry["pinned"] = pinned
    return arch, report


def _pinned(source) -> str | None:
    """A pinned external artifact, named for the report; None for a local path."""
    if source.helm is not None:
        return f"{source.helm.chart} {source.helm.version} ({source.helm.repo})"
    if source.git is not None:
        return f"{source.git.repo}@{source.git.ref[:12]}:{source.git.path}"
    return None
