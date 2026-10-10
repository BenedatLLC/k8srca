"""Running the cluster-architecture eval on k8srca's cases (005 §8.1).

The scoring is kubewiki's (`kubewiki.eval`): ground truth, scores, the docs judge.
What stays in k8srca is running a case: replaying its capture through
k8srca's k8stools container and building the skill through k8srca's adapter.
The cases themselves are k8srca's installs, under tests/evals/architecture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kubewiki.eval import (Case, CaseSource, DocContradiction, DocVerdict, Reference,  # noqa: F401
                       Reviewed,
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
             judge: tuple[Any, str] | None = None, model: str | None = None,
             max_usd: float = 2.0) -> WikiScore:
    """Replay the case's capture, build kubewiki's wiki against it, score the wiki.

    The wiki, not the legacy skill k8srca still syncs: that is what the agent
    will read once kubewiki replaces the skill (kubewiki design §10, step 4).

    With `model`, the wiki is synthesised (kubewiki design §5) and its kinds are
    scored too. Synthesis is cached under .k8srca/synthesis by its inputs, so
    re-running a case against an unchanged capture costs nothing.

    `cfg` is the deployment's configuration; only its first MCP server is used,
    re-pointed at the replay, and its architecture sources are replaced by the
    case's.
    """
    import asyncio

    from kubewiki.build import build
    from kubewiki.fetch import resolve
    from kubewiki.pipeline import generate
    from kubewiki.verify import check

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

    from kubewiki.review import load_review

    built = generate(arch, dest, model=model, review=load_review(case_dir / "review.yaml"),
                     cache=Path(".k8srca"), max_usd=max_usd)
    g = built.graph

    observed = derive_observed(capture, case.namespaces)
    chart = next((s for s in sources if s.type == "chart_repo"), None)
    declared = derive_declared(Path(chart.path)) if chart and chart.path else None
    result = score_wiki(case.name, g, observed, truth, declared)
    result.synthesis_usd = 0.0 if built.cached else built.usage.usd
    result.check_findings = [str(f) for f in check(dest)]
    if case.reference is not None:
        from kubewiki.eval import check_reference, diagram_dependencies
        from kubewiki.fetch import fetch_git

        page = fetch_git(ArchSource(type="docs", git=case.reference.git), cache=CACHE)
        result.reference_unexplained = check_reference(
            truth, diagram_dependencies(page.read_text(), case.reference),
            set(observed) | set(g["components"]))
    if case.traces is not None:
        from kubewiki.eval import check_traces, trace_dependencies

        traced = trace_dependencies(json.loads((case_dir / case.traces.file).read_text()),
                                    case.traces)
        result.trace_unexplained = check_traces(truth, traced, case.traces,
                                                set(observed) | set(g["components"]))
    if judge is not None:
        client, model = judge
        result.docs_pages_judged, result.docs_contradictions, result.judge_usd = \
            judge_pages(wiki_pages(g), observed, client, model)
    return result


def discover(root: Path = CASES) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "case.yaml").exists()) if root.exists() else []
