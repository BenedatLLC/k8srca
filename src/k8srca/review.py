"""Checks the host runs on a final answer before it goes out (design 007 §6.1).

The knowledge prints what an answer owes, and answers still leave it out: in
the seventh gate (007 §11.2) both runs that dropped memory-leak and
workload-spike had read `open`'s line naming them. Text the agent reads
reaches the step, not the answer. So the host checks the answer itself, and
sends what it found back to the session as one revision turn.

**Completeness**, deterministic. Each `kb_query.py open` run leaves its rows
beside the evidence record:

    <workdir>/evidence/open/<n>.json

The answer owes a place (most likely, not ruled out, ruled out; 008 §3) to
every candidate `open` listed, and to every sub-cause it ruled out or holds at
moderate or strong support (008 §4.2), or finds conflicting. Open and weak
sub-causes are left out: there are dozens, a weak one is most often a
background condition that fits, and naming each would bury the answer. A claim
counts as addressed when the answer names its id or its title.

**The reviewer**, a model call and optional (`review.reviewer`). The checks
above cannot see an inference, such as "crash-looping since the rollout" from
a pod's age, the largest kind of unsupported claim (007 §11.2). One call
reads the answer against the evidence record and flags claims no result
supports. Certainty is its own kind, `overstated`: in the offline eval the
reviewer disputed the right cause because the reason read Error, and an
answer that dropped the cause over it would be worse off. An `overstated`
flag moves a strength down one grade and never moves a claim between
sections (008 §7). The reviewer reads `open`'s last standings, so it holds
the answer to the standard the knowledge computed rather than to its own.

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

#: Standings that make a sub-cause owed, not only a top-level candidate.
DECIDED = ("strong", "moderate", "ruled-out", "conflicting")


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
                         "every sub-cause it rules out or supports, a place: most likely, "
                         "not ruled out, or ruled out. Where `open` leaves a candidate "
                         "open or could-not-check, that says the facts do not decide it, "
                         "not that it is unlikely.")
        elif self.missing:
            lines = "\n".join(f"- {m['id']} ({m['title']}): `open` says {m['status']}"
                               for m in self.missing)
            parts.append("Checked against your last `kb_query.py open`, it does not address "
                         f"these claims:\n{lines}\n"
                         "Give each a place (most likely, not ruled out, or ruled out) and "
                         "the fact behind it. One line each is enough, and ones nothing "
                         "pointed at may share a line under not ruled out.")
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
                         "Keep each claim in the place you gave it: most likely stays most "
                         "likely, and nothing moves to not ruled out or could not check. "
                         "Lower its strength one grade (strong to moderate, moderate to "
                         "weak; a weak one keeps weak and says what it rests on), replace "
                         "words like \"confirmed\" with that strength, and say why.")
        if not parts:
            return None
        return ("Before this answer goes out, a check of it found:\n\n"
                + "\n\n".join(parts)
                + "\n\nReply with the complete answer again, revised for these. "
                  "Change nothing else. In particular, keep your most likely cause "
                  "and its strength unless a result you can cite changes them: "
                  "these findings are about what the answer says, not new evidence.")

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
            # `owed` is kb_query's own judgement (009 §7.5); older rows lack it.
            due = (row["owed"] if "owed" in row
                   else row.get("depth", 0) == 0 or row.get("status") in DECIDED)
            if due:
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
`overstated`: the claim stays where the answer put it, and its strength (strong,
moderate or weak) drops one grade. The answer ranks causes; it is not required to
prove the one it puts first. Never flag a supported
conclusion as contradicted because another explanation remains possible.

Overstated is about the strength of a cause the answer keeps: most likely, or
not ruled out. A ruling-out is a place, not a strength, and is never overstated.
A ruling-out no result supports is unsupported, and one a result contradicts is
contradicted, each with the result that shows it.

The knowledge base's standings, when given, are the strength its rule computed
from the facts the investigator recorded. A strength at or below its standing is
not overstated. One above it is overstated unless the answer gives a reason
from the results. A standing is not evidence against a ruling-out: the facts
recorded may leave out the one that rules a cause out, and a standing lifted
from a sub-cause is that sub-cause's support, not the cause's own.

A fact covers what the record retains. "Killed in every retained life" is
supported when every termination the record shows was a kill; "killed on every
life", with no such qualifier, claims a history the record does not hold, and
is overstated, not unsupported.

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


def standings_text(records: list[dict]) -> str:
    """`open`'s last standings on each facts file, one line a claim: the
    candidates, and the sub-causes it decided or found leading."""
    last = {}
    for r in records:
        last[(r.get("symptom"), r.get("facts"))] = r
    lines = []
    for (symptom, facts), r in last.items():
        lines.append(f"{symptom} ({facts or 'no facts file'}):")
        for row in r.get("rows", []):
            if row.get("depth", 0) == 0 or row.get("status") in DECIDED or row.get("leads"):
                lead = ", leads" if row.get("leads") else ""
                # A grade lifted from a sub-cause is that sub-cause's support,
                # not the claim's own: it said "weak" for an application crash
                # the results had ruled out, and the review un-ruled it (008 §8.3).
                via = (f", lifted from its sub-cause {row['via']}, not its own"
                       if row.get("via") else "")
                lines.append(f"{'  ' * (row.get('depth', 0) + 1)}- {row['id']} "
                             f"({row['title']}): {row['status']}{lead}{via}")
    return "\n".join(lines)


def reviewer(client: Any, answer: str, record: Path, model: str,
             standings: str = "") -> list[dict] | None:
    """The reviewer's flags, or None when it could not run: a failed review
    holds nothing back, since the answer is better sent than lost."""
    known = (f"<standings>\n{standings}\n</standings>\n\n" if standings else "")
    try:
        msg = client.messages.create(
            model=model, max_tokens=4000, system=REVIEWER_SYSTEM, tools=[REPORT_TOOL],
            messages=[{"role": "user", "content":
                       f"<evidence>\n{evidence_text(record)}\n</evidence>\n\n"
                       f"{known}<answer>\n{answer}\n</answer>"}])
        calls = [b.input for b in msg.content if b.type == "tool_use"]
        if not calls:
            log.warning("reviewer made no report (stop=%s)", msg.stop_reason)
            return None
        flags = calls[0].get("flags")
        if isinstance(flags, str):
            # Seen once in 90 offline calls: the list sent as a JSON string.
            try:
                flags = json.loads(flags)
            except ValueError:
                flags = None
        if not isinstance(flags, list):
            log.warning("reviewer report malformed: %r", str(flags)[:200])
            return None
        # A quote that is not in the answer cannot be revised; drop it.
        return [f for f in flags if isinstance(f, dict) and f.get("quote")
                and f["quote"] in answer and f.get("kind")]
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
                                cfg.reviewer_model, standings_text(open_records(workspace)))
    return review
