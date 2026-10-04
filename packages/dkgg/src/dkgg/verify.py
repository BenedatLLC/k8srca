"""`dkgg check`: the deterministic checks on a built wiki (docs/design.md §7.1).

Each row is something a wiki must satisfy whatever wrote its pages. For a wiki
rendered from the graph alone (migration step 2) they hold by construction;
they exist for when synthesis writes pages (step 3), and pages can drift from
the graph they describe.

Not here yet: the review rows (`review.yaml`, step 3), the diagnostic-guidance
row, and the consistency judge.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

#: A citation: `[docs: cart/index.md]`, `[chart: ...]`, `[derived: ...]`.
CITATION = re.compile(r"\[(docs|chart|derived|review|env): [^\]]+\]")

#: Sections whose every statement must carry a citation. "Connections" is not
#: among them: it states each edge's evidence inline, by construction.
CITED_SECTIONS = {"Documentation", "Declared configuration", "Purpose", "If it fails"}

LINK = re.compile(r"\]\(([^)\s]+\.md)\)")


@dataclass(frozen=True)
class Finding:
    row: str            # inventory | edges | links | citations | graph
    detail: str

    def __str__(self) -> str:
        return f"{self.row:<10} {self.detail}"


def check(wiki: Path) -> list[Finding]:
    graph_path = wiki / "graph.json"
    if not graph_path.exists():
        return [Finding("graph", f"{graph_path} is missing: not a built wiki")]
    g = json.loads(graph_path.read_text())
    findings: list[Finding] = []
    findings += _inventory(wiki, g)
    findings += _graph(g)
    findings += _edges(wiki, g)
    findings += _links(wiki)
    findings += _citations(wiki)
    return findings


def _inventory(wiki: Path, g: dict) -> list[Finding]:
    pages = {p.stem for p in (wiki / "components").glob("*.md")}
    components = set(g["components"])
    return ([Finding("inventory", f"{c} has no page") for c in sorted(components - pages)]
            + [Finding("inventory", f"components/{p}.md describes no component")
               for p in sorted(pages - components)])


def _graph(g: dict) -> list[Finding]:
    components = set(g["components"])
    out = []
    for e in g["edges"]:
        for end in (e["from"], e["to"]):
            if end not in components:
                out.append(Finding("graph", f"edge {e['from']} -> {e['to']} names "
                                            f"{end!r}, which is not a component"))
        if not e.get("evidence"):
            out.append(Finding("graph", f"edge {e['from']} -> {e['to']} has no evidence"))
    return out


def _page_edges(text: str) -> set[str]:
    line = next((ln for ln in text.splitlines() if ln.startswith("**Connects:**")), "")
    return set(re.findall(r"\[([^\]]+)\]\([^)]+\.md\)", line))


def _edges(wiki: Path, g: dict) -> list[Finding]:
    out = []
    for name in g["components"]:
        page = wiki / "components" / f"{name}.md"
        if not page.exists():
            continue
        stated = _page_edges(page.read_text())
        actual = {e["to"] for e in g["edges"] if e["from"] == name}
        out += [Finding("edges", f"{name}'s page names {t}, which is not an edge")
                for t in sorted(stated - actual)]
        out += [Finding("edges", f"{name}'s page omits its edge to {t}")
                for t in sorted(actual - stated)]
    return out


def _links(wiki: Path) -> list[Finding]:
    out = []
    for page in sorted(wiki.rglob("*.md")):
        for target in LINK.findall(page.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            if not (page.parent / target).exists():
                out.append(Finding("links", f"{page.relative_to(wiki)} links {target}, "
                                            f"which does not exist"))
    return out


def _citations(wiki: Path) -> list[Finding]:
    out = []
    for page in sorted((wiki / "components").glob("*.md")):
        for section, statement in _statements(page.read_text()):
            if section in CITED_SECTIONS and not CITATION.search(statement):
                out.append(Finding("citations", f"{page.relative_to(wiki)} ({section}): "
                                                f"uncited: {statement[:80]!r}"))
    return out


def _statements(text: str):
    """(section, statement) for each paragraph or bullet outside quotes and code.

    A quoted block counts as cited when the paragraph right after it is a
    citation, which is how a page quotes the documentation.
    """
    section, buf, fenced = None, [], False
    blocks: list[tuple[str | None, str]] = []

    def flush():
        if buf:
            blocks.append((section, " ".join(buf)))
            buf.clear()

    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            flush()
            continue
        if fenced:
            continue
        if line.startswith("## "):
            flush()
            section = line[3:].strip()
            continue
        if not line.strip() or line.startswith("#"):
            flush()
            continue
        if line.startswith(">"):
            flush()
            blocks.append((section, "> quoted"))
            continue
        if line.startswith("- "):
            flush()
        buf.append(line.strip())
    flush()

    for i, (sec, stmt) in enumerate(blocks):
        if stmt == "> quoted":
            continue
        if CITATION.fullmatch(stmt.strip()) and i and blocks[i - 1][1] == "> quoted":
            continue        # the citation for the quote just above
        yield sec, stmt
