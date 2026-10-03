"""The cluster-architecture generator, behind the generator contract (005 §6.2)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..core.generator import GeneratorReport
from . import render

if TYPE_CHECKING:
    from ..config import Config


class ArchitectureGenerator:
    """Every configured source -> the `cluster-architecture` skill (001 §5.2)."""

    name = "cluster-architecture"
    format = render.FORMAT

    async def generate(self, cfg: "Config", dest: Path) -> GeneratorReport:
        from .build import build

        if not cfg.architecture.active():
            raise ValueError("no architecture sources configured (k8srca.yaml: architecture)")
        arch, lines = await build(cfg)
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
