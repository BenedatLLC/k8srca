# Design 004 — Scenario testing

**Status:** proposed, 2026-09-12. S0 landed in **k8stools 2.0.3** (2026-09-19):
`k8s-capture-state` writes a capture, `--state-file` replays it, and
`--state-time frozen` pins the clock. A 27-pod OTel-demo capture is 2.4 MB at
200 log lines per container.

**Blocked on [k8stools#6](https://github.com/BenedatLLC/k8stools/issues/6).**
`get_logs_for_pod_and_container` returns the client's raw `bytes`, so logs
arrive as a `repr()` blob, they bypass redaction (`_redact` dispatches on `str`
and falls through for `bytes`), and `previous=True` returns the current
instance. Authoring is paused rather than slowed: a capture taken today bakes
all three into a committed fixture, and §6.3 makes re-recording invalidate
`truth.yaml`. Scenario 1 loses its log evidence but survives on container
status; **scenario 8 cannot be authored at all**, since it exists to test
`unavailable` vs `absence` and that distinction does not currently exist at the
tool layer.

Companion to [001](001-architecture.md) (platform), [002](002-investigation-model.md)
(how the agent reasons) and [003](003-operations.md) (running it). This document
covers how we find out whether any of it works.

---

## 1. Why: what we want to prove or disprove

Every change to a system prompt, a knowledge-base entry or the tool split is
currently unfalsifiable. We have run the agent against a real cluster a handful
of times and read the answers. That is enough to know it *can* work. It is not
enough to know whether the next edit made it better.

Five open questions across the three designs say, in so many words, *measure
this*: [001 §12.3](001-architecture.md#12-open-questions) (does `xhigh` effort
pay for itself), [§12.4](001-architecture.md#12-open-questions) (which of the 81
KB rules ever fire), [§12.7](001-architecture.md#12-open-questions) (where the
triage/specialist line sits), [002 §11.1](002-investigation-model.md#11-open-questions)
(does L1 structured output degrade reasoning) and
[§11.3](002-investigation-model.md#11-open-questions) (do model-authored
discriminators hold up). [002 §9](002-investigation-model.md#9-staging-from-prompt-to-method)
makes the dependency explicit: L2 and beyond come *after* the suite, because
"optimizing without measurement is how prompts accumulate folklore."

But the gating questions are not the main reason. Three properties matter more.

**We need to be able to disprove that our own machinery helps.** The observed L0
baseline in [001 §13](001-architecture.md#13-build-order) — no system prompt, no
skills — chained pod summaries to container statuses to events and found an
`OOMKilled` loop unaided. That is a *high* floor. The knowledge base, the
coordinator/specialist split and the architecture skill are all assumed to beat
it, and none of them has been measured against it. A suite that cannot report
"the KB made no difference on six of eight scenarios" is not doing its job.

**We need to catch the failure mode that looks like success.** The most
instructive result so far is [002 §11.0](002-investigation-model.md#11-open-questions):
the agent retrieved three candidate causes for `OOMKilled`, discussed one, and
silently dropped the other two. The answer read as well-supported *because* the
alternatives went unmentioned. No output-quality heuristic catches that — the
prose is excellent. Only a check that knows which rivals existed can. This is
the single strongest constraint on the design of grading, and it is why §6
scores method rather than conclusions.

**We need regression detection, not a benchmark number.** The purpose is not to
publish a score. It is to notice when an edit intended to fix one scenario
breaks three others — the ordinary way prompt-driven systems rot.

### What the suite is deliberately not for

It does not test the platform. Session plumbing, Slack handlers, the poller and
session reuse have hermetic tests already, and the end-to-end path has
`tests/test_e2e_slack.py`. The suite runs against `session.create` directly, not
through Slack. **Reasoning is the only variable.**

---

## 2. What a scenario is

A scenario is a directory: a question, the captured state needed to answer it,
and the truth.

```
tests/scenarios/jvm-oom-on-startup/
  scenario.yaml         # question, sources, budget
  truth.yaml            # cause, rivals, traps, known gaps
  k8s.json              # k8stools capture
  prometheus.openmetrics# (later) metrics capture
  README.md             # how the cluster was broken, so it can be re-made
```

`scenario.yaml` declares which sources the scenario needs; the runner stands up
exactly those and points the agent at them:

```yaml
id: jvm-oom-on-startup
question: |
  The ad service in otel-demo is crash-looping. Why?
sources:
  k8stools:
    state: k8s.json
    clock: frozen
budget:
  max_tool_calls: 25
  max_usd: 0.25
follow_up: |
  Was anything deployed recently that could explain it?
```

**The contract is the scenario, not the mocking mechanism.** Adding Prometheus
adds a source kind and a container; it does not change the runner, the grader,
or any existing scenario. That is the whole extensibility story, and §3 is just
three instances of it.

---

## 3. How a scenario is served

### 3.1 The replayability contract

A data source can back a scenario if it can do two things:

1. **Answer arbitrary queries from a captured artifact.** Not a recording of the
   calls we happened to make — the agent's tool calls vary run to run, and a
   transcript that misses is a transcript that lies.
2. **Rebase its time origin onto suite start.** Everything the agent reasons
   about is relative: *this pod restarted 30 seconds after that deploy*. A
   capture whose timestamps are three weeks old, replayed naively, yields a
   cluster where nothing has happened recently and every interval is wrong.

Those two properties are the extension point. Any source that satisfies them
plugs in; any source that cannot falls back to §3.3 and accepts the limits.

### 3.2 k8stools — state capture (primary)

The k8stools design already satisfies both. `k8s-capture-state` snapshots a live
cluster to JSON; `MockState` serves the same queries the real tools do; the MCP
server takes `--state-file`. Ages are stored relative to `captured_at` and
reconstructed relative to server start, which is requirement 2 exactly.

Three additions were made to that design for this suite: `replicasets`,
previous-instance logs, and a frozen replay clock. The reasoning is recorded in
k8stools' `designs/mock-state-capture.md` rather than duplicated here.

**k8srca needs no changes at all to consume it.** The rule in `CLAUDE.md` — all
cluster access goes through k8stools — means there is exactly one seam, and the
mock drops into it. The runner swaps a container argument. Nothing above the MCP
boundary can tell the difference, which is the same property that makes the
read-only guarantee checkable in one place.

### 3.3 Prometheus — a real server on a captured block (first-class)

Metrics are the first thing the agent asks for that it cannot get. In the one
real-cluster diagnosis we have, the gap it named itself was the absence of a
metrics backend to show the working-set trajectory. Prometheus is therefore not
a hypothetical second source; it is the next one, and the suite should be built
so it does not need reshaping when it arrives.

Prometheus meets the contract without a mock at all:

- **Arbitrary queries**: snapshot a real TSDB block (`/api/v1/admin/tsdb/snapshot`,
  or build one with `promtool tsdb create-blocks-from openmetrics`) and serve it
  with a real Prometheus. Every PromQL expression works, because it *is*
  Prometheus. No query-shape guessing, no mock to keep in sync with the server.
- **Rebasing**: shift sample timestamps at scenario-build time so the capture
  window ends at suite start. Same idea as k8stools' `captured_at` →
  `server_start_time`, different substrate.

This is strictly better than a mock MCP server for metrics, and it is the
argument for treating "capture the real thing and re-serve it" as the default
approach for every source rather than a k8stools special case.

### 3.4 Third-party servers — generic MCP record/replay (fallback)

For a server we do not control — an OpenSearch or Loki MCP server someone else
ships — neither property is available on our terms. The fallback is a
record/replay shim at the MCP protocol layer, and k8srca is unusually well
placed to host one: finding [001 F1](001-architecture.md) means **the worker is
the MCP client**, and `tools.py` already wraps every call. One shim covers every
server we ever add.

|  | Captured state (§3.2, §3.3) | Generic transcript proxy (§3.4) |
| --- | --- | --- |
| Unseen queries | Served from the state | Miss |
| Work per new server | A capture path each | Zero |
| Fidelity | Semantic model of the domain | Byte-identical responses |
| Lives in | The server | k8srca, once |

Its weakness is the miss. A miss must be returned as an explicit *unavailable*
result rather than an error or an empty success — [002 §5.1](002-investigation-model.md)
already makes `unavailable` a first-class evidence kind, so the agent has
somewhere honest to put it.

**That honesty is not a licence to tolerate misses.** A run whose miss rate is
high is measuring the fixture, not the agent. The runner records the miss rate
per scenario and **fails the run above a threshold** (proposed: 10% of calls to
that source), rather than quietly producing a degraded score.

So: captured state for sources we own or that can be snapshotted, transcript
proxy only for third-party servers, both behind the same `scenario.yaml`.

---

## 4. Where scenarios come from

### 4.1 Break it for real, then capture

Scenarios are authored by breaking a real cluster and snapshotting the result —
not by writing the state by hand.

This is the central methodological choice and it costs real effort, so the
reason matters. **When you author cluster state, you author the symptom and the
truth together**, and you can only encode failures you already understand. The
result tends to be a tidy cluster where the answer is over-determined and the
agent scores better than it deserves.

Real breakage produces evidence that is self-consistent for free: restart
counts, event ordering, exit codes, log truncation and container ages line up
because they actually happened. It also produces the things nobody would think
to write. The most valuable single observation from our real-cluster run was
that the container's `reason` was `Error` and not the `OOMKilled` the hypothesis
predicted — a runtime labelling quirk. No author invents that. It is now
scenario 1's designed trap (§5).

### 4.2 minikube, not kind

001 §12.5 said kind; that was habit. kind's advantages — fast create/destroy,
CI-friendliness, trivial multi-node — are all about *provisioning clusters in
CI*, and the capture architecture means CI never provisions a cluster. It
replays JSON. The cluster is authoring scaffolding, touched once per scenario.

minikube is what the OTel demo already runs on, what the existing k8stools
fixture was captured from, and what the team knows. `minikube node add` covers
multi-node if a scheduling or node-pressure scenario needs it. 001 §12.5 should
be amended to say minikube.

### 4.3 KWOK for the states minikube cannot produce

Some states cannot be produced on demand at any speed: a ReplicaSet that is
genuinely 145 days old, a 400-pod cluster, sustained node memory pressure, a
precisely ordered event flood. KWOK can express all of these — `Stage` resources
write arbitrary pod status via Go templates, and `Logs`/`ClusterLogs` serve fake
container logs from files, including previous-instance logs.

KWOK is therefore a **scenario generator of last resort**, used where §4.1 is
impossible rather than where it is inconvenient, and its scenarios are marked as
authored so their results are read with the §4.1 caveat in mind.

SimKube was considered and rejected — see §9.

---

## 5. The scenarios

Each scenario exists to discriminate something. A failure taxonomy alone would
produce eight ways of asking "did it find the broken pod"; what follows is
organised by the *reasoning* each one is designed to stress, with the failure
mode as the vehicle.

| # | Scenario | Vehicle | What it discriminates |
| --- | --- | --- | --- |
| 1 | `jvm-oom-on-startup` | JVM with `limit == request`, no heap flags | Handling evidence that contradicts the hypothesis: `reason: Error`, not `OOMKilled`. Does it flag the discrepancy or smooth it over? |
| 2 | `probe-too-aggressive` | Readiness probe with a 1 s timeout on a slow starter | Broken app vs. broken *check*. The pod restarts and looks unhealthy; nothing is wrong with the code. |
| 3 | `image-pull-typo` | Misspelled image tag | **Stopping.** The answer is three tool calls deep. Does it stop, or investigate for thirty? [002 §5.4](002-investigation-model.md) is as much about when to stop as what to conclude. |
| 4 | `pvc-pending-no-storageclass` | PVC referencing a nonexistent StorageClass | Widening the search. The pod's own events say only `FailedScheduling`; the answer lives on another object. |
| 5 | `deploy-regression` | Deployment updated 20 min ago to a broken image, prior ReplicaSet intact | Change correlation. Does it reach for `get_replicaset_summaries` and tie the failure to the deploy? This is the scenario that justifies the k8stools 1.2.0 work. |
| 6 | `innocent-bystander` | Service B fails *because* service A is down | Cause vs. symptom. Two things are broken; reporting both as independent findings is the failure. |
| 7 | `nothing-is-wrong` | Cluster with *unrelated* breakage, asked why checkout is slow | **Whether it can decline** — and resist a tempting wrong answer. Two pods are visibly crash-looping and neither has anything to do with checkout. An agent that always produces a confident cause is worse than useless during an incident. |
| 8 | `evidence-unavailable` | Crash loop whose previous-instance logs were garbage-collected | `unavailable` vs. `absence` ([002 §5.1](002-investigation-model.md)). "No error was logged" and "the logs are gone" are different claims. |

Scenarios 1 and 5 carry a `follow_up`, so they also exercise multi-turn
continuity — which is the stated gate for [002 §9's L2](002-investigation-model.md#9-staging-from-prompt-to-method):
"multi-turn threads demonstrably reuse prior evidence."

**Scenario 7 is the most important one in the suite and the one a naive suite
omits.** Every other scenario rewards finding something. Only this one rewards
not finding something, and without it the suite actively trains us toward an
agent that confabulates under pressure.

It originally specified a *healthy* cluster. The cluster we author from has two
long-running JVM crash loops that have nothing to do with checkout, and rather
than heal it for this one capture, the scenario now keeps them — the harder and
more honest version of the test. A pristine cluster only asks whether the agent
can say "nothing is wrong"; this asks whether it can say so *while looking at
something that is plainly broken*, which is the situation an SRE is actually in
when they ask about latency during an unrelated incident. The failure it now
catches — reaching for the nearest visible breakage and asserting a causal link
to the question — is a real one that the original framing could not produce.

---

## 6. Ground truth and grading

### 6.1 The closed world makes fabrication mechanically checkable

A capture is a *closed world*: every pod, container, event, image, restart count
and log line the agent could legitimately cite is in the file. So any concrete
noun in the answer can be checked against it — and anything that is not there
was invented.

This is a stronger property than determinism, and it is the best argument for
the capture approach over a live cluster. Against a live cluster you can only
ask whether an answer sounds right. Against a capture you can ask whether every
entity it names exists.

**The capture is not the whole closed world, and assuming it was made the first
grader wrong.** The agent also carries the cluster-architecture skill, and cites
it constantly: declared images from charts, drift between declared and observed,
probe configuration, chart-level settings. None of that is in a k8stools capture
— it is not cluster state. Graded against the capture alone, scenario 1's answer
came back with nine fabrications, and all nine were real facts the agent had
read out of `arch_query.py`. Adding the skill to the grader's reference dropped
that to three, and those three were genuine.

So a scenario snapshots `architecture.json` beside `k8s.json`, and `truth.yaml`
pins its digest the same way it pins `captured_at`. Two sources, both versioned
with the truth written against them.

**That fixes grading and does not fix running.** The snapshot is read by the
grader; the agent answering a scenario still gets whatever skill bundle was last
`k8srca sync`ed, which is rebuilt from the *live* cluster by `k8srca arch build`
on its own schedule. The same capture and the same truth can therefore produce
different answers after an unrelated `arch build`, and the suite would report a
regression that is skill drift. §3.1's replayability contract covers data
sources reached through MCP; a skill baked into the agent is neither replayed
nor pinned, and needs its own answer — see §11.7.

### 6.2 Two kinds of check

**Deterministic assertions** run first, cost nothing, and never flake. They
gate, so they must fail closed on fabrication and open on everything else: a
check that fails a correct answer gets switched off, and then it catches
nothing. Thresholds come from measurement once there is any — every one set
from an estimate here produced a false failure first.

- every pod, container and namespace named in the answer exists in the capture
- every numeric claim that is checkable (restart counts, replica counts,
  revisions, limits) matches the capture
- required tool calls were made (scenario 5: `get_replicaset_summaries`)
- tool calls stayed inside `budget`; cost is reported but does not gate
- the source miss rate (§3.4) is under threshold

**Rubric grading** handles what cannot be asserted. A grader model receives the
answer, `truth.yaml`, *and the capture*, and scores dimensions rather than
producing an overall impression:

| Dimension | Question |
| --- | --- |
| Cause | Is the stated root cause the one in `truth.yaml`? |
| Evidence | Is each claim tied to a tool result that supports it? |
| Rivals | Was every rival in `truth.yaml` given a disposition — confirmed, weakened, refuted, or could-not-check? "Worth checking later" does not count ([002 §11.0](002-investigation-model.md#11-open-questions)). |
| Traps | Was the designed trap handled as specified? |
| Gaps | Were known-unavailable things reported as unavailable rather than asserted away? |
| Restraint | Scenario 7: did it decline to invent a cause? |

Giving the grader the ground truth *and* the capture turns it from a judge into
a checker. That matters because we are using Claude to grade Claude, and a
grader asked for an opinion shares the generator's blind spots; a grader asked
"is this claim supported by this file" largely does not.

### 6.3 `truth.yaml`

```yaml
id: jvm-oom-on-startup
cause:
  summary: >-
    The ad container's memory limit equals its request (300Mi) with no JVM heap
    sizing, so the JVM's default heap exceeds the cgroup limit and the kernel
    kills it during startup.
  must_identify: [ad, otel-demo, memory limit]
rivals:                       # each must be dispositioned, not merely mentioned
  - id: memory-leak
    disposition: weakened     # container dies in ~2s; no time to leak
  - id: workload-spike
    disposition: refuted      # dies before serving traffic
  - id: node-pressure
    disposition: refuted      # node reports no memory pressure
traps:
  - id: reason-says-error
    expect: flagged
    note: >-
      lastState.terminated.reason is "Error", not "OOMKilled". The conclusion is
      still correct, but asserting the OOM killer fired without noting the
      discrepancy is a failure.
gaps:
  - id: no-metrics
    expect: reported_unavailable
```

**Truth is versioned with the capture.** Re-record `k8s.json` and `truth.yaml`
is suspect until re-reviewed: restart counts move, ages change, and a trap can
disappear. The runner refuses to grade a scenario whose capture is newer than
its truth file.

### 6.4 Sampling and reporting

The agent is nondeterministic, so a single run is a sample. Default **n=6** per
scenario, reporting per-dimension pass rates rather than a single score — a
scenario that passes four times in six is a real signal and a pass/fail gate
would hide it.

**n=3 was the original default and it is not enough.** Two n=3 runs of
`jvm-oom-on-startup`, same capture and a byte-identical grader, read `traps`
1/3 and then 3/3 — 4/6 pooled. The 3/3 was unanimous, so nothing flagged it,
and it was written up as a stable pass. By the rule of three, *r* unanimous
runs put the 95% upper bound on the unseen outcome at 3/*r*: at n=3 that bound
is 1.0, so the reading excludes nothing at all. Six is the first n where
unanimity says more than "at least one of these happened", and a dimension
sitting near 50% needs nearer ten. The runner reports both ways a reading can
be uninformative — split, and unanimous-but-thin — because they look different
and only the first is obvious.

The output is a comparison against the recorded baseline, not a number:

```
scenario                     cause  evid  rivals  traps  gaps   Δ vs baseline
jvm-oom-on-startup            3/3   3/3    3/3    2/3    3/3    traps -1
nothing-is-wrong              3/3    —      —      —      —     =
deploy-regression             2/3   3/3    2/3    3/3    3/3    cause -1  ⚠
```

---

## 7. Running it

```bash
k8srca scenario list
k8srca scenario record <id>     # capture from the live cluster into a scenario dir
k8srca scenario run  [<id>...]  # stand up sources, run, grade
k8srca scenario baseline        # record the current results as the comparison point
```

`run` spends money on every invocation, so it is a command, not a pytest target.
The *grading logic* is ordinary code and gets ordinary hermetic tests —
rubric parsing, the closed-world entity check, budget enforcement and the
capture-newer-than-truth guard all run in `uv run pytest` against fixtures.

Cost was extrapolated from the multi-session measurements ($0.06–0.09 for a
real investigation, $0.03 for a trivial one) at roughly $2–3 for a full
8-scenario run at n=3. **The first measured run says that is about 3x too
low.** `jvm-oom-on-startup` cost **$0.25** in 24 tool calls and 151 s of active
time — one run, both turns, coordinator plus one delegated log check. At that
rate a full suite at n=3 is nearer **$6**.

It is one sample of the most expensive shape we have (a follow-up turn and a
subagent), so it is an upper bound rather than the average, and scenarios like
`image-pull-typo` should come in far below it.

**Grading is not free either, and is the larger half.** The grader is shown the
truth, a digest of the capture and the architecture snapshot — 75K tokens for
scenario 1, or $0.38 on Opus. The reference sits behind a cache breakpoint and
is deliberately independent of the answer, so the n−1 later runs of a scenario
read it from cache at about a tenth of that. One scenario at n=3 is therefore
roughly $0.75 of runs plus $0.45 of grading, and eight scenarios nearer **$10**
than the $2–3 first estimated. Still cheap against the cost of an undetected
prompt regression, but worth knowing before enabling n=5 for baselines. The conclusion is unchanged —
$6 is still cheap enough to run on every prompt or KB change — but the budgets
in individual scenarios were written against the low estimate and bind at the
wrong moment: scenario 1's 25-call, $0.25 budget was exceeded-or-equalled on
both axes by a run that answered correctly.

---

## 8. Anti-goals

- **Not a benchmark.** No headline score. The output is a diff against a
  baseline.
- **Not a test of the platform.** §1.
- **Not a scenario per Kubernetes failure mode.** Scenarios are expensive to
  author and each must earn its place by discriminating something no other one
  does. Eight good scenarios beat forty that all ask "find the broken pod."
- **Not a gate that blocks merges initially.** Until the baseline is stable
  enough that we trust its variance, it reports; it does not block.

---

## 9. Rejected alternatives

**SimKube.** A record-and-replay simulator for the Kubernetes *control plane*,
built on KWOK, aimed at testing autoscalers, schedulers and upgrades against
recorded production traces. Its value is fidelity *over time* — pod churn as a
time series. We need a handful of static broken states. Adopting `sk-ctrl`,
`sk-tracer`, `sk-driver` and a trace store to use one feature of the system is
not justified. Worth revisiting for exactly one thing: recording the shape of a
large real cluster to test the agent under context-window pressure.

**KWOK as the foundation rather than the fallback.** Capable — it can fake
container statuses, restart counts, termination reasons and per-container logs
including previous instances. Rejected as the *primary* source because §4.1: you
author both the symptom and the truth, so the suite only ever tests failures you
already understood. Retained for §4.3.

**A live cluster per run.** Simplest to build — no capture, no replay. Rejected
on three counts: restart counts climb between runs, so assertions must tolerate
drift; CI would need Docker and a cluster; and a suite that needs infrastructure
to run is a suite that stops being run.

**A call transcript instead of captured state.** Record the agent's tool calls
and responses once, replay them. Rejected for the primary source: the agent
makes 30+ k8stools calls per investigation and its path varies run to run, so
misses would be routine. Retained for third-party servers (§3.4), where call
volume is low.

**kind.** See §4.2 — its advantages evaporate once CI does not provision
clusters.

**Exact-match answer checking.** Measures phrasing, not reasoning, and would
fossilise the current output format. §6.2 instead.

---

## 10. Staging

| Step | Deliverable | Gate |
| --- | --- | --- |
| S0 | k8stools capture/replay with the three additions | `--state-file` serves a captured OTel-demo cluster |
| S1 | Runner + scenarios 1 and 7 + deterministic checks only | A scenario runs end to end and the closed-world entity check catches a fabricated pod name |
| S2 | Rubric grader; scenarios 2–6, 8 | Grading agrees with a human read on a scenario we already understand |
| S3 | Baseline recorded; KB-on/KB-off comparison | The first real measurement: does the knowledge base beat the L0 floor? |
| S4 | Prometheus source (§3.3) | A scenario answers a question that needs a metric |
| S5 | Generic MCP proxy (§3.4); KWOK scenarios (§4.3) | A third data source lands without reshaping the suite |

S3 is where this stops being infrastructure and starts paying: it is the first
time we can answer an open question instead of arguing about it.

---

## 11. Open questions

1. **Grader bias.** Claude grading Claude. §6.2 mitigates by giving the grader
   the ground truth and the capture, making it a checker rather than a judge —
   but this should be validated against human grading on the first few
   scenarios before the baseline is trusted.
2. ~~**Sample size versus cost.**~~ **Answered: n=6.** Per-dimension variance
   is not low. Across 13 runs of one scenario, `cause` and `gaps` were 6/6
   while `traps` was 4/6 and `evidence` 1/6, and an n=3 reading of `traps` came
   back unanimous in both directions on different days. n=1 is not a halving of
   cost, it is a coin toss with a number printed next to it.

   The cost model moves with it. At ~$0.40 a run plus ~$0.46 of grading for the
   first and ~$0.05 cached thereafter, one scenario at n=6 is about $3 and an
   eight-scenario suite nearer **$25** than the $2–3 in §7. Still cheap against
   an undetected prompt regression, but no longer cheap enough to run on every
   edit without thinking — which argues for running the scenarios a change
   plausibly touches, and the full suite before a baseline.
3. **Whose fault is a failure?** A scenario can fail because the prompt is
   worse, or because the capture drifted, or because the grader is wrong. The
   report needs to make that distinguishable or every red line costs an
   investigation of its own.

   Two of those are now pinned rather than left to be argued about. The capture
   and the cluster-architecture skill are pinned in `truth.yaml` (§6.1, §6.3),
   and the *grader* is pinned in the baseline: its rubric, output schema, model
   and digest scope hash to one digest, and a baseline recorded under a
   different one reports "grader changed" instead of deltas. This was not
   hypothetical. Widening the digest and splitting unverifiable claims out of
   unsupported ones moved `rivals` from 0/3 to 2/3 across two runs of one
   unchanged scenario, and the first reading had already been written up as a
   stable failure of the agent. The instrument moved; the agent did not.

   A third source of red lines has no pin and probably cannot have one: the
   harness's own rules. Estimate-derived budgets failed four correct answers,
   and `numeric_claims` failed a fifth for deriving "154 days at the 5m backoff
   cap would produce ~44,000 restarts" to argue the looping was intermittent.
   Every one was a rule written from what an answer was expected to look like,
   meeting one that was better than expected. Deterministic checks should fail
   closed on fabrication and open on everything else, and their thresholds
   should come from measurement once there is any.
4. **Does the frozen clock change behaviour?** Freezing removes intra-run drift,
   but an agent that sees identical ages across a five-minute session is seeing
   something no real cluster does. Probably harmless; worth one A/B before
   assuming it.
5. **Re-recording cadence.** Captures encode a k8stools version and a cluster
   shape. When k8stools adds a tool, existing captures cannot answer it — the
   mock will report unavailable for a tool the real system has. Do captures get
   re-recorded on every k8stools release, or do scenarios pin a k8stools
   version?
6. **Skills are an unpinned input (§6.1).** The agent's cluster-architecture
   skill is rebuilt from the live cluster and synced independently of any
   scenario, so it can change under a fixed capture. Options: pin a skill
   bundle per scenario and sync it before a run, which makes scenarios
   genuinely hermetic but means the suite measures an agent slightly unlike
   production's; or accept the drift and use the snapshot's digest to *detect*
   it, so a red line can at least be attributed. The digest is recorded either
   way, so this is a question of what to do when it changes, not of whether we
   notice.

7. **Where scenarios live.** `tests/scenarios/` keeps them with the code, but
   captures with logs are large. Secrets are no longer the blocker — k8stools
   captures are redacted by default, matching whatever redaction the server in
   front of the same cluster would apply — so the remaining question is size,
   and whether large JSON fixtures belong in the repository or in an artifact
   store the runner fetches.
