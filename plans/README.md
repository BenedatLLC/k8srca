# Plans

A plan says how a change is carried out: its steps, how each step is measured, and what happened.
It goes with a design of the same number and name in [designs/](../designs/README.md), or stands
alone for a one-step change to the agent. The design says what and why and rarely changes once
approved; the plan changes as the work proceeds.

## What a plan holds

- **Steps,** each stating before any code is written the five fields of
  [process.md §1](../docs/process.md#1-plan-the-measurement-before-building): hypothesis, expected
  effect, whether the measurement can see that effect, budget, and what finishes or stops the step.
  A step that changes no agent behaviour (a tool, a refactor) needs only what it delivers and how
  it is tested.
- **An issue for each step,** a sub-issue of the design's issue, linked from the step.
- **A results table:** for each step, the plan, the outcome, and the decision taken. A checkpoint
  ([process.md §3](../docs/process.md#3-the-checkpoint)) is written here, ending in continue,
  change the plan, or close the step.
- **A checklist** of each step's deliverables, ticked as they are merged.

Run-by-run logs go in [docs/journal/](../docs/journal/), linked from the results table.

## Writing one

Copy [TEMPLATE.md](TEMPLATE.md) to `plans/NNN-short-name.md`, using the design's number and name.
Review it with the design, or on its own before the first step starts. When the plan's steps are
done, record the design's end state in the design's header and in the
[design index](../designs/README.md#index).

## Plans

| Plan | Status |
| --- | --- |
| [011 — Decision models](011-decision-models.md) | not started |

Designs 004 to 015 otherwise hold their plans inline; a design that resumes work moves its
remaining steps here.
