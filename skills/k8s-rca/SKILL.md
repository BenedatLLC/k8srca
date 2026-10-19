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

**You will never see the whole history.** Kubernetes keeps a container's last
termination and a restart count, not every restart. Events cover a window of
hours to days. Logs rotate. So you will rarely *prove* a cause. You rank
candidates: what the evidence fits best, how strongly, and what would change
it. The person asking can see and change more than you can, and your answer
hands the rest to them.

**Every candidate ends the investigation with a place**, not a mention. There
are three places:
- **most likely**: what the evidence fits best, at each level (OOM, and which
  kind of OOM);
- **not ruled out**: still possible, with why it ranks lower;
- **ruled out**: excluded by something you observed.

"Worth checking later" is not a place. An answer that lists a candidate and
leaves it hanging reads as better supported than it is, because the
alternative went unexamined.

**Not proven is not unlikely.** A cause you cannot confirm, because the history
that would prove it is not retained, is still most likely if the evidence fits
it best. Say how strongly, and what would settle it. Demoting it to "could not
check" does not make the answer more careful. It makes it less useful, and
hands the reader no cause.

`kb_query.py lookup <alert>` gives each candidate its quickest test here,
cheapest first, and the checks those tests need. `kb_query.py claim <id> ...`
gives a candidate's full discriminator: what counts for and against it and how
much, what rules it out, its notes, and where the evidence needed is
unavailable. Read it for any
candidate the quickest test does not settle, and for the cause you conclude on.
Refutation is the more valuable half. A hypothesis you cannot refute is one you
are about to confirm by default.

**Before you conclude, run `kb_query.py open <alert> --facts <file>`.** Write
what you observed as facts, one per line, in the vocabulary `claim` and `open`
print: `last_exit_code[C, 137]`, or `not liveness_probe_configured[C]` for
something you checked and found false. `C`, `W`, `P` and `N` are the container,
workload, pod and node of the one workload the file describes; use one file
per failing workload. `open` gives every candidate, and the sub-causes of each
that survives, a standing from those facts, and says which leads among its
siblings. Leave out what you did not observe: unknown is not false. Every
claim it lists needs a place in the answer.

`open`'s standing is not the place, but it is where the place starts:
- **ruled-out** is ruled out;
- **strong**, **moderate** and **weak** are the support the facts give it,
  after what counts against it. The one that leads is the candidate for most
  likely, at that strength;
- **open** and **could-not-check** say the facts do not decide it. They do not
  say it is unlikely. Rank it against the others on what you observed. If it
  fits best, it is most likely, at the strength the evidence gives;
- a weak **against** lowers nothing, and the answer says it in a sentence: the
  observation that does not fit, and why the cause still stands.

The rule behind the standing is crude so that a reader can follow it. Depart
from it where the evidence says more than the facts recorded, and say why.

A fact covers what the record retains, and its name says so:
`killed_in_every_retained_life`. Record it when every retained termination
shows it, and say "every retained life" in the answer.

Where a candidate has **no authored discriminator**, the lookup says so. You
then have to form the criterion yourself, and should state that you did — a
place resting on a criterion you invented is weaker than one resting on a
criterion you were given, and the reader cannot tell the difference unless you
say.

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

## Using what is deployed

The cluster-architecture skill says what is deployed and how it connects; the
cluster says what it is doing now. Between them:

- **A workload failing on its own:** its resources and probes are usually the
  first things worth looking at. A container with no probes configured cannot
  be restart-looping because of a failed health check, which rules out a whole
  family of explanations immediately.
- **A failing dependency produces symptoms in everything upstream of it.**
  `wiki.py blast <component>` says what else will look broken. Work *down* the
  dependency graph toward the cause rather than treating each symptomatic
  service as its own problem.
- **Dependencies are configured, not observed.** The graph shows what a service
  *can* call, not what it called during the incident.
- **Declared against running.** The wiki records what each component is
  declared to run; only the cluster says what actually runs, so compare the
  two by asking the cluster. A difference is drift, worth citing only when it
  is specific to the service you are investigating and plausibly connected to
  the symptom. A whole-chart version bump shows up on every service it
  touched, healthy ones too: a property of the deployment, not an explanation
  of a failure.
- **Other failures in the cluster are usually unrelated, and the graph shows
  it.** A cluster under investigation often has other things broken. Before
  keeping one as a candidate for the symptom you were asked about, check
  whether it can reach that symptom at all:
  - *Not on the path.* No dependency edge connects it to the service in
    question, in either direction (`wiki.py`), and the two share nothing the
    service needs, such as a node under pressure or a datastore it writes. It
    is ruled out as unrelated, and the graph is the observation that rules it
    out. That you cannot prove it has no effect does not keep it open.
  - *Only on the far side of a queue.* Consumers that are down or restarting
    leave messages unread. They do not slow the producer while the queue
    itself is up.
  - *Shared by healthy services.* A property many healthy workloads also have,
    such as running near a memory limit, does not single anything out. It needs
    evidence specific to the service before it is a candidate.

  These go under **Ruled out**, one line each: "unrelated: not on payment-api's
  path".

