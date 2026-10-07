"""The cluster-architecture skill: dkgg's wiki, behind k8srca's generator contract.

The generator itself is dkgg (packages/dkgg), a standalone package that never
imports k8srca. This adapter is the whole of k8srca's side: it maps k8srca's
configuration onto dkgg's inputs and fits dkgg's output to the contract in
005 §6.2.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from dkgg import wiki
from dkgg.sources import Server

from ..core.generator import GeneratorReport

if TYPE_CHECKING:
    from dkgg.model import Architecture
    from dkgg.pipeline import Result

    from ..config import Config

#: k8srca's cache of pinned charts and docs, kept where it always was so an
#: existing deployment does not fetch everything again.
CACHE = Path(".k8srca/sources")
#: Where synthesis is cached (`<here>/synthesis/<digest>.json`), so an
#: unchanged deployment is rebuilt without a model call.
SYNTHESIS_CACHE = Path(".k8srca")


def servers(cfg: "Config") -> list[Server]:
    """k8srca's MCP servers as dkgg sees them: from the host, where builds run."""
    return [Server(name=s.name, url=s.host_url(), timeout_s=float(s.timeout_s))
            for s in cfg.mcp]


#: The replay used to build from a capture: not the scenario runner's port,
#: network or container, so a build can run while a scenario batch does.
REPLAY_PORT = 8011
REPLAY_NETWORK = "k8srca-eval-net"
REPLAY_CONTAINER = "k8srca-eval-k8stools"


def collect_from_capture(capture: Path, cfg: "Config", sources: list, namespaces: list[str],
                         image: str) -> tuple["Architecture", list]:
    """Collect an architecture from a k8stools capture instead of the live cluster.

    The capture is replayed through k8stools, and `sources` are read against
    the replay: a live source sees the capture, and pinned charts and docs are
    resolved here, once, and returned resolved so a caller reads the same
    rendered chart. An eval case and a scenario both build this way, so the
    wiki describes the world the capture recorded, whenever it is built.
    """
    import asyncio

    from dkgg.build import build
    from dkgg.fetch import resolve

    from ..config import ArchSource
    from ..scenario.sources import replay

    with replay(capture.resolve(), image, host_port=REPLAY_PORT, network=REPLAY_NETWORK,
                container=REPLAY_CONTAINER) as rp:
        server = cfg.mcp[0].model_copy(update={"url": rp.host_url, "sync_url": rp.host_url})
        resolved = [resolve(ArchSource(type=s.type, server=server.name if s.type in
                                       ("live_cluster", "change_history") else None,
                                       namespaces=namespaces,
                                       path=Path(s.path) if s.path else None,
                                       helm=s.helm, git=s.git), cache=CACHE)
                    for s in sources]
        arch, _ = asyncio.run(build(resolved, [Server(name=server.name, url=rp.host_url,
                                                      timeout_s=float(server.timeout_s))],
                                    cache=CACHE))
    return arch, resolved


def write_wiki(arch: "Architecture", cfg: "Config", dest: Path,
               model: str | None = None) -> "Result":
    """dkgg's wiki for `arch` into `dest`, emptied first.

    Emptied, because the wiki writer replaces only its own files and the skill
    uploader sends everything in the directory: the legacy skill's
    `architecture.json` would otherwise ride along with the wiki that replaced
    it. `model` overrides the config's (an eval's `--model`).
    """
    from dkgg.pipeline import generate
    from dkgg.review import load_review

    arch_cfg = cfg.architecture
    if dest.exists() and any(dest.iterdir()):
        # Only ever a skill bundle: a mistyped --dest must not empty a directory.
        if not (dest / "SKILL.md").exists():
            raise ValueError(f"{dest} is not empty and holds no SKILL.md: not a skill "
                             f"bundle, so it will not be replaced")
        shutil.rmtree(dest)
    return generate(arch, dest, model=model or arch_cfg.model,
                    review=load_review(arch_cfg.review), cache=SYNTHESIS_CACHE,
                    max_usd=arch_cfg.max_usd)


class ArchitectureGenerator:
    """Every configured source -> the `cluster-architecture` skill (001 §5.2)."""

    name = "cluster-architecture"
    format = wiki.FORMAT

    async def generate(self, cfg: "Config", dest: Path) -> GeneratorReport:
        from dkgg.build import build
        from dkgg.verify import check

        if not cfg.architecture.active():
            raise ValueError("no architecture sources configured (k8srca.yaml: architecture)")
        arch, lines = await build(cfg.architecture.sources, servers(cfg), cache=CACHE)
        result = write_wiki(arch, cfg, dest)
        report = GeneratorReport(generator=self.name, lines=lines)
        report.counts = {
            "components": len(result.graph["components"]),
            "edges": len(result.graph["edges"]),
            "sources": len(result.graph["sources"]),
        }
        if result.synthesised:
            report.lines.append(
                f"synthesis      {cfg.architecture.model}: " + (
                    "cached, no call" if result.cached else
                    f"{result.usage.input_tokens} in / {result.usage.output_tokens} out"
                    + (f", ${result.usage.usd:.2f}" if result.usage.usd is not None else "")))
        else:
            report.warnings.append("not synthesised (architecture.model unset): no prose, "
                                   "every kind unclassified")
        report.warnings += [f"check: {f}" for f in check(dest, review=None)]
        if not result.graph["components"]:
            report.warnings.append("no components found: the skill is empty")
        return report
