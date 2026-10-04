"""dkgg's command line (docs/design.md §6.1).

    dkgg build --config dkgg.yaml [--namespace NS ...] [--out DIR]
    dkgg check DIR
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .sources import ArchSource, GitRef, HelmChartRef, Server


class _Strict(BaseModel):
    # An unknown key is a mistake, not something to ignore silently.
    model_config = ConfigDict(extra="forbid")


class ClusterConfig(_Strict):
    mcp: str                                      # a running k8stools MCP server
    namespaces: list[str] = Field(default_factory=lambda: ["default"])
    timeout_s: float = 60.0


class ChartConfig(_Strict):
    helm: HelmChartRef | None = None
    path: Path | None = None                      # rendered manifests, instead of helm


class DocsConfig(_Strict):
    git: GitRef | None = None
    path: Path | None = None                      # a local directory, instead of git


class DkggConfig(_Strict):
    cluster: ClusterConfig | None = None          # optional: a chart and docs alone work
    chart: ChartConfig | None = None
    docs: DocsConfig | None = None
    out: Path = Path("wiki")
    cache: Path = Path(".dkgg/sources")
    #: The synthesis model (docs/design.md §5.3); none means a deterministic
    #: wiki with no prose and every kind "unclassified".
    model: str | None = None
    review: Path | None = None
    max_usd: float = 2.0

    def sources(self, namespaces: list[str] | None = None) -> list[ArchSource]:
        ns = namespaces or (self.cluster.namespaces if self.cluster else ["default"])
        out: list[ArchSource] = []
        if self.cluster:
            out.append(ArchSource(type="live_cluster", server="cluster", namespaces=ns))
        if self.chart:
            out.append(ArchSource(type="chart_repo", namespaces=ns,
                                  helm=self.chart.helm, path=self.chart.path))
        if self.docs:
            out.append(ArchSource(type="docs", git=self.docs.git, path=self.docs.path))
        return out

    def servers(self) -> list[Server]:
        if not self.cluster:
            return []
        return [Server(name="cluster", url=self.cluster.mcp, timeout_s=self.cluster.timeout_s)]


def load_config(path: Path) -> DkggConfig:
    return DkggConfig.model_validate(yaml.safe_load(path.read_text()) or {})


def cmd_build(args: argparse.Namespace) -> int:
    from . import wiki
    from .build import build

    cfg = load_config(Path(args.config))
    sources = cfg.sources(args.namespace or None)
    if not sources:
        print("nothing to build from: configure a cluster, a chart or docs", file=sys.stderr)
        return 2
    from .pipeline import CostLimit, generate
    from .review import load_review

    arch, lines = asyncio.run(build(sources, cfg.servers(), cache=cfg.cache))
    for line in lines:
        print(f"  {line}")
    out = Path(args.out) if args.out else cfg.out
    model = None if args.no_synthesis else (args.model or cfg.model)
    review = load_review(cfg.review)
    try:
        r = generate(arch, out, model=model, review=review, cache=cfg.cache.parent,
                     max_usd=args.max_usd if args.max_usd is not None else cfg.max_usd)
    except CostLimit as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 2
    g = r.graph
    print(f"wrote {out}: {len(g['components'])} components, {len(g['edges'])} edges")
    if r.synthesised:
        cost = "cached, no call" if r.cached else (
            f"{r.usage.input_tokens} in / {r.usage.output_tokens} out tokens"
            + (f", ${r.usage.usd:.2f}" if r.usage.usd is not None else ", cost unknown"))
        print(f"synthesis: {model}, {cost}")
    return cmd_check(argparse.Namespace(wiki=str(out), review=review))


def cmd_check(args: argparse.Namespace) -> int:
    from .verify import check

    from .review import load_review

    review = getattr(args, "review", None)
    if review is None and getattr(args, "review_file", None):
        review = load_review(Path(args.review_file))
    findings = check(Path(args.wiki), review=review)
    for f in findings:
        print(f)
    print(f"{len(findings)} finding(s)" if findings else "check: all rows pass")
    return 1 if findings else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dkgg", description=__doc__.strip().splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build a wiki from a config")
    b.add_argument("--config", default="dkgg.yaml")
    b.add_argument("--namespace", action="append",
                   help="limit to this namespace (repeatable); overrides the config")
    b.add_argument("--out", help="output directory; overrides the config")
    b.add_argument("--model", help="synthesis model: claude-opus-5, or openai:<model>")
    b.add_argument("--no-synthesis", action="store_true",
                   help="deterministic wiki only: no model call")
    b.add_argument("--max-usd", type=float, help="refuse a synthesis estimated above this")
    b.set_defaults(fn=cmd_build)
    c = sub.add_parser("check", help="check a built wiki")
    c.add_argument("wiki")
    c.add_argument("--review", dest="review_file", help="review.yaml to check against")
    c.set_defaults(fn=cmd_check)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
