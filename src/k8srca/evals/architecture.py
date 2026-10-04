"""Component eval for the cluster-architecture generator (design 005 §8.1).

Runs the generator against a known install, with no agent and no model call,
and scores the skill it writes. An install is a **case**: a k8stools capture,
replayed as if it were the live cluster, plus the other sources the generator
should read.

Two kinds of truth, kept apart because they are trusted differently:

- **Derived truth** is computed from the case's own inputs by a different path
  than the generator takes: workload facts straight from the capture JSON
  (images from each Deployment's *current* ReplicaSet, not from whichever pod
  the generator met first), declared facts straight from the chart YAML. It
  needs no review, and it catches facts the generator loses or mangles between
  k8stools, the merge and the renderer.
- **Reviewed truth** (`truth.yaml`) covers what the inputs cannot settle
  mechanically: which environment references are real dependencies. It is
  written by hand and records who reviewed it.

Scores:

| | |
|---|---|
| completeness | expected services present; expected facts present |
| accuracy | present facts equal to the derived truth |
| invention | services with no workload or Service behind them; facts with no provenance |
| dependencies | precision and recall of `depends_on` against the reviewed truth |
| drift | declared-vs-observed differences reported as conflicts |
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..arch import normalise
from ..config import GitRef, HelmChartRef

#: The case directories, one per install.
CASES = Path("tests/evals/architecture")


# ---------------------------------------------------------------------------
# Case and reviewed truth
# ---------------------------------------------------------------------------

class CaseSource(BaseModel):
    # Unknown keys are an error: a field this model does not carry would
    # otherwise be dropped silently, and the case would run without a source.
    model_config = ConfigDict(extra="forbid")

    type: str                             # live_cluster | change_history | docs | chart_repo
    path: str | None = None               # docs/chart_repo; relative to the repo root
    helm: HelmChartRef | None = None      # chart_repo, pinned (arch/fetch.py)
    git: GitRef | None = None             # docs, pinned


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    capture: str                          # relative to the case directory
    namespaces: list[str]
    sources: list[CaseSource]


class Reviewed(BaseModel):
    by: str
    on: str


class Truth(BaseModel):
    #: service -> the services it really calls. Services not listed are
    #: expected to have none.
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    #: Targets neither required nor penalised: telemetry sinks, the API server.
    ignore_dependencies: list[str] = Field(default_factory=list)
    reviewed: Reviewed | None = None


def load_case(case_dir: Path) -> tuple[Case, Truth]:
    case = Case.model_validate(yaml.safe_load((case_dir / "case.yaml").read_text()))
    truth = Truth.model_validate(yaml.safe_load((case_dir / "truth.yaml").read_text()) or {})
    return case, truth


# ---------------------------------------------------------------------------
# Derived truth
# ---------------------------------------------------------------------------

def _current_revision_pods(capture: dict, ns: str) -> dict[str, dict]:
    """workload -> one pod of its Deployment's current ReplicaSet."""
    current: dict[str, str] = {}
    for rs in capture.get("replicasets") or []:
        if rs.get("namespace") != ns or not rs.get("owner_deployment"):
            continue
        dep = rs["owner_deployment"]
        best = current.get(dep)
        if best is None or int(rs.get("revision") or 0) > best[1]:
            current[dep] = (rs["name"], int(rs.get("revision") or 0))
    by_rs = {name: dep for dep, (name, _) in current.items()}
    out: dict[str, dict] = {}
    for pod in capture.get("pods") or []:
        if pod["summary"].get("namespace") != ns:
            continue
        h = (pod.get("labels") or {}).get("pod-template-hash")
        dep = by_rs.get(next((n for n in by_rs if h and n.endswith("-" + h)), ""))
        if dep and dep not in out:
            out[dep] = pod
    return out


