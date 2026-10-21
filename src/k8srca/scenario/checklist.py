"""Checklist grading (design 010 §7).

Each scenario's `checklist.yaml` lists statements an answer should make
(`expect: present`) or should not (`expect: absent`). The grader is asked one
narrow question per item -- does the answer make this statement? -- and never
told which way the item is expected, so it reads the answer rather than
looking for what it was told to find. Whether that meets the item is settled
here, from the file.

Its reference is the checklist and the answer's cited facts: a few thousand
tokens, not the capture. The rubric grader (`grader.py`) stays as it was, for
comparisons with runs before 010.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .grader import GRADER_MODEL

Group = Literal["cause", "ruled_out", "traps", "gaps", "next"]
Says = Literal["yes", "no", "unclear"]

#: The 008 dimensions, kept as groups of items so the history stays readable.
GROUPS: tuple[str, ...] = ("cause", "ruled_out", "traps", "gaps", "next")


class Item(BaseModel):
    id: str
    expect: Literal["present", "absent"]
    group: Group
    #: The statement, in one sentence.
    statement: str
    #: Phrasings that make the statement.
    accept: list[str] = Field(default_factory=list)
    #: For an absent item: the tempting wrong version, as an answer would put it.
    tempting: list[str] = Field(default_factory=list)
    #: Nearby things an answer may say that do NOT make the statement.
    allowed: list[str] = Field(default_factory=list)


class Checklist(BaseModel):
    id: str
    items: list[Item]

    @classmethod
    def load(cls, path: Path) -> "Checklist":
        checklist = cls.model_validate(yaml.safe_load(path.read_text()))
        ids = [i.id for i in checklist.items]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"{path}: duplicate item ids {dupes}")
        return checklist


class ItemVerdict(BaseModel):
    id: str
    says: Says = Field(description="yes: the answer makes the statement. no: it does "
                                   "not. unclear: it half-makes it, or two of its "
                                   "sentences disagree")
    quote: str = Field(description="The answer's sentence you relied on, exactly. "
                                   "Empty when it says nothing on this")


class ChecklistGrade(BaseModel):
    items: list[ItemVerdict]


def met(item: Item, verdict: ItemVerdict | None) -> bool:
    """`unclear`, or no verdict at all, meets nothing."""
    if verdict is None or verdict.says == "unclear":
        return False
    return (verdict.says == "yes") == (item.expect == "present")


def score(checklist: Checklist, grade: ChecklistGrade) -> dict[str, Any]:
    """Items met out of items listed, overall and per group."""
    by_id = {v.id: v for v in grade.items}
    out: dict[str, Any] = {"met": 0, "listed": len(checklist.items), "groups": {},
                           "missed": []}
    for item in checklist.items:
        ok = met(item, by_id.get(item.id))
        g = out["groups"].setdefault(item.group, [0, 0])
        g[0] += ok
        g[1] += 1
        out["met"] += ok
        if not ok:
            out["missed"].append(item.id)
    return out


SYSTEM = """You are checking whether a Kubernetes root-cause answer makes each of a
list of statements.

For each item, answer one question: does the answer make this statement?
- `yes`: some sentence in the answer says it, in any wording. `accept` lists
  phrasings that count; `tempting` lists how an answer typically puts it.
  Either is enough, and other wordings with the same meaning count too.
- `no`: nothing in the answer says it. Saying something nearby is not saying
  it; `allowed` lists nearby things that do not count.
- `unclear`: only when two of the answer's sentences disagree on it. Not for
  "I am unsure whether the statement is true": you are not judging truth,
  only what the answer says.

Rules:
- A statement offered as likely, possible, plausible or a guess is made. The
  items ask what the answer says, not how sure it is, unless the item itself
  is about certainty.
- Judge each item on its own. Do not reason about which way an item ought to
  go, or about the scenario.
- A statement made and then retracted in the same answer ("at first this
  looked like X, but it is not") is not made.
- A statement the answer attributes to a source it quotes, and endorses, is
  made. One it quotes in order to reject is not.
- Quote the sentence you relied on exactly. For `no`, leave the quote empty.
- Return one verdict for every item id, in the order given."""


def _items_for_grader(checklist: Checklist) -> list[dict]:
    """The items without `expect` or `group`: the grader is not told which way
    an item should go."""
    return [item.model_dump(include={"id", "statement", "accept", "tempting", "allowed"},
                            exclude_defaults=True)
            for item in checklist.items]


def apparatus_digest(model: str = GRADER_MODEL) -> str:
    """Identity of the checklist grader, for pinning a measurement to it (process §5).
    The checklists themselves are pinned separately, per scenario."""
    material = json.dumps({"system": SYSTEM, "schema": ChecklistGrade.model_json_schema(),
                           "model": model}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def checklist_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def build_request(checklist: Checklist, question: str, answer: str,
                  facts: list[str] | None = None) -> dict:
    """The checklist goes in `system` behind a cache breakpoint, the answer in
    `messages`: every answer to one scenario reads the same prefix."""
    reference = json.dumps({"items": _items_for_grader(checklist)}, indent=1)
    content = f"<question>\n{question.strip()}\n</question>\n\n<answer>\n{answer}\n</answer>\n\n"
    if facts:
        content += "<cited_facts>\n" + "\n".join(facts) + "\n</cited_facts>\n\n"
    content += "Give a verdict for every item in <checklist>."
    return {
        "system": [
            {"type": "text", "text": SYSTEM},
            {"type": "text", "text": f"<checklist>\n{reference}\n</checklist>",
             "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": content}],
    }


def grade(checklist: Checklist, question: str, answer: str, *, client: Any,
          model: str = GRADER_MODEL, facts: list[str] | None = None) -> ChecklistGrade:
    """One grading. Items the grader skipped come back `unclear`, so they meet
    nothing and show as a split rather than vanishing."""
    request = build_request(checklist, question, answer, facts)
    response = client.messages.parse(
        model=model, max_tokens=16000, thinking={"type": "adaptive"},
        system=request["system"], messages=request["messages"],
        output_format=ChecklistGrade)
    given = {v.id: v for v in response.parsed_output.items}
    return ChecklistGrade(items=[given.get(i.id) or ItemVerdict(id=i.id, says="unclear", quote="")
                                 for i in checklist.items])


def agreement(gradings: list[list[ChecklistGrade]], checklist: Checklist) -> dict[str, dict]:
    """Per item, how often a grading agrees with the majority for its answer.

    `gradings` is one list per answer, of that answer's repeated gradings. An
    item's agreement is the share of all its gradings that match their
    answer's modal verdict (010 §7: 97% or more). `splits` names the answers
    whose gradings were not unanimous, by index.
    """
    out = {}
    for item in checklist.items:
        agree = total = 0
        splits = []
        for n, runs in enumerate(gradings):
            verdicts = [next(v.says for v in g.items if v.id == item.id) for g in runs]
            modal = Counter(verdicts).most_common(1)[0][1]
            agree += modal
            total += len(verdicts)
            if modal < len(verdicts):
                splits.append(n)
        out[item.id] = {"agreement": agree / total if total else None,
                        "gradings": total, "splits": splits}
    return out
