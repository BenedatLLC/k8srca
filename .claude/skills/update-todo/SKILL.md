---
name: update-todo
description: Bring docs/todo.md back in line with the GitHub issues, the designs and the plans. Reconciles the state it states, updates the project plan diagram, regenerates the diagram of the current milestone's issues, and reports what a person has to resolve. Use when asked to update, refresh or reconcile todo.md, or after issues, a milestone or a design's status changed.
---

# Update docs/todo.md

`docs/todo.md` is a map of the work: the order among unblocked work and why. On state it is a
copy. The GitHub issues say what is open, who owns it and what blocks what; each design's and
plan's front matter says its status. When todo.md disagrees with them, todo.md is what changes
(`docs/writing.md`, "One home for each fact").

This skill changes `docs/todo.md` only. It does not edit issues, milestones, assignees, designs,
plans or their indexes, and it does not commit. Anything wrong outside todo.md is reported.

An optional argument names the milestone (`/update-todo V2`). Without one, the script uses the
milestone named in todo.md's marker, else the open milestone.

## 1. Read the state

```bash
python3 .claude/skills/update-todo/todo_state.py report
```

It prints each issue in the milestone with its state (done, in progress, ready, blocked) and
three lists of findings: among the issues, todo.md against the issues, and designs and plans
against their indexes. Read all of it before editing. If `gh` fails, stop and say so: do not
update todo.md from memory.

For an issue whose state you must describe in prose and whose title is not enough, read it
(`gh issue view N --comments`). Do not read hold-out material to do this
(`docs/constitution.md`, the hold-out rule).

## 2. Regenerate the milestone diagram

```bash
python3 .claude/skills/update-todo/todo_state.py graph --write docs/todo.md
```

It replaces everything between the `milestone-graph` markers. Never edit that block by hand: if
the diagram is wrong, the issues' relationships are wrong (report it) or the script is (fix the
script, with a test in `tests/test_todo_state.py`).

## 3. Reconcile what todo.md states

Go through todo.md top to bottom and correct every statement of state that the report or the
documents contradict:

- **The open-tickets table.** Every open issue is named in a row: its own, or its parent's row
  for a plan step's sub-issue. Remove a closed issue's row, and a closed sub-issue from its
  parent's row. Take a new row's "what it is" from the issue's title and its "when" from the
  plan item it belongs to.
  If you cannot tell where an open issue fits in the order, add the row with "Unscheduled" and
  report it.
- **Prose about an issue.** Where a closed issue is described as still to do, or a blocker that
  has closed is still said to block, rewrite the sentence to say what is true now. Keep the
  reasons and the order: they are todo.md's own content.
- **Designs and plans.** Where todo.md gives a design's or plan's status ("on hold", "done in
  part", "not built"), make it match the front matter.
- **"Next to pick up".** Remove items that are done. Do not reorder what remains or add items:
  the order is the owner's decision. If the list is now empty or its first item is blocked,
  report it.

Do not rewrite text that is still true, and follow `docs/writing.md`.

## 4. Update the project plan diagram

The flowchart under "The plan, in order" has one box per numbered item. For each item, decide
its state from the issues it names and its design's status:

- **done**: every issue it names is closed;
- **under way**: some are closed or claimed and not blocked;
- **waiting**: blocked or on hold; say on what, in the box's last line;
- **not started**: otherwise.

Put the state in the box's text ("done", "now", "on hold until ...") and mark it with a class:
`done`, `active` or `waiting`, defined as in the milestone diagram's `classDef` lines
(`waiting` uses `blocked`'s style), with a sentence under the diagram saying what each means. Keep the boxes, their numbers and the arrows; a finished
item stays in the diagram. Keep the heading of each numbered section below it in step with its
box. If the items themselves no longer describe the plan (work has been added or dropped),
do not restructure: report it.

## 5. Report

End with a short report to the person:

1. What changed in todo.md, in a few lines.
2. **To resolve**: everything you could not settle by editing todo.md. Include every finding
   from the script that is still true, and anything you noticed that it cannot see. Typical
   items: an issue in the milestone blocked by one outside it; a sub-issue in the milestone
   whose parent is not; an issue claimed but blocked; a design's status that disagrees with its
   index row or its issue; an open issue with no place in the plan; a closed parent with open
   sub-issues. For each, say what disagrees with what and who decides. Do not pick a side.

Leave the change uncommitted unless asked to commit.