def derive_observed(capture: dict, namespaces: list[str]) -> dict[str, dict[str, Any]]:
    """service -> fact -> value, read directly from the capture."""
    truth: dict[str, dict[str, Any]] = {}
    for ns in namespaces:
        for svc in capture.get("services") or []:
            if svc.get("namespace") != ns:
                continue
            f = truth.setdefault(svc["name"], {})
            f["type"] = svc.get("type")
            f["ports"] = [p.get("port") for p in (svc.get("ports") or [])]
            f["selector"] = svc.get("selector")
        for dep in capture.get("deployments") or []:
            if dep.get("namespace") != ns:
                continue
            f = truth.setdefault(dep["name"], {})
            f["replicas"] = dep.get("total_replicas")
            f["ready_replicas"] = dep.get("ready_replicas")
        # DaemonSets. From k8stools 2.3.0 a capture lists them and each pod
        # names its owner. Older captures hold only the pods: there,
        # `pod-template-generation` (set on DaemonSet pods alone) marks one, and
        # its name is the DaemonSet's plus a 5-character suffix.
        for ds in capture.get("daemonsets") or []:
            if ds.get("namespace") == ns:
                f = truth.setdefault(ds["name"], {})
                f["replicas"] = ds.get("desired_number_scheduled")
                f["ready_replicas"] = ds.get("number_ready")
        daemon_pods: dict[str, dict] = {}
        for pod in capture.get("pods") or []:
            if pod["summary"].get("namespace") != ns:
                continue
            kind, _, owner = (pod["summary"].get("owner") or "").partition("/")
            if kind == "DaemonSet":
                daemon_pods.setdefault(owner, pod)
            elif not kind and "pod-template-generation" in (pod.get("labels") or {}):
                daemon_pods.setdefault(pod["summary"]["name"].rsplit("-", 1)[0], pod)
        for dep, pod in [*_current_revision_pods(capture, ns).items(), *daemon_pods.items()]:
            c = (pod["spec"].get("containers") or [{}])[0]
            f = truth.setdefault(dep, {})
            f["image"] = c.get("image")
            f["resources"] = normalise.resources(c.get("resources"))
            f["probes"] = normalise.probes([p for p in normalise.PROBE_KEYS if c.get(p)])
    # An empty value is not a fact: the generator records none for it, by
    # design (tests/test_arch.py::test_empty_values_are_not_facts).
    return {svc: {k: v for k, v in facts.items() if v not in (None, "", [], {})}
            for svc, facts in truth.items()}


