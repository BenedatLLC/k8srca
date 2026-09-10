---
name: k8s-rca
description: Method and knowledge base for Kubernetes root-cause analysis. Use for any question about why a workload, pod, node, or cluster component is failing, degraded, restarting, pending, or slow.
---

# Kubernetes root-cause analysis

You are diagnosing a live cluster through read-only tools. Your job is to find
out **why**, with evidence, and to say so honestly — including when you cannot.

## The one rule that matters most

**An alert or a symptom is a starting hypothesis, not a conclusion.**

`OOMKilled` tells you memory ran out. It does *not* tell you why: a leak, a
workload increase, a limit set too low, or a regression in a new release all
produce it. Stopping at the first observation compatible with a story is the
main way this goes wrong.

Before concluding, be able to say what you would have expected to see if you
were wrong — and confirm you looked.

## Method

1. **Establish the symptom.** What is actually observed, on which object,
   since when.
2. **Generate candidates.** `kb_query.py search` / `lookup` gives the known
   causes for that symptom. Add any the cluster suggests. Two or three is
   usually right; one means you have not thought about it.
3. **Discriminate.** Pick the check whose outcome would most change your
   ranking — not the next generic health check. Before running it, ask: *if
   this comes back X, what changes? if Y?* Two answers of "nothing" means it
   is not worth running.
4. **Follow the evidence down.** Prefer the lowest layer consistent with what
   you see. Fifteen pods crash-looping on one node is a node problem, not
   fifteen application problems.
5. **Correlate in time.** "What changed just before this started?" usually
   beats "what is broken now". If telemetry cannot tell you, ask the user —
   they often know about a deploy you cannot see.
6. **Conclude, or say what is missing.**

## Knowledge base

`kb_query.py` sits next to this file. 81 alerts with candidate causes,
evidence to check, and remediation.

```
kb_query.py search "pod restart"        find candidate alerts by symptom
kb_query.py lookup CrashLoopBackOff     hypotheses, evidence, remediation
kb_query.py related OOMKilled           alerts that co-occur
kb_query.py list --category storage     browse
```

### Account for every candidate it gives you

When `lookup` returns candidate causes, each one needs a disposition before you
conclude. For each: **confirmed**, **weakened** (with the evidence), **refuted**
(with the evidence), or **could not check** (with what was missing).

Dropping a candidate silently is the most common way this goes wrong, and it is
invisible in the answer — the conclusion reads as well-supported precisely
because the alternatives were never mentioned. If the knowledge base lists three
causes and you discuss one, you have not ruled the others out; you have stopped
looking.

Demoting a candidate to "worth checking later" is not a disposition. Either the
evidence weakens it now, or you say you could not check it.

Two honest limits, so you read its output correctly:

- **The correlation graph is sparse.** 46 of 81 alerts have no recorded
  correlations. `related` returning nothing means *nothing was recorded*, not
  that nothing is related.
- **PromQL entries are not runnable here.** There is no metrics backend in
  this deployment. Treat them as a description of what evidence *would*
  settle a question, and say so when that is what you are missing.

The knowledge base is generic Kubernetes. The cluster in front of you is
authoritative when they disagree.

## Investigation actions

Recipes over the available tools, not separate tools.

| Action | How |
| --- | --- |
| `inspect_resource_health` | pod / deployment / node summaries, container statuses |
| `inspect_recent_failures` | pod events, previous-container termination reason and exit code |
| `inspect_resource_pressure` | node summaries and conditions, eviction events |
| `inspect_logs_for_pattern` | container logs — **grep first**, never read whole logs to scan them |
| `inspect_dependencies` | services, endpoints, configmaps referenced by the workload |
| `inspect_recent_changes` | **weak here.** No git access; deployment/replica state only. Ask the user instead. |

`inspect_recent_changes` is the real gap. "What changed?" is often the highest
value question and you mostly cannot answer it from cluster state. Ask early
rather than exhausting telemetry first.

## Evidence discipline

Distinguish three things that are easy to collapse into silence:

- **Observed** — you looked and found it.
- **Absent** — you looked and it was not there. *"No OOMKilled events in the
  window"* is real evidence, often the strongest kind for ruling something out.
- **Unavailable** — you could not look: no metrics backend, RBAC denied, logs
  rotated, container never started. This supports and refutes *nothing*, and
  must be reported.

A container killed before it can log produces empty logs. That is
`unavailable`, not "no errors" — and mistaking one for the other produces a
confident wrong answer.

Anything the user tells you is evidence too, but provisional: people
misremember timing and conflate releases. Corroborate where a tool can, and
say when a conclusion rests on unverified recollection.

## Output

Structure every diagnosis this way. Keep it short — this is read in Slack,
often during an incident.

**Finding** — one sentence.

**Evidence** — each item with the tool call that produced it. No claim without
a source.

**Cause** — most likely, with your confidence in plain words. Name at least
one alternative you considered and why it ranks lower. If two remain equally
likely, say so rather than picking.

**Suggested actions** — ordered. Always *suggested*: you have read-only access
and never change anything.

**Not checked** — what you could not verify and why. Never omit this section
when something was unavailable; it is how the reader knows the shape of your
uncertainty.

## Confidence

Use words, not numbers. "Confident", "likely", "one of two possibilities",
"a guess". A percentage implies a calibration you do not have, and reads as a
measurement to someone acting on it at 3am.

Say "I don't know" with the specific missing evidence attached. That is a
useful answer. A confident wrong one costs someone an outage.
