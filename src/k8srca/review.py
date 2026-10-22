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

**The answer checks** (010 §5.2), deterministic, for an answer written with
fact markers: every marker resolves, the prose is within its length, and
every number in it is in a fact it cites. They replace completeness where the
answer is the short one, which no longer places every candidate.

**The light reviewer** (010 §6), a model call and optional
(`review.light_reviewer`). It reads the answer, the statements of the facts
it cites, and the strength `open` gave the leading cause, and nothing else:
not the evidence record. It asks one question of each sentence: does it say
only what its cited facts say? Its flags go back in the revision turn.

Every check reads only the workspace, which holds what the model already
saw. None makes a cluster call (CLAUDE.md).
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
    #: What the answer checks found (010 §5.2), one sentence each; None when
    #: they did not run.
    answer: list[str] | None = None
    #: The light reviewer's flags (010 §6): {"quote", "kind", "why"}; None
    #: when it did not run.
    light: list[dict] | None = None

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
        if self.answer:
            lines = "\n".join(f"- {p}" for p in self.answer)
            parts.append(f"Checked against the facts it cites:\n{lines}")
        if self.light:
            lines = "\n".join(f"- \"{f['quote']}\" ({f['kind']}): {f['why']}"
                               for f in self.light)
            parts.append("A review of each sentence against the facts it cites found:\n"
                         f"{lines}\n"
                         "For each: say only what its facts say, add the marker of the "
                         "fact that states it, or remove it. A confidence word follows "
                         "the ranking's strength: very likely for strong, likely for "
                         "moderate, possible for weak.")
        if not parts:
            return None
        return ("Before this answer goes out, a check of it found:\n\n"
                + "\n\n".join(parts)
                + "\n\nReply with the complete answer again, revised for these. "
                  "Change nothing else. In particular, keep your most likely cause "
                  "and its strength unless a result you can cite changes them: "
                  "these findings are about what the answer says, not new evidence.")

    def as_dict(self) -> dict:
        return {"opens": self.opens, "missing": self.missing, "answer": self.answer,
                "light": self.light}


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


def completeness(answer: str, workspace: Path) -> Review | None:
    """None when the host cannot see the session's workspace (a cloud
    environment, or the orchestrator on another host): nothing to check
    against is not the same as no `open` run."""
    if not workspace.is_dir():
        return None
    records = open_records(workspace)
    return Review(missing=[c for c in owed(records) if not named(answer, c)],
                  opens=len(records))


def answer_problems(answer: str, workspace: Path) -> list[str]:
    """The checks of 010 §5.2 on an answer written with fact markers."""
    from .facts import FactLog
    from .facts.render import MAX_WORDS, render, uncited_numbers

    facts = FactLog(workspace).facts
    r = render(answer, facts)
    out = []
    if r.unresolved:
        out.append(f"{', '.join(r.unresolved)} name no fact in the record. Cite only "
                   f"facts that `kb_query.py facts` lists.")
    if not r.cited:
        out.append("It cites no facts. Mark each finding with the facts it rests on, "
                   "as [F<n>].")
    if r.words > MAX_WORDS:
        out.append(f"It is {r.words} words; the limit is {MAX_WORDS}. Cut what does not "
                   f"bear on the cause, what is ruled out, or the next steps.")
    if numbers := uncited_numbers(answer, facts):
        out.append(f"These figures are in no fact it cites: {', '.join(numbers)}. Cite "
                   f"the fact that states each, or drop the figure: the notes carry "
                   f"the exact values.")
    return out


