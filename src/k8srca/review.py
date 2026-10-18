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

**The reviewer**, a model call and optional (`review.reviewer`). The checks
above cannot see an inference, such as "crash-looping since the rollout" from
a pod's age, the largest kind of unsupported claim (007 §11.2). One call
reads the answer against the evidence record and flags claims no result
supports. Certainty is its own kind, `overstated`: in the offline eval the
reviewer disputed the right cause because the reason read Error, and an
answer that dropped the cause over it would be worse off.

Both checks read only the workspace, which holds what the model already
saw. Neither makes a cluster call (CLAUDE.md).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("k8srca.review")

#: Statuses that make a sub-cause owed, not only a top-level candidate.
DECIDED = ("confirmed", "refuted", "conflicting")


@dataclass
class Review:
    #: Owed claims the answer does not name: {"id", "title", "status"}.
    missing: list[dict] = field(default_factory=list)
    #: How many `open` runs the session left; 0 means it never ran `open`,
    #: None that the completeness check was off.
    opens: int | None = 0
    #: The reviewer's flags: {"quote", "kind", "why", "evidence"}; None when
    #: it did not run.
    flags: list[dict] | None = None

    @property
    def prompt(self) -> str | None:
        """The revision turn's message, or None when nothing was found."""
        parts = []
        if self.opens == 0:
            parts.append("It was written without running `kb_query.py open`. Run it with "
                         "the facts you observed, and give every candidate it lists, and "
                         "every sub-cause it confirms or refutes, a disposition.")
        elif self.missing:
            lines = "\n".join(f"- {m['id']} ({m['title']}): `open` says {m['status']}"
                               for m in self.missing)
            parts.append("Checked against your last `kb_query.py open`, it does not address "
                         f"these claims:\n{lines}\n"
                         "Give each a disposition (confirmed, weakened, refuted, "
                         "could-not-check or not checked) and the fact behind it. One line "
                         "each is enough, and ones not checked may share a line.")
        stated = [f for f in self.flags or [] if f["kind"] != "overstated"]
        certain = [f for f in self.flags or [] if f["kind"] == "overstated"]
        if stated:
            lines = "\n".join(f"- \"{f['quote']}\": {f['why']}" for f in stated)
            parts.append("A review against the tool results found these statements "
                         f"unsupported:\n{lines}\n"
                         "For each: restate it as what the results show, say it is not "
                         "established, or remove it. If a result you saw does support "
                         "it, cite that result instead.")
        if certain:
            lines = "\n".join(f"- \"{f['quote']}\": {f['why']}" for f in certain)
            parts.append("These are stated with more certainty than the results give:\n"
                         f"{lines}\n"
                         "Keep each claim; state its certainty as the results allow, with "
                         "the reason.")
        if not parts:
            return None
        return ("Before this answer goes out, a check of it found:\n\n"
                + "\n\n".join(parts)
                + "\n\nReply with the complete answer again, revised for these. "
                  "Change nothing else.")

    def as_dict(self) -> dict:
        return {"opens": self.opens, "missing": self.missing, "flags": self.flags}


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


REVIEWER_SYSTEM = """You review a Kubernetes root-cause answer before it is sent to an SRE.
You get the answer and the evidence record: every cluster tool result the
investigator saw, numbered. You did not investigate, and you cannot run tools.

The record holds cluster tool results only. The investigator also read a
diagnostic knowledge base (kb_query.py) and an architecture wiki (wiki.py, for
example which service depends on which); their output is not in the record. Do
not flag a claim for citing them. A cluster fact they are applied to still needs
a result.

Check two properties:
- Valid: every factual claim about this cluster is stated by a result, or follows
  from results by a step a reader can check. Flag a claim no result states, a
  claim a result contradicts, and an inference written as if it were observed.
- Relevant: each conclusion rests on at least one observation, not only on
  general knowledge or defaults.

Rules for reading the evidence, which the investigator also had:
- A change is evidence of a cause only when the onset is observed and the change
  took effect just before it. An event series' first_seen says the failures were
  already happening then, not that they began then. A pod's age, a revision's
  age and a restart count are not an onset, and none of them says the failures
  ran continuously over that span. Without an observed onset, "it ran fine until
  N days ago" is not observed either.
- A container's instance lifetime is how long one instance ran; the restart
  interval also includes the back-off, up to 5 minutes.
- A summary like "the others are healthy" must hold for every workload it covers,
  restart counts included.

Certainty is separate from support. A conclusion the results support, stated with
more certainty than they give ("confirmed" where they make it likely), is
`overstated`: the claim stays and its certainty changes. Never flag a supported
conclusion as contradicted because another explanation remains possible.

Do not flag: recommendations and next steps; general Kubernetes knowledge stated
as such; hypotheses the answer itself marks as unconfirmed; statements about what
the investigator could not check; arithmetic that is right on stated numbers.

Quote each flagged claim verbatim, the shortest span that carries it. Report
nothing rather than a doubtful flag: a wrong flag makes the author delete a true
finding. Call `report` once."""

