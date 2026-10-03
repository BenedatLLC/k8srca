"""The RCA knowledge-base generator, behind the generator contract (005 §6.2)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..core.generator import GeneratorReport
from . import build as kb

if TYPE_CHECKING:
    from ..config import Config


class KnowledgeBaseGenerator:
    """The source knowledge base + authored discriminators -> `k8s-rca`.

    Unlike the architecture generator it writes one file into a bundle whose
    SKILL.md and kb_query.py are written by hand and committed. The manifest
    covers the whole bundle, hand-written files included, because that is what
    the agent receives.
    """

    name = "k8s-rca"
    format = kb.FORMAT

    def __init__(self, source: Path = kb.SOURCE, discriminators: Path = kb.DISCRIMINATORS):
        self.source = source
        self.discriminators = discriminators

    async def generate(self, cfg: "Config | None", dest: Path) -> GeneratorReport:
        if not self.source.exists():
            raise FileNotFoundError(f"knowledge base source not found: {self.source}")
        built = kb.write(dest / kb.DEST.name, self.source, self.discriminators)
        # Unresolved names become warnings below, not summary lines.
        lines = [ln.strip() for ln in built.render().splitlines()
                 if not ln.strip().startswith("UNRESOLVED")]
        report = GeneratorReport(generator=self.name, lines=lines)
        report.counts = {"alerts": built.alerts, "hypotheses": built.hypotheses,
                         "discriminators": built.discriminators,
                         "refinements": built.refinements}
        report.warnings += [f"unresolved alert name: {n}" for n in built.unresolved]
        report.warnings += [f"unresolved discriminator: {n}"
                            for n in built.unresolved_discriminators]
        return report