LIGHT_SYSTEM = """You check a short Kubernetes root-cause answer before it is sent.
You get the answer, which marks each finding with the facts it rests on as
[F<n>], and the statements of those facts, each with its scope in brackets.
You do not see the cluster or anything else the investigator saw.

<knowledge>, when given, is the knowledge base's ranking: the leading cause
and its strength, and the causes it rules out. Its rulings follow from the
facts by rules you do not see. A sentence that gives the leading cause as the
cause, at its strength, or rules out a cause the knowledge lists as ruled out,
with a cited fact, is supported: do not flag it as beyond its facts.

Ask one question of each sentence: does it say only what its cited facts say?
Flag three kinds of sentence:
- `beyond-facts`: it claims more than its facts state. "Every" or "always"
  from a fact about the last one; a count, duration or time no cited fact
  gives; a fact's scope dropped, so that "the oldest event retained" becomes
  "when it began"; a cause or mechanism written as observed when no fact
  observes it.
- `no-marker`: it states something about this cluster as observed and cites
  no fact.
- `overstated`: the confidence word for the most likely cause is higher than
  the knowledge base's strength (very likely for strong, likely for moderate,
  possible for weak).

Do not flag: next steps and suggestions; general Kubernetes knowledge stated as
such; inferences the answer marks as its own ("which fits", "suggests");
statements that something could not be seen or checked; arithmetic that is
right on cited figures.

Quote each flagged sentence verbatim: the shortest span that carries the
claim. Report nothing rather than a doubtful flag: a wrong flag makes the
author delete a true finding. Call `report` once."""

LIGHT_TOOL = {
    "name": "report", "description": "The review's findings.",
    "input_schema": {"type": "object", "required": ["flags"], "properties": {"flags": {
        "type": "array", "items": {"type": "object", "required": ["quote", "kind", "why"],
        "properties": {
            "quote": {"type": "string", "description": "verbatim from the answer"},
            "kind": {"type": "string", "enum": ["beyond-facts", "no-marker", "overstated"]},
            "why": {"type": "string", "description": "one sentence"}}}}}}}


def leader_text(records: list[dict]) -> str:
    """The knowledge base's ranking from `open`'s last run on each symptom: the
    leading causes with their strength, then what it rules out."""
    last = {}
    for r in records:
        last[r.get("symptom")] = r
    rows = [row for r in last.values() for row in r.get("rows", [])]
    lines = [f"most likely: {row['title']} [{row['id']}], {row['status']}"
             for row in rows if row.get("leads")]
    ruled = [f"{row['title']} [{row['id']}]" for row in rows if row["status"] == "ruled-out"]
    if ruled:
        lines.append("ruled out: " + "; ".join(dict.fromkeys(ruled)))
    return "\n".join(lines)


def light_review(client: Any, answer: str, cited: list, model: str,
                 leader: str = "") -> list[dict] | None:
    """The light reviewer's flags, or None when it could not run: a failed
    review holds nothing back."""
    facts = "\n".join(f"[{f.id}] {f.statement}" + (f" ({f.scope})" if f.scope else "")
                      for f in cited) or "(the answer cites no facts)"
    known = f"<knowledge>\n{leader}\n</knowledge>\n\n" if leader else ""
    try:
        msg = client.messages.create(
            model=model, max_tokens=3000, system=LIGHT_SYSTEM, tools=[LIGHT_TOOL],
            messages=[{"role": "user", "content":
                       f"<facts>\n{facts}\n</facts>\n\n{known}<answer>\n{answer}\n</answer>"}])
        calls = [b.input for b in msg.content if b.type == "tool_use"]
        flags = calls[0].get("flags") if calls else None
        if isinstance(flags, str):
            try:
                flags = json.loads(flags)
            except ValueError:
                flags = None
        if not isinstance(flags, list):
            log.warning("light reviewer report malformed: %r", str(flags)[:200])
            return None
        return [f for f in flags if isinstance(f, dict) and f.get("quote")
                and f["quote"] in answer and f.get("kind")]
    except Exception as exc:  # noqa: BLE001 - never lose the answer to the review
        log.warning("light reviewer failed: %s", exc)
        return None


def check(answer: str, workspace: Path, session_id: str, cfg: Any,
          client: Any = None) -> Review | None:
    """Every check `cfg` (a ReviewConfig) turns on, as one Review."""
    review = completeness(answer, workspace) if cfg.completeness else None
    if getattr(cfg, "answer", False) and workspace.is_dir():
        review = review or Review(opens=None)
        review.answer = answer_problems(answer, workspace)
    if getattr(cfg, "light_reviewer", False) and client is not None and workspace.is_dir():
        from .facts import FactLog
        from .facts.render import render

        review = review or Review(opens=None)
        review.light = light_review(client, answer, render(answer, FactLog(workspace).facts).cited,
                                    cfg.reviewer_model, leader_text(open_records(workspace)))
    return review
