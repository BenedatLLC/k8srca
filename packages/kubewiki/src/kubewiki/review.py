"""review.yaml: corrections that survive regeneration (docs/design.md §5.4).

A reviewer does not edit pages; the next build would overwrite them. They record
decisions here, and every build respects them:

    edges:
      add:    [{from: product-catalog, to: postgresql, kind: datastore, why: ...}]
      remove: [{from: frontend, to: kubernetes, why: ...}]
      kind:   [{from: cart, to: flagd, kind: feature-flags, soft: true}]
    components:
      flagd: {kind: feature-flags}
    claims:
      - {page: ad, reject: "never reaches readiness", why: "..."}
    reviewed: {by: jfischer, on: 2026-10-04}

Edge additions and removals change the graph before synthesis; kinds are
forced after it; rejected claims are given to the model and checked for after.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EdgeRef(_Strict):
    from_: str = Field(alias="from")
    to: str
    kind: str | None = None
    soft: bool = False
    why: str = ""


class EdgeReview(_Strict):
    add: list[EdgeRef] = Field(default_factory=list)
    remove: list[EdgeRef] = Field(default_factory=list)
    kind: list[EdgeRef] = Field(default_factory=list)


class ComponentReview(_Strict):
    kind: str | None = None


class Claim(_Strict):
    page: str
    reject: str
    why: str = ""


class Reviewed(_Strict):
    by: str
    on: str


class Review(_Strict):
    edges: EdgeReview = Field(default_factory=EdgeReview)
    components: dict[str, ComponentReview] = Field(default_factory=dict)
    claims: list[Claim] = Field(default_factory=list)
    reviewed: Reviewed | None = None

    @property
    def component_kinds(self) -> dict[str, str]:
        return {n: c.kind for n, c in self.components.items() if c.kind}

    def edge_kind(self, src: str, dst: str) -> tuple[str, bool] | None:
        for e in self.edges.kind + self.edges.add:
            if e.from_ == src and e.to == dst and e.kind:
                return e.kind, e.soft
        return None

    def adjust(self, g: dict) -> dict:
        """Apply edge additions and removals to a graph, before synthesis."""
        removed = {(e.from_, e.to) for e in self.edges.remove}
        g["edges"] = [e for e in g["edges"] if (e["from"], e["to"]) not in removed]
        present = {(e["from"], e["to"]) for e in g["edges"]}
        for e in self.edges.add:
            if (e.from_, e.to) in present:
                continue
            g["edges"].append({"from": e.from_, "to": e.to, "kind": e.kind or "unclassified",
                               "soft": e.soft,
                               "evidence": [{"var": "", "source": "review",
                                             "origin": "review.yaml"}]})
        g["edges"].sort(key=lambda e: (e["from"], e["to"]))
        return g

    def for_prompt(self) -> dict:
        return {
            "component_kinds": self.component_kinds,
            "edge_kinds": [{"from": e.from_, "to": e.to, "kind": e.kind, "soft": e.soft}
                           for e in self.edges.kind + self.edges.add if e.kind],
            "rejected_claims": [{"page": c.page, "claim": c.reject, "why": c.why}
                                for c in self.claims],
        }


def load_review(path: Path | None) -> Review | None:
    if path is None or not Path(path).exists():
        return None
    return Review.model_validate(yaml.safe_load(Path(path).read_text()) or {})
