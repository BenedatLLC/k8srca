"""Diagnostic knowledge as facts, actions and claims (design 007 §3).

The vendored knowledge base names candidate causes per alert and says nothing
about telling them apart. This layer says it as structure: a claim is confirmed
or refuted by conditions on facts, a fact is observed by actions, an action
needs a capability the deployment may not have. The model reads the result
through `kb_query.py`; nothing here runs a solver (007 §10.2).

What the build checks is what 007 §7 lists. The failures are names that do not
resolve, a claim nothing could refute, a fact nothing could observe -- each of
which would otherwise leave the agent a criterion it cannot use and cannot tell
it cannot use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

#: The authored layer (007 §2). One file per symptom family; merged.
KNOWLEDGE = Path("docs/rca/knowledge")

#: What an action can require (007 §4.1). Named for what is provided, not for a
#: product. `human` is never available to the agent.
CAPABILITIES = frozenset({"k8stools", "prometheus", "tracing", "logs-backend",
                          "audit-log", "human"})

#: What a deployment has unless it says otherwise: k8stools is always enabled
#: (005 §5.1), and nothing else is today.
DEFAULT_AVAILABLE = frozenset({"k8stools"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Symptom(_Strict):
    title: str
    claims: list[str]


class AlertMapping(_Strict):
    symptom: str
    bind: dict[str, str] = Field(default_factory=dict)


class Fact(_Strict):
    params: list[str]
    says: str
    functional: bool = False


class Action(_Strict):
    requires: str
    produces: list[str]
    call: str | None = None
    read: str = ""
    cost: int = 1
    quality: Literal["complete", "partial", "point"] = "complete"
    limits: str = ""


#: An alternative: one condition, or a list that must all hold.
Alternative = str | list[str]


class Claim(_Strict):
    title: str
    confirm: list[Alternative]
    refute: list[Alternative]
    supports: list[str] = Field(default_factory=list)
    refined_by: list[str] = Field(default_factory=list)
    #: A vendored alert whose hypotheses decompose this claim, for claims whose
    #: refinement has not been converted yet.
    refines_alert: str | None = None
    note: str = ""


class Layer(_Strict):
    """One knowledge file."""

    symptoms: dict[str, Symptom] = Field(default_factory=dict)
    alerts: dict[str, AlertMapping] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    actions: dict[str, Action] = Field(default_factory=dict)
    claims: dict[str, Claim] = Field(default_factory=dict)
    exclusive: list[list[str]] = Field(default_factory=list)


# --- conditions ---------------------------------------------------------------

_CONDITION = re.compile(
    r"^(?P<neg>not\s+)?(?P<fact>[a-z][a-z0-9_]*)\[(?P<args>[^\]]*)\]"
    r"(?P<rest>(?:\s*,\s*[A-Za-z0-9_]+\s*(?:==|!=|<=|>=|<|>)\s*[A-Za-z0-9_.]+)*)\s*$")
_COMPARE = re.compile(r"([A-Za-z0-9_]+)\s*(==|!=|<=|>=|<|>)\s*([A-Za-z0-9_.]+)")


@dataclass(frozen=True)
class Condition:
    """A parsed condition, or prose (`fact is None`)."""

    text: str
    fact: str | None = None
    args: tuple[str, ...] = ()
    negated: bool = False
    compare: tuple[tuple[str, str, str], ...] = ()

    @property
    def prose(self) -> bool:
        return self.fact is None


def parse_condition(text: str) -> Condition:
    m = _CONDITION.match(text.strip())
    if not m:
        return Condition(text=text.strip())
    args = tuple(a.strip() for a in m["args"].split(",")) if m["args"].strip() else ()
    return Condition(text=text.strip(), fact=m["fact"], args=args,
                     negated=bool(m["neg"]),
                     compare=tuple(_COMPARE.findall(m["rest"] or "")))


def alternatives(raw: list[Alternative]) -> list[list[Condition]]:
    return [[parse_condition(c) for c in ([alt] if isinstance(alt, str) else alt)]
            for alt in raw]


def claim_conditions(claim: Claim) -> list[Condition]:
    out = [c for alt in alternatives(claim.confirm) + alternatives(claim.refute) for c in alt]
    return out + [parse_condition(s) for s in claim.supports]


# --- loading and checking -------------------------------------------------------


class KnowledgeError(ValueError):
    """The authored knowledge does not hold together. Every problem, not the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("knowledge does not check:\n  " + "\n  ".join(problems))


