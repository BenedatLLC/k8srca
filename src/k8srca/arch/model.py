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


@dataclass
class Service:
    name: str
    namespace: str = "default"
    facts: dict[str, list[Fact]] = field(default_factory=dict)
    depends_on: set[str] = field(default_factory=set)
    notes: list[Fact] = field(default_factory=list)

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
            seen.setdefault(str(f.value), f)
        return list(seen.values()) if len(seen) > 1 else []


@dataclass
class Architecture:
    services: dict[str, Service] = field(default_factory=dict)
    sources: list[dict[str, str]] = field(default_factory=list)   # what ran, and when

    def service(self, name: str, namespace: str = "default") -> Service:
        return self.services.setdefault(name, Service(name=name, namespace=namespace))

    def dependents_of(self, name: str) -> list[str]:
        """Who calls this service. The direction RCA usually travels.

        A failing dependency explains symptoms in everything upstream of it, so
        `dependents_of` answers "what else will look broken because of this".
        """
        return sorted(s.name for s in self.services.values() if name in s.depends_on)
