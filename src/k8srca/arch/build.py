"""Assemble the architecture from every configured source."""

from __future__ import annotations

from ..config import Config
from .model import Architecture


async def build(cfg: Config) -> tuple[Architecture, list[str]]:
    """Run each source in order, merging into one Architecture.

    Order matters only for reporting; facts are kept per-source with
    provenance, so a later source never overwrites an earlier one.
    """
    arch = Architecture()
    report: list[str] = []

    for source in cfg.architecture.active():
        pinned = _pinned(source)
        if pinned:
            from .fetch import resolve

            source = resolve(source)
            report.append(f"{source.type:<14} {pinned} -> {source.path}")
        if source.type == "live_cluster":
            from .live import collect

            server = cfg.server(source.server or cfg.mcp[0].name)
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

            server = cfg.server(source.server or cfg.mcp[0].name)
            n = await collect_history(source, arch, server)
            report.append(f"change_history {server.name}: {n} workload(s) with revision history")
        elif source.type == "docs":
            from .docs import collect_docs

            n = collect_docs(source, arch)
            report.append(f"docs           {source.path or source.url}: {n} note(s)")
    return arch, report


def _pinned(source) -> str | None:
    """A pinned external artifact, named for the report; None for a local path."""
    if source.helm is not None:
        return f"{source.helm.chart} {source.helm.version} ({source.helm.repo})"
    if source.git is not None:
        return f"{source.git.repo}@{source.git.ref[:12]}:{source.git.path}"
    return None
