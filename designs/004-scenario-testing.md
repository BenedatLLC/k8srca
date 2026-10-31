# Design 004 — Scenario testing

**Changed by [010](010-facts-and-short-answers.md) (done, 2026-10-23).**
Sections affected say so.
- **The checklist is the measurement.** Each scenario is graded against a
  checklist of statements (010 §7). The rubric of §6.2 is opt-in, with
  `--rubric` (010 §9.10).
- **The suite is ten scenarios**, from §5, with substitutions (010 §8,
  §9.9–9.12).
- **The screening rule of §6.4 is replaced in practice.** Each change was
  measured at n=3 on the scenarios it reaches, and merged on its step's own
  done criteria (010 §10).

**Status:** done in part, 2026-10-24 (owner); revised 2026-10-30.
- **Done:**
  - S0–S4 and X1 (§10), with X1's four more imports (ITBench 24, 80, 102
    and 105) landed 2026-10-30 in §12.3. Each has room, and 80's cause was
    missed in every run.
  - The suite is fifteen scenarios, all with checklists, and the checklist
    baseline is recorded (step 1, §10.2–§10.4; §12.3 step 8; §12.4). The gate is
    each checklist's marked diagnosis, and §6.4 has the screening rule.
  - jvm-oom-with-metrics re-recorded with a full week of metrics and
    re-baselined alone, 2026-10-30 (§10.4,
    [issue #3](https://github.com/BenedatLLC/k8srca/issues/3); 009 §7.7).
  - ITBench Scenario 31, the NetworkPolicy decoy, landed 2026-10-30 (§12.4).
    Its diagnosis was missed in every run.
- **What step 7 found** is filed as design input, not done here
  ([issue #10](https://github.com/BenedatLLC/k8srca/issues/10)). So are
  the two misses from the jvm-oom-with-metrics re-baseline (009 §7.7):
  - `no-onset-date`, 10/10: the 193-day-old image bump offered as the
    trigger;
  - `cause-no-heap-sizing`, 5/10: the limit named, the missing heap flags
    not.
- **Possible remaining work,** no tickets yet. Each is its own work if
  wanted:
  - **Grader against a human read** (§11 question 1). Hand-grade about ten
    saved answers and measure agreement with the checklist grader.
  - **Frozen-clock A/B** (§11 question 4).
  - **Re-recording policy** (§11 question 5): when captures are re-recorded
    against a new k8stools release. 4.4.0's "not recorded" made the safe
    reading the default.
  - **Scenario 8, `evidence-unavailable`** (§5), from ITBench Scenario 34
    (§12.3).
  - **X2:** the ITBench feature-flag and Chaos Mesh imports (§12.1). They
    need metrics and traces.
  - **S5:** the generic MCP proxy and the KWOK scenarios (§3.4, §4.3).
  - **k8stools gaps:**
    - The workload report shows one pod's log for a multi-pod Deployment,
      and cuts long lines short.
    - Events can't be filtered by name prefix or by the pods a workload
      owns.
  - **kubewiki gap:** a workload with no pods loses its outbound edges.
- **Dropped:**
  - X3, SREGym: reserved as a hold-out candidate for 006 (§12.2).
  - Scenario 6, `innocent-bystander`: 24 and 105 already test cause
    against symptom.
  - §11 question 7: answered by issue #7.

**Before that:** in progress from 2026-10-23 (owner). Proposed 2026-09-12. S0 landed in **k8stools 2.0.3** (2026-09-19):
`k8s-capture-state` writes a capture, `--state-file` replays it, and
`--state-time frozen` pins the clock. A 27-pod OTel-demo capture is 2.4 MB at
200 log lines per container.

**Was blocked on [k8stools#6](https://github.com/BenedatLLC/k8stools/issues/6)** (closed since).
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

**Since 2026-10-26 (issue #7),** the capture and the pinned skills are not
committed here. They live in `BenedatLLC/k8srca-scenario-data`, beside the
metrics blocks (009 §7.4): `captures/<digest>.json.gz` and
`skills/<digest>.tar.gz`. `scenario.yaml` pins the capture
(`sources.k8stools.digest`, the digest of its JSON), and `truth.yaml` already
pins each skill by its bundle digest. Both are fetched on first use to the
paths above, which git ignores, and `k8srca scenario fetch` fetches them all.
Tests never fetch: a test that needs a scenario's data skips until it has
been fetched.

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

*009 (proposed) works this out against the demo cluster, and captures by query rather than by TSDB snapshot: the snapshot needs the admin API, which stays off (009 §5).*

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
| 7 | `nothing-is-wrong` | Cluster with *unrelated* breakage and a clean checkout path (below), asked why checkout is slow | **Whether it can decline** — and resist a tempting wrong answer. Two pods are visibly crash-looping and neither has anything to do with checkout. An agent that always produces a confident cause is worse than useless during an incident. |
| 8 | `evidence-unavailable` | Crash loop whose previous-instance logs were garbage-collected | `unavailable` vs. `absence` ([002 §5.1](002-investigation-model.md)). "No error was logged" and "the logs are gone" are different claims. |

**Changed ([010](010-facts-and-short-answers.md) §8, done):** 010's step 5
recorded the scenarios on benben, each with a Prometheus window and a
checklist from the start (010 §9.9–9.12). jvm is frozen: no change is tuned
on it alone.
- **Recorded as listed:** 3 `image-pull-typo`, 4 `pvc-pending-no-storageclass`
  and 5 `deploy-regression`.
- **2 became `liveness-probe-wrong-port`:** a liveness probe on a port the
  app does not listen on. It is the same broken check on a healthy app.
- **7 was recorded clean, asking about cart** (below).
- **Added:** `downstream-latency`. frontend is slow because product-catalog's
  CPU limit throttles it, which tests latency whose cause is a dependency's
  resources (010 §9.11).
- **Dropped:** `image-slow-load`. The demo flag acts in the browser, and
  benben's load generator sends no browser traffic (010 §9.10).
- **Not recorded:** 6 `innocent-bystander` and 8 `evidence-unavailable`.

The suite in `tests/scenarios/` is these six plus `jvm-oom-on-startup`,
`jvm-oom-with-metrics`, `checkout-gc-pressure` and
`itbench-33-invalid-node-selector`.

Imported scenarios sit beside these and are tracked in §12:
`itbench-33-invalid-node-selector` discriminates severity -- a blocked rollout
behind a healthy previous revision, which the source benchmark itself called an
outage.

`checkout-gc-pressure` began as the first recording of scenario 7 and is not
on the list above: a subtle real cause beside loud unrelated breakage. Asked
why checkout is slow, the capture's one metrics-server sample shows checkout
above its Go `GOMEMLIMIT` (19Mi against 16MiB, 20Mi limit) at 118m CPU, while
product-catalog with identical settings idles at 7m -- and ad and
fraud-detection crash-loop, accounting is OOM-killed, postgresql logs duplicate
keys from accounting, and frontend logs ad's connection refusals. It
discriminates widening and noise rejection: finding the one quiet lead, at the
right confidence, without blaming anything loud.

**Scenario 7 needs a clean path before it can be recorded.** The first
recording asked about checkout on the cluster as it stands, and the truth said
nothing near checkout was wrong. Every run of the first batch disagreed, and
three of them were right: the truth had been written without looking at the
`metrics` section. So the order is now: (1) remove the lead -- raise checkout's
and product-catalog's memory limits, or drop their `GOMEMLIMIT`, and let the
load generator run long enough for a steady state; (2) record; (3) before
writing a word of truth, confirm from the capture -- usage sample included --
that every service on checkout's path is unremarkable. The unrelated breakage
stays: declining *while looking at something plainly broken* is the test.

**As recorded (2026-10-23, 010 §9.11):** checkout is not healthy at
baseline. It is garbage-collecting constantly at its 20Mi limit, which is
checkout-gc-pressure's cause. So the clean-path recipe above was not used,
and the question asks whether cart is broken instead. No fault was injected.
At 010's close, the owner allowed one hedge: shipping's recent outage may
explain the report, if the customer's timing matches it, because frontend's
cart page fetches shipping quotes (010 §9.13).

Scenarios 1 and 5 carry a `follow_up`, so they also exercise multi-turn
continuity — which is the stated gate for [002 §9's L2](002-investigation-model.md#9-staging-from-prompt-to-method):
"multi-turn threads demonstrably reuse prior evidence."

**A trap can be wrong, and the capture is what settles it.** Scenario 1 shipped
with a trap forbidding the agent from reading its two crash loops as a shared
condition — on the theory that they were independent instances of one
misconfiguration and tying them together was an error. The capture disagrees:
exactly two containers in the cluster carry a 300Mi limit, they are exactly the
two crash-looping, and every other container has a different limit and is
Running. "The 300Mi services are under-provisioned" is the best-supported
inference available, and the trap failed answers for reaching it.

The same mistake was then made twice more, in the neighbouring trap. It said
`accounting`'s large restart count was "normal history rather than a current
fault" — and the capture says `last_state.reason: OOMKilled`, a ~15-minute
lifetime, and `1/1` ready. It is genuinely memory-pressured, so "three services
here are memory-starved" was correct and the trap failed answers for saying so.
Two captures a week apart both showed it, so this was never a transient. The
`traps 4/6` reported as a real agent error was substantially the truth being
wrong.

Worse, the hand-written intent note for that service asserted the same falsehood,
which is the failure mode the `documented` provenance was always going to have:
it is the one source that can be wrong rather than merely stale. The corrected
pair keeps the distinction that does hold — `ad` and `fraud-detection` die during
startup and never become ready, `accounting` completes startup and is killed
between useful work — and forbids only flattening those severities together.

The pattern across all three: a trap written from a mental model of what a good
answer looks like, meeting a capture that disagreed. It was caught by asking why
a dimension was unstable rather than assuming the agent was at fault — `traps` read 1/3, 3/3 and 5/6 across batches. The rule it
suggests: when a dimension disagrees with itself, suspect the truth before the
agent, because a mis-specified trap is indistinguishable from a flaky one in the
report and much cheaper to fix. The replacement forbids only the claim the
capture actually contradicts — a *dependency* between the two services — and the
neighbouring `accounting` trap now marks where the shared-cause reading stops
being supported, at a pod that is neither at 300Mi nor failing.

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

*(Since resolved: the runner uploads each scenario's pinned skill as that
scenario's own skill object and points the scenario's agent at it, so the agent
reads the snapshot the grader does — §11.6.)*

**Two forms of the skill (2026-10-12).** The skill is becoming kubewiki's wiki
(kubewiki design §10, step 4), and whether the agent does as well with it is a
scenario question. So a scenario carries both: the legacy snapshot in
`skill/cluster-architecture/` and the wiki in `skill/wiki/cluster-architecture/`,
built by `k8srca scenario build-wiki` from the scenario's own capture, replayed.
`truth.yaml` pins each form it was reviewed against (`capture.skill_digests`),
and a run against a form its truth was never read against is refused: a trap
written for what the snapshot says may not hold for what the wiki says. A run
chooses with `--arch legacy|wiki`; the grader's reference is that form's data
file (`architecture.json` or `graph.json`), under one rubric worded for both, so
the two are graded by the same instrument.

Both forms, like the capture, have lived in the scenario data repository since
2026-10-26, fetched by the digest `truth.yaml` pins (§2, issue #7).

### 6.2 Two kinds of check

**Changed ([010](010-facts-and-short-answers.md) §7, done):** the LLM
judgment is a per-scenario checklist (`checklist.yaml`) of statements an answer
should and should not make, and the dimensions below became groups of items.
- **How it grades.** The grader answers yes, no or unclear per item. It is
  not told which way an item is expected. It reads the checklist and the
  posted answer, whose Evidence block prints the cited facts, but not the
  capture.
- **How steady it is.** The bar is 97% agreement overall, with no item split
  on more than one answer (010 §9.2). `scenario checklist <runs> --grades 3`
  measures it.
- **Cost.** A checklist grading costs about $0.07. The rubric below costs
  about $0.70 an answer, since it reads a reference of about 190K tokens
  (010 §9.10).
- **When the rubric runs.** `scenario run` grades on the checklist. The rubric
  runs only with `--rubric`, or for a scenario with no checklist, such as
  the hold-out suite's (006).

**Deterministic assertions** run first, cost nothing, and never flake. They
gate, so they must fail closed on fabrication and open on everything else: a
check that fails a correct answer gets switched off, and then it catches
nothing. Thresholds come from measurement once there is any — every one set
from an estimate here produced a false failure first.

- every pod, container and namespace named in the answer exists in the capture.
  An object the capture's events name counts, so a pod deleted before the
  capture but named in its events is not fabricated (issue #4, fixed
  2026-10-23; 010 §9.12)
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
| Standing | Was every candidate in `truth.yaml` placed (most likely, not ruled out, or ruled out) where the truth or an `also_accept` puts it? "Worth checking later" does not count ([002 §11.0](002-investigation-model.md#11-open-questions)). Was `rivals` until 008 |
| Calibration | Is the leading cause held as strongly as the capture allows, no more and no less: its strength inside the truth's range? Is every candidate ruled out on a fact that excludes it? (008 §6.2) |
| Traps | Was the designed trap handled as specified? |
| Gaps | Were known-unavailable things reported as unavailable rather than asserted away? |
| Next steps | Does a next step bear on what would change the leading cause, or on a candidate not ruled out? A lean: read, not yet with a measured spread |
| Restraint | Scenario 7: did it decline to invent a cause? |

**Standing and calibration replaced rivals (008 §6, 2026-10-18).** The old
dimension asked for a verdict. In jvm, "confirmed" OOM meant "the hypothesis
that survives", and answers that read it as proven failed. The truth file now
says what the capture allows. The grader reports what the answer did with
each candidate: its standing, the strength read from its own words, and for
one ruled out, whether a fact excludes it. `settle_candidates` then compares
that with `truth.yaml` in code, the same way on every run. Splitting the two
keeps a grade one step off from looking like a candidate dropped.

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
    disposition: weakened     # no memory time series to exclude growth
    also_accept: [refuted]    # ...or: dies in startup, no operating period to leak over
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

**Since 008 (2026-10-18),** `rivals` is `candidates`. Each has a `standing`,
and a `strength` range where it is not ruled out. The cause has a `strength`
range of its own:

```yaml
cause:
  most_likely: memory-limit-too-low
  strength: [moderate, strong]
candidates:
  - id: memory-leak
    standing: ruled_out
    also_accept: [not_ruled_out]
    strength: [weak]
```

Ours were migrated by 008 §4.3's table, and their ranges were set by reading
each capture. A file still written with `rivals` reads by the same table, with
no ranges. That keeps the hold-out suite's files (006) working until their
author migrates them.

**`also_accept` is for rivals the capture sits on the boundary of**, where two
dispositions each follow from a sound reading and demanding one grades the
author's preference rather than the agent. It is not a way to soften a rival
the agent keeps getting wrong: the note must say what argument leads to each.
`memory-leak` above earned it after 4 of 6 runs refuted it on the startup
argument and lost the dimension for it.

**Truth is versioned with the capture.** Re-record `k8s.json` and `truth.yaml`
is suspect until re-reviewed: restart counts move, ages change, and a trap can
disappear. The runner refuses to grade a scenario whose capture is newer than
its truth file.

### 6.4 Sampling and reporting

**Since 2026-10-24 the suite reads the checklist (step 1, §10.2–§10.4).**
- **n=3 per scenario**, as every 010 result was read. The baseline is one
  batch of all ten scenarios at n=3, with each checklist past the agreement
  bar on that batch's answers.
- **The gate is the diagnosis.** Each checklist marks the item or items
  stating its diagnosis (§10.3). A scenario **regresses** when:
  - fewer of its runs meet every diagnosis item than in the baseline, at
    the baseline's rate; or
  - its mean checklist score is lower than the baseline's by more than the
    baseline's spread for that scenario, its highest run less its lowest.
- **Anything smaller is noise.** The table prints it as `noise` and shows
  each moved cell as `was→now`. The groups (cause, ruled out, traps, gaps,
  next) are read for where to look, never gated.
- **The baseline's spreads** are 0 to 2 items (§10.4). A scenario with
  spread 0 (downstream-latency, itbench-33) is flagged on any drop in its
  mean, and one with spread 2 (checkout, nothing-is-wrong) only on a drop of
  more than 2. That is n=3's resolution, accepted for it: a flag on a spread-0
  scenario is read against its runs before it is believed.
- **The grader is the agent's smaller source of noise here:** 0.6% of the
  baseline's 1,035 gradings disagree with their answer's majority, against a
  spread of up to 2 items between runs.

The rest of this section is the rubric's, before 010. It still governs runs
graded with `--rubric`.

The agent is nondeterministic, so a single run is a sample. Default **n=5** per
scenario to screen a change, and **10 or more, pooled,** for a baseline (the
screening rule is at the end of this section). Reporting is per-dimension pass
rates rather than a single score — a scenario that passes four times in six is
a real signal and a pass/fail gate would hide it.

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

Beside the pass rates, the table and `scenario compare` give **unsupported
claims per run** (`unsup`). `evidence` fails an answer on any one claim the
capture does not support, which makes it the right gate and a blunt
measurement: a change that halves those claims without ending them reads as no
change. That is how it went on `jvm-oom-on-startup` — a skill rule, then a
coordinator rule, aimed at the claims its answers kept making (onset dated to
deployment, "the only JVM services"); evidence stayed at 2/6 then 1/6 while
the claims moved from 11 to 9 per six runs, which is noise too. The count does
not replace the dimension and has no baseline rule of its own yet; it is there
so the next such change is read on a scale that can move.

**The agent varies more than the grader, and the noise rule follows the
agent.** The rule was "a move of one run in six is noise; two or more counts",
and the majority-of-three grader (§11.2) made it hold *for the grader*. It
never measured the other half: the same skill, run again, at n=6. On
2026-10-16 that was measured, by accident and then on purpose, and the rule
does not survive it:

| comparison | input | moves |
| --- | --- | --- |
| `jvm-oom-on-startup`, recorded baseline vs a repeat | identical skill, prompts, capture, grader | evidence 2/6 → 0/6, **rivals 5/6 → 2/6**, traps 6/6 → 5/6 |
| `checkout-gc-pressure`, baseline vs the 007 stage-1 batch | `kb_query` output unchanged for every lookup the sessions made | **evidence 0/6 → 3/6, traps 3/6 → 6/6** |
| `itbench-33`, the same | likewise | evidence 4/6 → 2/6 |

The repeat failed rivals the way stage 1 was first suspected of failing it
(two OOM sub-causes silently dropped), and failed the two traps stage 1 was
first blamed for (restart cadence stated in seconds, the event window read as
the pod's age). Every regression in that gate was in the old skill's own
spread.

So, until a larger sample says otherwise:

- **A single n=6 batch against the recorded baseline resolves a move of four
  runs or more.** The largest move seen on unchanged input is three. Four is
  the smallest that has not yet appeared as noise -- a floor from three
  comparisons, not a bound.
- **A smaller move is a lean, until both arms are measured in the same
  sitting**: the change at n=6, and the baseline skill re-run at n=6 alongside
  it, with `scenario compare` between the two. Pool to n=12 per arm before
  calling a two- or three-run move a result.
- **A recorded baseline is one sample.** jvm's line in `baseline.json` reads
  rivals 5/6; its repeat read 2/6. Comparing against it alone mistook a good
  draw for the old skill's level.
- **The baseline pools across sittings while its skill and capture are
  unchanged**, with a drift check of n=6 alongside each new arm. A scenario
  answers from a fixed capture, so the old skill's input does not change
  between days. Only the service side can drift, and the check would catch
  that. On jvm, three sittings of the stage-1 skill read rivals 9/12, 12/12
  and 5/6, which is spread rather than drift. 30 pooled runs are a steadier
  reference than any one sitting's 12 (007 §11.2).

**Screening at n=5, against a pooled baseline (2026-10-18).** Arms of 10
became the habit after the rule above, and on checkout a pair of them cost
about $25 of sessions and three hours, since scenarios share one replay
container and run one at a time. Most changes either move a dimension a lot or
not at all, and an arm of 5 tells those apart when the baseline is large. The
comparison's spread is mostly the small arm's: at a 50% pass rate, 5 runs
against a pooled 10 leaves a standard deviation of about 27 points on the
difference, against 29 for the 6-against-6 comparisons whose largest noise
move was 50 points (three runs in six). So:

**Changed ([010](010-facts-and-short-answers.md) §8, §10, done):** two n=5
arms of an identical agent differ by 2 or more on evidence about 30% of the
time, and the grader disagrees with itself on it 11–16% of the time. So the
rule below cannot separate small effects from noise.

Under 010, each change was measured at n=3 on every scenario it reaches,
graded on the checklists. It merged on its step's own done criteria, read
scenario by scenario against the noise at n=3 (010 §9.4, §9.6). The rule
below is the rubric era's.

- **The baseline is 10 or more runs**, pooled across sittings while its skill
  and capture are unchanged, as above.
- **A change is screened at n=5.** Scale the baseline's rate to five runs.
  A move of **three or more** is a result. A move of **two** is extended to
  n=10 in the same sitting and then read by the rule for 10. A move of **one
  or none** says there is no large effect. Whether to keep the change is then
  a judgement on what it is for, not a reading of the number.
- **A gate recorded as a decision** in a design document is run at 10 and
  graded with `--grades 3`.
- **Run only the scenarios the change can reach.** Write that down first, from
  what the change touches. For example, a rule about scheduler reasons reaches
  itbench-33 but not jvm or checkout. A scenario left out is not "unchanged":
  it is unmeasured. The full suite runs before a new baseline is recorded,
  and every time the investigation rather than the answer changes: tools,
  `kb_query` output, the coordinator prompt's method.
- **There is no shortcut for changes to how the answer is written**
  (tried 2026-10-18, dropped). The idea was to replay a saved session up to
  its first answer and ask only for the answer again, under the changed skill.
  It was built (commit 8b43852, removed after): it replayed each session's
  history through the Messages API with the session's own tools, re-ran its
  bash in a network-less sandbox with the current skills, served repeat
  cluster calls from the record and refused new ones. On the ten step-3b
  checkout runs (008 §8.1), under the skill those runs used, it got the cause
  right in 7 of 11 answers, where the live runs were 10 of 10. The same run
  screened twice gave different causes, so the error was in the step that
  writes the answer and not in particular runs. Two reasons were seen:
  - **The session's reasoning is not in its history.** The platform records
    that the model thought, not what it thought. The live coordinator wrote
    its answer with its own reasoning in context. The replay has only the
    results, re-weighs them in one pass, and sometimes lands on another
    cause, such as frontend's connection errors to `ad`, which the live run
    had already discounted.
  - **At the cut, the replay still wanted evidence.** Every logged screen
    asked for cluster calls the session never made, at the point where the
    live model chose to answer.

  It also cost about two thirds of a live run per answer, since the answer
  turn and the revision turn at high effort are most of a session's tokens.
  Two of ten sessions could not be replayed at all: they made a tool call
  while earlier results were still outstanding. So changes to how the answer
  is written are measured like any other: live, at n=5, on the scenarios they
  reach.

Read earlier conclusions in this document with that in mind. The ones resting
on moves of five or six runs stand: rivals 0/6 → 5/6 from the discriminators,
and 5/6 → 0/6 with the KB off (§10.1). The ones resting on two (most evidence
readings, and the coordinator- and skill-rule experiments above) were never
resolvable at n=6.

---

## 7. Running it

```bash
k8srca scenario list
k8srca scenario record <id>     # capture from the live cluster into a scenario dir
k8srca scenario run  [<id>...]  # stand up sources, run, grade
k8srca scenario baseline --agreement G   # promote the last run's results; G is `scenario checklist F --grades 3` on its answers
k8srca scenario run <id> --local-skill k8s-rca   # measure a working-tree skill change
k8srca scenario backfill-histories <id>          # add workload histories to an older capture
k8srca scenario build-wiki [<id>...]             # build each scenario's wiki from its capture (§6.1)
k8srca scenario run <id> --arch wiki --save F    # run with the wiki skill; keep the results in F
k8srca scenario compare F [G]                    # two saved runs side by side (G: the last run)
k8srca scenario checklist F --grades 3           # grade saved answers on checklist.yaml; per-item agreement (010 §7)
k8srca scenario regrade F                        # the rubric again on saved answers, for comparisons before 010
k8srca scenario rescore F                        # saved checklist verdicts scored on the current checklists; no grader calls (§10.3)
k8srca eval answer                               # the answer writer on a scenario's fixed facts, graded on its checklist (010 §8)
```

**The baseline is read on the checklist (§10.2, §10.3).** A run saves each
scenario's checklist scores, its runs that met the diagnosis, each group's
items met, and its checklist's sha, with the checklist grader's digest.
`scenario run`'s table is §10.3's results format: diagnosis, checklist and
the groups, each moved cell as `was→now`, and the screening rule's verdict.
`scenario rescore` scores saved verdicts again on the current checklists,
without the grader, when only what the score reads (`diagnosis`, `expect`,
`group`) has changed.
`scenario compare` sets two checklist-graded runs side by side. It refuses
across a checklist grader, and per scenario across a capture, truth or
checklist. `scenario baseline` refuses a checklist-graded run unless
`--agreement` shows each checklist past the agreement bar on that run's
answers. A run graded on the rubric only still compares on the rubric.

**A run that never finished is not a result** (k8srca#2). The event stream can
close mid-turn without a terminal event; the runner took that as the end of the
turn and graded a "still waiting…" message as the answer. Now a stream that
ends without one is treated as a dropped connection and reconnected, and a run
whose turn still did not finish -- or whose sandbox failed (the poller logs
`sandbox_failed`), or that passes 45 minutes -- is marked invalid, its session
interrupted, and it is neither graded nor counted: reported as `SKIP`, its
spend still totted up. Production's Slack path delivers only a finished turn's
answer for the same reason.

**The scenario's agents are built against its replay.** Every agent with tools
is synced per scenario against the replayed k8stools, not production's
container: tool declarations are snapshotted at sync, and the sandbox refuses a
server whose surface differs. Inherited from production, the investigator
carried k8stools 2.3.0's tools into a 2.4.0 replay and every session failed. It
also means a newer k8stools is measurable before production runs it
(`K8SRCA_SCENARIO_K8STOOLS_IMAGE` picks the replay image).

**`--local-skill`** publishes a working-tree skill (k8s-rca) as the scenario's
own copy, never a version of production's -- which would repoint the Slack bot
at an unmeasured change -- and folds its digest into the run's skill digest, so
such a run reports "skill re-pinned" against a baseline instead of a delta.

**`backfill-histories`** adds k8stools 2.4.0's workload histories to a capture
taken before them, without re-recording: each history is read now, its ages
shifted back to `captured_at`, and the merge refused outright if any revision
or ConfigMap write is newer than the capture. History is a record of the past,
so read this way it is the captured world's; anything newer would splice two
worlds together (§6.1).

`baseline` promotes what a run already measured rather than running the suite
again — the numbers are the same either way and a second suite costs what the
first one did. Every run writes its aggregate to `.k8srca/scenario/last-run.json`
for it to pick up.

**The baseline is committed, at `tests/scenarios/baseline.json`.** A run's own
results are local state, but the comparison point is not: kept out of the tree
it is per-machine, so a teammate's run reports "new" for every scenario and CI
has nothing to compare against — a suite whose whole purpose is regression
detection, detecting none. In the tree it is also reviewable, which is the real
argument: a changed baseline appears in a diff as "we accepted a new rate", and
that is the moment worth a second pair of eyes. It records the capture timestamp,
the skill digest and the digest of `truth.yaml` it was measured against, so a
reviewer can tell an accepted regression from a re-recorded world or a rewritten
rubric. Those three plus the grader digest (§11.3) are the whole apparatus: the
world, the rubric, and the instrument.

It refuses a run whose dimensions disagreed with themselves, because a delta
against a split column is noise; `--force` records anyway and labels those
columns indicative. It also refuses a run graded by a different apparatus
(§11.3). And a recorded baseline carries the capture timestamp and skill digest
it was measured against, so a later comparison across a re-record reports
"capture re-recorded" instead of inventing a regression.

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

**Under 010 (2026-10-23):**
- **Sessions:** with the short answer, a run costs $0.08–0.72 (010 §9.6,
  §9.10, §9.12).
- **Grading:** about $0.07 a checklist grading.
- **The suite:** n=3 over the ten scenarios is about $9 in sessions and $2
  in grading.
- **The rubric,** when it is asked for, is about $0.70 an answer.
- **Unlogged:** grader calls record no cost. These figures were recounted
  from token counts (010 §9.10).

---

## 8. Anti-goals

- **Not a benchmark.** No headline score. The output is a diff against a
  baseline. Importing *scenarios* from public benchmarks (§12) does not change
  this: we take their captures, not their scoring, and grade them like our own.
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
| S3 | Baseline recorded; KB-on/KB-off comparison — **done, §10.1** | The first real measurement: does the knowledge base beat the L0 floor? |
| S4 | Prometheus source (§3.3) | A scenario answers a question that needs a metric |
| S5 | Generic MCP proxy (§3.4); KWOK scenarios (§4.3) | A third data source lands without reshaping the suite |
| X1 | ITBench-Lite Kubernetes-native imports (§12.1) — **Scenario-33, 24, 80, 102 and 105 landed** (§12.3) | Each import's truth re-derived from its capture, and it runs under the same grader |
| X2 | ITBench-Lite feature-flag and, if kept, Chaos Mesh imports (§12.1) | After S4: their symptoms are in metrics and traces |
| X3 | SREGym as a periodic live exam (§12.2) | After S4: a diagnosis run against a live SREGym problem, read-only, graded by our rubric |

S3 is where this stops being infrastructure and starts paying: it is the first
time we can answer an open question instead of arguing about it.

### 10.1 S3 result: the knowledge base on and off (2026-10-15)

**The knowledge base does not find causes; it makes the agent rule on
alternatives.** Measured on the three baseline scenarios at n=6, under the
panel grader (`0615f2080c506284`), against `tests/scenarios/baseline.json`.

The off arm is the k8s-rca skill with `kb_query.py`, `knowledge_base.json` and
its "Knowledge base" section removed. The method stays: generate two or three
candidates, give every candidate a verdict. The candidates come from the agent's own
knowledge instead. The coordinator prompt's two mentions of the KB were reworded
to "every candidate you raised". The variant was run through `--config` and
`--local-skill k8s-rca=DIR` from an untracked directory. None of its 18 sessions
called `kb_query`.

| scenario | | cause | evidence | rivals | traps | gaps | unsup/run | calls | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| checkout-gc-pressure | KB on | 4/6 | 0/6 | 5/6 | 3/6 | 5/6 | 3.5 | 45 | 0.66 |
| | KB off | 5/6 | 0/6 | 0/6 | 2/6 | 5/6 | 2.7 | 37 | 0.46 |
| itbench-33 | KB on | 6/6 | 4/6 | 0/6 | 6/6 | 6/6 | 0.5 | 23 | 0.21 |
| | KB off | 6/6 | 3/6 | 0/6 | 6/6 | 6/6 | 0.8 | 24 | 0.21 |
| jvm-oom-on-startup | KB on | 6/6 | 2/6 | 5/6 | 6/6 | 6/6 | 1.8 | 16 | 0.22 |
| | KB off | 6/6 | 0/6 | 0/6 | 6/6 | 6/6 | 3.2 | 20 | 0.26 |

- **Cause, traps, gaps: no move.** Nothing moves by two runs or more, let alone
  the four a single batch can resolve (§6.4). On
  these scenarios the KB does not beat the L0 floor at finding the answer or
  at avoiding the tempting mistakes.
- **Rivals: 5/6 → 0/6 on both scenarios where the KB had them.** This is partly
  circular: the rival lists were written with the KB's candidates in view. The
  per-rival dispositions show a real loss too.
  - On checkout, 14 of 24 rival slots were never raised: CPU throttling, DNS
    and storage almost never.
  - On jvm, 12 of 42 were never raised. Another 12 were "confirmed": OOM and
    the low limit were taken as the cause rather than ruled between.

  Without a list, the agent raises fewer alternatives, and stops at the first
  that fits. That is the 002 §11.0 failure mode.
- **itbench-33 is the control.** `PendingPodsDetected` is not in the KB, so
  both arms form their own candidates. Without the KB the agent still ruled on
  most of them (13 refuted, 8 absent of 24). Rivals is 0/6 in both arms, for
  the dispositions given, not for want of alternatives.
- **Evidence on jvm, 2/6 → 0/6; unsupported claims 1.8 → 3.2 a run.** This is
  at the noise threshold, so it is a lean, not a finding. It is consistent with
  the authored discriminators (`docs/rca/discriminators.yaml`, `OOMKilled`)
  giving the agent specific checks to run in place of assertions.
- **Cost:** flat, except checkout, which was cheaper without the KB (37 calls
  against 45). Fewer alternatives were checked.

**Decision: keep the KB.** Its measured value is the method 002 asks for:
weighing and ruling on alternatives. It does not add accuracy on cause.
Where it could add accuracy is coverage. On checkout, `HighRequestLatency`
returns four generic candidates and GC pressure is not among them. Only
`CrashLoopBackOff` and `OOMKilled` have authored discriminators, which
answers part of 001 §12.4. Extending candidates and discriminators to the
alerts the scenarios raise is the next KB work. Re-run this comparison after
it, judged on cause and evidence, which do not depend on the KB's own lists.

### 10.2 Step 1 plan: the checklist baseline (2026-10-23, approved at n=3)

**Why.** Since 010, every scenario is graded on its checklist (010 §7). But
`scenario baseline` and `scenario compare` still read only the rubric, and the
committed `tests/scenarios/baseline.json` is from 2026-10-15, under rubric
grader `0615f2080c506284`, on three scenarios. So the suite has nothing to
check a change against. 010's comparisons were read by hand from checklist
tables. This step makes the checklist the baseline's measurement, then records
one.

**Hypothesis.** One batch of the current agent at n=3 on all ten scenarios
gives a baseline whose spread per scenario is small enough that a later run
can be screened against it mechanically. The batch includes three checklist
gradings for each answer.

**Expected effect.** No change to the agent: this step measures it. From
010's step 5 the expectation is:
- cause 3/3 on every scenario except possibly checkout-gc-pressure, which was
  2/3 to 3/3 in 010 §9.4 and §9.6;
- a checklist spread of 0–2 items a run within each scenario;
- every checklist still passing the agreement bar (97% overall, no item split
  on more than one answer).

**Can we see it?** The batch is the noise measurement, so this step can't miss
its own effect. What it can't do is resolve small moves later.
- A baseline at n=3 shows a scenario the agent can no longer do (cause at most
  1/3 against 3/3), and a drop larger than the scenario's own spread.
- It does not show a move of one item, or of one cause in three.
- §6.4 asks 10 or more, pooled, for a baseline. process.md §6, written later
  and in force, runs n=2–3 on what a change reaches.
- n=3, the owner's decision: it is the scale every 010 result was read on.
  n=5 would have cost about $10 more.

**The code.** No agent change, and the instrument is unchanged: no checklist,
truth or grader is edited.

1. **What a run saves.** `report._payload`, and so `last-run.json` and
   `--save` files, also saves each scenario's checklist results:
   - items met per run, the items listed, and the cause group's (met, listed);
   - the checklist's sha (`checklist.checklist_sha`);
   - the checklist grader's digest (`checklist.apparatus_digest`) and the
     number of grades.
2. **Which baseline compares.** A baseline is comparable on the checklist when
   its checklist grader digest matches. Each scenario is also pinned, as now,
   to its capture, truth and skill, and additionally to its checklist's sha. A
   rubric-only baseline reads "baseline predates the checklist", not as a
   delta.
3. **`scenario run`'s table** shows each scenario's checklist score against the
   baseline, under the screening rule of item 6. `checklist_table` gains the
   delta column. The rubric table stays for `--rubric`.
4. **`scenario baseline`** promotes a checklist-graded run. In place of the
   split-dimensions refusal it refuses a checklist that failed the agreement
   bar in that run.
5. **`scenario compare`** sets the two runs' checklist scores and cause items
   side by side. It refuses on a different checklist grader, checklist sha,
   capture or truth, as it refuses a different rubric grader now.
6. **§6.4 rewritten for the checklist**, with its numbers taken from this
   batch:
   - a scenario regresses when cause falls below its baseline count, or when
     its mean checklist score falls by more than the baseline's spread for
     that scenario;
   - anything smaller is read as noise.

   The rule is written after the batch and before anything is compared
   against it. The rubric text moves under a "before 010" heading.
7. **Tests**, hermetic, for each of the above. The old `baseline.json` is
   replaced; git keeps it.

**The batch.**
- **The command:** `k8srca scenario run <all ten> --n 3 --local-skill k8s-rca`
  from a clean worktree of main. The run grades each answer once, and that
  grading is what the baseline records. Then `scenario checklist F --grades 3`
  grades the saved answers three more times, to check the agreement bar.
- **The agent:** main, which is 010's closed agent.
- **Reuse:** none. Step 5's 18 runs are not reused, so all ten scenarios are
  measured in one batch on one agent.
- **The runs file** is copied out before the worktree is removed.
- **jvm-oom-with-metrics is re-recorded under issue #3**, on or after
  2026-10-25. Its baseline row is then refused as "capture re-recorded", and it
  is re-baselined alone at n=3, for about $2. That is planned now, and it is
  not a fourth change.

**Budget: $24, one day.**
- Sessions: about $10. That is 30 runs at about $0.28 each for step 5's six
  scenarios, and about $0.20–0.65 for the other four.
- Checklist grading: about $8.40, for 120 gradings at about $0.07 (30 in the
  run, then 90 for agreement).
- The remaining $5–6 covers one regrade, if a checklist falls below the bar.

**Done:**
- items 1–7 merged with tests passing;
- `tests/scenarios/baseline.json` committed, recorded on the checklist for
  all ten scenarios at n=3;
- §6.4's checklist rule written down;
- a result table here: scenario, checklist score, cause, spread, and $ a run.

**Stop:**
- **A checklist fails the agreement bar.** The instrument is frozen, so the
  checklist isn't edited in this step. That scenario is left out of the
  baseline, and the owner decides at a checkpoint.
- **A scenario's cause falls to 1/3 or less**, against its 3/3 in 010. That
  is a finding, not a baseline. It is recorded, and the owner decides whether
  to baseline it as is.
- **The budget is spent.**
- **Any of process.md §2's tripwires.**

### 10.3 Step 1 checkpoint (2026-10-24)

**The tripwire:** step 1's second stop criterion fired. Three scenarios read
cause 1/3 against 3/3 in 010: jvm-oom-on-startup, jvm-oom-with-metrics and
liveness-probe-wrong-port. Work stopped before the agreement gradings, and no
baseline was written.

**The batch,** 30 runs from `../k8srca-step1` at 243226f, each answer graded
once (`runs/20261024T020439Z-2582615.jsonl`). Sessions cost $9.83 and
in-run grading about $2.10, so about $12 of the $24 budget is spent.

| Scenario | Checklist | Cause items | All cause items met |
| --- | --- | --- | --- |
| checkout-gc-pressure | 12.0/13 | 11/12 | 2/3 |
| deploy-regression | 13.7/14 | 12/12 | 3/3 |
| downstream-latency | 10.0/11 | 9/9 | 3/3 |
| image-pull-typo | 8.3/9 | 9/9 | 3/3 |
| itbench-33-invalid-node-selector | 10.0/12 | 9/9 | 3/3 |
| jvm-oom-on-startup | 12.3/14 | 7/9 | 1/3 |
| jvm-oom-with-metrics | 13.3/15 | 10/12 | 1/3 |
| liveness-probe-wrong-port | 8.3/9 | 10/12 | 1/3 |
| nothing-is-wrong | 7.3/8 | 6/6 | 3/3 |
| pvc-pending-no-storageclass | 9.3/10 | 8/9 | 2/3 |

**Was the hypothesis wrong?** No. The spread within each scenario is 0–2
items, as expected.

**Could the measurement see the effect?** It measured something else.
- **010's "cause"** was whether the answer names the cause.
- **Step 1's column** counts a run only when every item in the checklist's
  cause group is met. That group also holds supporting statements: mechanism,
  impact, contrasts.
- **Most misses are supporting statements:**
  - jvm's `cause-no-heap-sizing` (no heap flags), missed in four of six runs;
  - liveness's `cause-unavailable` (shipping has no ready replica), missed in
    two of three;
  - checkout's `cause-cpu-contrast`, missed in one.
- **Each of those answers names the diagnosis.** For example: "**Most likely
  (very likely): the liveness probe added to `shipping`'s pod spec checks the
  wrong port.**"
- **One run does lose the diagnosis.** pvc-pending run 3 says "a PVC named
  `quote-cache` that doesn't exist", and that "'not found' means the PVC
  itself is missing, not a StorageClass/capacity issue". The PVC exists; its
  StorageClass does not.
- **The strict column also double-counts.** A supporting miss already costs
  its point in the checklist score, and then fails the whole run on cause.

**Is the implementation incomplete?** Yes: the gate has no way to tell the
diagnosis from the items around it.

**How other evaluations score.** The strict gates elsewhere are on the
diagnosis alone:
- **ITBench-AA** (IBM, 59 Kubernetes tasks × 3 repeats) scores a structured
  list of root-cause entities by average precision at full recall. Missing
  any ground-truth entity scores 0 for that repeat; finding all of them scores
  the share of submitted entities that are real causes. It is all-or-nothing
  on the entities, not on any explanation.
- **SREGym** grades a free-text diagnosis, as we do, with an LLM judge
  answering a checklist of 9 yes/no questions in 3 dimensions. Each dimension
  scores the fraction met, and the total is a weighted sum. Pass is 7/9, a
  threshold that forbids passing with a whole dimension missing; one miss
  inside a dimension is tolerated.
- **OpenRCA** (Microsoft) asks for one to three elements of the root cause
  (time, component, reason). It gives a full score when all requested elements
  are right and partial when at least one is.
- **AIOpsLab** (Microsoft) scores detection, localization, analysis and
  mitigation as separate tasks, each on correctness, plus time or steps and
  cost.

None of them gates on supporting detail. SREGym is closest to this suite: a
checklist, an LLM judge, scores by fraction, and a gate on the essential part
only. Sources:
[ITBench-AA](https://artificialanalysis.ai/articles/itbench-aa-launch),
[SREGym](https://arxiv.org/pdf/2605.07161),
[OpenRCA](https://proceedings.iclr.cc/paper_files/paper/2025/file/d29b8d53678015079e1d245c023e49d2-Paper-Conference.pdf),
[AIOpsLab](https://www.microsoft.com/en-us/research/publication/aiopslab-a-holistic-framework-to-evaluate-ai-agents-for-enabling-autonomous-cloud).

**Also seen, not part of this checkpoint.** The deterministic checks failed
six runs. They gate a run's PASS/FAIL line, not the checklist:
- itbench-33, all three runs: `numeric_claims` on "2 restarts" for the new ad
  and cart pods;
- downstream-latency: one run over 50 tool calls;
- pvc-pending, two runs: `must_identify: fast-ssd`.

The itbench-33 finding may be a check defect like issue #4. It is unread, and
it is filed if it is one.

**Decision (owner, 2026-10-24): change the plan.**

1. **Each checklist marks its diagnosis.**
   - **The mark:** an item gains `diagnosis: true` when it states the
     diagnosis, following the truth's `cause.summary`. A diagnosis that takes
     two statements marks both.
   - **Only the score reads it.** The grader is not shown it, as it is not
     shown `expect` or `group`.
   - **This is an instrument change,** made once, before the baseline
     (process.md §5). The grader's digest and its verdicts on the saved
     answers do not move; every checklist's sha does. The marks are below.
2. **The gate is the diagnosis.**
   - **The rule:** a scenario regresses when fewer of its runs meet every
     marked item, at the baseline's rate, or when its mean checklist score
     falls by more than the baseline's spread.
   - **The categories** are shown, not gated.
   - **The strict column** ("all cause items met") is dropped.
3. **A new command, `scenario rescore F`,** scores saved verdicts against the
   current checklists and writes a run payload, for `baseline` and `compare`.
   - **No grader calls.**
   - **It refuses** when an item's grader-visible text (statement, accept,
     tempting, allowed) differs from what was graded, so it only re-reads a
     mark, never a changed statement.
   - **This batch is rescored,** not run again.
4. **The results table** is the one below, in `scenario run`, `rescore` and
   `compare`.
5. **The rest of step 1 is unchanged:** three agreement gradings an answer
   (about $6.30); `scenario baseline --agreement`; §6.4's rule; this
   section's result table. The budget stays $24, with about $12 left.

**Diagnosis items** (proposed, confirmed by the owner 2026-10-24):

| Scenario | Diagnosis (marked) | Supporting (scored, not gated) |
| --- | --- | --- |
| checkout-gc-pressure | `cause-gc-pressure` | `cause-checkout-near-limit`, `cause-cpu-contrast`, `no-confirmed-cause` |
| deploy-regression | `cause-port-setting` | `cause-recent-revision`, `cause-payment-down`, `impact-checkout` |
| downstream-latency | `cause-cpu-limit` | `cause-recent-revision`, `cause-call-path` |
| image-pull-typo | `cause-bad-tag` | `cause-recent-revision`, `cause-old-pod-serving` |
| itbench-33-invalid-node-selector | `cause-node-selector` | `cause-recent-change`, `cause-old-pods-serving` |
| jvm-oom-on-startup | `cause-oom`, `cause-limit-too-low` | `cause-no-heap-sizing` |
| jvm-oom-with-metrics | `cause-oom`, `cause-limit-too-low` | `cause-no-heap-sizing`, `cause-at-limit-from-start` |
| liveness-probe-wrong-port | `cause-probe-wrong-port` | `cause-kubelet-kills`, `cause-recent-revision`, `cause-unavailable` |
| nothing-is-wrong | `cause-cart-healthy` | `cause-additem-succeeds` |
| pvc-pending-no-storageclass | `cause-missing-storage-class` | `cause-recent-revision`, `cause-old-pod-serving` |

**The results format.** One row per scenario. With the proposed marks, the
batch reads:

| Scenario | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next |
| --- | --- | --- | --- | --- | --- | --- | --- |
| checkout-gc-pressure | 3/3 | 12.0/13 | 11/12 | 3/3 | 14/15 | 5/6 | 3/3 |
| deploy-regression | 3/3 | 13.7/14 | 12/12 | 6/6 | 14/15 | 6/6 | 3/3 |
| downstream-latency | 3/3 | 10.0/11 | 9/9 | 0/3 | 12/12 | 6/6 | 3/3 |
| image-pull-typo | 3/3 | 8.3/9 | 9/9 | 1/3 | 9/9 | 3/3 | 3/3 |
| itbench-33-invalid-node-selector | 3/3 | 10.0/12 | 9/9 | 6/9 | 9/9 | 4/6 | 2/3 |
| jvm-oom-on-startup | 3/3 | 12.3/14 | 7/9 | 9/9 | 15/18 | 3/3 | 3/3 |
| jvm-oom-with-metrics | 3/3 | 13.3/15 | 10/12 | 9/9 | 15/18 | 3/3 | 3/3 |
| liveness-probe-wrong-port | 3/3 | 8.3/9 | 10/12 | 3/3 | 6/6 | 3/3 | 3/3 |
| nothing-is-wrong | 3/3 | 7.3/8 | 6/6 | 3/3 | 8/9 | 3/3 | 2/3 |
| pvc-pending-no-storageclass | 2/3 | 9.3/10 | 8/9 | 2/3 | 9/9 | 6/6 | 3/3 |

- **Diagnosis:** runs that met every marked item. This is the gate.
- **Checklist:** the mean of items met a run, over the items listed. The
  screening rule reads it against the baseline's spread.
- **The categories** are the checklist's groups: items met summed over the
  runs, out of items × runs. They show where to look and carry no rule of
  their own. A group of one item moves by a third on a single miss, and
  groups differ in size between scenarios, so a cell is read down its own
  row's history, not across rows.
- **Against the baseline,** each cell carries its move (`0/3 → 2/3`). A
  scenario the rule flags ends its row with `REGRESSION`.
- **In `compare`,** each scenario gets a before row, an after row, and a row of
  moves, in the same columns.

### 10.4 Step 1 result (2026-10-24): done

**The baseline** is `tests/scenarios/baseline.json`: the batch of §10.3,
rescored on the marked checklists (`scenario rescore`, no grader calls),
under checklist grader `da606073821551c6`.

| Scenario | Diagnosis | Checklist | Per run | Cause | Ruled out | Traps | Gaps | Next |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| checkout-gc-pressure | 3/3 | 12.0/13 | 11, 12, 13 | 11/12 | 3/3 | 14/15 | 5/6 | 3/3 |
| deploy-regression | 3/3 | 13.7/14 | 13, 14, 14 | 12/12 | 6/6 | 14/15 | 6/6 | 3/3 |
| downstream-latency | 3/3 | 10.0/11 | 10, 10, 10 | 9/9 | 0/3 | 12/12 | 6/6 | 3/3 |
| image-pull-typo | 3/3 | 8.3/9 | 8, 9, 8 | 9/9 | 1/3 | 9/9 | 3/3 | 3/3 |
| itbench-33-invalid-node-selector | 3/3 | 10.0/12 | 10, 10, 10 | 9/9 | 6/9 | 9/9 | 4/6 | 2/3 |
| jvm-oom-on-startup | 3/3 | 12.3/14 | 12, 13, 12 | 7/9 | 9/9 | 15/18 | 3/3 | 3/3 |
| jvm-oom-with-metrics | 3/3 | 13.3/15 | 14, 13, 13 | 10/12 | 9/9 | 15/18 | 3/3 | 3/3 |
| liveness-probe-wrong-port | 3/3 | 8.3/9 | 8, 9, 8 | 10/12 | 3/3 | 6/6 | 3/3 | 3/3 |
| nothing-is-wrong | 3/3 | 7.3/8 | 8, 8, 6 | 6/6 | 3/3 | 8/9 | 3/3 | 2/3 |
| pvc-pending-no-storageclass | 2/3 | 9.3/10 | 10, 9, 9 | 8/9 | 2/3 | 9/9 | 6/6 | 3/3 |

- **Diagnosis:** 29 of 30 runs. The miss is pvc-pending run 3, which blames
  a missing PVC instead of the missing StorageClass. All three extra
  gradings agree it misses.
- **Agreement:** every checklist passes the bar, at 98.8–100% (99.4% of
  1,035 gradings overall). Six items split on one answer each, which the
  bar allows.
- **Where to look next,** not decided here:
  - **ruled out:** downstream-latency 0/3, image-pull-typo 1/3 and itbench-33
    6/9 are the weakest cells;
  - **jvm's traps:** both versions miss `no-onset-date` in all six runs,
    dating the onset to the 163-day-old revision.
- **Spent:** about $18 of $24. Sessions $9.83; checklist gradings about
  $8.40 (30 in the run and 90 for agreement, at about $0.07); the rescore,
  nothing.

**Against the done criterion:** met.
- Items 1–7 merged: dec7531 and 243226f.
- The baseline committed.
- §6.4's checklist rule written, before anything was compared against it.
- This table.

**Owed:** jvm-oom-with-metrics is re-baselined alone after issue #3's
re-recording, on or after 2026-10-25 (§10.2). **Done 2026-10-30,** at n=10
rather than n=3, because the same run is 009 step 5's re-run (009 §7.7).

---

## 11. Open questions

1. **Grader bias.** Claude grading Claude. §6.2 mitigates by giving the grader
   the ground truth and the capture, making it a checker rather than a judge —
   but this should be validated against human grading on the first few
   scenarios before the baseline is trusted.
2. ~~**Sample size versus cost.**~~ **Answered: n=6**, since revised to n=5
   for screening against a pooled baseline of 10 or more (§6.4). Per-dimension variance
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

   It happened again with what the digest left out. Pods were digested as
   summary, container statuses and logs, without their spec, and k8stools
   reports empty resource requests in container statuses. So the only requests
   the grader could see were none. Three ITBench answers that quoted the real
   ones (ad 10m/225Mi, cart 15m/55Mi) were marked down for inventing them,
   which read as the wiki costing two runs of evidence. The digest now carries
   each pod's spec less mounts and defaults (`grader.SPEC_DROPPED`, about a
   quarter of its size). `k8srca scenario regrade` grades saved answers again
   under the current grader, so a fix like this can be checked against runs
   already paid for, at grading cost only, rather than against a fresh sample.

   And a third time, twice in one scenario (`checkout-gc-pressure`): the
   capture's metrics-server sample and its rollout history were not in the
   digest either, so "checkout at 97% of its limit" and a true "GOMEMLIMIT was
   added with the 2.2.0 image" were both graded as invented. The digest now
   carries `metrics`, `workload_histories` (less label churn), StatefulSets and
   PVCs, and the prompt says ConfigMap contents are absent, so a claim about
   one is unverifiable rather than unsupported. The rule these three share:
   **anything a tool can show the agent, the grader must be able to see.** A
   new section in the capture is a grader change until it is in the digest.

   Regrading the same baseline answers under these changes also showed the
   grader's own spread: ITBench `evidence` read 5/6, 3/6 and 4/6 across three
   graders that differed only in what they could see of unrelated sections.
   Some of that is the visibility fixes; some is a model grading the same text
   twice. Measured on 2026-10-15: the same 18 saved answers (three scenarios,
   six each) graded four times by one grader (`c469f59fd7b631e0`):

   | dimension | answers whose verdict changed across the four | widest swing in a scenario's total |
   | --- | --- | --- |
   | cause | 0 / 18 | 0 |
   | traps | 0 / 18 | 0 |
   | rivals | 1 / 18 | 1 |
   | gaps | 1 / 18 | 1 |
   | evidence | 4 / 18 | **2** (jvm: 2, 0, 1, 2 of 6) |

   Unsupported claims per scenario moved 16–22, 9–14 and 1–2, and by up to
   ±2 on a single answer. So the 2-run rule holds where the grader is close to
   deterministic, and does not hold for `evidence`: the grader alone produces
   the 2-run move the rule reads as a change in the agent. (The agent's own
   spread turned out to be larger still, which retired the 2-run rule
   altogether: §6.4.)

   So every answer is now graded three times, independently, and each
   dimension takes the majority (`grader.GRADES`, `--grades`). The notes and
   claims shown are from the member that agrees with the majority most, so
   they explain the verdict reported; a split prints as `evidence=NO(1/3)`.
   The panel size is part of the grader digest. It triples grading -- about
   $6 for an 18-run suite, against $7 of sessions -- which is the price of a
   rule that reads the agent rather than the grader.

   **Since 2026-10-18 the default is one grade again**, with `--grades 3`
   kept for recorded gates. The panel's votes were counted across the 277
   saved answers that have them (jvm 203, checkout 40, itbench-33 34):

   | dimension | the three split | a single grade differs from the majority |
   | --- | --- | --- |
   | evidence | 36 (13%) | 17 (6%) |
   | traps | 13 (5%) | 7 (3%) |
   | gaps | 5 | 0 |
   | cause | 2 | 1 |
   | rivals | 2 of 229 | 1 |
   | standing, calibration, next | 1 of 48 | 0 |

   A single grade would have changed a verdict on 24 answers (9%), which is
   0.6 runs in ten on evidence and less elsewhere. The agent's spread, which
   §6.4's rule now follows, is several times that. So the panel was buying
   accuracy that the rule cannot use.

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

   **Resolved: pinned.** Each scenario's skill is uploaded as its own skill
   object before a run (`scenario/runner.py`), never as a version of
   production's, which agents reference at "latest". With two forms (§6.1)
   each is pinned separately.

7. **Where scenarios live.** `tests/scenarios/` keeps them with the code, but
   captures with logs are large. Secrets are no longer the blocker — k8stools
   captures are redacted by default, matching whatever redaction the server in
   front of the same cluster would apply — so the remaining question is size,
   and whether large JSON fixtures belong in the repository or in an artifact
   store the runner fetches.

---

## 12. External benchmarks

Reviewed 2026-09-30: Cloud-OpsBench (arXiv 2603.00468), ITBench-AA and the
ITBench-Lite dataset behind it, SREGym (arXiv 2605.07161), and NOFire's AI SRE
benchmark. None replaces this suite. They all grade *whether the cause was
named*; §6 grades whether rivals were dispositioned, traps handled, gaps
reported and a non-answer given when one is right, and none of them has a
scenario-7 equivalent. What they do offer is captures of real breakage we did
not have to make, and one of them a live, MCP-native harness.

| Benchmark | Use | Why |
| --- | --- | --- |
| ITBench-Lite | **Import scenarios** (§12.1) | Same application (OTel demo), Apache 2.0, raw API objects that convert losslessly to a k8stools capture |
| SREGym | **Periodic live exam, after S4** (§12.2) | The only one where the agent pulls through MCP from a live system, which is the property most worth testing |
| Cloud-OpsBench | Coverage checklist only | 452 cases, but LLM-synthesised textbook misconfigurations on Online Boutique, and its snapshots are ~487 precomputed tool answers per case -- the call-transcript design §9 rejected |
| NOFire / RCAEval | Not used | Vendor-run with the harness withheld; RCAEval is telemetry for resource faults with little Kubernetes object state for k8stools to serve |

Their leaderboards are not comparable with us and we should not try: they run
bare models in a fixed harness, and k8srca is a system whose value is the tools
and skills around the model. The comparisons that mean something are ablations
on their scenarios -- skills on vs off (S3), and k8stools pulling from a capture
vs the same data dumped into the workspace as ITBench-AA does, which is the
direct test of the pull-not-push design.

### 12.1 ITBench-Lite

35 SRE scenarios, each a snapshot of a real fault injected into the OTel demo
(2.1.3, chart 0.38.6) on a 5-node AWS cluster: `k8s_objects_raw.tsv` (raw API
objects from the OTel k8sobjects receiver, pulled every 5 minutes),
`k8s_events_raw.tsv` (an event watch stream), OTel logs, traces, Prometheus
metrics, alerts, and a `ground_truth.yaml` of root-cause entities with a
propagation chain.

**Conversion.** The raw objects are full API objects, so nothing has to be
reconstructed. They are deserialised into Kubernetes client models, served
through fake CoreV1/AppsV1/BatchV1 clients, and k8stools' own `capture_state`
runs against them with its clock pinned to the chosen pull -- one capture
format, produced by the code that produces every other capture. Points learned
on Scenario-33:

- Pick a pull, do not merge them. Taking the last pull as "now" and filtering
  events and logs to it gives a consistent instant.
- The earlier pulls are a gift: they show the cluster **before the fault**. The
  architecture skill is built from a replay of the last pre-fault pull, with
  only the `live_cluster` and `change_history` sources -- which is how
  production works (the skill predates the incident), and unlike our own
  captures, where skill and capture are one moment (§6.1). Our charts and
  `docs/architecture/otel-demo` notes are excluded: they describe our install,
  and already disagree with theirs (ITBench's `ad` limit is 450Mi, our note
  says 300Mi).
  The wiki (§6.1) is built from the incident capture instead, with the same
  two sources, because it holds no state: none of its pages carries the
  fault, and its synthesis inputs are identical to the pre-fault pull's.
- Some events arrive in the `events.k8s.io/v1` shape (`regarding`/`note`) and
  are normalised to core/v1.
- Logs are OTel SDK records, not container stdout: about 40% of pods have none,
  and there are no previous-instance logs. Crash-loop scenarios lose evidence.
- The converter imports the `kubernetes` client, so it cannot live in k8srca
  (CLAUDE.md). **It belongs in k8stools** as an import path beside
  `k8s-capture-state`.

**Their ground truth cannot be adopted as-is.** On the first scenario converted
it was wrong twice: it omitted `cart`, which carries the identical fault, and
it described an outage (no endpoints, frontend errors) that the capture shows
did not happen -- the previous revision kept serving, and the only alert in
alerting state was `PendingPodsDetected`. ITBench's precision-at-full-recall
scoring would punish the correct answer on both counts. This is §5's lesson
again from the other side: truth written from a mental model of the fault
rather than from the capture. Every import re-derives `truth.yaml` from the
capture, and the source's ground truth is an input to that review, not a
substitute for it. Budget an hour or two per scenario on top of conversion.

**The 35, by what it would take:**

| Group | Scenarios | Status |
| --- | --- | --- |
| Kubernetes-native | 33 (invalid nodeSelector) | **Landed** as `itbench-33-invalid-node-selector` |
| | 16 (shipping `QUOTE_ADDR` port 0000), 24 (checkout `KAFKA_ADDR` port 9999) | Next: change correlation through ReplicaSet revisions, symptoms in logs -- check the callers have OTel logs first |
| | 34 (valkey password enabled, cart not updated), 102 (namespace ResourceQuota) | Convertible; 102 needs checking that FailedCreate events are enough without a quota tool. Checked 2026-10-26 (§12.3): 102 is taken, 16 dropped, 34 held |
| | 38 (misconfigured HPA) | Needs an HPA tool in k8stools first |
| | 1 (load-generator, condition "To be specified") | Skip unless the change can be reconstructed from the capture |
| Feature flags (`flagd-config` ConfigMap) | 2, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15 | **Deferred to after S4.** The cause is a ConfigMap edit (no ReplicaSet revision, so `change_history` cannot see it) and the symptoms -- error rates, latency, consumer lag -- are mostly in traces and metrics |
| Chaos Mesh | 17, 18, 19, 21, 22, 25, 29, 35, 80, 81, 83, 91 | **Undecided, needs investigation.** The truth entity is a chaos CRD (`NetworkChaos`, `StressChaos`, `JVMChaos`…) that k8stools does not expose -- and that a real incident would not have. Either rewrite truth to the mechanism (checkout cannot reach Kafka; memory pressure on valkey) and grade on that, or skip. Most need metrics regardless |
| Empty ground truth | 20, 23, 31, 105 | Investigate: candidates for restraint scenarios (§5, scenario 7) only if the capture shows nothing wrong |

**A k8stools gap found on the way.** `get_deployment_summaries` reports total 1,
ready 1, up-to-date 1, available 1 for a Deployment whose rollout is stuck --
the up-to-date replica is the Pending pod and the available one is the old
revision. Only the ReplicaSet pair shows it. Exposing `status.replicas`,
`unavailableReplicas` or the `Progressing` condition would make a stuck rollout
visible in one call. Scenario-33 has it as a trap until then.

### 12.2 SREGym — after S4

**Set aside, 2026-10-25.** The owner keeps SREGym as a candidate for the
hold-out suite (006), so we don't run it or tune against it. What follows is
the plan as first written.

90 problems over DeathStarBench, Train Ticket, the OTel demo and others, built
from 47 fault primitives (application, Kubernetes, OS and hardware via eBPF),
with injected noise and metastable and correlated failures. Agents connect over
**MCP** to kubectl, Prometheus, Loki and Jaeger servers on a live cluster;
diagnosis is graded by an LLM judge on 9 yes/no questions (κ=0.90 against
humans), and a mitigation phase follows.

It is the best fit for what makes k8srca different -- an agent that queries for
what it needs rather than being handed a dump -- and the least work to connect:
k8stools is pointed at the SREGym cluster with the same read-only ClusterRole
(the kubeconfig goes into the k8stools container and nowhere else, CLAUDE.md),
and the cluster-architecture skill is built with `k8srca arch build` **before
the fault is injected**. Its noise injection is the nearest thing in any
benchmark to scenarios 6 and 7.

Why after S4: most of its harder problems -- metastable failures, latency,
resource contention -- are only visible in metrics and traces. Run before
Prometheus lands, it mostly measures the absence of a data source.

How it fits rather than replaces:

- **Diagnosis only.** k8srca does not mitigate; the mitigation phase is skipped
  and its score ignored.
- **An exam, not a regression suite.** It needs a live cluster per run, which
  is exactly what §9 rejected for the suite: restart counts drift, CI would
  need a cluster, and it is slow (up to 30 minutes per problem). Run it before
  a release or after a large change to the method, not on every prompt edit.
- **Grading.** Their judge scores cause localisation, characterisation and
  scope -- roughly our `cause` dimension. Rivals, traps and gaps need truth we
  author per problem; start with `cause` alone on a subset and write fuller
  truth only for problems that turn out to discriminate.
- **Capture what it shows us.** A problem where k8srca does interestingly
  badly is a candidate for a captured scenario of our own, so the finding
  becomes a regression check rather than a one-off.

Open before starting: the OS- and hardware-layer faults need node-level signals
k8stools does not have, and the cost of a run (cluster, plus n=6 per problem
per §6.4) has not been estimated.

### 12.3 More Kubernetes-native imports: 24, 80, 102 and 105 (planned 2026-10-25, revised 2026-10-26)

X1 continued, as its own work after this design closed (status above), and
tracked in [issue #8]. It changes the instrument by adding to it: no
existing truth, checklist or baseline row changes.

**Why now.** The suite is near its ceiling. At the 2026-10-25 baseline,
diagnosis is 29/30 and cause items 93/99. A change can only show in a
scenario the agent can still fail. Few of the ten turn on reading a log, and
those are what 011's step 2, its first live step, needs.

**Revised after the feasibility check (2026-10-26).** The first plan named
16, 24 and 34, each with its cause read from a caller's log. The free check
(step 1 below) passed only 24, so eleven snapshots were checked in all. The
check reads the TSVs as text, with no `kubernetes` import:

| ITBench | Fault | What the snapshot shows | Verdict |
| --- | --- | --- | --- |
| 16 | shipping's `QUOTE_ADDR` set to `quote:0000` | The revision shows it. Shipping logs `Requesting quote` 333 times and never a reply; no service logs an error | dropped: no log states it |
| 24 | checkout's `KAFKA_ADDR` set to port 9999 | The revision shows it; checkout logs `Failed to connect to broker kafka:9999: i/o timeout`. **Checkout is down by the last pull**: the new pod restarted and isn't ready, the old revision is scaled to 0, and the Deployment is `Available=False`. ITBench's truth says it "continues processing orders" | **taken** |
| 34 | valkey's password enabled, cart not updated | No revision and no restart: the password was set at runtime, outside anything Kubernetes records. Cart logs `Wasn't able to connect to redis`, not an auth error; valkey-cart has no logs | held: a candidate for scenario 8, `evidence-unavailable` (§5) |
| 80 | Chaos Mesh partition, checkout → Kafka | **Two faults.** Only in this snapshot, Kafka runs `replicas: 2`, so two separate single-node brokers sit behind one Service. From the first record, checkout logs about 30 failed writes a minute: `topic or partition that does not exist on this broker`, `circuit breaker is open`. From 17:54:37, the partition adds `i/o timeout` and `run out of available brokers`. The chaos object isn't recorded, but its events are (corrected 2026-10-28: a Chaos Mesh `Schedule` spawns a NetworkChaos each minute from 17:54:00). Orders keep being placed, yet every checkout request times out at the proxy (`504 UT`, 15 s), before the partition as after | **taken** |
| 102 | namespace memory ResourceQuota | `ad` has no pods; its Deployment shows `ReplicaFailure`, and FailedCreate events say `exceeded quota: otel-demo-memory`. The quota is recorded too. The composites already report the `ReplicaFailure` condition with its message, so no quota tool is needed | **taken** |
| 105 | product-catalog `command: ["invalid-command"]` | The revision shows it; product-catalog crash-loops with exit 128 (the OCI "executable not found" error) and is down. Checkout logs nothing about it. A bystander, fraud-detection, was OOMKilled once | **taken** |
| 31 | NetworkPolicy `frontend-block-all-ports` | **The fault had no effect.** The policy's `ingress: [{}]` is an empty rule, which allows all ingress, and frontend-proxy's 5xx count is unchanged (11, 11, 8 per 10 minutes) | held: a restraint scenario with a decoy, once k8stools can show NetworkPolicies |
| 20 | product-catalog image changed to one that doesn't exist | ImagePullBackOff, with the old pod gone | dropped: near image-pull-typo |
| 81 | Chaos Mesh partition, shipping → quote | Shipping logs nothing, as in 16 | dropped |
| 1 | load-generator users raised | The only change is a rollout restart, and the 504s start 25 minutes before it | dropped |

Two findings hold across the snapshots:
- **frontend-proxy logs a steady trickle of `503 UC`,** about 0.15% of
  requests, before and after every fault. It's a trap in each scenario.
- **ITBench's truth is wrong again** in 24 (checkout is down) and in 80
  (the Kafka replicas, which cause most of checkout's errors, aren't in it).

**The four, and what each tests:**

| ITBench | Read from | The likely misreading |
| --- | --- | --- |
| 24 | checkout's log and its revision | blaming Kafka, or calling checkout healthy |
| 80 | checkout's log and Kafka's `replicas: 2` | blaming the network alone, or Kafka's health; calling checkout down |
| 102 | a Deployment condition and FailedCreate events | blaming `ad` itself, or looking for a crash |
| 105 | container status and the revision | blaming checkout or frontend for product-catalog's outage |

24 and 80 are the log-reading pair: the same service and the same symptom,
with a config change behind one and something the cluster shows only
in its events (the chaos objects aren't recorded)
behind the other. 80 also ranks two causes, which is 008's work. "Topic or
partition does not exist on this broker" has no knowledge-base predicate, a
content gap this work records and doesn't fix.

**Hypothesis.** On at least two of the four, today's agent misses a cause
item, or names the wrong cause, in at least two of three runs. On 24 and
80, the traces show the misses coming from how a log was read, or not read.

**The work, in order:**
1. **Feasibility, free.** Done, above.
2. **A k8stools issue for the import path:** an ITBench-Lite snapshot and a
   chosen pull, in; a capture, out. It is the Scenario-33 converter (§12.1),
   which wasn't kept and can't live here (CLAUDE.md): a fake API client
   feeding `capture_state`, with the clock pinned and events normalised.
   The k8stools agent builds and releases it. Filed as
   [k8stools#30](https://github.com/BenedatLLC/k8stools/issues/30); its
   review and the snapshot format are in the issue.
3. **Check the release on the ten existing scenarios.** k8stools#30 asks
   for one replay rule for every capture: anything absent is "not
   recorded", not "none". That changes how itbench-33 and
   jvm-oom-on-startup replay (their missing kinds), and one missing log in
   image-pull-typo. Run `record-results` old against new, and n=3 on the
   scenarios that change, before the new release is used.

   **Done 2026-10-28, on 4.4.0; adopted (b93f96a).**
   - **Offline.** `record-results`, 4.3.0 against 4.4.0, on all ten. Beyond
     time shifts and one-second rounding, three things differ:
     - every composite result gains "Not recorded in this capture, so
       unknown rather than absent: NetworkPolicies". No capture records
       them, so step 3 reached all ten scenarios, not three;
     - itbench-33 and jvm-oom-on-startup also list DaemonSets, HPAs,
       Service endpoints and Ingresses there;
     - image-pull-typo's `email` log reads "not recorded".
   - **Live, at n=3, on five scenarios:** the three with real changes, plus
     downstream-latency and nothing-is-wrong, where an unknown NetworkPolicy
     could pull an answer off course. The owner chose five over ten to keep
     the budget. No answer mentions NetworkPolicies, and the new tool was
     never called.
   - **A measurement error, caught.** That batch ran without
     `--local-skill`, so it used production's k8s-rca skill (`cefc3…`), older
     than the baseline's (`07f2…`). On it, jvm-oom-on-startup missed
     `cause-no-heap-sizing` in all three runs, and the screen said
     REGRESSION.
   - **An A/B on jvm-oom-on-startup** with the same code and skill:
     `cause-no-heap-sizing` was met 0/3 on 4.3.0 and 0/3 on 4.4.0. The
     release doesn't move it.
   - **Rerun with the baseline's skill** (rebuilt from `97d0a13~1`, digest
     verified): checklist 13, 12, 12 against 13, 13, 13; cause 7/9 against
     8/9. The screen still says REGRESSION, but one cause in three is below
     what n=3 resolves (§12.3, "Can we see it?"). The owner read it as noise
     and adopted 4.4.0.
   - **Not re-run on the baseline's skill:** downstream-latency (10, 10, 10)
     and image-pull-typo (9, 9, 9) matched the baseline even on production's
     older skill. itbench-33 and nothing-is-wrong screened as noise.
   - **Recorded, not fixed (tripwire 3):** `cause-no-heap-sizing` is patchy
     because its fact exists only when the agent calls `get_pod_spec`, which
     it often doesn't.
   - **The baseline isn't replaced.** These runs differ from it in skill
     (production's) and code (b629ced's numeric-claims check came after it).
   - **4.4.1 (k8stools#32), 2026-10-29, offline only.** Requests and limits
     now come from the pod spec when the container status has none (before
     1.33, in every ITBench import, and in containers that never started).
     `record-results`, 4.4.0 against 4.4.1, on the ten: two lines change,
     both corrections. itbench-33's `ad` and image-pull-typo's `email` read
     their real limits instead of "no limit". The facts layer already read
     the spec, so no fact changes, and no live run was made.
4. **Per scenario, as for Scenario-33:**
   - the incident capture, at the last pull;
   - the architecture skill from the last pull before the fault, with only
     `live_cluster` and `change_history`. In 80 no pull predates the Kafka
     replicas, so its skill is built from the last pull before the
     partition;
   - the wiki, from `scenario build-wiki`;
   - `record-results` fixtures;
   - a README saying where ITBench's ground truth differs from the capture.
5. **Truth and checklist, from the capture, before any run.** Claude
   drafts them and the owner reviews. ITBench's `ground_truth.yaml` is an
   input, not the answer. Traps cover the callee blamed for its caller's
   errors, and the background 503s. In 80, "two brokers" is read from a log
   message and the replica count, so the truth ranks it most likely, not
   confirmed. The files are frozen once reviewed, and no run sees them
   first.

   **Done 2026-10-29; frozen.** Drafted 2026-10-28. Then each scenario got a
   review against ITBench's raw snapshot and the capture, one sub-agent per
   scenario, and the owner accepted every finding. All four root causes
   held. One mechanism didn't: 80's draft had checkout waiting 15 s on its
   failed Kafka write. Before the partition, though, each write fails
   within 0.8 s, so the capture doesn't show why the requests time out.
   80's strength drops to moderate, and the impact item gates. The other
   changes:
   - count corrections (105's 500s, 102's 308 redirects, 80's broker
     log counts);
   - trap wording that would have caught true statements (24's logged
     `KafkaProducerClient:<nil>`, 102's "would exceed the quota");
   - new items: frontend ruled out (24, 80, 102), a load-generator
     trap (105), downstream impact (80, 105), the readiness probe
     (24) and the quota's time as a gap (102).

   **Step 6, done 2026-10-29.** n=3 on each scenario: main at 3dc2262,
   `--local-skill k8s-rca`, the wiki skill. Sessions cost $4.90, and
   grading three times for agreement about as much again. Checklist
   scores, by majority of three gradings:

   | Scenario | Checklist | Diagnosis item |
   |---|---|---|
   | 105 invalid command | 18, 18, 18 / 20 | 3/3 |
   | 24 Kafka port | 14, 15, 15 / 16 | 3/3 |
   | 102 memory quota | 11, 16, 14 / 17 | 3/3 |
   | 80 two brokers | 10, 8, 10 / 21 | **0/3** |

   **Agreement.** The first grading reached 98.2% overall, but three items
   split on more than one answer: 105's `impact-scope`, 24's
   `no-observed-exit-mechanism` and 80's `cause-orders-still-placed`.
   - **First rewording.** The owner had all three reworded so each says
     plainly what truth means, then the answers were regraded. Each
     reworded item came back unanimous.
   - **New splits.** In the same regrade, three items that hadn't been
     touched split instead. 24's `no-shell-error` had a clear cause: its
     tempting phrase was the footnote our own code prints. 80's
     `cause-ad-restart` and `no-ad-checkout` had split in neither earlier
     grading, so they are grader noise.
   - **Checkpoint.** The owner chose to reword `no-shell-error` only. It
     came back unanimous, and 24's agreement reached 99.3%. 80 stays at
     96.8%, short of 97% because of those two noisy items, and is landed
     with that noted.
   - **The lesson.** With only three answers, "no item split on more than
     one answer" catches noise as well as bad wording.
   - **Not a tripwire.** These checklist changes came after the runs but
     before the baseline row (step 8). Every verdict above is on the final
     wording.

   **Step 7, done 2026-10-29.** One sub-agent per scenario read every miss's
   trace. A miss that depends on another, such as a next step that follows
   from a missed cause, is counted once.

   | Label | 105 | 24 | 102 | 80 | All |
   |---|---|---|---|---|---|
   | Log misread | 0 | 0 | 1 | 4 | 5 |
   | Log never read | 5 | 2 | 4 | 12 | 23 |
   | Cause read, not ranked | 0 | 2 | 3 | 2 | 7 |
   | Something else | 0 | 2* | 2 | 4 | 8 |

   \* grader errors, removed by the rewording above. The other entries
   under "something else":
   - three misses lost to the revision turn (102 ×2, 80 ×1): the 300-word
     cut, or figures flagged as uncited although they were derived
     correctly;
   - three were `rules-out-checkout-config` in 80, a hypothesis no run
     raised.

   **The finding.** Most misses are evidence never fetched, not evidence
   misread. In 80, no run read checkout's log further back than 60 lines,
   and every tail it read started after the partition. No run found the
   Chaos Mesh events either. They are Normal, and named after the pods,
   so the Warning and exact-name filters the runs used miss them. Kafka's
   workload report shows one pod's log out of two, with long lines cut
   short, which hid `TOPIC_ALREADY_EXISTS`. Two runs read the end of the
   proxy's log as the 504s stopping.

   **Room.** Every scenario has room. 80's diagnosis item scores 0/3, and
   each of the other three misses some item in every run: 24's readiness
   probe, 102's scale to 0 and back, 105's downstream impact.

   **For a later design, not done here (tripwire 3).**
   - Short default log tails.
   - Events the facts layer doesn't state: a scale to 0, a rollout deleting
     the old pod.
   - The revision turn's cuts.
   - Impact read from the call graph instead of from callers' logs.
   - A fact-ID collision between the host and `kb_query.py fact` is a
     defect, not tuning, and is filed as k8srca #9.

   **Step 8, done 2026-10-30.** The four rows are added to
   `tests/scenarios/baseline.json`, and the existing ten are not touched.
   - **No regrading.** Each row is the majority verdict of step 6's three
     gradings on the final checklists, scored with `scenario rescore`, so no
     grader call was made. The existing rows are each one run's single
     grading.
   - **Merged by hand.** `scenario baseline --agreement` checked the four,
     but it writes the whole file, so its rows were merged into the existing
     baseline. Each new row carries its own `taken_at`.
   - **80 needed `--force`,** as the owner decided at the checkpoint.

   | Scenario | Checklist | Diagnosis item | Cause items |
   |---|---|---|---|
   | 105 | 18.0/20 | 3/3 | 12/18 |
   | 24 | 14.7/16 | 3/3 | 12/15 |
   | 102 | 13.7/17 | 3/3 | 7/12 |
   | 80 | 9.7/21 | 0/3 | 5/21 |

   The skill is the working tree's k8s-rca (`03f15100f6d8ae12`), not the
   older rows' `07f249377879fe54`. Each row records its own digest.

   **§12.3 is done.** All four landed, each with its n=3 runs, its misses
   labelled and its baseline row. Every scenario has room, and 28 of the 43
   independent misses were evidence never read or misread. The exception is
   80's agreement: 96.8%, with two items split. That is grader noise, and
   80 was landed with it noted.
6. **The live run:** n=3 on each scenario, on `main` as it is, with the
   saved answers graded three times for agreement.
7. **Read every miss's trace,** as was done for run 3 of 007's step 7. Each
   miss gets one of four labels:
   - the log was misread;
   - the log was never read;
   - the cause was read and not ranked;
   - something else.
8. **Extend the baseline** with the new rows. Existing rows are not touched.

**Expected effect.** Two of the four fall below their checklist's ceiling,
80 most likely, with log-reading misses among the causes on 24 and 80. That
rests on one run in 40-odd and on the scenarios' shape, so it may be wrong.
This work measures it.

**Can we see it?**
- **Room, yes.** A cause item missed in two of three runs is a real
  pattern, not grader noise: the checklist grader dissents on about 1% of
  verdicts.
- **A rare miss, no.** One miss in three, at n=3, can't tell a 10% miss
  rate from a 50% one. Such a scenario is reported as "room unclear".
- **Why the agent missed,** from the traces: as a count of labels, not a
  rate.

**Budget.**
- **Money: $30** (raised from $20 on 2026-10-28). Twelve runs at about
  $0.10–0.90 each, the grading for agreement, four wiki builds at about $1
  each, and step 3's runs. Step 3 took about $14: its first five-scenario
  batch ran without `--local-skill`, so it measured production's older
  k8s-rca skill rather than the baseline's, and had to be redone.
- **Calendar: two weeks,** most of it waiting on the k8stools release.
- **The owner's time:** one to two hours per truth review (§12.1), and two
  to three for 80.

**Done** when:
- each of the four is either landed or dropped with its reason;
- each landed checklist agrees on at least 97% of verdicts, with no item
  split on more than one answer;
- each landed scenario has its n=3 runs, its misses labelled, and its row
  in the baseline;
- the results below say which scenarios have room, and how many of their
  misses come from reading logs.

**Stop, and say which,** when:
- **the k8stools release is two weeks away or more.** Park the work;
- **step 3 shows an existing scenario's score moving.** That's a
  checkpoint: the owner decides whether the new replay is the baseline;
- **every landed scenario saturates:** 3/3 on every cause item. Record it:
  this group gives no room, and the next source is the owner's call;
- **the budget is spent;**
- **a fix to the agent, the knowledge base or the facts layer** is proposed
  from these runs. That is process.md's tripwire 3, a rule written from a
  run. It is written down for a later design and not made here.

**Interactions.**
- **[Issue #7]**, captures moved to the data repo. Either order works: if
  the new captures land first, they move with the rest.
- **011 step 1.** If its labels aren't frozen when the imports land, the
  logs of 24 and 80 join its corpus (011 §9).
- **Held for later:** 34, as scenario 8 `evidence-unavailable`; and 31,
  once k8stools has a NetworkPolicy tool
  ([k8stools#31](https://github.com/BenedatLLC/k8stools/issues/31)). Neither is in this work.
  31 landed on its own afterwards (§12.4).

**SREGym stays out** (§12.2): it is held back as a hold-out candidate.

**Result:** done 2026-10-30; each step's result is above.

### 12.4 Scenario 31, the NetworkPolicy decoy (2026-10-30)

`itbench-31-network-policy-decoy`, imported once k8stools 4.4 could show
NetworkPolicies, on the same steps as §12.3.

**The scenario.** The injector created `frontend-block-all-ports` at
17:51:21, selecting frontend's one pod. Its rule is `ingress: [{}]`, one
empty rule, which allows all ingress, so it blocks nothing.
`get_networkpolicy_summaries` says so (`ingress_effect: allows all
ingress`). frontend-proxy's log, all 10,000 lines of it, shows about 670
requests a minute routed to frontend, before and after the policy. Its 15
`503 UC` come at the same rate on both sides of it: 0.148% before and
0.154% after. The RequestErrorRate alert apparently fires on any error rate above 0,
so that trickle fires it by itself. ITBench's own ground truth says the
policy blocked all ingress and that frontend was down. The capture shows
neither.

**Built and frozen before any run:**
- the truth and the checklist, with 14 items, reviewed by a sub-agent;
- the wiki (`002d6d7c3acae487`, its synthesis cached);
- the fixtures;
- the capture, published as `c3ce532efc48a43c`.

The review added the sharpest trap. No 503 falls between 17:48:41 and
17:54:11, a gap that spans the policy. So any log tail under about 5,700
lines shows 503s only after the policy, and the default tail is 1,000
lines.

**Hypothesis:** at least one of three runs blames the policy. *Not borne
out, for the wrong reason.* No run mentions the policy at all, and none
called `get_networkpolicy_summaries`. No composite result names the policy,
and the agent never went looking for one. The decoy's trap was never
reached.

**Results at n=3**, with `--local-skill k8s-rca`:
- **Diagnosis:** 0/3.
- **Checklist:** 10.0 of 14 on average, split by group:

  | cause | ruled out | traps | gaps | next |
  | --- | --- | --- | --- | --- |
  | 3/9 | 3/6 | 15/15 | 6/9 | 3/3 |

- **Agreement:** the three gradings agree on 125 of 126 verdicts (99.2%).
  Only `no-invented-impact` is split, on one answer.
- **Cost:** $3.66 in sessions, at 29, 40 and 60 calls. Run 3 went over the
  50-call budget and is marked FAIL.

**The misses, labelled:**
- **`cause-background-trickle` and `cause-rate-unchanged`, 3/3: the log was
  never read.**
  - Every run read only the end of frontend-proxy's log, and saw the last
    three 503s (17:56:49–50) as a one-off burst.
  - Run 1 adds "nothing since". Run 2 calls the rest of the log "200/308".
  - Their conclusion, a transient blip and not an outage, is close to
    right. But "one-off" is wrong: the trickle runs from the log's first
    minute.
  - The workload report's `grep` doesn't reach past the default tail
    either. k8stools greps the last 1,000 lines only.
  - Filed as [k8stools#34](https://github.com/BenedatLLC/k8stools/issues/34),
    a log pattern summary over the whole retained log, and noted on
    [issue #10](https://github.com/BenedatLLC/k8srca/issues/10).
- **`rules-out-network-policy`, 3/3: something else.** The policy was never
  in front of the agent. Nothing in the facts layer or the KB prompts a
  look at NetworkPolicies, for a symptom that only connection resets
  suggest.
- **`no-trickle-cause-as-observed`, 1/3: an over-reach.** Run 2 states a
  startup race between the pods as the likely cause, and says the fix is
  readiness probes. Both pods are 40 minutes old.

**Landed** with room, at 0/3. Its row is in the baseline, merged by hand as
in step 8. The scenario discriminates in a different way than planned: it
tests reading a log's history before calling errors a burst, and it tests
restraint about the decoy only once the agent finds the policy. Neither is
fixed here (process.md tripwire 3). Both are written down in #10 and
k8stools#34.

[issue #8]: https://github.com/BenedatLLC/k8srca/issues/8
[Issue #7]: https://github.com/BenedatLLC/k8srca/issues/7
