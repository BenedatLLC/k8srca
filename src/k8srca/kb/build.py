"""Normalize the RCA knowledge base into the form the agent queries.

Design 002 §8. The source is 81 flat records with `;`-separated multi-values.
Three transformations matter:

1. **Split multi-values.** `root_causes` carries the candidate hypotheses for
   an alert -- 66 of 81 records list two or more. Splitting them is what turns
   "alert -> the root cause" into "alert -> candidate hypotheses".

2. **Build the graph from `correlated_alerts`, not `causal_parent`.**
   002 §5.5 assumed `causal_parent` linked alerts. It does not: it holds 19
   abstract category labels, and `environment_or_application_failure` alone
   covers 41 of 81 records. It is a coarse grouping, not a causal model.
   `correlated_alerts` *does* reference alert names (31 of 32 resolve), so it
   is the edge set worth indexing -- made bidirectional, since correlation is
   symmetric and the source only records it one way.

3. **Validate cross-references.** An unresolved name is a typo in the source,
   and silently dropping it would leave a dead end the agent cannot see.

4. **Merge the authored knowledge** (design 007). The vendored records name
   candidate causes and say nothing about telling them apart. `docs/rca/knowledge/`
   says it as structure -- symptoms, claims with confirm and refute conditions,
   facts, and the actions that observe them -- and maps alert names onto
   symptoms. It is separate on purpose: merging our text into the vendored
   source would lose the distinction between received and authored. An alert
   the knowledge maps is *converted*; `kb_query.py` answers for it from the
   knowledge, and for every other alert from its vendored record, so coverage
   never regresses (002 §8).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import knowledge

#: The knowledge base as received, vendored unedited (vendor/kb/README.md).
SOURCE = Path("vendor/kb/kubernetes_rca_knowledge_base_v2.json")
#: The authored knowledge, kept out of the vendored source so what we received
#: stays distinguishable from what we wrote (see §4 above).
KNOWLEDGE = knowledge.KNOWLEDGE
DEST = Path("skills/k8s-rca/knowledge_base.json")
MINED_NAME = knowledge.MINED.name


def mined_dir(knowledge_dir: Path) -> Path:
    """The mined layer beside an authored one: docs/rca/mined for docs/rca/knowledge."""
    return knowledge_dir.parent / MINED_NAME

MULTI = ("root_causes", "supporting_evidence", "potential_solutions", "promql_signals",
         "correlated_alerts", "metric_evidence", "event_evidence", "log_evidence",
         "dependency_checks")


#: Output format of knowledge_base.json, recorded in it and in the bundle manifest
#: (core.generator). Bump it when the meaning of the output changes.
FORMAT = 5


def split_values(raw: str | None) -> list[str]:
    """Split a `;`-separated field, tolerating stray whitespace and empties."""
    if not raw:
        return []
    return [part.strip() for part in re.split(r"\s*;\s*", raw) if part.strip()]


@dataclass
class BuildReport:
    alerts: int = 0
    hypotheses: int = 0
    edges: int = 0
    unresolved: list[str] = field(default_factory=list)
    isolated: list[str] = field(default_factory=list)
    symptoms: int = 0
    claims: int = 0
    facts: int = 0
    actions: int = 0
    #: Vendored alerts the knowledge maps to a symptom, and the hypotheses of
    #: the rest -- the measure of how far conversion has to go.
    converted: list[str] = field(default_factory=list)
    unconverted_hypotheses: int = 0
    prose: list[str] = field(default_factory=list)
    undecidable: list[str] = field(default_factory=list)
    #: Claims from the mined layer (007 §8), and lower-layer names a higher one hid.
    mined_claims: int = 0
    overridden: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  alerts        {self.alerts}",
            f"  hypotheses    {self.hypotheses} (from root_causes)",
            f"  edges         {self.edges} (bidirectional, from correlated_alerts)",
            f"  isolated      {len(self.isolated)} alerts with no correlations",
            f"  knowledge     {self.symptoms} symptoms, {self.claims} claims, "
            f"{self.facts} facts, {self.actions} actions"
            + (f" ({self.mined_claims} claims mined)" if self.mined_claims else ""),
            f"  converted     {len(self.converted)} alerts "
            f"({', '.join(self.converted) or 'none'}); "
            f"{self.unconverted_hypotheses} hypotheses not yet",
        ]
        if self.prose:
            lines.append(f"  prose         {len(self.prose)} conditions not in fact form")
        if self.undecidable:
            lines.append(f"  undecidable   {len(self.undecidable)} claims with k8stools "
                         f"alone: {', '.join(self.undecidable)}")
        for o in self.overridden:
            lines.append(f"  overridden    {o}")
        if self.unresolved:
            lines.append(f"  UNRESOLVED    {len(self.unresolved)}: {', '.join(self.unresolved)}")
        return "\n".join(lines)


def build(source: Path = SOURCE,
          knowledge_dir: Path = KNOWLEDGE) -> tuple[dict[str, Any], BuildReport]:
    records = json.loads(source.read_text())
    report = BuildReport(alerts=len(records))
    names = {r["alert_name"] for r in records}

    alerts: dict[str, Any] = {}
    for r in records:
        name = r["alert_name"]
        hypotheses = split_values(r.get("root_causes"))
        report.hypotheses += len(hypotheses)
        alerts[name] = {
            "alert": name,
            "resource_type": r.get("resource_type"),
            "symptom": r.get("symptom"),
            # Candidate explanations, not conclusions. The agent must
            # discriminate between them (002 §5.4).
            "hypotheses": hypotheses,
            "evidence": {
                "events": split_values(r.get("event_evidence")),
                "logs": split_values(r.get("log_evidence")),
                "metrics": split_values(r.get("metric_evidence")),
                "traces": split_values(r.get("trace_evidence")),
            },
            "promql": split_values(r.get("promql_signals")),
            "dependency_checks": split_values(r.get("dependency_checks")),
            "remediation": split_values(r.get("potential_solutions")),
            "category": r.get("cause_category"),
            "severity": r.get("severity"),
            "remediation_priority": r.get("remediation_priority"),
            # 'manual' on 78 of 81. The agent never acts regardless (001 §8);
            # this is carried so recommendations can be ordered honestly.
            "automation_safety": r.get("automation_safety"),
            # Coarse grouping label only -- NOT a causal parent. See module docstring.
            "group": r.get("causal_parent"),
        }

    # The authored knowledge. A problem in it fails the build: a name that does
    # not resolve leaves the agent a criterion it cannot use and cannot tell it
    # cannot use.
    # Mined under authored (007 §9.3). The mined layer sits beside the authored
    # one, so a test's knowledge directory brings its own (usually none).
    k = knowledge.load_layers([mined_dir(knowledge_dir), knowledge_dir])
    checked = knowledge.check(k, legacy_alerts=set(alerts), legacy_hypotheses={
        n: a["hypotheses"] for n, a in alerts.items()})
    if checked.problems:
        raise knowledge.KnowledgeError(checked.problems)
    L = k.layer
    report.symptoms, report.claims = len(L.symptoms), len(L.claims)
    report.facts, report.actions = len(L.facts), len(L.actions)
    report.prose, report.undecidable = checked.prose, checked.undecidable
    report.converted = sorted(a for a in L.alerts if a in alerts)
    report.mined_claims = sum(1 for n in L.claims
                              if k.origin.get(f"claims:{n}", "").startswith(MINED_NAME + "/"))
    report.overridden = k.overridden

    # Correlation graph, made symmetric.
    edges: dict[str, set[str]] = defaultdict(set)
    unresolved: set[str] = set()
    for r in records:
        src = r["alert_name"]
        for other in split_values(r.get("correlated_alerts")):
            if other not in names:
                unresolved.add(f"{src} -> {other}")
                continue
            edges[src].add(other)
            edges[other].add(src)

    for name in alerts:
        alerts[name]["related"] = sorted(edges.get(name, ()))
        if not alerts[name]["related"]:
            report.isolated.append(name)

    report.edges = sum(len(v) for v in edges.values())
    report.unresolved = sorted(unresolved)

    groups: dict[str, list[str]] = defaultdict(list)
    for name, a in alerts.items():
        groups[a["group"]].append(name)

    report.unconverted_hypotheses = sum(
        len(a["hypotheses"]) for n, a in alerts.items() if n not in L.alerts)

    return {
        "version": FORMAT,
        "source": source.name,
        "note": (
            "Built by k8srca.kb.build. 'related' is the bidirectional correlation "
            "graph from correlated_alerts. 'group' is a coarse label, not a causal parent. "
            "'knowledge' is the authored layer (design 007); an alert it maps to a "
            "symptom is answered from there."
        ),
        "alerts": alerts,
        "groups": {k: sorted(v) for k, v in sorted(groups.items())},
        "knowledge": knowledge.to_json(k),
    }, report


def write(dest: Path = DEST, source: Path = SOURCE,
          knowledge_dir: Path = KNOWLEDGE) -> BuildReport:
    data, report = build(source, knowledge_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return report
