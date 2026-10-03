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

4. **Merge authored discriminators.** The vendored records name candidate causes
   and say nothing about telling them apart, so `would_confirm` was left to the
   model to invent at investigation time -- 002 §11.3 calls that the weakest
   link in §5.4. `docs/rca/discriminators.yaml` supplies them for the alerts we
   have written, and is a separate file on purpose: merging our text into
   `background/` would lose the distinction between received and authored. Each
   hypothesis records whether it has one, so the agent can tell a criterion it
   was given from one it is about to invent.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCE = Path("background/kubernetes_rca_knowledge_base_v2.json")
#: Hand-authored discriminators, kept out of the vendored source so what we
#: received stays distinguishable from what we wrote (see §4 below).
DISCRIMINATORS = Path("docs/rca/discriminators.yaml")
DEST = Path("skills/k8s-rca/knowledge_base.json")

MULTI = ("root_causes", "supporting_evidence", "potential_solutions", "promql_signals",
         "correlated_alerts", "metric_evidence", "event_evidence", "log_evidence",
         "dependency_checks")


#: Output format of knowledge_base.json, recorded in it and in the bundle manifest
#: (core.generator). Bump it when the meaning of the output changes.
FORMAT = 4


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
    discriminators: int = 0
    refinements: int = 0
    undiscriminated: int = 0
    unresolved_discriminators: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"  alerts        {self.alerts}",
            f"  hypotheses    {self.hypotheses} (from root_causes)",
            f"  edges         {self.edges} (bidirectional, from correlated_alerts)",
            f"  isolated      {len(self.isolated)} alerts with no correlations",
            f"  discriminators {self.discriminators} authored, "
            f"{self.undiscriminated} hypotheses without one",
            f"  refinements   {self.refinements} hypothesis -> alert links",
        ]
        if self.unresolved_discriminators:
            lines.append(f"  UNRESOLVED DISCRIMINATORS {len(self.unresolved_discriminators)}: "
                         + ", ".join(self.unresolved_discriminators))
        if self.unresolved:
            lines.append(f"  UNRESOLVED    {len(self.unresolved)}: {', '.join(self.unresolved)}")
        return "\n".join(lines)


def load_discriminators(path: Path = DISCRIMINATORS) -> dict[str, dict[str, dict]]:
    """Read the authored layer. Absent is legitimate; malformed is not."""
    if not path.exists():
        return {}
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of alert -> hypothesis -> criteria")
    return data


def build(source: Path = SOURCE,
          discriminators: Path = DISCRIMINATORS) -> tuple[dict[str, Any], BuildReport]:
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

    # Authored discriminators, attached per hypothesis.
    authored = load_discriminators(discriminators)

    # `refines` is a separate top-level block, not an alert.
    refines = authored.pop("refines", {}) or {}
    for alert_name, mapping in refines.items():
        if alert_name not in alerts:
            report.unresolved_discriminators.append(f"refines: alert {alert_name}")
            continue
        known = set(alerts[alert_name]["hypotheses"])
        for hypothesis, target in mapping.items():
            if hypothesis not in known:
                report.unresolved_discriminators.append(
                    f"refines: {alert_name} -> {hypothesis!r}")
            elif target not in alerts:
                report.unresolved_discriminators.append(
                    f"refines: {alert_name}/{hypothesis} -> alert {target}")
            else:
                alerts[alert_name].setdefault("refines", {})[hypothesis] = target
                report.refinements += 1

    for alert_name, per_hypothesis in authored.items():
        if alert_name not in alerts:
            report.unresolved_discriminators.append(f"alert {alert_name}")
            continue
        known = set(alerts[alert_name]["hypotheses"])
        for hypothesis, body in per_hypothesis.items():
            if hypothesis not in known:
                # A typo here silently attaches a discriminator to nothing, and
                # the hypothesis it was meant for keeps being invented instead.
                report.unresolved_discriminators.append(
                    f"{alert_name} -> {hypothesis!r}")
                continue
            alerts[alert_name].setdefault("discriminators", {})[hypothesis] = {
                **body, "source": "authored"}
            report.discriminators += 1

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

    report.undiscriminated = sum(
        1 for a in alerts.values() for h in a["hypotheses"]
        if h not in (a.get("discriminators") or {}))

    return {
        "version": FORMAT,
        "source": source.name,
        "discriminator_source": discriminators.name if authored else None,
        "note": (
            "Built by k8srca.kb.build. 'related' is the bidirectional correlation "
            "graph from correlated_alerts. 'group' is a coarse label, not a causal parent."
        ),
        "alerts": alerts,
        "groups": {k: sorted(v) for k, v in sorted(groups.items())},
    }, report


def write(dest: Path = DEST, source: Path = SOURCE,
          discriminators: Path = DISCRIMINATORS) -> BuildReport:
    data, report = build(source, discriminators)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return report