## Knowledge base

`kb_query.py` sits next to this file. 81 alerts with candidate causes,
evidence to check, and remediation.

```
kb_query.py search "pod restart"        find candidate alerts by symptom
kb_query.py lookup CrashLoopBackOff     candidates, each with its quickest test
kb_query.py claim oom-kill bad-config   a candidate's full criteria and notes
kb_query.py refine oom-kill             sub-causes, once a cause survives (--all: uncommon ones too)
kb_query.py open CrashLoopBackOff --facts ad.facts   every candidate's status from your facts
kb_query.py related OOMKilled           alerts that co-occur
kb_query.py list --category storage     browse
```

### Account for every candidate it gives you

When `lookup` returns candidate causes, each one needs a place before you
conclude: **most likely** (with its strength), **not ruled out** (with why it
ranks lower, and what would decide it), or **ruled out** (with the fact that
excludes it).

Dropping a candidate silently is the most common way this goes wrong, and it is
invisible in the answer — the conclusion reads as well-supported precisely
because the alternatives were never mentioned. If the knowledge base lists three
causes and you discuss one, you have not ruled the others out; you have stopped
looking.

Demoting a candidate to "worth checking later" is not a place. It is not ruled
out, and you say why it ranks lower or what you could not check.

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

**A result that gives the reason for each object rules out the reasons it
does not give.** Some results account for every case. A `FailedScheduling`
event names why each node refused the pod: "0/3 nodes are available: 2
node(s) didn't match Pod's node affinity/selector, 1 node(s) had untolerated
taint". That accounts for every node.
So insufficient CPU or memory, which it does not name, is ruled out by it. A
reason it names for only some nodes does not explain why the others refused:
a standard taint on the control-plane node is a fact about that node, not a
cause of this failure. Rule both out on the result and quote it. Keeping them
under "Not ruled out" because they are true of the cluster reads as doubt the
result does not leave.

A container killed before it can log produces empty logs. That is
`unavailable`, not "no errors" — and mistaking one for the other produces a
confident wrong answer.

Anything the user tells you is evidence too, but provisional: people
misremember timing and conflate releases. Corroborate where a tool can, and
say when a conclusion rests on unverified recollection.

## Output

Four sections, in this order. Keep it short: this is read in Slack, often
during an incident, often on a phone. Cite the tool call behind each fact
where you use it. Every claim needs a source.

**Most likely** — the cause, and which kind where there are sub-causes. Give:
- its strength, in words: strong, moderate or weak (below);
- the facts for it;
- the facts against it, if any;
- **what would change it**: the observation that would move it off first
  place. This becomes the reader's first check, so make it concrete. If two
  candidates fit equally, say "no leading cause", and name what would separate
  them.

**Not ruled out** — the candidates still possible, most likely first. Each gets
one line: why it ranks lower, and what would decide it. Where nothing you can
reach would decide it, name what is missing (no metrics backend, a rotated
log). Rare candidates that nothing pointed at share one line at the end: "Not
checked, nothing pointed at them: …".

**Ruled out** — each with the fact that excludes it. Ruling out needs an
observation, not an absence you could not have seen. A failure elsewhere that
cannot reach the symptom is ruled out by that observation: name what keeps it
off the path ("Using what is deployed", above).

**Next steps** — for the person asking, most useful first. They can see more
than you and can change the system. Say which entry above each step would
settle or move. Remediation counts when trying it would settle the cause.
These are always suggestions: you have read-only access and never change
anything.

Where something you needed was unavailable, it goes where it bears: in why a
candidate is not ruled out, or in what would change the most likely cause.
Never leave it out. That is how the reader knows the shape of your
uncertainty.

## Strength

Use words, not numbers. A percentage implies a calibration you do not have,
and reads as a measurement to someone acting on it at 3am.

| Strength | When |
| --- | --- |
| **strong** | What you observed is what this cause predicts and its rivals do not. Nothing observed counts against it that you cannot explain |
| **moderate** | It fits better than the rest, but one rival or an unexplained observation remains |
| **weak** | It fits, nothing fits better, and little points at it specifically |

There is nothing above strong. A cause a result states outright is strong.
"Confirmed" and "certain" read as strong, so use them only for a strong cause.

Something observed against the most likely cause does not demote it. Say what
it is and how much it weighs. Missing history does not demote it either. It is
what "what would change it" is for.
