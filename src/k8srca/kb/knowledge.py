"""Diagnostic knowledge as facts, actions and claims (design 007 §3).

The vendored knowledge base names candidate causes per alert and says nothing
about telling them apart. This layer says it as structure: conditions on facts
count for or against a claim, with a weight, or rule it out (008 §4.2); a fact
is observed by actions, an action needs a capability the deployment may not
have. The model reads the result
through `kb_query.py`; nothing here runs a solver (007 §10.2).

What the build checks is what 007 §7 lists, and 008 §4.1's scope rule. The
failures are names that do not resolve, a claim nothing could rule out, a fact
nothing could observe, a fact that claims all of history from a record that
keeps part of it -- each of which would otherwise leave the agent a criterion
it cannot use and cannot tell it cannot use.
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
#: The mined layer (007 §8): extracted from public guides, then reviewed. It sits
#: beside the authored layer, under it in precedence (§9.3).
MINED = Path("docs/rca/mined")

#: What an action can require (007 §4.1). Named for what is provided, not for a
#: product. `human` is never available to the agent.
CAPABILITIES = frozenset({"k8stools", "prometheus", "tracing", "logs-backend",
                          "audit-log", "human"})

#: What a deployment has unless it says otherwise: k8stools is always enabled
#: (005 §5.1), and nothing else is today.
DEFAULT_AVAILABLE = frozenset({"k8stools"})

#: Timing (007 §3.4). A change is evidence of a cause only when the onset is
#: observed and the change took effect just before it, so every claim says it
#: with these two facts rather than coining its own: `changed_before_onset[X,
#: Kind]` names a kind from `changes`, which says when that kind takes effect.
CHANGE_FACT = "changed_before_onset"
ONSET_FACT = "onset_observed"
#: Names that look like a timing fact coined instead of using the two above.
_TIMING_NAME = re.compile(r"precede|before_onset|changed?_before|"
                          r"_near_(?:restart|onset|incident|failure)")
#: Words that quantify over all time (008 §4.1). A fact saying one is a claim
#: about a history the record may not retain: "killed on every life" read from
#: the last state and one event series. Allowed when the fact says it is over
#: what is retained, or when an action producing it reads the whole history.
_ALL_TIME = re.compile(r"\b(every|always|never|first|since)\b", re.I)
_SCOPED = re.compile(r"\bretain", re.I)

#: How much a condition counts for or against a claim (008 §4.2, §5.2): bands of
#: a likelihood ratio -- strong 10 or more, moderate 3 to 10, weak under 3.
Weight = Literal["strong", "moderate", "weak"]
GRADE: dict[str, int] = {"weak": 1, "moderate": 2, "strong": 3}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Cite(_Strict):
    """Where a mined item came from (007 §8.3): a source id from the mining
    manifest, and the passage in it, verbatim."""

    source: str
    quote: str


class Symptom(_Strict):
    title: str
    claims: list[str]
    sources: list[Cite] = Field(default_factory=list)


class AlertMapping(_Strict):
    symptom: str
    bind: dict[str, str] = Field(default_factory=dict)
    sources: list[Cite] = Field(default_factory=list)


class Fact(_Strict):
    params: list[str]
    says: str
    functional: bool = False
    sources: list[Cite] = Field(default_factory=list)


class Action(_Strict):
    requires: str
    produces: list[str]
    call: str | None = None
    read: str = ""
    cost: int = 1
    quality: Literal["complete", "partial", "point"] = "complete"
    limits: str = ""
    #: What its facts cover: what the record retains (the default, and every
    #: k8stools action), or the whole history, as a metrics series over the
    #: pod's life would (008 §4.1).
    scope: Literal["retained", "history"] = "retained"
    sources: list[Cite] = Field(default_factory=list)


class Change(_Strict):
    """A kind of change (007 §3.4): what it is, when it takes effect, and the
    actions that can see one."""

    title: str
    takes_effect: str
    seen_by: list[str] = Field(default_factory=list)
    sources: list[Cite] = Field(default_factory=list)


#: An alternative: one condition, or a list that must all hold.
Alternative = str | list[str]


class Weighted(_Strict):
    """An alternative that counts for or against a claim, by `weight`."""

    when: Alternative
    weight: Weight


class Claim(_Strict):
    title: str
    #: What counts for it. Its support is the highest weight that holds.
    for_: list[Weighted] = Field(alias="for")
    #: What counts against it: strong lowers its support two grades, moderate
    #: one; weak lowers nothing but is owed a sentence in the answer.
    against: list[Weighted] = Field(default_factory=list)
    #: Decisive: any alternative that holds rules it out.
    rules_out: list[Alternative]
    refined_by: list[str] = Field(default_factory=list)
    #: A vendored alert whose hypotheses decompose this claim, for claims whose
    #: refinement has not been converted yet.
    refines_alert: str | None = None
    note: str = ""
    sources: list[Cite] = Field(default_factory=list)
    #: `rules_out` was drafted by a model rather than stated by a source.
    #: Public guides rarely say what rules a cause out (007 §8.3); this marks
    #: where review has to look hardest.
    refute_drafted: bool = False
    #: As a sub-cause, how often this is the kind. An uncommon one is counted
    #: under its parent but not named there until asked for: answers covered
    #: the rare sub-causes in place of the likely ones once a parent had six
    #: (007 §6, §11.2).
    prevalence: Literal["common", "uncommon"] = "common"


class Declined(_Strict):
    """A vendored hypothesis deliberately not converted, and why (007 §8.5)."""

    declined: str


#: What a vendored hypothesis became: a claim's name, or a reasoned refusal.
Conversion = str | Declined

#: A reason that has not been written yet. The mining pipeline writes it for a
#: hypothesis it could not place, and the build fails on it, so the decision
#: is made in review rather than defaulted.
UNDECIDED = "TODO"


class Layer(_Strict):
    """One knowledge file."""

    symptoms: dict[str, Symptom] = Field(default_factory=dict)
    alerts: dict[str, AlertMapping] = Field(default_factory=dict)
    facts: dict[str, Fact] = Field(default_factory=dict)
    actions: dict[str, Action] = Field(default_factory=dict)
    claims: dict[str, Claim] = Field(default_factory=dict)
    changes: dict[str, Change] = Field(default_factory=dict)
    exclusive: list[list[str]] = Field(default_factory=list)
    #: Sub-causes added to a claim defined elsewhere, possibly in another layer:
    #: how a lower layer adds to a claim it cannot redefine (§9.3).
    refinements: dict[str, list[str]] = Field(default_factory=dict)
    #: The conversion table for vendored alerts (007 §8.5): per alert, what
    #: each of its hypotheses became. Explicit, so a candidate dropped in
    #: conversion is a reviewed decision, not an accident of naming.
    converts: dict[str, dict[str, Conversion]] = Field(default_factory=dict)


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


def weighed(raw: list[Weighted]) -> list[list[Condition]]:
    return alternatives([w.when for w in raw])


def every_alternative(claim: Claim) -> list[tuple[str, list[Condition]]]:
    """Each alternative, labelled for, against or rules_out."""
    return ([("for", a) for a in weighed(claim.for_)]
            + [("against", a) for a in weighed(claim.against)]
            + [("rules_out", a) for a in alternatives(claim.rules_out)])


def claim_conditions(claim: Claim) -> list[Condition]:
    return [c for _, alt in every_alternative(claim) for c in alt]


#: The role letters, which stand for the subject rather than for a value.
ROLES = frozenset({"C", "W", "P", "N", "S"})
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def literals(L: "Layer", alt: list[Condition]) -> set[str]:
    """The capitalised arguments in an alternative that are values, not
    variables (`ErrImagePull` in `waiting_reason[C, ErrImagePull]`). An
    argument is a variable when it is its fact's own parameter name, appears in
    a comparison, or recurs in the alternative; anything else is a literal.
    Lower-case and numeric arguments are always literals."""
    compared = {w for c in alt for t in c.compare for w in (t[0], t[2])}
    compared |= {w for c in alt if c.prose for w in _WORD.findall(c.text)}
    args = [a for c in alt if not c.prose for a in c.args]
    out = set()
    for c in alt:
        fact = L.facts.get(c.fact) if c.fact else None
        if fact is None:
            continue
        for a, param in zip(c.args, fact.params):
            if (a[:1].isupper() and a not in ROLES and a != param
                    and a not in compared and args.count(a) < 2):
                out.add(a)
    return out


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
    #: Names a higher layer redefined, hiding the lower layer's definition.
    overridden: list[str] = field(default_factory=list)
    #: Problems found while combining layers, reported by `check`.
    problems: list[str] = field(default_factory=list)


_SECTIONS = ("symptoms", "alerts", "facts", "actions", "claims", "changes")


def load(directory: Path = KNOWLEDGE, exclude: tuple[str, ...] = ()) -> Knowledge:
    """Merge every `*.yaml` under `directory` but those named in `exclude`.
    A name defined twice is an error."""
    merged = Layer()
    origin: dict[str, str] = {}
    problems: list[str] = []
    files = sorted(p for p in directory.glob("*.yaml")
                   if p.name not in exclude) if directory.exists() else []
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
        for claim, subs in layer.refinements.items():
            merged.refinements.setdefault(claim, [])
            merged.refinements[claim] += [s for s in subs if s not in merged.refinements[claim]]
        for alert, table in layer.converts.items():
            for h, conv in table.items():
                if h in merged.converts.get(alert, {}):
                    problems.append(f"converts {alert} / {h!r} given twice in {path.name}'s "
                                    f"directory")
                merged.converts.setdefault(alert, {})[h] = conv
    if problems:
        raise KnowledgeError(problems)
    k = Knowledge(layer=merged, origin=origin, sources=[p.name for p in files])
    return _apply_refinements(k)


def load_layers(directories: list[Path]) -> Knowledge:
    """Merge layers, lowest precedence first (007 §9.3).

    A higher layer can add anything and can redefine a fact, action, claim or
    alert mapping by name; the lower definition is hidden and reported. It
    cannot take a candidate away: a symptom defined in two layers lists the
    union of their claims, the higher layer's first, and exclusions and
    refinements accumulate.
    """
    return combine([(d.name, load(d)) for d in directories])


def combine(layers: list[tuple[str, Knowledge]]) -> Knowledge:
    """`load_layers` over layers already loaded, each with a label for provenance."""
    out = Knowledge(layer=Layer())
    L = out.layer
    for label, k in layers:
        out.sources += [f"{label}/{s}" for s in k.sources]
        for name, s in k.layer.symptoms.items():
            if name in L.symptoms:
                lower = L.symptoms[name]
                L.symptoms[name] = s.model_copy(update={
                    "claims": s.claims + [c for c in lower.claims if c not in s.claims],
                    "sources": lower.sources + s.sources})
            else:
                L.symptoms[name] = s
            out.origin[f"symptoms:{name}"] = f"{label}/{k.origin.get(f'symptoms:{name}', '')}"
        for section in ("alerts", "facts", "actions", "claims", "changes"):
            into = getattr(L, section)
            for name, value in getattr(k.layer, section).items():
                key = f"{section}:{name}"
                if name in into:
                    out.overridden.append(f"{section} {name}: {out.origin[key]} "
                                          f"hidden by {label}/{k.origin.get(key, '')}")
                into[name] = value
                out.origin[key] = f"{label}/{k.origin.get(key, '')}"
        L.exclusive += [p for p in k.layer.exclusive if p not in L.exclusive]
        for claim, subs in k.layer.refinements.items():
            L.refinements.setdefault(claim, [])
            L.refinements[claim] += [s for s in subs if s not in L.refinements[claim]]
        for alert, table in k.layer.converts.items():
            L.converts.setdefault(alert, {}).update(table)  # the higher layer's word wins
    return _apply_refinements(out)


def _apply_refinements(k: Knowledge) -> Knowledge:
    """Fold `refinements` into each claim's `refined_by`, so everything that
    reads the knowledge sees one graph. One whose claim is not defined in any
    layer loaded so far is held back: a single layer may refine a claim
    another layer defines, and `check` reports it only if it never resolves."""
    L = k.layer
    for claim, subs in L.refinements.items():
        if claim not in L.claims:
            continue
        have = L.claims[claim].refined_by
        L.claims[claim] = L.claims[claim].model_copy(
            update={"refined_by": have + [s for s in subs if s not in have]})
    return k


@dataclass
class CheckReport:
    problems: list[str] = field(default_factory=list)
    #: Conditions that did not parse as `fact[args]` (007 §3.2): allowed, counted.
    prose: list[str] = field(default_factory=list)
    #: Claims none of whose alternatives can be decided by `DEFAULT_AVAILABLE`.
    undecidable: list[str] = field(default_factory=list)
    #: Capitalised arguments read as values, not variables (`literals`), so
    #: that a misread one is seen at build time.
    literals: list[str] = field(default_factory=list)


def check(k: Knowledge, *, legacy_alerts: set[str] = frozenset(),
          known_tools: set[str] | None = None,
          prometheus_tools: set[str] | None = None,
          legacy_hypotheses: dict[str, list[str]] | None = None) -> CheckReport:
    """007 §7. `known_tools`, when given, is k8stools' tool names, and
    `prometheus_tools` promtools' (design 009 §6).
    `legacy_hypotheses`, when given, is each vendored alert's hypotheses, and
    turns on the conversion check (§8.5)."""
    L, r = k.layer, CheckReport(problems=list(k.problems))

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
        tools = {"k8stools": known_tools, "prometheus": prometheus_tools}
        if a.requires in tools:
            names = tools[a.requires]
            server = "k8stools" if a.requires == "k8stools" else "promtools"
            if not a.call:
                r.problems.append(f"action {name}: a {a.requires} action needs a `call`")
            elif names is not None and a.call not in names:
                r.problems.append(f"action {name}: {a.call!r} is not a {server} tool")
        for f in a.produces:
            if f not in L.facts:
                r.problems.append(f"action {name}: produces {f!r}, which does not resolve")
            produced.add(f)
        if CHANGE_FACT in a.produces:
            r.problems.append(f"action {name}: lists {CHANGE_FACT}; name it in the `seen_by` "
                              f"of the change kinds it sees instead (007 §3.4)")
    for kind, ch in L.changes.items():
        for a in ch.seen_by:
            if a not in L.actions:
                r.problems.append(f"change {kind}: seen_by {a!r} does not resolve")
            else:
                produced.add(CHANGE_FACT)
    history = {f for a in L.actions.values() if a.scope == "history" for f in a.produces}
    for f, fact in L.facts.items():
        if f not in produced:
            r.problems.append(f"fact {f}: no action produces it")
        word = _ALL_TIME.search(fact.says)
        if word and not _SCOPED.search(fact.says) and f not in history:
            r.problems.append(f"fact {f}: says {word[0]!r}, which no action producing it "
                              f"can observe: scope it to what the record retains, or "
                              f"have a `scope: history` action produce it (008 §4.1)")
        if f not in (CHANGE_FACT, ONSET_FACT) and _TIMING_NAME.search(f):
            r.problems.append(f"fact {f}: looks like a timing fact; say it with "
                              f"{ONSET_FACT} and {CHANGE_FACT}[X, Kind] (007 §3.4)")

    refined = {sub for c in L.claims.values() for sub in c.refined_by}
    for name, c in L.claims.items():
        if c.prevalence == "uncommon" and name not in refined:
            r.problems.append(f"claim {name}: prevalence is how often it is the kind "
                              f"among its siblings, and it refines nothing (007 §6)")
        if not c.rules_out:
            r.problems.append(f"claim {name}: no `rules_out` -- a claim that can only be "
                              f"supported invites confirmation-seeking (002 §6)")
        if not c.for_:
            r.problems.append(f"claim {name}: no `for`")
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
        r.problems += _timing_problems(L, name, c)
        for _, alt in every_alternative(c):
            r.literals += [lit for lit in sorted(literals(L, alt)) if lit not in r.literals]
        if not any(decidable(k, alt, DEFAULT_AVAILABLE)
                   for part, alt in every_alternative(c) if part != "against"):
            r.undecidable.append(name)

    for claim, subs in L.refinements.items():
        if claim not in L.claims:
            r.problems.append(f"refinements: claim {claim!r} does not resolve")
        for sub in subs:
            if sub not in L.claims:
                r.problems.append(f"refinements of {claim}: {sub!r} does not resolve")
    r.problems += _refinement_cycles(L)
    if legacy_hypotheses is not None:
        r.problems += _conversion_problems(L, legacy_hypotheses)
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


def _timing_problems(L: Layer, name: str, c: Claim) -> list[str]:
    """007 §3.4: a change counts only against an observed onset, and only as
    its kind takes effect. An alternative that names a change without the
    onset would let a revision months old confirm a failure of unknown age."""
    problems = []
    for part, alt in every_alternative(c):
        changes = [x for x in alt if x.fact == CHANGE_FACT]
        for x in changes:
            if len(x.args) == 2 and x.args[1] not in L.changes:
                problems.append(f"claim {name}: {x.text!r} names change kind "
                                f"{x.args[1]!r}, which does not resolve")
        if changes and not any(x.fact == ONSET_FACT and not x.negated for x in alt):
            problems.append(f"claim {name}: {part} alternative "
                            f"{' AND '.join(x.text for x in alt)!r} names a change "
                            f"without {ONSET_FACT}: a change is evidence only against "
                            f"an observed onset")
    return problems


def reachable(L: Layer, symptom: str) -> set[str]:
    """The claims an investigation of `symptom` can arrive at: its candidates
    and, through refinement, their sub-causes."""
    out: set[str] = set()
    todo = list(L.symptoms[symptom].claims) if symptom in L.symptoms else []
    while todo:
        c = todo.pop()
        if c in out or c not in L.claims:
            continue
        out.add(c)
        todo += L.claims[c].refined_by
    return out


def _conversion_problems(L: Layer, legacy: dict[str, list[str]]) -> list[str]:
    """Every hypothesis of a vendored alert the knowledge answers for is either
    a claim that alert's symptom reaches, or declined with a reason. Once an
    alert is mapped, `kb_query` stops showing its vendored record, so a
    hypothesis missing here is a candidate the agent silently stops weighing."""
    problems = []
    for alert, table in L.converts.items():
        if alert not in legacy:
            problems.append(f"converts {alert}: not a vendored alert")
            continue
        for h in table:
            if h not in legacy[alert]:
                problems.append(f"converts {alert}: {h!r} is not one of its hypotheses "
                                f"({'; '.join(legacy[alert])})")
    for alert, m in L.alerts.items():
        if alert not in legacy:
            continue
        reach = reachable(L, m.symptom)
        table = L.converts.get(alert, {})
        for h in legacy[alert]:
            conv = table.get(h)
            if conv is None:
                problems.append(f"converts {alert}: hypothesis {h!r} is not converted; "
                                f"name the claim it became, or decline it with a reason")
            elif isinstance(conv, Declined):
                if not conv.declined.strip() or conv.declined.strip().startswith(UNDECIDED):
                    problems.append(f"converts {alert}: {h!r} is undecided -- convert it "
                                    f"or write why not")
            elif conv not in L.claims:
                problems.append(f"converts {alert}: {h!r} -> {conv!r}, which does not resolve")
            elif conv not in reach:
                problems.append(f"converts {alert}: {h!r} -> {conv}, which {m.symptom} "
                                f"does not reach, so `lookup {alert}` would not offer it")
    return problems


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


def observable_condition(k: Knowledge, c: Condition, available: frozenset[str]) -> bool:
    """A change is observable by the actions its kind names, not by every
    action that sees some change (007 §3.4)."""
    L = k.layer
    if c.fact == CHANGE_FACT and len(c.args) == 2 and c.args[1] in L.changes:
        return any(a in L.actions and L.actions[a].requires in available
                   for a in L.changes[c.args[1]].seen_by)
    return observable(k, c.fact, available)


def decidable(k: Knowledge, alternative: list[Condition], available: frozenset[str]) -> bool:
    """Every fact the alternative names can be observed here. Prose counts as
    decidable: whether it is, is the model's call."""
    return all(c.prose or observable_condition(k, c, available) for c in alternative)


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
        "claims": {n: {**c.model_dump(by_alias=True), "source": src("claims", n),
                       # Parsed here so kb_query.py, which ships stdlib-only
                       # inside the skill, never parses a condition itself.
                       "for": [{"when": _alt_json(L, alt), "weight": w.weight}
                               for w, alt in zip(c.for_, weighed(c.for_))],
                       "against": [{"when": _alt_json(L, alt), "weight": w.weight}
                                   for w, alt in zip(c.against, weighed(c.against))],
                       "rules_out": [_alt_json(L, alt)
                                     for alt in alternatives(c.rules_out)]}
                   for n, c in L.claims.items()},
        "changes": {n: ch.model_dump() for n, ch in L.changes.items()},
        "exclusive": L.exclusive,
    }


def _alt_json(L: Layer, alt: list[Condition]) -> list[dict[str, Any]]:
    lits = literals(L, alt)
    return [_cond_json(x, lits) for x in alt]


def _cond_json(c: Condition, lits: set[str] = frozenset()) -> dict[str, Any]:
    if c.prose:
        return {"text": c.text}
    out = {"text": c.text, "fact": c.fact, "args": list(c.args), "negated": c.negated,
           "compare": [list(t) for t in c.compare]}
    if mine := sorted(lits & set(c.args)):
        # kb_query.py binds a capitalised argument as a variable unless it is
        # listed here.
        out["literals"] = mine
    return out
