"""The cluster-architecture skill: dkgg, behind k8srca's generator contract.

The generator itself is dkgg (packages/dkgg), a standalone package that never
imports k8srca. This adapter is the whole of k8srca's side: it maps k8srca's
configuration onto dkgg's inputs and fits dkgg's output to the contract in
005 §6.2.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dkgg import render
from dkgg.sources import Server

from ..core.generator import GeneratorReport

if TYPE_CHECKING:
    from ..config import Config

#: k8srca's cache of pinned charts and docs, kept where it always was so an
#: existing deployment does not fetch everything again.
CACHE = Path(".k8srca/sources")


def servers(cfg: "Config") -> list[Server]:
    """k8srca's MCP servers as dkgg sees them: from the host, where builds run."""
    return [Server(name=s.name, url=s.host_url(), timeout_s=float(s.timeout_s))
            for s in cfg.mcp]


class ArchitectureGenerator:
    """Every configured source -> the `cluster-architecture` skill (001 §5.2)."""

    name = "cluster-architecture"
    format = render.FORMAT

    async def generate(self, cfg: "Config", dest: Path) -> GeneratorReport:
        from dkgg.build import build

        if not cfg.architecture.active():
            raise ValueError("no architecture sources configured (k8srca.yaml: architecture)")
        arch, lines = await build(cfg.architecture.sources, servers(cfg), cache=CACHE)
        data = render.write(arch, dest)
        report = GeneratorReport(generator=self.name, lines=lines)
        report.counts = {
            "services": len(data["services"]),
            "conflicts": sum(1 for s in data["services"].values()
                             for f in s["facts"].values() if f.get("conflicts")),
            "sources": len(data["sources"]),
        }
        if not data["services"]:
            report.warnings.append("no services found: the skill is empty")
        return report
