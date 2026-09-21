"""Reporting a suite run (design 004 §6.4).

The output is per-dimension pass rates against a baseline, not a score. A
scenario that passes twice in three runs is a real signal, and a pass/fail gate
would hide exactly that -- which matters here because the agent is
nondeterministic and a single run is one sample.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, pstdev
from typing import Iterable

#: Column order for the table. `restraint` last because only scenario 7 has it.
DIMENSIONS = ("cause", "evidence", "rivals", "traps", "gaps", "restraint")


@dataclass
class Aggregate:
    """One scenario's results across n runs."""

    scenario_id: str
    runs: int = 0
    #: dimension -> (passed, applicable). A dimension the scenario does not
    #: exercise is never applicable, and prints as "-" rather than 0/n: absence
    #: is not failure, and a suite that conflates them reports phantom
    #: regressions the first time a scenario stops having traps.
    dimension: dict[str, list[int]] = field(default_factory=dict)
    tool_calls: list[int] = field(default_factory=list)
    usd: list[float] = field(default_factory=list)
    checks_passed: int = 0
    budget_failures: int = 0

    def add(self, run) -> None:
        self.runs += 1
        self.tool_calls.append(len(run.tool_calls))
        self.usd.append(run.usd)
        if run.checks is not None:
            if run.checks.passed:
                self.checks_passed += 1
            if any(f.check == "budget" for f in run.checks.findings):
                self.budget_failures += 1
        if run.grade is None:
            return
        for name, value in run.grade.dimensions().items():
            passed, applicable = self.dimension.setdefault(name, [0, 0])
            if value is None:
                continue
            self.dimension[name] = [passed + (1 if value else 0), applicable + 1]

    def cell(self, name: str) -> str:
        passed, applicable = self.dimension.get(name, [0, 0])
        return "-" if not applicable else f"{passed}/{applicable}"

    def spread(self, values: Iterable[float]) -> str:
        vals = list(values)
        if not vals:
            return "-"
        if len(vals) == 1:
            return f"{vals[0]:.2f}"
        return f"{min(vals):.2f}-{max(vals):.2f}"


def aggregate(runs: Iterable) -> dict[str, Aggregate]:
    out: dict[str, Aggregate] = {}
    for run in runs:
        out.setdefault(run.scenario_id, Aggregate(run.scenario_id)).add(run)
    return out


def table(aggregates: dict[str, Aggregate], baseline: dict | None = None) -> list[str]:
    """The §6.4 table: pass rates per dimension, and the delta against baseline."""
    head = f"{'scenario':<28}" + "".join(f"{d[:6]:>8}" for d in DIMENSIONS)
    head += f"{'calls':>12}{'$':>14}"
    comparable = baseline_is_comparable(baseline)
    prior = scenarios(baseline) if comparable else {}
    if baseline:
        head += "   Δ vs baseline" if comparable else "   (baseline: grader changed)"
    lines = [head, "-" * len(head)]
    for sid in sorted(aggregates):
        agg = aggregates[sid]
        row = f"{sid:<28}" + "".join(f"{agg.cell(d):>8}" for d in DIMENSIONS)
        calls = agg.spread(float(c) for c in agg.tool_calls)
        row += f"{calls:>12}{agg.spread(agg.usd):>14}"
        if baseline:
            row += "   " + (delta(agg, prior.get(sid)) or "=" if comparable else "n/a")
        lines.append(row)
    return lines


def delta(agg: Aggregate, prior: dict | None) -> str:
    """How each dimension moved against the baseline, as counts not rates.

    Reported per dimension rather than as one number: "traps -1" says where to
    look, while a single score says only that something moved.
    """
    if not prior:
        return "new"
    moves = []
    for name in DIMENSIONS:
        passed, applicable = agg.dimension.get(name, [0, 0])
        was = (prior.get("dimension") or {}).get(name)
        if not applicable or not was:
            continue
        # Compare rates, so a baseline recorded at a different n still compares.
        now_rate = passed / applicable
        was_rate = was[0] / was[1] if was[1] else 0.0
        if abs(now_rate - was_rate) < 1e-9:
            continue
        moves.append(f"{name} {'+' if now_rate > was_rate else ''}"
                     f"{(now_rate - was_rate) * applicable:+.0f}".replace("++", "+"))
    return "  ".join(moves) if moves else "="


#: Unanimity below this many runs is not evidence of stability.
#:
#: By the rule of three, r unanimous runs with no counter-example put the 95%
#: upper bound on the other outcome at 3/r. At n=3 that bound is 1.0 -- the
#: reading excludes nothing. At n=6 it is 0.5, which is the first point where
#: "3/3" says more than "at least one of these happened".
MIN_RUNS_FOR_UNANIMITY = 6


def variance_note(agg: Aggregate) -> str | None:
    """Flag dimensions this run cannot actually call.

    Two ways a reading is uninformative, and only the first is obvious:

    * **Split.** A dimension that disagreed with itself. 2/3 is the suite
      declining to call it, and a baseline built on it is noise.
    * **Unanimous, but too few runs.** This one is dangerous because it looks
      like a result. `traps` read 1/3 in one n=3 run and 3/3 in the next, on the
      same scenario, the same capture and a byte-identical grader -- pooled, 4/6.
      The 3/3 flagged nothing and meant nothing; a dimension can come back
      unanimous and still be close to a coin flip.
    """
    split, thin = [], []
    for name in DIMENSIONS:
        passed, applicable = agg.dimension.get(name, [0, 0])
        if applicable < 1:
            continue
        if 0 < passed < applicable:
            split.append(name)
        elif applicable < MIN_RUNS_FOR_UNANIMITY:
            thin.append(name)

    notes = []
    if split:
        notes.append("unstable across runs: " + ", ".join(split) +
                     " -- a baseline on these would be noise")
    if thin:
        runs = agg.dimension[thin[0]][1]
        notes.append(f"unanimous but thin ({runs} run(s)): " + ", ".join(thin) +
                     f" -- rule of three puts the other outcome as high as "
                     f"{min(3 / runs, 1.0):.0%}, so this is not yet evidence of "
                     f"stability")
    return "; ".join(notes) or None


def save_baseline(aggregates: dict[str, Aggregate], path: Path) -> None:
    from .grader import apparatus_digest

    payload = {
        "grader": apparatus_digest(),
        "scenarios": {sid: {"runs": a.runs, "dimension": a.dimension,
                            "tool_calls": a.tool_calls, "usd": a.usd}
                      for sid, a in aggregates.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_baseline(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def baseline_is_comparable(baseline: dict | None) -> bool:
    """False when the grader has changed since the baseline was recorded.

    Comparing across a grader edit attributes the instrument's movement to the
    agent, which is the most misleading thing this report could do -- worse
    than having no baseline, because it looks like a finding.
    """
    from .grader import apparatus_digest

    if not baseline:
        return False
    return baseline.get("grader") == apparatus_digest()


def scenarios(baseline: dict | None) -> dict:
    return (baseline or {}).get("scenarios") or {}


def cost_summary(aggregates: dict[str, Aggregate]) -> str:
    total = sum(sum(a.usd) for a in aggregates.values())
    runs = sum(a.runs for a in aggregates.values())
    calls = [c for a in aggregates.values() for c in a.tool_calls]
    spread = f"{min(calls)}-{max(calls)}" if calls else "-"
    sd = f" (sd {pstdev(calls):.1f})" if len(calls) > 1 else ""
    return (f"{runs} run(s), ${total:.2f} in sessions, "
            f"{mean(calls):.0f} tool calls avg{sd}, range {spread}")
