# How we change the agent

From 2026-09-20 to 2026-10-21 the same loop ran several times:
1. a new design;
2. implement and measure it;
3. a rut of small changes and ambiguous measurements;
4. back to 1, with the previous design unfinished.

About $450 went on sessions and grading, 82% of runs were on one scenario, and
about 21 of 25 measured arms in the last week were inside the noise. Designs
007, 008 and 009 were all still open when 010 started. The review behind these
numbers is in [010 §1](../designs/010-facts-and-short-answers.md).

This document is how we avoid that loop. It applies to anything measured with
the scenario suite.

## 1. Plan the measurement before building

Every design step states five things before any code is written:

| Field | Example |
| --- | --- |
| **Hypothesis** | Deriving facts in code removes most horizon and count claims |
| **Expected effect** | Unsupported claims per run halve across the suite |
| **Can we see it?** | The effect against known noise at the planned n (§6) |
| **Budget** | Dollars and calendar days |
| **Done and stop** | What finishes the step, and what ends it early |

A change whose expected effect is smaller than the noise at an affordable n
does not get a live run. It gets an offline eval, or it is decided on judgment
and said to be.

## 2. Tripwires

Any one of these stops work on the step until a checkpoint (§3) is written:

1. **Two inconclusive arms in a row.** Two measurements of the same goal read
   "no large effect" or "can't tell".
2. **The third change for one thing.** A third change aims at the same
   dimension, scenario or failure.
3. **A rule from a run.** An instruction, rule or knowledge-base text is
   written from one scenario's failing run or trap.
4. **The instrument moved.** The grader, a truth file or a checklist changed
   during the step.
5. **Budget spent.** The step's budget is used up.
6. **Drift.** The work no longer matches the design's stated goal. 009's last
   two arms, for example, were about the knowledge base's owed rule on jvm,
   not about metrics.

Whoever notices one says so at once, before proposing anything else. That
includes Claude.

## 3. The checkpoint

Half a page, in the design's results section. It answers:
- Was the hypothesis wrong?
- Was the measurement unable to see the effect?
- Is the implementation incomplete?

It ends in one of three decisions, made by the project owner:
- **Continue** as planned. The tripwire was a false alarm, and the checkpoint
  says why.
- **Change the plan.** A new hypothesis, measurement or budget, written down as
  in §1.
- **Close the step** (§4).

The checkpoint never ends in another small change to try.

## 4. Designs end

One design is in progress at a time. It ends in one of three states, recorded
on its status line:
- **Done.** Every step met its done criterion.
- **Done in part.** What was dropped, and why.
- **Abandoned.** Why, and what was learned.

A new design starts only when the current one has ended, or is **parked** with
a one-line reason and the step it stopped at.

Each step's results go in a short table in the design: plan, outcome,
decision. Run-by-run logs go to a measurement journal (`docs/journal/`,
created with its first entry), so
the design stays short and its status stays honest.

## 5. The instrument is frozen per design

The grader, the truth files and the checklists are fixed when a design's
measurements start. A change to any of them is its own small piece of work. It
is checked for noise on saved answers before use, and every arm of the design
is measured on one version of the instrument.

## 6. Two loops

**The inner loop is cheap and runs often.**
- Unit tests.
- Offline evals: for example, the facts layer on recorded tool results, and
  the answer writer on fixed facts (010 §8).

Most changes should be settled here.

**The outer loop is a live suite run.** It happens only at milestones a step's
plan names in advance, at n=2–3 across every scenario the change reaches,
within the step's budget.

**Known noise, for planning:**
- Two n=5 arms of an identical agent differ by 2 or more on a 30%-pass
  dimension about 30% of the time.
- The current grader disagrees with itself on evidence for an identical answer
  11–16% of the time.
- `cause`, `standing` and `next` do not flip between gradings; traps, gaps
  and calibration do, about 4% of the time.
- The checklist grader (010 §7), the measurement since 2026-10-22, is
  steadier: about 0.9% of its verdicts dissent, and step 5's checklists
  agree 98–100% item by item (010 §9.2, §9.10, §9.12). The figures above
  are the rubric's, which is now opt-in.
- Remeasure these numbers when the grader changes.

## 7. Design states (2026-10-25)

| Design | State | Next |
| --- | --- | --- |
| 004 scenario testing | done in part | Closed 2026-10-24: the checklist baseline (§10.4); S5, X2, X3 and scenarios 6 and 8 dropped. ITBench 16, 24 and 34 planned as their own work, 2026-10-25 (§12.3) |
| 006 hold-out suite | done in part, on hold | Put on hold by the owner, 2026-10-23: the kit is built; the tag waits on the owner |
| 007 knowledge representation | done in part | Closed 2026-10-25 (§11.4 step 7): the five families the suite reaches; four families wait on scenarios that reach them, and runbooks (§9) on a delivery decision and a scenario. What each needs is at the top of 007 |
| 008 conclusions under incomplete evidence | done in part | Revisited after 010: ranking, strength and the scoped facts stay; the answer, rubric and reviewer are 010's |
| 009 metrics | done in part | The issue #3 re-recording (from 2026-10-25); latency and throttling reports, as a new design |
| 010 facts and short answers | done | Closed 2026-10-23 (§9.13) |
| 011 decision models | draft | Step 1 (text-read facts) planned, offline only; step 2 waits on 004 §12.3's imports; the reviewer part is held for issue #6 |
