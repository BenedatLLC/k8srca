"""The cluster-architecture skill: kubewiki, behind k8srca's generator contract.

The generator itself is kubewiki (packages/kubewiki), a standalone package that never
imports k8srca. This adapter is the whole of k8srca's side: it maps k8srca's
configuration onto kubewiki's inputs and fits kubewiki's output to the contract in
005 §6.2.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from kubewiki.sources import Server

from ..core.generator import MANIFEST, GeneratorReport

if TYPE_CHECKING:
    from ..config import Config

#: k8srca's cache of pinned charts and docs, kept where it always was so an
#: existing deployment does not fetch everything again.
CACHE = Path(".k8srca/sources")

#: Where synthesis is cached by its inputs, shared with `k8srca eval arch`: a
#: wiki the eval already synthesised costs nothing to build here.
SYNTHESIS_CACHE = Path(".k8srca")


class BuildRejected(RuntimeError):
    """The wiki failed `kubewiki check`; the skill already in place is kept."""


def servers(cfg: "Config") -> list[Server]:
    """k8srca's MCP servers as kubewiki sees them: from the host, where builds run."""
    return [Server(name=s.name, url=s.host_url(), timeout_s=float(s.timeout_s))
            for s in cfg.mcp]


class ArchitectureGenerator:
    """Every configured source -> the `cluster-architecture` skill (001 §5.2)."""

    name = "cluster-architecture"
    #: 2: kubewiki's wiki. 1 was the snapshot of observed state it replaced,
    #: whose renderer is gone; a bundle with format 1 is that snapshot.
    format = 2

    def __init__(self, model: str | None = None, synthesise: bool = True,
                 max_usd: float | None = None):
        #: Overrides for one build; the configuration decides otherwise.
        self.model, self.synthesise, self.max_usd = model, synthesise, max_usd

    async def generate(self, cfg: "Config", dest: Path) -> GeneratorReport:
        from kubewiki.build import build
        from kubewiki.pipeline import generate
        from kubewiki.review import load_review
        from kubewiki.verify import check

        settings = cfg.architecture
        if not settings.active():
            raise ValueError("no architecture sources configured (k8srca.yaml: architecture)")
        model = (self.model or getattr(settings, "model", None)) if self.synthesise else None
        review = load_review(getattr(settings, "review", None))
        max_usd = self.max_usd if self.max_usd is not None else getattr(settings, "max_usd", 2.0)

        arch, lines = await build(settings.active(), servers(cfg), cache=CACHE)
        report = GeneratorReport(generator=self.name, lines=lines)
        # Built beside the skill and swapped in only once it passes: a wiki that
        # fails its checks must never be left where `k8srca sync` would ship it.
        # The previous build is the starting point, so log.md keeps its history.
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dest.parent, prefix=f".{dest.name}-") as tmp:
            staged = Path(tmp) / dest.name
            if (dest / "graph.json").exists():
                shutil.copytree(dest, staged)
            staged.mkdir(exist_ok=True)
            built = generate(arch, staged, model=model, review=review,
                             cache=SYNTHESIS_CACHE, max_usd=max_usd)
            findings = check(staged, review=review)
            if findings:
                raise BuildRejected(
                    f"the wiki failed {len(findings)} check(s); {dest} was left as it was:\n"
                    + "\n".join(f"  {f}" for f in findings))
            (staged / MANIFEST).unlink(missing_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            staged.rename(dest)

        g = built.graph
        report.counts = {"components": len(g["components"]), "edges": len(g["edges"])}
        if built.synthesised:
            report.lines.append(
                f"synthesis: {model}, " + ("cached, no call" if built.cached else
                                           f"${built.usage.usd:.2f}" if built.usage.usd is not None
                                           else "cost unknown"))
        else:
            report.warnings.append("not synthesised: every component's kind is "
                                   "'unclassified' and pages carry no prose")
        if not g["components"]:
            report.warnings.append("no components found: the skill is empty")
        return report
