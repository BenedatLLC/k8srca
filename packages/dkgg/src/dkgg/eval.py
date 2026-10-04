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

from . import normalise
from .sources import GitRef, HelmChartRef

#: The case directories, one per install.

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
    #: component -> its kind (synthesis, design §5.1). Scored only when the
    #: wiki was synthesised; components not listed are not scored.
    kinds: dict[str, str] = Field(default_factory=dict)
    #: "from -> to" -> its edge kind, with " soft" appended when the caller
    #: carries on without the target: `cart -> flagd: feature-flags soft`.
    edge_kinds: dict[str, str] = Field(default_factory=dict)
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
    #: Documentation (the `docs` source). Coverage is free; consistency needs
    #: the judge (`--judge`) and is None when it did not run.
    docs_workloads: int = 0
    docs_undocumented: list[str] = field(default_factory=list)
    docs_pages_judged: int | None = None
    docs_contradictions: list[str] = field(default_factory=list)
    judge_usd: float = 0.0

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
            "doc_coverage": self._rate(self.docs_workloads - len(self.docs_undocumented),
                                       self.docs_workloads),
            "doc_consistency": None if self.docs_pages_judged is None else self._rate(
                self.docs_pages_judged - len({c.split(":", 1)[0]
                                              for c in self.docs_contradictions}),
                self.docs_pages_judged),
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
    # Documentation coverage: every workload should have a page attached.
    # A workload is anything the capture shows running (it has an image);
    # Services that only front one, and the API server, are not expected to.
    # A skill built without a docs source has nothing to cover: not scored,
    # rather than 0%.
    has_docs = bool(skill.get("general")) or any(e.get("notes") for e in services.values())
    workloads = sorted(svc for svc, facts in observed.items() if "image" in facts)
    if has_docs:
        s.docs_workloads = len(workloads)
        s.docs_undocumented = [w for w in workloads
                               if not (services.get(w) or {}).get("notes")]

    if truth.reviewed:
        s.truth_reviewed = f"{truth.reviewed.by}, {truth.reviewed.on}"
    return s


# ---------------------------------------------------------------------------
# Documentation consistency (the judge)
# ---------------------------------------------------------------------------

class DocContradiction(BaseModel):
    page: str = Field(description="The page's origin, exactly as given")
    quote: str = Field(description="The documentation's claim, quoted exactly")
    fact: str = Field(description="The observed fact it contradicts: service.fact = value")
    why: str


class DocVerdict(BaseModel):
    pages_checked: int = Field(description="How many pages were read")
    contradictions: list[DocContradiction]


JUDGE_SYSTEM = """You check a Kubernetes deployment's documentation against what the cluster was
observed to be. You are a checker, not a reviewer of the writing.

Report a contradiction only when a page makes a concrete claim about this
deployment's state or configuration -- readiness, probes, resource limits or
requests, images or versions, replica counts, ports, which service calls which
-- and an observed fact below directly says otherwise.

Not contradictions:
- claims the facts cannot check: instrumentation, code structure, telemetry
  details, what a service does for users, how to run it locally;
- general statements about Kubernetes or the software, not this deployment;
- a fact the page does not mention, or a page that is merely incomplete;
- declared values (from a chart) that differ from observed ones: that is drift,
  scored elsewhere.

Quote the page exactly; do not paraphrase. An empty list is a real answer, and
the expected one for accurate documentation. A checker that flags plausible-
sounding disagreements gets ignored, and then catches nothing."""


#: Opus 5 list prices, $ per million tokens (in, out), for reporting cost.
_PRICE = {"claude-opus-5": (5.0, 25.0)}


def _facts_digest(skill: dict) -> dict:
    """The observed facts and dependencies, without the notes being judged."""
    out = {}
    for name, s in (skill.get("services") or {}).items():
        facts = {k: f.get("value") for k, f in (s.get("facts") or {}).items()
                 if f.get("source") == "observed"}
        if facts or s.get("depends_on"):
            out[name] = {**facts, "depends_on": s.get("depends_on") or []}
    return out


