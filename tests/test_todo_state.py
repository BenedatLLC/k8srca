"""The update-todo skill's script: issue states, the milestone diagram, the findings.

Hermetic: the issues are built here, and nothing calls `gh`.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "todo_state", ROOT / ".claude/skills/update-todo/todo_state.py")
ts = importlib.util.module_from_spec(_spec)
import sys  # noqa: E402

sys.modules["todo_state"] = ts
_spec.loader.exec_module(ts)

REPO = "acme/app"


def node(number, *, closed=False, milestone="V1", assignees=(), parent=None, blocked_by=(),
         reason=None, title=None):
    return {
        "number": number, "title": title or f"Issue {number}",
        "state": "CLOSED" if closed else "OPEN", "stateReason": reason,
        "milestone": {"title": milestone} if milestone else None,
        "assignees": {"nodes": [{"login": a} for a in assignees]},
        "labels": {"nodes": []},
        "parent": {"number": parent, "repository": {"nameWithOwner": REPO}} if parent else None,
        "blockedBy": {"nodes": [
            {"number": b[1] if isinstance(b, tuple) else b, "title": "t",
             "state": "CLOSED" if isinstance(b, tuple) and b[2] else "OPEN",
             "repository": {"nameWithOwner": b[0] if isinstance(b, tuple) else REPO}}
            for b in blocked_by]},
    }


def issues(*nodes):
    return ts.parse_issues(list(nodes), REPO)


class TestStatus:
    def test_the_five_states(self):
        got = issues(
            node(1, closed=True),
            node(2, closed=True, reason="NOT_PLANNED"),
            node(3, blocked_by=[5]),
            node(4, assignees=["ann"]),
            node(5),
        )
        assert {n: ts.status(i, got) for n, i in got.items()} == {
            1: "done", 2: "dropped", 3: "blocked", 4: "in progress", 5: "ready"}

    def test_a_closed_blocker_does_not_block(self):
        got = issues(node(1, closed=True), node(2, blocked_by=[1], assignees=["ann"]))
        assert ts.status(got[2], got) == "in progress"

    def test_an_open_blocker_in_another_repository_blocks(self):
        got = issues(node(1, blocked_by=[("acme/tools", 9, False)]),
                     node(2, blocked_by=[("acme/tools", 8, True)]))
        assert ts.status(got[1], got) == "blocked"
        assert ts.status(got[2], got) == "ready"
        assert got[1].external_blockers == [("tools#9", False)]


class TestGraph:
    def test_an_arrow_runs_from_the_blocker_to_the_blocked(self):
        text = ts.render_graph(issues(node(1), node(2, blocked_by=[1])), "V1")
        assert "i1 --> i2" in text and "i2 --> i1" not in text

    def test_an_implied_arrow_is_left_out(self):
        got = issues(node(1), node(2, blocked_by=[1]), node(3, blocked_by=[1, 2]))
        text = ts.render_graph(got, "V1")
        assert "i1 --> i2" in text and "i2 --> i3" in text and "i1 --> i3" not in text

    def test_a_cycle_keeps_every_arrow(self):
        edges = {(1, 2), (2, 1)}
        assert ts.reduce_edges(edges) == edges
        assert ts.find_cycle(edges)

    def test_state_and_assignee_are_in_the_node(self):
        text = ts.render_graph(issues(node(1, assignees=["ann"]), node(2, closed=True)), "V1")
        assert "in progress · @ann" in text and ":::active" in text
        assert "<i>done</i>" in text and ":::done" in text

    def test_a_parent_is_grouped_with_its_sub_issues(self):
        text = ts.render_graph(issues(node(1), node(2, parent=1), node(3)), "V1")
        lines = text.splitlines()
        start = lines.index('  subgraph g1["#1 and its sub-issues"]')
        end = lines.index("  end")
        inside = "\n".join(lines[start:end])
        assert "i1[" in inside and "i2[" in inside and "i3[" not in inside

    def test_a_parent_outside_the_milestone_is_named_but_not_drawn(self):
        text = ts.render_graph(issues(node(1, milestone=None), node(2, parent=1)), "V1")
        assert "Sub-issues of #1" in text and "(no milestone)" in text and "i1[" not in text

    def test_only_the_milestones_issues_and_their_open_outside_blockers_are_drawn(self):
        got = issues(node(1, blocked_by=[2, 3]), node(2, milestone=None),
                     node(3, milestone="V2", closed=True), node(4, milestone="V2"))
        text = ts.render_graph(got, "V1")
        assert "i2[" in text and ":::outside" in text and "i2 --> i1" in text
        assert "i3[" not in text and "i4[" not in text

    def test_a_title_cannot_break_the_diagram(self):
        text = ts.render_graph(issues(node(1, title='A "quoted" [title] with `code` | <b>')), "V1")
        label = text.splitlines()[1]
        assert label.count('"') == 2 and "<b>" not in label and "[title]" not in label


class TestBlock:
    def test_the_block_replaces_what_was_between_the_markers(self):
        got = issues(node(1), node(2, closed=True))
        block = ts.graph_block(got, "V1", "2026-01-02")
        text = ('before\n<!-- milestone-graph:start milestone="V1" -->\nold\n'
                "<!-- milestone-graph:end -->\nafter\n")
        out = ts.replace_block(text, block)
        assert out.startswith("before\n") and out.endswith("\nafter\n")
        assert "old" not in out and "2 issues: 1 done, 1 ready." in out
        assert ts.replace_block(out, block) == out
        assert ts.marker_milestone(out) == "V1"

    def test_no_markers_is_an_error_not_an_append(self):
        with pytest.raises(ValueError):
            ts.replace_block("no markers here\n", "block")

    def test_the_milestone_asked_for_wins_then_the_marker_then_the_open_one(self):
        both = [{"title": "V1", "dueOn": None}, {"title": "V2", "dueOn": "2027-01-01"}]
        assert ts.choose_milestone("V3", "V1", both) == "V3"
        assert ts.choose_milestone(None, "V1", both) == "V1"
        assert ts.choose_milestone(None, None, both[:1]) == "V1"
        assert ts.choose_milestone(None, None, both) == "V2"
        with pytest.raises(SystemExit):
            ts.choose_milestone(None, None, [{"title": "A", "dueOn": None},
                                             {"title": "B", "dueOn": None}])


class TestFindings:
    def test_a_blocker_outside_the_milestone(self):
        found = ts.issue_findings(issues(node(1, blocked_by=[2]), node(2, milestone=None)), "V1")
        assert any("#1 is in V1 but is blocked by #2" in f for f in found)

    def test_a_claimed_issue_that_is_blocked(self):
        found = ts.issue_findings(issues(node(1, blocked_by=[2], assignees=["ann"]), node(2)), "V1")
        assert any("#1 is assigned to @ann but is blocked" in f for f in found)

    def test_parents_and_sub_issues_that_disagree(self):
        got = issues(node(1, closed=True), node(2, parent=1),
                     node(3), node(4, parent=3, closed=True),
                     node(5, milestone=None), node(6, parent=5))
        found = "\n".join(ts.issue_findings(got, "V1"))
        assert "#1 is closed but its sub-issues #2 are open" in found
        assert "#3 is open but all of its sub-issues are closed" in found
        assert "#6 is in V1 but its parent #5 is in no milestone" in found

    def test_a_consistent_milestone_has_no_findings(self):
        got = issues(node(1, assignees=["ann"]), node(2, blocked_by=[1]),
                     node(3, parent=2, closed=True), node(4, parent=2))
        assert ts.issue_findings(got, "V1") == []

    def test_todo_against_the_issues(self):
        def link(n):
            return f"[#{n}](https://github.com/{REPO}/issues/{n})"
        text = "\n".join([
            f"Fix {link(1)} first.",
            "| Ticket | What | When |",
            f"| {link(1)} | closed one | now |",
            f"| {link(2)} | open one; sub-issue {link(4)} | later |",
            '<!-- milestone-graph:start milestone="V1" -->',
            f"| {link(9)} | generated, not read | - |",
            "<!-- milestone-graph:end -->",
        ])
        found = "\n".join(ts.todo_findings(
            text, issues(node(1, closed=True), node(2), node(3), node(4)), REPO))
        assert "table lists #1, which is closed" in found
        assert "#3 is open but todo.md's open-tickets table does not name it" in found
        assert "#2 is open" not in found and "#4 is open" not in found
        assert "mentions closed issue #1 on line 1, 3" in found
        assert "#9" not in found

    def test_designs_and_plans_against_their_indexes(self, tmp_path):
        for kind in ("designs", "plans"):
            (tmp_path / kind).mkdir()
        (tmp_path / "designs/001-a.md").write_text("---\nstatus: on hold\nissue: 1\n---\n")
        (tmp_path / "designs/002-b.md").write_text("---\nstatus: done\nissue: 2\n---\n")
        (tmp_path / "designs/README.md").write_text(
            "| Design | Type | Status | Where |\n| --- | --- | --- | --- |\n"
            "| [001 — A](001-a.md) | change | in progress | x |\n"
            "| [002 — B](002-b.md) | change | done | x |\n")
        (tmp_path / "plans/001-a.md").write_text("---\nstatus: in progress\nissue: 1\n---\n")
        (tmp_path / "plans/README.md").write_text(
            "| Plan | Status |\n| --- | --- |\n| [001 — A](001-a.md) | in progress |\n")
        found = "\n".join(ts.document_findings(
            tmp_path, issues(node(1, closed=True), node(2, closed=True))))
        assert "designs/001-a.md says 'on hold' but its row in designs/README.md says 'in progress'" in found
        assert "designs/001-a.md is 'on hold' but its issue #1 is closed" in found
        assert "plans/001-a.md says 'in progress' but designs/001-a.md says 'on hold'" in found
        assert "002-b" not in found
