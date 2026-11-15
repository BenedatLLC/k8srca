---
id: "NNN"
title: <Title>
type: change
status: draft
measured: false
owner: unassigned
issue: <N>
plan: plans/NNN-name.md
built: []
not_built:
  - <what is planned, in a few words each>
waits_on: []
replaces: []
related: ["001"]
last_updated: YYYY-MM-DD
---

# NNN — Title

<A paragraph for someone new to the project: the problem this addresses, and what to know before
reading. Link the [glossary](../docs/glossary.md) for project terms.>

## 1. Problem

What is wrong or missing, with the evidence: a measurement, a scenario, an incident, a ticket.
Say how often it happens and what it costs.

## 2. Goal

What will be true when this is done, stated so someone could check it. Also what is out of
scope.

## 3. Approach

How it works. Cover what is relevant:
- components and data flow, with a diagram;
- what the agent sees or does differently;
- configuration, and its default;
- how it keeps the [constitution](../docs/constitution.md): credentials, read-only, what data
  leaves, tests.

## 4. Alternatives considered

Each alternative and why it was not chosen.

## 5. What it changes

- Other designs and reference documents it replaces or changes, by section.
- The measuring instrument, if at all (it should not change mid-design;
  [constitution P3](../docs/constitution.md#p3-the-measuring-instrument-is-frozen-for-the-length-of-a-design)).
- Upstream requests (k8stools issues).

## 6. How we will know it worked

The measurement in outline: which scenarios or evaluations, what result would count as success,
and what would make us stop. The plan states each step's version of this in full.

## 7. Open questions

- [ ] Question, and who decides it.

---

## Appendix: history and results

Only what a later reader needs: an approach abandoned and why, or a result other documents cite.
Run-by-run logs go in [docs/journal/](../docs/journal/).