def doc_pages(skill: dict) -> list[dict]:
    pages = [{"page": n.get("origin", ""), "about": name, "text": n.get("text", "")}
             for name, s in (skill.get("services") or {}).items() for n in s.get("notes") or []]
    pages += [{"page": g.get("origin", ""), "about": "(the system)", "text": g.get("text", "")}
              for g in skill.get("general") or []]
    return pages


def judge_docs(skill: dict, client: Any, model: str) -> tuple[int, list[str], float]:
    """The legacy skill: its pages against the observed facts it carries."""
    return judge_pages(doc_pages(skill), _facts_digest(skill), client, model)


def judge_pages(pages: list[dict], observed: dict, client: Any,
                model: str) -> tuple[int, list[str], float]:
    """(pages judged, contradictions as "page: quote -- fact", cost in $).

    `observed` is the reference: facts about the deployment, by component.
    """
    if not pages:
        return 0, [], 0.0
    reference = json.dumps({"observed": observed}, indent=1, sort_keys=True, default=str)
    docs = "\n\n".join(f'<page origin="{p["page"]}" about="{p["about"]}">\n{p["text"]}\n</page>'
                         for p in pages)
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": JUDGE_SYSTEM},
                {"type": "text", "text": f"<observed>\n{reference}\n</observed>"}],
        messages=[{"role": "user", "content":
                   f"<documentation>\n{docs}\n</documentation>\n\n"
                   "Report every claim the observed facts contradict."}],
        output_format=DocVerdict,
    )
    verdict: DocVerdict = response.parsed_output
    found = [f"{c.page}: {c.quote!r} -- {c.fact} ({c.why})" for c in verdict.contradictions]
    usage = getattr(response, "usage", None)
    price_in, price_out = _PRICE.get(model, (0.0, 0.0))
    usd = 0.0
    if usage is not None:
        usd = ((getattr(usage, "input_tokens", 0) or 0) * price_in
               + (getattr(usage, "output_tokens", 0) or 0) * price_out) / 1e6
    return len(pages), found, round(usd, 4)


