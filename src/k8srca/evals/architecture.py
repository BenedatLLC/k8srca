"""Running the cluster-architecture eval on k8srca's cases (005 §8.1).

The scoring is dkgg's (`dkgg.eval`): ground truth, scores, the docs judge.
What stays in k8srca is running a case: replaying its capture through
k8srca's k8stools container and building the skill through k8srca's adapter.
The cases themselves are k8srca's installs, under tests/evals/architecture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dkgg.eval import (Case, CaseSource, DocContradiction, DocVerdict, Reviewed,  # noqa: F401
                       Score, Truth, derive_declared, derive_observed, doc_pages,
                       expected_conflicts, judge_docs, load_case, render, score)

#: The case directories, one per install.
CASES = Path("tests/evals/architecture")


# ---------------------------------------------------------------------------
# Running a case
# ---------------------------------------------------------------------------

#: Not the scenario runner's port, network or container, so an eval can run
#: while a scenario batch does.
EVAL_PORT = 8011
EVAL_NETWORK = "k8srca-eval-net"
EVAL_CONTAINER = "k8srca-eval-k8stools"


async def _generate(cfg, dest: Path, reports: Path):
    from ..arch.generator import ArchitectureGenerator
    from ..core.generator import run

    return await run(ArchitectureGenerator(), cfg, dest, reports=reports)


def run_case(case_dir: Path, cfg, image: str, workdir: Path,
             judge: tuple[Any, str] | None = None) -> Score:
    """Replay the case's capture, run the generator against it, score the skill.

    `cfg` is the deployment's configuration; only its first MCP server is used,
    re-pointed at the replay, and its architecture sources are replaced by the
    case's.
    """
    import asyncio

    from dkgg.fetch import resolve

    from ..arch.generator import CACHE
    from ..config import ArchitectureConfig, ArchSource
    from ..scenario.sources import replay

    case, truth = load_case(case_dir)
    capture_path = (case_dir / case.capture).resolve()
    capture = json.loads(capture_path.read_text())

    with replay(capture_path, image, host_port=EVAL_PORT, network=EVAL_NETWORK,
                container=EVAL_CONTAINER) as rp:
        server = cfg.mcp[0].model_copy(update={"url": rp.host_url, "sync_url": rp.host_url})
        # Pinned sources are resolved here, once, so the generator and the
        # declared truth below read the same rendered chart.
        sources = [resolve(ArchSource(type=s.type, server=server.name if s.type in
                                      ("live_cluster", "change_history") else None,
                                      namespaces=case.namespaces,
                                      path=Path(s.path) if s.path else None,
                                      helm=s.helm, git=s.git), cache=CACHE)
                   for s in case.sources]
        eval_cfg = cfg.model_copy(update={
            "mcp": [server], "architecture": ArchitectureConfig(sources=sources)})
        dest = workdir / case.name / "cluster-architecture"
        asyncio.run(_generate(eval_cfg, dest, workdir / case.name / "reports"))

    skill = json.loads((dest / "architecture.json").read_text())
    observed = derive_observed(capture, case.namespaces)
    chart = next((s for s in sources if s.type == "chart_repo"), None)
    declared = derive_declared(Path(chart.path)) if chart and chart.path else None
    result = score(case.name, skill, observed, truth, declared)
    if judge is not None:
        client, model = judge
        result.docs_pages_judged, result.docs_contradictions, result.judge_usd = \
            judge_docs(skill, client, model)
    return result


def discover(root: Path = CASES) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "case.yaml").exists()) if root.exists() else []
