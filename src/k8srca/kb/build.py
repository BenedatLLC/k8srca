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
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE = Path("background/kubernetes_rca_knowledge_base_v2.json")
DEST = Path("skills/k8s-rca/knowledge_base.json")

MULTI = ("root_causes", "supporting_evidence", "potential_solutions", "promql_signals",
         "correlated_alerts", "metric_evidence", "event_evidence", "log_evidence",
         "dependency_checks")


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

    def render(self) -> str:
        lines = [
            f"  alerts        {self.alerts}",
            f"  hypotheses    {self.hypotheses} (from root_causes)",
            f"  edges         {self.edges} (bidirectional, from correlated_alerts)",
            f"  isolated      {len(self.isolated)} alerts with no correlations",
        ]
        if self.unresolved:
            lines.append(f"  UNRESOLVED    {len(self.unresolved)}: {', '.join(self.unresolved)}")
        return "\n".join(lines)


def build(source: Path = SOURCE) -> tuple[dict[str, Any], BuildReport]:
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

    return {
        "version": 3,
        "source": source.name,
        "note": (
            "Built by k8srca.kb.build. 'related' is the bidirectional correlation "
            "graph from correlated_alerts. 'group' is a coarse label, not a causal parent."
        ),
        "alerts": alerts,
        "groups": {k: sorted(v) for k, v in sorted(groups.items())},
    }, report


def write(dest: Path = DEST, source: Path = SOURCE) -> BuildReport:
    data, report = build(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return report