REPORT_TOOL = {
    "name": "report", "description": "The review's findings.",
    "input_schema": {"type": "object", "required": ["flags"], "properties": {"flags": {
        "type": "array", "items": {"type": "object", "required": ["quote", "kind", "why"],
        "properties": {
            "quote": {"type": "string", "description": "verbatim from the answer"},
            "kind": {"type": "string", "enum": ["unsupported", "contradicted",
                                                "inference-as-observation", "ungrounded",
                                                "overstated"]},
            "evidence": {"type": "string",
                         "description": "result numbers it rests on or contradicts, or 'none'"},
            "why": {"type": "string", "description": "one sentence"}}}}}}}

#: Per result, and in all: a record past this is cut, and says so.
RESULT_CHARS, RECORD_CHARS = 40_000, 600_000


def evidence_text(record: Path) -> str:
    """The evidence record as the reviewer reads it: numbered, one result each."""
    parts, total = [], 0
    for p in sorted(record.glob("*.json")):
        try:
            e = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        text = "\n".join(e.get("text") or [])
        if len(text) > RESULT_CHARS:
            text = text[:RESULT_CHARS] + "\n[cut]"
        part = (f"[{e.get('n')}] {e.get('tool')}({json.dumps(e.get('arguments', {}))})"
                + (" ERROR" if e.get("is_error") else "") + "\n" + text)
        if total + len(part) > RECORD_CHARS:
            parts.append("[the rest of the record is cut]")
            break
        parts.append(part)
        total += len(part)
    return "\n\n".join(parts)


def reviewer(client: Any, answer: str, record: Path, model: str) -> list[dict] | None:
    """The reviewer's flags, or None when it could not run: a failed review
    holds nothing back, since the answer is better sent than lost."""
    try:
        msg = client.messages.create(
            model=model, max_tokens=4000, system=REVIEWER_SYSTEM, tools=[REPORT_TOOL],
            messages=[{"role": "user", "content":
                       f"<evidence>\n{evidence_text(record)}\n</evidence>\n\n"
                       f"<answer>\n{answer}\n</answer>"}])
        calls = [b.input for b in msg.content if b.type == "tool_use"]
        if not calls:
            log.warning("reviewer made no report (stop=%s)", msg.stop_reason)
            return None
        # A quote that is not in the answer cannot be revised; drop it.
        return [f for f in calls[0].get("flags", []) if f.get("quote") and f["quote"] in answer]
    except Exception as exc:  # noqa: BLE001 - never lose the answer to the review
        log.warning("reviewer failed: %s", exc)
        return None


def completeness(answer: str, workspace: Path) -> Review | None:
    """None when the host cannot see the session's workspace (a cloud
    environment, or the orchestrator on another host): nothing to check
    against is not the same as no `open` run."""
    if not workspace.is_dir():
        return None
    records = open_records(workspace)
    return Review(missing=[c for c in owed(records) if not named(answer, c)],
                  opens=len(records))


def check(answer: str, workspace: Path, session_id: str, cfg: Any,
          client: Any = None) -> Review | None:
    """Every check `cfg` (a ReviewConfig) turns on, as one Review."""
    review = completeness(answer, workspace) if cfg.completeness else None
    if cfg.reviewer and client is not None and workspace.is_dir():
        review = review or Review(opens=None)
        review.flags = reviewer(client, answer, workspace / "evidence" / session_id,
                                cfg.reviewer_model)
    return review