def render(s: Score, detail: int = 8) -> str:
    lines = [f"{s.case}"]
    for name, rate in s.rates().items():
        lines.append(f"  {name:<22} {'-' if rate is None else f'{rate:.0%}'}")
    groups = [("services missing", s.services_missing), ("services invented", s.services_invented),
              ("declared, not deployed", s.services_declared_only),
              ("facts missing", s.facts_missing), ("facts wrong", s.facts_wrong),
              ("facts unsourced", s.facts_unsourced), ("dependencies missed", s.deps_missed),
              ("dependencies extra", s.deps_extra), ("drift missed", s.conflicts_missed),
              ("drift invented", s.conflicts_spurious),
              ("undocumented workloads", s.docs_undocumented),
              ("documentation contradicted", s.docs_contradictions)]
    for label, items in groups:
        if items:
            shown = items[:detail] + ([f"... {len(items) - detail} more"] if len(items) > detail else [])
            lines.append(f"  {label} ({len(items)}):")
            lines += [f"    {i}" for i in shown]
    if s.docs_pages_judged is None:
        lines.append("  doc consistency: not judged (--judge)")
    else:
        lines.append(f"  doc consistency: {s.docs_pages_judged} page(s) judged, ${s.judge_usd:.2f}")
    lines.append(f"  dependency truth: "
                 f"{'reviewed by ' + s.truth_reviewed if s.truth_reviewed else 'NOT REVIEWED'}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The wiki (docs/design.md §7.2, migration step 2)
# ---------------------------------------------------------------------------

#: Declared facts the eval checks against the chart.
DECLARED_CHECKED = ("image", "resources")


@dataclass
class WikiScore:
    """A built wiki against a case's truth.

    No observed facts and no drift: the wiki holds no state, so there is
    nothing observed to be accurate about and nothing to drift from.
    """

    case: str
    components_expected: int = 0
    components_missing: list[str] = field(default_factory=list)
    components_invented: list[str] = field(default_factory=list)
    components_declared_only: list[str] = field(default_factory=list)
    declared_expected: int = 0
    declared_missing: list[str] = field(default_factory=list)
    declared_wrong: list[str] = field(default_factory=list)
    deps_expected: int = 0
    deps_found: int = 0
    deps_missed: list[str] = field(default_factory=list)
    deps_extra: list[str] = field(default_factory=list)
    docs_workloads: int = 0
    docs_undocumented: list[str] = field(default_factory=list)
    docs_pages_judged: int | None = None
    docs_contradictions: list[str] = field(default_factory=list)
    judge_usd: float = 0.0
    synthesised: bool = False
    synthesis_usd: float | None = None
    kinds_expected: int = 0
    kinds_wrong: list[str] = field(default_factory=list)
    edge_kinds_expected: int = 0
    edge_kinds_wrong: list[str] = field(default_factory=list)
    check_findings: list[str] = field(default_factory=list)
    truth_reviewed: str | None = None

    _rate = staticmethod(Score._rate)

    def rates(self) -> dict[str, float | None]:
        present = self.declared_expected - len(self.declared_missing)
        return {
            "inventory_completeness": self._rate(
                self.components_expected - len(self.components_missing),
                self.components_expected),
            "declared_completeness": self._rate(present, self.declared_expected),
            "declared_accuracy": self._rate(present - len(self.declared_wrong), present),
            "dependency_recall": self._rate(self.deps_expected - len(self.deps_missed),
                                            self.deps_expected),
            "dependency_precision": self._rate(self.deps_found - len(self.deps_extra),
                                               self.deps_found),
            "doc_coverage": self._rate(self.docs_workloads - len(self.docs_undocumented),
                                       self.docs_workloads),
            "doc_consistency": None if self.docs_pages_judged is None else self._rate(
                self.docs_pages_judged - len({c.split(":", 1)[0]
                                              for c in self.docs_contradictions}),
                self.docs_pages_judged),
            "kind_accuracy": None if not self.synthesised else self._rate(
                self.kinds_expected - len(self.kinds_wrong), self.kinds_expected),
            "edge_kind_accuracy": None if not self.synthesised else self._rate(
                self.edge_kinds_expected - len(self.edge_kinds_wrong),
                self.edge_kinds_expected),
        }

    def to_json(self) -> dict:
        return {**asdict(self), "rates": self.rates()}


def score_wiki(case: str, g: dict, observed: dict, truth: Truth,
               declared: dict | None = None) -> WikiScore:
    """Score a wiki's graph.json against derived and reviewed truth."""
    s = WikiScore(case=case)
    components = g.get("components") or {}
    known_declared = set(declared or {})

    s.components_expected = len(observed)
    s.components_missing = sorted(set(observed) - set(components))
    s.components_invented = sorted(set(components) - set(observed) - known_declared)
    s.components_declared_only = sorted((set(components) & known_declared) - set(observed))

    for name, facts in sorted((declared or {}).items()):
        have = ((components.get(name) or {}).get("declared")) or {}
        for key in DECLARED_CHECKED:
            if key not in facts or facts[key] in (None, "", [], {}):
                continue
            s.declared_expected += 1
            if key not in have:
                s.declared_missing.append(f"{name}.{key}")
            elif have[key].get("value") != facts[key]:
                s.declared_wrong.append(f"{name}.{key}: wiki {have[key].get('value')!r}, "
                                        f"chart {facts[key]!r}")

    ignore = set(truth.ignore_dependencies)
    want = {(a, b) for a, bs in truth.dependencies.items() for b in bs if b not in ignore}
    have_edges = {(e["from"], e["to"]) for e in g.get("edges") or [] if e["to"] not in ignore}
    s.deps_expected, s.deps_found = len(want), len(have_edges)
    s.deps_missed = sorted(f"{a} -> {b}" for a, b in want - have_edges)
    s.deps_extra = sorted(f"{a} -> {b}" for a, b in have_edges - want)

    has_docs = bool(g.get("general_docs")) or any(c.get("docs") for c in components.values())
    workloads = sorted(n for n, f in observed.items() if "image" in f)
    if has_docs:
        s.docs_workloads = len(workloads)
        s.docs_undocumented = [w for w in workloads if not (components.get(w) or {}).get("docs")]

    s.synthesised = any(c.get("kind", "unclassified") != "unclassified"
                        for c in components.values())
    if s.synthesised:
        for name, kind in sorted(truth.kinds.items()):
            if name not in components:
                continue
            s.kinds_expected += 1
            if components[name].get("kind") != kind:
                s.kinds_wrong.append(f"{name}: wiki {components[name].get('kind')}, "
                                     f"truth {kind}")
        edges = {f"{e['from']} -> {e['to']}": e for e in g.get("edges") or []}
        for pair, want_kind in sorted(truth.edge_kinds.items()):
            e = edges.get(pair)
            if e is None:
                continue        # a missed edge is dependency recall's to report
            s.edge_kinds_expected += 1
            have = e.get("kind", "unclassified") + (" soft" if e.get("soft") else "")
            if have != want_kind.strip():
                s.edge_kinds_wrong.append(f"{pair}: wiki {have}, truth {want_kind}")

    if truth.reviewed:
        s.truth_reviewed = f"{truth.reviewed.by}, {truth.reviewed.on}"
    return s


def wiki_pages(g: dict) -> list[dict]:
    """The documentation a wiki carries, as pages for the judge."""
    pages = [{"page": d.get("origin", ""), "about": name, "text": d.get("text", "")}
             for name, c in (g.get("components") or {}).items() for d in c.get("docs") or []]
    pages += [{"page": d.get("origin", ""), "about": "(the system)", "text": d.get("text", "")}
              for d in g.get("general_docs") or []]
    # What synthesis wrote is judged by the same rule as what the publisher did.
    for name, c in sorted((g.get("components") or {}).items()):
        text = " ".join(st["text"] for st in (c.get("purpose") or []) + (c.get("if_it_fails") or []))
        if text:
            pages.append({"page": f"components/{name}.md (generated)", "about": name,
                          "text": text})
    if g.get("overview"):
        pages.append({"page": "index.md (generated)", "about": "(the system)",
                      "text": " ".join(st["text"] for st in g["overview"])})
    return pages


def render_wiki(s: WikiScore, detail: int = 8) -> str:
    lines = [f"{s.case}"]
    for name, rate in s.rates().items():
        lines.append(f"  {name:<24} {'-' if rate is None else f'{rate:.0%}'}")
    groups = [("components missing", s.components_missing),
              ("components invented", s.components_invented),
              ("declared, not deployed", s.components_declared_only),
              ("declared config missing", s.declared_missing),
              ("declared config wrong", s.declared_wrong),
              ("dependencies missed", s.deps_missed), ("dependencies extra", s.deps_extra),
              ("undocumented workloads", s.docs_undocumented),
              ("documentation contradicted", s.docs_contradictions),
              ("component kinds wrong", s.kinds_wrong),
              ("edge kinds wrong", s.edge_kinds_wrong),
              ("check findings", s.check_findings)]
    for label, items in groups:
        if items:
            shown = items[:detail] + ([f"... {len(items) - detail} more"] if len(items) > detail else [])
            lines.append(f"  {label} ({len(items)}):")
            lines += [f"    {i}" for i in shown]
    if s.docs_pages_judged is None:
        lines.append("  doc consistency: not judged (--judge)")
    else:
        lines.append(f"  doc consistency: {s.docs_pages_judged} page(s) judged, ${s.judge_usd:.2f}")
    if not s.synthesised:
        lines.append("  kinds: not synthesised (--model)")
    elif s.synthesis_usd is not None:
        lines.append(f"  synthesis: ${s.synthesis_usd:.2f}")
    lines.append(f"  dependency truth: "
                 f"{'reviewed by ' + s.truth_reviewed if s.truth_reviewed else 'NOT REVIEWED'}")
    return "\n".join(lines)
