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
                       Score, Truth, WikiScore, derive_declared, derive_observed,
                       doc_pages, expected_conflicts, judge_docs, judge_pages,
                       load_case, render, render_wiki, score, score_wiki, wiki_pages)

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


def run_case(case_dir: Path, cfg, image: str, workdir: Path,
             judge: tuple[Any, str] | None = None) -> WikiScore:
    """Replay the case's capture, build dkgg's wiki against it, score the wiki.

    The wiki, not the legacy skill k8srca still syncs: that is what the agent
    will read once dkgg replaces the skill (dkgg design §10, step 4).

    `cfg` is the deployment's configuration; only its first MCP server is used,
    re-pointed at the replay, and its architecture sources are replaced by the
    case's.
    """
    import asyncio

    from dkgg import wiki as dkgg_wiki
    from dkgg.build import build
    from dkgg.fetch import resolve
    from dkgg.verify import check

    from ..arch.generator import CACHE, servers
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
        dest = workdir / case.name / "wiki"
        arch, _ = asyncio.run(build(eval_cfg.architecture.sources, servers(eval_cfg),
                                    cache=CACHE))
        g = dkgg_wiki.write(arch, dest)

    observed = derive_observed(capture, case.namespaces)
    chart = next((s for s in sources if s.type == "chart_repo"), None)
    declared = derive_declared(Path(chart.path)) if chart and chart.path else None
    result = score_wiki(case.name, g, observed, truth, declared)
    result.check_findings = [str(f) for f in check(dest)]
    if judge is not None:
        client, model = judge
        result.docs_pages_judged, result.docs_contradictions, result.judge_usd = \
            judge_pages(wiki_pages(g), observed, client, model)
    return result


def discover(root: Path = CASES) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "case.yaml").exists()) if root.exists() else []
