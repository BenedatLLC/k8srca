"""Checks the host runs on a final answer before it goes out (design 007 §6.1).

The knowledge prints what an answer owes, and answers still leave it out: in
the seventh gate (007 §11.2) both runs that dropped memory-leak and
workload-spike had read `open`'s line naming them. Text the agent reads
reaches the step, not the answer. So the host checks the answer itself, and
sends what it found back to the session as one revision turn.

**Completeness**, deterministic. Each `kb_query.py open` run leaves its rows
beside the evidence record:

    <workdir>/evidence/open/<n>.json

The answer owes a disposition to every candidate `open` listed, and to every
sub-cause it decided (confirmed, refuted or conflicting). Open sub-causes are
left out: there are dozens, and naming each would bury the answer. A claim
counts as addressed when the answer names its id or its title.

The check reads only the workspace, which holds what the model already saw.
It makes no cluster call (CLAUDE.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Statuses that make a sub-cause owed, not only a top-level candidate.
DECIDED = ("confirmed", "refuted", "conflicting")


@dataclass
class Review:
    #: Owed claims the answer does not name: {"id", "title", "status"}.
    missing: list[dict] = field(default_factory=list)
    #: How many `open` runs the session left; 0 means it never ran `open`.
    opens: int = 0

    @property
    def prompt(self) -> str | None:
        """The revision turn's message, or None when nothing was found."""
        if not self.opens:
            return ("Before this answer goes out: it was written without running "
                    "`kb_query.py open`. Run it with the facts you observed, then reply "
                    "with the complete answer again, revised so that every candidate it "
                    "lists, and every sub-cause it confirms or refutes, has a "
                    "disposition. Change nothing else.")
        if not self.missing:
            return None
        lines = "\n".join(f"- {m['id']} ({m['title']}): `open` says {m['status']}"
                          for m in self.missing)
        return ("Before this answer goes out: checked against your last "
                "`kb_query.py open`, it does not address these claims:\n"
                f"{lines}\n\n"
                "Reply with the complete answer again, revised: give each of these a "
                "disposition (confirmed, weakened, refuted, could-not-check or not "
                "checked) and the fact behind it. One line each is enough, and ones "
                "not checked may share a line. Change nothing else.")

    def as_dict(self) -> dict:
        return {"opens": self.opens, "missing": self.missing}


def open_records(workspace: Path) -> list[dict]:
    """Every `open` run the session left, oldest first."""
    d = workspace / "evidence" / "open"
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            continue
    return out


def owed(records: list[dict]) -> list[dict]:
    """The claims the answer owes a disposition, from the last `open` run on
    each facts file (one per failing workload)."""
    last = {}
    for r in records:
        last[(r.get("symptom"), r.get("facts"))] = r
    out: dict[str, dict] = {}
    for r in last.values():
        for row in r.get("rows", []):
            if row.get("depth", 0) == 0 or row.get("status") in DECIDED:
                prev = out.get(row["id"])
                # The same claim decided for one workload and open for another
                # is owed as decided: that is the disposition worth stating.
                if prev is None or row["status"] in DECIDED:
                    out[row["id"]] = {k: row[k] for k in ("id", "title", "status")}
    return list(out.values())


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def named(answer: str, claim: dict) -> bool:
    a = f" {_norm(answer)} "
    return any(f" {_norm(n)} " in a for n in (claim["id"], claim["title"]) if n)


def completeness(answer: str, workspace: Path) -> Review | None:
    """None when the host cannot see the session's workspace (a cloud
    environment, or the orchestrator on another host): nothing to check
    against is not the same as no `open` run."""
    if not workspace.is_dir():
        return None
    records = open_records(workspace)
    return Review(missing=[c for c in owed(records) if not named(answer, c)],
                  opens=len(records))
