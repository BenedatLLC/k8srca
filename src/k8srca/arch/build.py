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

            kubeconfig = cfg.cluster_access.kubeconfig
            n = collect_history(source, arch, str(kubeconfig) if kubeconfig else None)
            report.append(f"change_history replicasets: {n} workload(s) with revision history")
        elif source.type == "docs":
            from .docs import collect_docs

            n = collect_docs(source, arch)
            report.append(f"docs           {source.path or source.url}: {n} note(s)")
    return arch, report
