#!/usr/bin/env python3
"""The mechanical half of the update-todo skill: what GitHub and the indexes say.

    python3 .claude/skills/update-todo/todo_state.py report
    python3 .claude/skills/update-todo/todo_state.py graph --write docs/todo.md

`report` prints the state of the milestone's issues and every inconsistency
code can find. `graph` draws the milestone's issues as a Mermaid diagram and,
with --write, replaces the block between the markers in docs/todo.md.

It reads GitHub through `gh` and writes nothing there. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

START = re.compile(r'<!-- milestone-graph:start(?: milestone="([^"]*)")? -->')
END = "<!-- milestone-graph:end -->"

QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    milestones(first: 50, states: OPEN) { nodes { title dueOn } }
    issues(first: 100, after: $cursor, states: [OPEN, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title state stateReason
        milestone { title }
        assignees(first: 10) { nodes { login } }
        labels(first: 20) { nodes { name } }
        parent { number repository { nameWithOwner } }
        blockedBy(first: 50) {
          nodes { number title state repository { nameWithOwner } }
        }
      }
    }
  }
}
"""


@dataclass
class Issue:
    number: int
    title: str
    closed: bool
    dropped: bool = False            # closed as not planned
    milestone: str | None = None
    assignees: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    parent: int | None = None        # a parent in this repository
    blocked_by: list[int] = field(default_factory=list)       # in this repository
    external_blockers: list[tuple[str, bool]] = field(default_factory=list)  # (ref, closed)


def parse_issues(nodes: list[dict], repo: str) -> dict[int, Issue]:
    """GraphQL issue nodes as Issues, keyed by number."""
    out: dict[int, Issue] = {}
    for n in nodes:
        issue = Issue(
            number=n["number"],
            title=n["title"],
            closed=n["state"] == "CLOSED",
            dropped=n.get("stateReason") == "NOT_PLANNED",
            milestone=(n.get("milestone") or {}).get("title"),
            assignees=[a["login"] for a in n["assignees"]["nodes"]],
            labels=[label["name"] for label in n["labels"]["nodes"]],
        )
        parent = n.get("parent")
        if parent and parent["repository"]["nameWithOwner"] == repo:
            issue.parent = parent["number"]
        for b in n["blockedBy"]["nodes"]:
            if b["repository"]["nameWithOwner"] == repo:
                issue.blocked_by.append(b["number"])
            else:
                ref = f'{b["repository"]["nameWithOwner"].split("/")[-1]}#{b["number"]}'
                issue.external_blockers.append((ref, b["state"] == "CLOSED"))
        issue.blocked_by.sort()
        out[issue.number] = issue
    return out


def fetch(repo: str) -> tuple[dict[int, Issue], list[dict]]:
    """Every issue in `repo`, and its open milestones."""
    owner, name = repo.split("/")
    nodes: list[dict] = []
    cursor = None
    while True:
        cmd = ["gh", "api", "graphql", "-f", f"query={QUERY}",
               "-f", f"owner={owner}", "-f", f"name={name}"]
        if cursor:
            cmd += ["-f", f"cursor={cursor}"]
        data = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
        repository = data["data"]["repository"]
        nodes += repository["issues"]["nodes"]
        page = repository["issues"]["pageInfo"]
        if not page["hasNextPage"]:
            return parse_issues(nodes, repo), repository["milestones"]["nodes"]
        cursor = page["endCursor"]


def current_repo() -> str:
    out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


def choose_milestone(asked: str | None, marker: str | None, open_milestones: list[dict]) -> str:
    """The milestone to draw: the one asked for, else the marker's, else the
    one open milestone, else the open one due soonest."""
    if asked:
        return asked
    if marker:
        return marker
    if len(open_milestones) == 1:
        return open_milestones[0]["title"]
    dated = sorted((m for m in open_milestones if m.get("dueOn")), key=lambda m: m["dueOn"])
    if dated:
        return dated[0]["title"]
    raise SystemExit("cannot tell which milestone is current: pass --milestone")


def status(issue: Issue, issues: dict[int, Issue]) -> str:
    """done, dropped, blocked, in progress (claimed and free to move) or ready."""
    if issue.closed:
        return "dropped" if issue.dropped else "done"
    open_blockers = [b for b in issue.blocked_by if b in issues and not issues[b].closed]
    if open_blockers or any(not closed for _, closed in issue.external_blockers):
        return "blocked"
    return "in progress" if issue.assignees else "ready"


# --- the diagram -----------------------------------------------------------

def _label(text: str, width: int = 30, limit: int = 64) -> str:
    text = re.sub(r'[`"<>\[\]{}|]', "'", " ".join(text.split()))
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return "<br/>".join(textwrap.wrap(text, width))