@dataclass
class Knowledge:
    layer: Layer
    #: Where each name came from, for provenance in the output (007 §8.3, §9.3).
    origin: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)


_SECTIONS = ("symptoms", "alerts", "facts", "actions", "claims")


def load(directory: Path = KNOWLEDGE) -> Knowledge:
    """Merge every `*.yaml` under `directory`. A name defined twice is an error."""
    merged = Layer()
    origin: dict[str, str] = {}
    problems: list[str] = []
    files = sorted(directory.glob("*.yaml")) if directory.exists() else []
    for path in files:
        try:
            layer = Layer.model_validate(yaml.safe_load(path.read_text()) or {})
        except ValidationError as e:
            problems.append(f"{path.name}: {e}")
            continue
        for section in _SECTIONS:
            into = getattr(merged, section)
            for name, value in getattr(layer, section).items():
                key = f"{section}:{name}"
                if key in origin:
                    problems.append(f"{section} {name!r} defined in both "
                                    f"{origin[key]} and {path.name}")
                    continue
                into[name] = value
                origin[key] = path.name
        merged.exclusive += layer.exclusive
    if problems:
        raise KnowledgeError(problems)
    return Knowledge(layer=merged, origin=origin, sources=[p.name for p in files])


@dataclass
class CheckReport:
    problems: list[str] = field(default_factory=list)
    #: Conditions that did not parse as `fact[args]` (007 §3.2): allowed, counted.
    prose: list[str] = field(default_factory=list)
    #: Claims none of whose alternatives can be decided by `DEFAULT_AVAILABLE`.
    undecidable: list[str] = field(default_factory=list)


def check(k: Knowledge, *, legacy_alerts: set[str] = frozenset(),
          known_tools: set[str] | None = None) -> CheckReport:
    """007 §7. `known_tools`, when given, is k8stools' tool names."""
    L, r = k.layer, CheckReport()

    for name, s in L.symptoms.items():
        for c in s.claims:
            if c not in L.claims:
                r.problems.append(f"symptom {name}: claim {c!r} does not resolve")
    for alert, m in L.alerts.items():
        if m.symptom not in L.symptoms:
            r.problems.append(f"alert {alert}: symptom {m.symptom!r} does not resolve")

    produced: set[str] = set()
    for name, a in L.actions.items():
        if a.requires not in CAPABILITIES:
            r.problems.append(f"action {name}: capability {a.requires!r} is not one of "
                              f"{sorted(CAPABILITIES)}")
        if a.requires == "k8stools":
            if not a.call:
                r.problems.append(f"action {name}: a k8stools action needs a `call`")
            elif known_tools is not None and a.call not in known_tools:
                r.problems.append(f"action {name}: {a.call!r} is not a k8stools tool")
        for f in a.produces:
            if f not in L.facts:
                r.problems.append(f"action {name}: produces {f!r}, which does not resolve")
            produced.add(f)
    for f in L.facts:
        if f not in produced:
            r.problems.append(f"fact {f}: no action produces it")

    for name, c in L.claims.items():
        if not c.refute:
            r.problems.append(f"claim {name}: no `refute` -- a claim that can only be "
                              f"confirmed invites confirmation-seeking (002 §6)")
        if not c.confirm:
            r.problems.append(f"claim {name}: no `confirm`")
        for sub in c.refined_by:
            if sub not in L.claims:
                r.problems.append(f"claim {name}: refined_by {sub!r} does not resolve")
        if c.refines_alert and c.refines_alert not in legacy_alerts:
            r.problems.append(f"claim {name}: refines_alert {c.refines_alert!r} "
                              f"is not a knowledge-base alert")
        for cond in claim_conditions(c):
            if cond.prose:
                r.prose.append(f"{name}: {cond.text}")
                continue
            fact = L.facts.get(cond.fact)
            if fact is None:
                r.problems.append(f"claim {name}: fact {cond.fact!r} does not resolve")
            elif len(cond.args) != len(fact.params):
                r.problems.append(f"claim {name}: {cond.text!r} gives {len(cond.args)} "
                                  f"argument(s); {cond.fact} takes {len(fact.params)}")
        if not any(decidable(k, alt, DEFAULT_AVAILABLE)
                   for alt in alternatives(c.confirm) + alternatives(c.refute)):
            r.undecidable.append(name)

    r.problems += _refinement_cycles(L)
    for pair in L.exclusive:
        if len(pair) != 2:
            r.problems.append(f"exclusive {pair}: a pair is two claims")
            continue
        if any(p not in L.claims for p in pair):
            r.problems.append(f"exclusive {pair}: a claim does not resolve")
        elif not _siblings(L, *pair):
            r.problems.append(f"exclusive {pair}: not siblings under one symptom or "
                              f"refinement, so not rivals (007 §3.1)")
    return r