def derive_declared(chart_dir: Path) -> dict[str, dict[str, Any]]:
    """service -> fact -> value, read directly from rendered manifests.

    Services the chart declares appear with no facts, so a declared Service
    that is not deployed is known, not invented.
    """
    declared: dict[str, dict[str, Any]] = {}
    for f in sorted(chart_dir.rglob("*.y*ml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "Service" and (doc.get("metadata") or {}).get("name"):
                declared.setdefault(doc["metadata"]["name"], {})
                continue
            if doc.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet"):
                continue
            name = (doc.get("metadata") or {}).get("name")
            containers = (((doc.get("spec") or {}).get("template") or {}).get("spec")
                          or {}).get("containers") or []
            if not name or not containers or "{{" in str(containers[0].get("image", "")):
                continue
            c = containers[0]
            declared.setdefault(name, {}).update(
                image=c.get("image"), resources=normalise.resources(c.get("resources")))
    return declared


def expected_conflicts(observed: dict, declared: dict) -> set[tuple[str, str]]:
    return {(svc, fact) for svc, facts in declared.items() if svc in observed
            for fact, value in facts.items()
            if fact in observed[svc] and observed[svc][fact] != value}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class Score:
    case: str
    services_expected: int = 0
    services_missing: list[str] = field(default_factory=list)
    services_invented: list[str] = field(default_factory=list)
    #: Declared by the chart but not deployed: drift, reported, not a failure.
    services_declared_only: list[str] = field(default_factory=list)
    facts_expected: int = 0
    facts_missing: list[str] = field(default_factory=list)
    facts_wrong: list[str] = field(default_factory=list)
    facts_unsourced: list[str] = field(default_factory=list)
    deps_expected: int = 0
    deps_found: int = 0
    deps_missed: list[str] = field(default_factory=list)
    deps_extra: list[str] = field(default_factory=list)
    conflicts_expected: int = 0
    conflicts_missed: list[str] = field(default_factory=list)
    #: Conflicts the skill reports that the inputs do not support: drift
    #: invented, which tells the agent two sources disagree when they do not.
    conflicts_found: int = 0
    conflicts_spurious: list[str] = field(default_factory=list)
    truth_reviewed: str | None = None

    @staticmethod
    def _rate(good: int, total: int) -> float | None:
        return None if total == 0 else round(good / total, 3)

    def rates(self) -> dict[str, float | None]:
        present = self.facts_expected - len(self.facts_missing)
        return {
            "service_completeness": self._rate(
                self.services_expected - len(self.services_missing), self.services_expected),
            "fact_completeness": self._rate(present, self.facts_expected),
            "fact_accuracy": self._rate(present - len(self.facts_wrong), present),
            "dependency_recall": self._rate(self.deps_expected - len(self.deps_missed),
                                            self.deps_expected),
            "dependency_precision": self._rate(self.deps_found - len(self.deps_extra),
                                               self.deps_found),
            "drift_recall": self._rate(self.conflicts_expected - len(self.conflicts_missed),
                                       self.conflicts_expected),
            "drift_precision": self._rate(self.conflicts_found - len(self.conflicts_spurious),
                                          self.conflicts_found),
        }

    def to_json(self) -> dict:
        return {**asdict(self), "rates": self.rates()}


def _fact_value(fact: dict, source: str) -> Any:
    """A fact's value as reported by one source, conflicts included."""
    if fact.get("source") == source:
        return fact.get("value")
    for c in fact.get("conflicts") or []:
        if c.get("source") == source:
            return c.get("value")
    return None


def score(case: str, skill: dict, observed: dict, truth: Truth,
          declared: dict | None = None) -> Score:
    s = Score(case=case)
    services = skill.get("services") or {}

    # Services: every workload or Service, and nothing else.
    s.services_expected = len(observed)
    s.services_missing = sorted(set(observed) - set(services))
    known_declared = set(declared or {})
    s.services_invented = sorted(set(services) - set(observed) - known_declared)
    s.services_declared_only = sorted((set(services) & known_declared) - set(observed))

    # Facts: present and equal to what the capture says.
    for svc, facts in sorted(observed.items()):
        have = (services.get(svc) or {}).get("facts") or {}
        for name, value in sorted(facts.items()):
            s.facts_expected += 1
            key = f"{svc}.{name}"
            if name not in have:
                s.facts_missing.append(key)
                continue
            got = _fact_value(have[name], "observed")
            if got != value:
                s.facts_wrong.append(f"{key}: skill {got!r}, capture {value!r}")
    for svc, entry in sorted(services.items()):
        for name, fact in sorted((entry.get("facts") or {}).items()):
            if not fact.get("source") or not fact.get("origin"):
                s.facts_unsourced.append(f"{svc}.{name}")

    # Dependencies, against the reviewed truth.
    ignore = set(truth.ignore_dependencies)
    want = {(a, b) for a, bs in truth.dependencies.items() for b in bs if b not in ignore}
    have_edges = {(a, b) for a, e in services.items()
                  for b in (e.get("depends_on") or []) if b not in ignore}
    s.deps_expected, s.deps_found = len(want), len(have_edges)
    s.deps_missed = sorted(f"{a} -> {b}" for a, b in want - have_edges)
    s.deps_extra = sorted(f"{a} -> {b}" for a, b in have_edges - want)

    # Drift: declared-vs-observed differences must surface as conflicts.
    if declared is not None:
        want_conflicts = expected_conflicts(observed, declared)
        s.conflicts_expected = len(want_conflicts)
        for svc, name in sorted(want_conflicts):
            fact = ((services.get(svc) or {}).get("facts") or {}).get(name) or {}
            if _fact_value(fact, "declared") is None:
                s.conflicts_missed.append(f"{svc}.{name}")
    # Any reported conflict whose values are all equal is invented drift.
    for svc, entry in sorted(services.items()):
        for name, fact in sorted((entry.get("facts") or {}).items()):
            values = [c.get("value") for c in fact.get("conflicts") or []]
            if not values:
                continue
            s.conflicts_found += 1
            if all(v == values[0] for v in values):
                s.conflicts_spurious.append(f"{svc}.{name}: every source says {values[0]!r}")
    if truth.reviewed:
        s.truth_reviewed = f"{truth.reviewed.by}, {truth.reviewed.on}"
    return s


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


def run_case(case_dir: Path, cfg, image: str, workdir: Path) -> Score:
    """Replay the case's capture, run the generator against it, score the skill.

    `cfg` is the deployment's configuration; only its first MCP server is used,
    re-pointed at the replay, and its architecture sources are replaced by the
    case's.
    """
    import asyncio

    from ..arch.fetch import resolve
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
                                      helm=s.helm, git=s.git))
                   for s in case.sources]
        eval_cfg = cfg.model_copy(update={
            "mcp": [server], "architecture": ArchitectureConfig(sources=sources)})
        dest = workdir / case.name / "cluster-architecture"
        asyncio.run(_generate(eval_cfg, dest, workdir / case.name / "reports"))

    skill = json.loads((dest / "architecture.json").read_text())
    observed = derive_observed(capture, case.namespaces)
    chart = next((s for s in sources if s.type == "chart_repo"), None)
    declared = derive_declared(Path(chart.path)) if chart and chart.path else None
    return score(case.name, skill, observed, truth, declared)


def discover(root: Path = CASES) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "case.yaml").exists()) if root.exists() else []


def render(s: Score, detail: int = 8) -> str:
    lines = [f"{s.case}"]
    for name, rate in s.rates().items():
        lines.append(f"  {name:<22} {'-' if rate is None else f'{rate:.0%}'}")
    groups = [("services missing", s.services_missing), ("services invented", s.services_invented),
              ("declared, not deployed", s.services_declared_only),
              ("facts missing", s.facts_missing), ("facts wrong", s.facts_wrong),
              ("facts unsourced", s.facts_unsourced), ("dependencies missed", s.deps_missed),
              ("dependencies extra", s.deps_extra), ("drift missed", s.conflicts_missed),
              ("drift invented", s.conflicts_spurious)]
    for label, items in groups:
        if items:
            shown = items[:detail] + ([f"... {len(items) - detail} more"] if len(items) > detail else [])
            lines.append(f"  {label} ({len(items)}):")
            lines += [f"    {i}" for i in shown]
    lines.append(f"  dependency truth: "
                 f"{'reviewed by ' + s.truth_reviewed if s.truth_reviewed else 'NOT REVIEWED'}")
    return "\n".join(lines)
