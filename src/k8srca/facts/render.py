"""The answer as a reader sees it (design 010 §5.2).

The agent writes plain sentences with markers that name facts, `[F7]`. Before
the answer goes out, the host numbers the markers in order of first use and
appends an Evidence block that prints each cited fact's statement and where it
came from. The model never writes the Evidence block, and readers never see an
id.

The checks of §5.2 run on the same parse: every marker resolves, the prose is
within the length limit, and every number in the prose is in a fact it cites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Fact

#: `[F7]`, and `[F7, F9]` or `[F7][F9]` for several.
MARKER = re.compile(r"\[(F\d+(?:\s*,\s*F\d+)*)\]")

#: The answer's prose limit, Evidence block excluded (010 §5.1).
MAX_WORDS = 300

#: Numbers worth checking: digits, with a decimal part or a unit glued on
#: (`300Mi`, `9m49s`, `97%`, `3d20h`). A bare one-digit number is left out, as
#: are list numbering and headings: "two to four facts" is prose, not a claim.
#: Thousands separators belong to the number: "10,000" is one figure, not 10
#: and 000 (issue #13).
_NUMBER = re.compile(r"(?<![\w.\-/])(\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?)([A-Za-z%]*)")
#: A comma between a digit and a group of three: dropped on both sides, so
#: "10,000" in the prose matches "10000" in a fact, and the reverse.
_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


@dataclass
class Rendered:
    #: What is posted: numbered notes in the prose, the Evidence block below.
    text: str
    #: The prose with numbered notes, without the Evidence block.
    prose: str
    #: The facts cited, in note order.
    cited: list[Fact] = field(default_factory=list)
    #: Markers naming no fact in the log.
    unresolved: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return len(MARKER.sub("", self.prose).split())


def _source(f: Fact) -> str:
    if f.by == "agent":
        n = f.source.get("n")
        return "the agent's reading" + (f" of result {n}" if n is not None else "")
    return f.source.get("tool") or "tool result"


def render(answer: str, facts: list[Fact]) -> Rendered:
    """Number the markers in `answer` against `facts`, and append the notes."""
    by_id = {f.id: f for f in facts}
    order: list[str] = []
    unresolved: list[str] = []

    def number(m: re.Match) -> str:
        notes = []
        for fid in (x.strip() for x in m.group(1).split(",")):
            if fid not in by_id:
                if fid not in unresolved:
                    unresolved.append(fid)
                continue
            if fid not in order:
                order.append(fid)
            notes.append(f"[{order.index(fid) + 1}]")
        return "".join(notes)

    prose = MARKER.sub(number, answer).rstrip()
    cited = [by_id[fid] for fid in order]
    if not cited:
        return Rendered(prose, prose, [], unresolved)
    notes = "\n".join(f"[{n}] {f.statement} ({_source(f)})" for n, f in enumerate(cited, 1))
    return Rendered(f"{prose}\n\n*Evidence*\n{notes}", prose, cited, unresolved)


def drop_unresolved(answer: str, facts: list[Fact]) -> tuple[str, list[str]]:
    """`answer` with every marker that names no fact in `facts` taken out, and
    the ids taken out.

    The revision turn is not checked again, so a marker it leaves unresolved
    would be posted looking like a citation (#49). A marker that resolves is
    kept, also inside a group: `[F1, F7]` with only F7 recorded becomes `[F7]`.
    A note the agent wrote for a dropped marker goes too: a line, or the rest
    of a line after a sentence, that starts with it ("[F1] the investigator's
    listing").
    """
    by_id = {f.id for f in facts}
    dropped: list[str] = []

    def unresolved(group: str) -> list[str]:
        return [x.strip() for x in group.split(",") if x.strip() not in by_id]

    def note(m: re.Match) -> str:
        ids = unresolved(m.group(2))
        if len(ids) != len(m.group(2).split(",")):
            return m.group(0)  # it names a recorded fact: not a note of ours to drop
        dropped.extend(i for i in ids if i not in dropped)
        return m.group(1)

    # A note: the marker starting a line and text after it; or starting the
    # last sentence of a line ("... 27 [F1]. [F1] Pod count by namespace.").
    # Mid-line, only the last sentence, so no claim after it is lost.
    text = re.sub(r"^()[ \t]*[-*]?[ \t]*\[(F\d+(?:\s*,\s*F\d+)*)\][:.]?[ \t]+\S[^\n]*",
                  note, answer, flags=re.MULTILINE)
    text = re.sub(r"(?<=[.!?])()[ \t]+\[(F\d+(?:\s*,\s*F\d+)*)\][:.]?[ \t]+"
                  r"[^\n.!?]*(?:[.!?](?![ \t]+\S))?[ \t]*$",
                  note, text, flags=re.MULTILINE)

    def inline(m: re.Match) -> str:
        ids = [x.strip() for x in m.group(1).split(",")]
        keep = [i for i in ids if i in by_id]
        dropped.extend(i for i in ids if i not in by_id and i not in dropped)
        return f"[{', '.join(keep)}]" if keep else ""

    text = MARKER.sub(inline, text)
    if not dropped:
        return answer, []
    # Tidy what removal leaves: a space before punctuation, runs of spaces,
    # trailing spaces and the blank lines a dropped note line leaves.
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, dropped


def render_workspace(answer: str, workspace) -> str:
    """The answer as posted, from the session's own facts log; unchanged when
    the workspace is not on this host."""
    from .log import FactLog

    try:
        facts = FactLog(workspace).facts
    except OSError:
        return answer
    # No log here is not a log of nothing: leave the markers for a reader to see.
    return render(answer, facts).text if facts else answer


def uncited_numbers(answer: str, facts: list[Fact]) -> list[str]:
    """Numbers in the prose that no fact the answer cites states.

    Checked against the facts the whole answer cites, not the sentence: "300Mi"
    passes where any cited fact says 300Mi. A number in a next step, such as a
    limit to try, is advice and not a claim, so lines under a next-steps heading
    are not checked.
    """
    cited = render(answer, facts).cited
    said = _SEPARATOR.sub("", " ".join(f"{f.statement} {f.scope}" for f in cited))
    out = []
    prose = MARKER.sub("", answer)
    for line in _claims(prose):
        for m in _NUMBER.finditer(line):
            token = m.group(0)
            if len(m.group(1)) == 1 and not m.group(2):
                continue
            figure = m.group(1).replace(",", "")
            if not re.search(rf"(?<![\d.]){re.escape(figure)}(?![\d])", said):
                if token not in out:
                    out.append(token)
    return out


def _claims(prose: str) -> list[str]:
    """The lines that state findings: everything before a next-steps heading."""
    out = []
    for line in prose.splitlines():
        if re.match(r"\W*next steps?\b", line.strip(), re.IGNORECASE):
            break
        out.append(line)
    return out
