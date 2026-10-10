"""The architecture model: facts about services, and where each came from.

Three sources answer different questions, and the difference between them is
itself diagnostic:

* **observed** — live cluster state. What is running *now*. Authoritative
  about reality, silent about intent.
* **declared** — charts and manifests. What the system is *supposed* to be.
  The full potential system, including things not currently deployed.
* **documented** — runbooks and upstream docs. Why it is shaped this way, and
  what to do when it breaks. Human intent, and the most likely to be stale.

**Disagreement between sources is a finding, not a merge conflict.** A chart
declaring two replicas while one is running, or a documented memory limit that
does not match the deployed one, is exactly the kind of drift an RCA wants
surfaced. So facts are kept per-source with provenance rather than collapsed,
and the renderer reports conflicts explicitly.
"""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Any, Literal

Provenance = Literal["observed", "declared", "documented"]

# Ordered by authority about *current reality*, which is what a live
# investigation needs first.
PROVENANCE_ORDER: tuple[Provenance, ...] = ("observed", "declared", "documented")


@dataclass
class Fact:
    """One attribute of a service, tagged with where it came from."""

    value: Any
    source: Provenance
    origin: str = ""      # e.g. "k8stools", "charts/otel-demo", "docs/README.md"

    def render(self) -> str:
        return f"{self.value}"


@dataclass(frozen=True)
class Evidence:
    """Why an edge exists: what names its target.

    `via="env"`: `var` is an environment variable. `via="config"`: `var` is a
    key in a mounted ConfigMap, `<configmap>/<path>` (an OpenTelemetry
    Collector's `otel-collector-agent/exporters.opensearch`).
    """

    var: str
    source: Provenance          # observed (live) or declared (chart)
    origin: str = ""
    via: str = "env"


@dataclass
class Service:
    name: str
    namespace: str = "default"
    facts: dict[str, list[Fact]] = field(default_factory=dict)
    #: Observed edges only: what the legacy skill renders. The wiki reads
    #: `edges`, which also holds declared ones, with their evidence.
    depends_on: set[str] = field(default_factory=set)
    notes: list[Fact] = field(default_factory=list)
    #: Deployment, StatefulSet, DaemonSet, Job; None for a Service with no
    #: workload behind it.
    workload: str | None = None
    edges: dict[str, list[Evidence]] = field(default_factory=dict)
    #: A headless Service (clusterIP None), which may be folded into the
    #: workload it fronts (`Architecture.fold_headless`).
    headless: bool = False
    #: Other names this component is reached by: headless Services folded in.
    aliases: list[str] = field(default_factory=list)

    def connect(self, target: str, evidence: Evidence) -> None:
        """Record an edge and why. Observed edges also join `depends_on`."""
        found = self.edges.setdefault(target, [])
        if evidence not in found:
            found.append(evidence)
        if evidence.source == "observed":
            self.depends_on.add(target)

    def add(self, key: str, value: Any, source: Provenance, origin: str = "") -> None:
        if value is None or value == [] or value == {}:
            return
        self.facts.setdefault(key, []).append(Fact(value, source, origin))

    def best(self, key: str) -> Fact | None:
        """The most authoritative value for a key, preferring observed."""
        candidates = self.facts.get(key) or []
        for provenance in PROVENANCE_ORDER:
            for f in candidates:
                if f.source == provenance:
                    return f
        return None

    def conflicts(self, key: str) -> list[Fact]:
        """Distinct values for a key across sources.

        More than one means declared and observed have drifted apart, which is
        worth an operator's attention on its own.
        """
        seen: dict[str, Fact] = {}
        for f in self.facts.get(key) or []:
            seen.setdefault(_canonical(f.value), f)
        return list(seen.values()) if len(seen) > 1 else []


def _canonical(value: Any) -> str:
    """A value's identity for comparison across sources.

    Not `str(value)`: a dict's string depends on key order, and the chart and
    the live cluster list a selector's keys in different orders. That reported
    six identical selectors as drift (found by `k8srca eval arch`).
    """
    return json.dumps(value, sort_keys=True, default=str)


@dataclass
class Architecture:
    services: dict[str, Service] = field(default_factory=dict)
    sources: list[dict[str, str]] = field(default_factory=list)   # what ran, and when
    #: Documentation about the system rather than about one service: how the
    #: parts fit together, which dependencies are load-bearing, what "broken"
    #: means here. It has nowhere else to go -- a fact belongs to a service, and
    #: this does not -- and dropping it was why the skill held 354 mechanical
    #: facts and no statement of intent.
    general: list[Fact] = field(default_factory=list)

    def service(self, name: str, namespace: str = "default") -> Service:
        return self.services.setdefault(name, Service(name=name, namespace=namespace))

    def fold_headless(self) -> list[tuple[str, str]]:
        """Fold each headless Service into the one workload it selects.

        A StatefulSet usually has two Services, `opensearch` and the headless
        `opensearch-headless` that gives each pod a stable DNS name. Both select
        the same pods: one component, reached by two names. Left apart, the
        headless one became a component with no workload, which synthesis had
        to guess a kind for. A headless Service matching no workload, or more
        than one, stays as it is. Returns (folded, into) pairs.
        """
        folded = []
        for name, s in sorted(self.services.items()):
            sel = s.best("selector")
            if not s.headless or s.workload is not None or sel is None:
                continue
            key = _canonical(sel.value)
            into = [o for o in self.services.values()
                    if o is not s and o.workload is not None
                    and any(_canonical(f.value) == key for f in o.facts.get("selector") or [])]
            if len(into) != 1:
                continue
            target = into[0]
            for other in self.services.values():
                if name in other.edges:
                    for ev in other.edges.pop(name):
                        other.connect(target.name, ev)
                    other.depends_on.discard(name)
            for dst, evs in s.edges.items():
                for ev in evs:
                    target.connect(dst, ev)
            target.notes += s.notes
            target.aliases = sorted({*target.aliases, name, *s.aliases})
            del self.services[name]
            folded.append((name, target.name))
        return folded

    def dependents_of(self, name: str) -> list[str]:
        """Who calls this service. The direction RCA usually travels.

        A failing dependency explains symptoms in everything upstream of it, so
        `dependents_of` answers "what else will look broken because of this".
        """
        return sorted(s.name for s in self.services.values() if name in s.depends_on)
