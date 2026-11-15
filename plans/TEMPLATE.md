---
id: "NNN"
title: <Title>
design: designs/NNN-name.md
owner: unassigned
issue: <N>            # the design's parent issue; each step is a sub-issue
status: not started   # not started | in progress | on hold | done | stopped
last_updated: YYYY-MM-DD
---

# Plan NNN — Title

Design: [NNN — Title](../designs/NNN-name.md).

<A sentence or two: what this plan delivers, and the order of its steps.>

```mermaid
flowchart LR
  S1["Step 1: ..."] --> S2["Step 2: ..."]
```

## Step 1 — <what it does, in plain words>

**Issue:** [#N](https://github.com/BenedatLLC/k8srca/issues/N).

| | |
| --- | --- |
| **Hypothesis** | What we expect to be true, and why |
| **Expected effect** | The size of the change we expect, on which measure |
| **Can we see it?** | Whether that effect is larger than the known noise at the planned number of runs ([process.md §6](../docs/process.md#6-two-loops)). If not, an offline evaluation or a judgment, said to be one |
| **Budget** | Dollars and calendar days |
| **Done and stop** | What finishes the step, and what ends it early |

What it builds, and which evaluations it runs
([development.md](../docs/development.md#evaluations-cheapest-first)).

- [ ] <deliverable>
- [ ] Tests
- [ ] Documents updated

## Results

| Step | Plan | Outcome | Decision |
| --- | --- | --- | --- |
| 1 | | | |

### Checkpoints

<Written here when a warning sign from [process.md §2](../docs/process.md#2-tripwires) fires:
was the hypothesis wrong, could the measurement not see the effect, or is the implementation
incomplete? It ends in continue, change the plan, or close the step.>