def _siblings(L: Layer, a: str, b: str) -> bool:
    groups = [s.claims for s in L.symptoms.values()]
    groups += [c.refined_by for c in L.claims.values()]
    return any(a in g and b in g for g in groups)


def _refinement_cycles(L: Layer) -> list[str]:
    problems, done = [], set()

    def visit(name: str, path: list[str]) -> None:
        if name in path:
            problems.append("refinement cycle: " + " -> ".join(path[path.index(name):] + [name]))
            return
        if name in done or name not in L.claims:
            return
        for sub in L.claims[name].refined_by:
            visit(sub, path + [name])
        done.add(name)

    for name in L.claims:
        visit(name, [])
    return problems


# --- availability (007 §4) ----------------------------------------------------------


def observable(k: Knowledge, fact: str, available: frozenset[str]) -> bool:
    return any(fact in a.produces and a.requires in available
               for a in k.layer.actions.values())


def decidable(k: Knowledge, alternative: list[Condition], available: frozenset[str]) -> bool:
    """Every fact the alternative names can be observed here. Prose counts as
    decidable: whether it is, is the model's call."""
    return all(c.prose or observable(k, c.fact, available) for c in alternative)


# --- output ---------------------------------------------------------------------------


def to_json(k: Knowledge) -> dict[str, Any]:
    """The `knowledge` section of knowledge_base.json, which `kb_query.py` reads."""
    L = k.layer

    def src(section: str, name: str) -> str:
        return k.origin.get(f"{section}:{name}", "")

    return {
        "sources": k.sources,
        "capabilities": sorted(CAPABILITIES),
        "symptoms": {n: {**s.model_dump(), "source": src("symptoms", n)}
                     for n, s in L.symptoms.items()},
        "alerts": {n: m.model_dump() for n, m in L.alerts.items()},
        "facts": {n: f.model_dump() for n, f in L.facts.items()},
        "actions": {n: a.model_dump() for n, a in L.actions.items()},
        "claims": {n: {**c.model_dump(), "source": src("claims", n),
                       # Parsed here so kb_query.py, which ships stdlib-only
                       # inside the skill, never parses a condition itself.
                       "confirm": [[_cond_json(x) for x in alt]
                                   for alt in alternatives(c.confirm)],
                       "refute": [[_cond_json(x) for x in alt]
                                  for alt in alternatives(c.refute)],
                       "supports": [_cond_json(parse_condition(x)) for x in c.supports]}
                   for n, c in L.claims.items()},
        "exclusive": L.exclusive,
    }


def _cond_json(c: Condition) -> dict[str, Any]:
    if c.prose:
        return {"text": c.text}
    return {"text": c.text, "fact": c.fact, "args": list(c.args), "negated": c.negated,
            "compare": [list(t) for t in c.compare]}