def find_cycle(edges: set[tuple[int, int]]) -> list[int] | None:
    """A dependency cycle among `edges` (blocker, blocked), or None."""
    after: dict[int, list[int]] = {}
    for a, b in sorted(edges):
        after.setdefault(a, []).append(b)
    state: dict[int, int] = {}

    def visit(node: int, path: list[int]) -> list[int] | None:
        state[node] = 1
        for nxt in after.get(node, []):
            if state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt] if nxt in path else [node, nxt]
            if nxt not in state:
                found = visit(nxt, path + [nxt])
                if found:
                    return found
        state[node] = 2
        return None

    for start in sorted(after):
        if start not in state:
            found = visit(start, [start])
            if found:
                return found
    return None


def reduce_edges(edges: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Drop an edge another path already implies: if A blocks B and B blocks
    C, the line from A to C says nothing more. Left alone when there is a cycle."""
    if find_cycle(edges):
        return set(edges)
    after: dict[int, set[int]] = {}
    for a, b in edges:
        after.setdefault(a, set()).add(b)

    def reaches(src: int, dst: int, skip: tuple[int, int]) -> bool:
        stack, seen = [src], set()
        while stack:
            node = stack.pop()
            for nxt in after.get(node, ()):
                if (node, nxt) == skip or nxt in seen:
                    continue
                if nxt == dst:
                    return True
                seen.add(nxt)
                stack.append(nxt)
        return False

    return {(a, b) for a, b in edges if not reaches(a, b, (a, b))}


def render_graph(issues: dict[int, Issue], milestone: str) -> str:
    """The milestone's issues as a Mermaid flowchart: an arrow runs from an
    issue to the issue it blocks, and a box groups a parent with its sub-issues."""
    inside = {n for n, i in issues.items() if i.milestone == milestone}
    # Open blockers outside the milestone are drawn too: they hold it up.
    outside = {b for n in inside for b in issues[n].blocked_by
               if b in issues and b not in inside and not issues[b].closed}
    shown = inside | outside
    edges = reduce_edges({(b, n) for n in inside for b in issues[n].blocked_by if b in shown})

    lines = ["flowchart TD"]

    def node(n: int, indent: str) -> str:
        issue = issues[n]
        state = status(issue, issues)
        tail = state + "".join(f" · @{a}" for a in issue.assignees)
        if n in outside:
            tail += f" · {issue.milestone or 'no milestone'}"
            cls = "outside"
        else:
            cls = {"in progress": "active"}.get(state, state)
        return f'{indent}i{n}["#{n} {_label(issue.title)}<br/><i>{tail}</i>"]:::{cls}'

    groups: dict[int, list[int]] = {}
    for n in sorted(shown):
        parent = issues[n].parent
        if parent is not None and parent in issues:
            groups.setdefault(parent, []).append(n)
    grouped = {n for members in groups.values() for n in members} | (set(groups) & shown)

    for n in sorted(shown - grouped):
        lines.append(node(n, "  "))
    for parent in sorted(groups):
        title = f"#{parent} and its sub-issues"
        if parent not in shown:
            where = issues[parent].milestone or "no milestone"
            title = f"Sub-issues of #{parent} {_label(issues[parent].title, 60, 60)} ({where})"
        lines.append(f'  subgraph g{parent}["{title}"]')
        if parent in shown:
            lines.append(node(parent, "    "))
        lines += [node(n, "    ") for n in groups[parent]]
        lines.append("  end")

    ext = sorted({(ref, n) for n in inside for ref, closed in issues[n].external_blockers
                  if not closed})
    for ref in sorted({ref for ref, _ in ext}):
        lines.append(f'  x{re.sub(r"[^A-Za-z0-9]", "_", ref)}["{ref}<br/><i>open, another repository</i>"]:::outside')
    lines += [f"  i{a} --> i{b}" for a, b in sorted(edges)]
    lines += [f'  x{re.sub(r"[^A-Za-z0-9]", "_", ref)} --> i{n}' for ref, n in ext]
    lines += [
        "  classDef done fill:#d3f9d8,stroke:#2f9e44,color:#1b1b1b",
        "  classDef dropped fill:#e9ecef,stroke:#868e96,color:#1b1b1b",
        "  classDef active fill:#d0ebff,stroke:#1971c2,color:#1b1b1b",
        "  classDef ready fill:#fff3bf,stroke:#e67700,color:#1b1b1b",
        "  classDef blocked fill:#f1f3f5,stroke:#868e96,color:#1b1b1b,stroke-dasharray: 5 5",
        "  classDef outside fill:#ffffff,stroke:#868e96,color:#1b1b1b,stroke-dasharray: 2 4",
    ]
    return "\n".join(lines)


def graph_block(issues: dict[int, Issue], milestone: str, today: str) -> str:
    """The marked block that goes into docs/todo.md."""
    counts: dict[str, int] = {}
    for issue in issues.values():
        if issue.milestone == milestone:
            state = status(issue, issues)
            counts[state] = counts.get(state, 0) + 1
    order = ["done", "in progress", "ready", "blocked", "dropped"]
    summary = ", ".join(f"{counts[s]} {s}" for s in order if s in counts)
    return "\n".join([
        f'<!-- milestone-graph:start milestone="{milestone}" -->',
        f"<!-- Generated by .claude/skills/update-todo/todo_state.py on {today}. "
        "Do not edit by hand. -->",
        "",
        f"{sum(counts.values())} issues: {summary}.",
        "",
        "```mermaid",
        render_graph(issues, milestone),
        "```",
        "",
        END,
    ])


def replace_block(text: str, block: str) -> str:
    """`text` with the marked block replaced. Raises if the markers are absent."""
    start = START.search(text)
    end = text.find(END, start.end()) if start else -1
    if not start or end < 0:
        raise ValueError("no milestone-graph markers found")
    return text[:start.start()] + block + text[end + len(END):]


def marker_milestone(text: str) -> str | None:
    m = START.search(text)
    return m.group(1) if m else None


# --- the report ------------------------------------------------------------

def issue_findings(issues: dict[int, Issue], milestone: str) -> list[str]:
    """What is inconsistent among the issues themselves."""
    out: list[str] = []
    inside = {n for n, i in issues.items() if i.milestone == milestone}
    children: dict[int, list[int]] = {}
    for n, issue in issues.items():
        if issue.parent in issues:
            children.setdefault(issue.parent, []).append(n)

    for n in sorted(inside):
        issue = issues[n]
        for b in issue.blocked_by:
            if b in issues and not issues[b].closed and b not in inside and not issue.closed:
                where = issues[b].milestone or "no milestone"
                out.append(f"#{n} is in {milestone} but is blocked by #{b}, which is open and in {where}.")
        if issue.closed:
            continue
        if issue.assignees and status(issue, issues) == "blocked" and n not in children:
            out.append(f"#{n} is assigned to {', '.join('@' + a for a in issue.assignees)} "
                       "but is blocked: a claim says work has started.")
        if issue.parent in issues and issue.parent not in inside and not issues[issue.parent].closed:
            where = issues[issue.parent].milestone or "no milestone"
            out.append(f"#{n} is in {milestone} but its parent #{issue.parent} is in {where}.")
    for parent, kids in sorted(children.items()):
        open_kids = [k for k in sorted(kids) if not issues[k].closed]
        if issues[parent].closed and open_kids:
            out.append(f"#{parent} is closed but its sub-issues "
                       f"{', '.join('#%d' % k for k in open_kids)} are open.")
        if not issues[parent].closed and not open_kids:
            out.append(f"#{parent} is open but all of its sub-issues are closed.")
        if parent in inside:
            stray = [k for k in open_kids if k not in inside]
            if stray:
                out.append(f"#{parent} is in {milestone} but its open sub-issues "
                           f"{', '.join('#%d' % k for k in stray)} are not.")
    cycle = find_cycle({(b, n) for n, i in issues.items() for b in i.blocked_by if b in issues})
    if cycle:
        out.append("Dependency cycle: " + " blocks ".join(f"#{n}" for n in cycle) + ".")
    return out


def todo_findings(text: str, issues: dict[int, Issue], repo: str) -> list[str]:
    """Where docs/todo.md disagrees with the issues' state."""
    out: list[str] = []
    link = re.compile(r"\[#(\d+)\]\(https://github\.com/" + re.escape(repo) + r"/issues/\d+\)")
    in_table: set[int] = set()      # the issue a row is about (its first cell)
    in_rows: set[int] = set()       # named anywhere in a row, as a parent's row names its sub-issues
    mentions: dict[int, list[int]] = {}
    graph = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if START.search(line):
            graph = True
        if graph:
            graph = END not in line
            continue
        first = line.split("|")[1] if line.startswith("| [#") else ""
        in_table |= {int(n) for n in link.findall(first)}
        if first:
            in_rows |= {int(n) for n in link.findall(line)}
        for n in link.findall(line):
            mentions.setdefault(int(n), []).append(lineno)
    for n in sorted(in_table):
        if n in issues and issues[n].closed:
            out.append(f"todo.md's open-tickets table lists #{n}, which is closed.")
    for n, issue in sorted(issues.items()):
        if not issue.closed and n not in in_rows:
            out.append(f"#{n} is open but todo.md's open-tickets table does not name it: {issue.title}")
    for n, lines in sorted(mentions.items()):
        if n in issues and issues[n].closed:
            where = ", ".join(str(x) for x in sorted(set(lines)))
            out.append(f"todo.md mentions closed issue #{n} on line {where}: "
                       "check the wording does not say it is still to do.")
        if n not in issues:
            out.append(f"todo.md links #{n}, which is not an issue in {repo}.")
    return out


def front_matter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"([A-Za-z_]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"')
    return out


ENDED = {"done", "done in part", "abandoned"}


def document_findings(root: Path, issues: dict[int, Issue]) -> list[str]:
    """Where a design's or plan's front matter disagrees with its index row,
    with its other half, or with its issue."""
    out: list[str] = []
    statuses: dict[tuple[str, str], str] = {}
    for kind, columns in (("designs", 3), ("plans", 2)):
        index = (root / kind / "README.md").read_text()
        rows = {}
        for line in index.splitlines():
            cells = [c.strip() for c in line.split("|")]
            m = re.match(r"\[.*\]\((\d{3}-[^)]+\.md)\)", cells[1]) if len(cells) > columns else None
            if m:
                rows[m.group(1)] = cells[columns]
        for path in sorted((root / kind).glob("[0-9][0-9][0-9]-*.md")):
            meta = front_matter(path)
            name = f"{kind}/{path.name}"
            state = meta.get("status")
            if state is None:
                out.append(f"{name} has no status in its front matter.")
                continue
            statuses[(kind, path.name)] = state
            row = rows.get(path.name)
            if row is None:
                out.append(f"{name} has no row in {kind}/README.md.")
            elif row.replace("*", "").strip() != state:
                out.append(f"{name} says '{state}' but its row in {kind}/README.md says '{row}'.")
            number = meta.get("issue", "")
            if number.isdigit() and int(number) in issues:
                closed = issues[int(number)].closed
                if closed and state not in ENDED:
                    out.append(f"{name} is '{state}' but its issue #{number} is closed.")
                if not closed and state in ENDED and kind == "plans":
                    out.append(f"{name} is '{state}' but its issue #{number} is open.")
    for (kind, name), state in sorted(statuses.items()):
        other = statuses.get(("designs", name))
        if kind == "plans" and other and other != state:
            out.append(f"plans/{name} says '{state}' but designs/{name} says '{other}'.")
    return out


def render_report(issues: dict[int, Issue], milestone: str, repo: str, root: Path) -> str:
    lines = [f"# State of {repo}, milestone {milestone}", ""]
    for n in sorted(i for i, issue in issues.items() if issue.milestone == milestone):
        issue = issues[n]
        who = " " + " ".join("@" + a for a in issue.assignees) if issue.assignees else ""
        waits = [f"#{b}" for b in issue.blocked_by if b in issues and not issues[b].closed]
        waits += [ref for ref, closed in issue.external_blockers if not closed]
        tail = f" (waits on {', '.join(waits)})" if waits and not issue.closed else ""
        lines.append(f"- #{n} [{status(issue, issues)}]{who} {issue.title}{tail}")
    todo = root / "docs" / "todo.md"
    sections = [
        ("Among the issues", issue_findings(issues, milestone)),
        ("docs/todo.md against the issues",
         todo_findings(todo.read_text(), issues, repo) if todo.exists() else []),
        ("Designs and plans", document_findings(root, issues)),
    ]
    for title, found in sections:
        lines += ["", f"## {title}", ""]
        lines += [f"- {f}" for f in found] or ["- nothing found"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["report", "graph"])
    parser.add_argument("--milestone", help="default: the marker's, else the open milestone")
    parser.add_argument("--write", metavar="FILE", help="graph: replace the marked block in FILE")
    parser.add_argument("--root", default=".", help="the repository's root")
    args = parser.parse_args(argv)

    root = Path(args.root)
    todo = root / "docs" / "todo.md"
    target = Path(args.write) if args.write else todo
    marker = marker_milestone(target.read_text()) if target.exists() else None
    repo = current_repo()
    issues, open_milestones = fetch(repo)
    milestone = choose_milestone(args.milestone, marker, open_milestones)
    if not any(i.milestone == milestone for i in issues.values()):
        raise SystemExit(f"no issue is in milestone {milestone!r}")

    if args.command == "report":
        sys.stdout.write(render_report(issues, milestone, repo, root))
        return 0
    block = graph_block(issues, milestone, datetime.date.today().isoformat())
    if args.write:
        target.write_text(replace_block(target.read_text(), block))
        print(f"wrote the {milestone} diagram to {target}")
    else:
        print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
