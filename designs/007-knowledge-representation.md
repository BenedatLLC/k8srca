# Design 007 — Diagnostic knowledge: representation, sources and runbooks

**Status:** in progress, 2026-10-24 (owner): the families that have scenarios (§11.4); step 1, `pod-pending`, done 2026-10-24; step 2, `image-and-sandbox` with `KubePodNotReady`, done 2026-10-24; step 3, the scheduling and image facts in code, done 2026-10-24; step 4, `storage`, done 2026-10-24; step 5, the scheduler's message and one join over nodes, done 2026-10-25; step 6, change and onset facts for pod-restarting (the `rollouts-and-jobs` reach check), done 2026-10-25. Before that: done in part, 2026-10-21; revisited after 010, 2026-10-23.
Built: the format and its build checks, `lookup`, `claim`, `refine` and
`open`, availability per deployment, the mining pipeline, and two mined
families: `pod-restarting` (§11.2) and `request-latency` (010 step 3b,
§11.3). Not done: the rest of the vendored alerts (§8.5), and runbooks
(§9), whose format is specified but which nothing reads.

**Replaced by [010](010-facts-and-short-answers.md)** (done 2026-10-23):
- **The hand-written facts file.** Facts come from code, from each tool
  result, into `evidence/facts.jsonl` (010 §3). The agent adds what needs
  reading text with `kb_query.py fact` (010 §3.4). `open --workload` reads
  the log. `open --facts <file>` stays for the tests; SKILL.md no longer
  mentions it.
- **§6.1's completeness check and "owed".** They are off
  (`review.completeness: false`). The code stays in `review.py`, and
  `kb_query.py` still computes `owed_ids` for it. 010's answer checks took
  its place: markers resolve, 300 words, every figure in a cited fact (010
  §5.2). The ruled-out checks 010 §6 planned were not built (010 §9.3).
- **The reviewer** of §6.1 is deleted. 010's light reviewer is built and off
  (010 §9.8).
- **`open`'s output** prints the leader with its facts, what is ruled out and
  by which fact, and the open candidates with their cheapest checks. The
  timing paragraph and the owed footer are gone from `lookup` and `open`;
  `claim` keeps the timing paragraph (010 §4).

What remains of 007 to do: the remaining vendored alerts (§8.5), and runbooks
(§9). Next, after 004 (owner, 2026-10-23): the families that have scenarios
(§8.5: `pod-pending`, `image-and-sandbox`, `rollouts-and-jobs`, `storage`, and
the rest of `service-traffic`), each a planned step measured on the scenarios
it reaches.

**Status before 2026-10-21:** draft, 2026-10-15. Stage 1 landed 2026-10-16: the format, its
build checks, `kb_query.py lookup`/`refine`, and `CrashLoopBackOff`/`OOMKilled`
converted, in `docs/rca/knowledge/` (§11.1). Stage 2, availability, landed
the same day: MCP servers declare what they `provide`, and the poller tells
each sandbox (§4.1). Stage 3, mining, is under way: the pipeline is built
(`k8srca kb mine`, §8.3), and the 79 vendored alerts not yet converted go
through it a family at a time, their records read as one more source (§8.5).
Supersedes the target
format in [002 §8](002-investigation-model.md#8-from-knowledge-base-to-playbooks),
which it extends rather than contradicts.

**Changing direction, 2026-10-18:** [008](008-conclusions-under-incomplete-evidence.md)
(proposed) replaces confirmation with ranking. Claims are no longer confirmed;
weighted evidence makes them most likely, at a strength. Facts are scoped to
what the record retains. Where a section below is affected, it says so. It
still describes what is built.

**Changing again, 2026-10-21:** [010](010-facts-and-short-answers.md) (proposed) derives facts in code from
tool results, with their scope, in place of the hand-written facts file (§3).
`open` keeps its ranking but drops "owed", and the completeness check and
revision turn of §6.1 give way to a light reviewer (010 §6).
**Changed (010, done 2026-10-23):** as the status line says. The light
reviewer is off, and the answer checks of 010 §5.2 run in place of
completeness (010 §9.3, §9.8).

Companion to [002](002-investigation-model.md) (how the agent reasons) and
[004](004-scenario-testing.md) (how we measure it). 002 says the agent forms
candidates, gathers evidence and disposes of every candidate. This says what the
knowledge it does that with looks like, where it comes from, and how a site's own
runbooks fit alongside it.

---

## Background for new readers

This section is the context the rest of the document assumes. Skip it if you
have followed the project.

**The agent.** k8srca answers "why is X broken?" questions about a Kubernetes
cluster. It runs as a Claude Managed Agents session that calls read-only
cluster tools (the *k8stools* MCP server) and a set of *skills*: files and
scripts delivered into its sandbox. One of those is the `k8s-rca` skill in
[`skills/k8s-rca/`](../skills/k8s-rca/). Its `SKILL.md` describes the
investigation method, and `kb_query.py` is a script the agent runs to look
things up in the knowledge base.

**The knowledge base (KB)** is the diagnostic content behind `kb_query.py`.
It has three parts:

- **Vendored.** [`vendor/kb/kubernetes_rca_knowledge_base_v2.json`](../vendor/kb/README.md)
  is a third-party dataset we received and do not edit. It has 81 records, one
  per alert name (`CrashLoopBackOff`, `FailedScheduling`, `HighRequestLatency`,
  …). Each record lists *hypotheses* (candidate causes) as strings, plus
  free-text evidence hints.
- **Authored.** Hand-written knowledge we add. Before this design it lived in
  `docs/rca/discriminators.yaml`, now deleted. That file held
  *discriminators* for two alerts: for each candidate cause, prose saying what
  evidence would confirm it and what would refute it. It also held one
  `refines` link, saying that `CrashLoopBackOff`'s "OOM" candidate is broken
  down further by `OOMKilled`'s candidates. Stage 1 converted all of it into
  [`docs/rca/knowledge/`](../docs/rca/knowledge/), in the format §3 describes.
- **Mined.** Knowledge extracted from public debugging guides by a model,
  then reviewed, in `docs/rca/mined/` (§8). The vendored records are being
  converted into the same format the same way.

`k8srca kb build` merges them into `skills/k8s-rca/knowledge_base.json`,
which ships in the skill ([`src/k8srca/kb/`](../src/k8srca/kb/)).

**The scenario suite** ([004](004-scenario-testing.md)) is how we find out
whether a change helps. A scenario is a cluster state captured once and
replayed to the agent, a question, and a `truth.yaml` saying what a good
answer contains. Three scenarios are recorded today, and this document
mentions all of them:

| Scenario | What happened in the cluster | What it tests |
| --- | --- | --- |
| `jvm-oom-on-startup` (scenario 1; "jvm") | Two Java services whose memory limit equals their request, with no heap-size flags, are killed by the kernel while starting. Their exit code is 137 (SIGKILL), but the termination `reason` field says `Error`, not `OOMKilled`. | Whether the agent notices and reports evidence that disagrees with its conclusion. |
| `checkout-gc-pressure` ("checkout") | Asked why checkout is slow. The quiet cause is a Go service running above its `GOMEMLIMIT`, so it spends its CPU on garbage collection. The only evidence is a single metrics-server sample. Nearby, other services crash-loop loudly for unrelated reasons. | Finding one quiet lead, at the right confidence, without blaming anything loud. |
| `itbench-33-invalid-node-selector` ("itbench-33") | Imported from the [ITBench](https://github.com/itbench-hub/ITBench) benchmark. A rollout is stuck because its new pods have a node selector no node matches. The previous revision still serves. The alert is `PendingPodsDetected`, which is not in the KB. | Judging severity. It is also a control for KB changes, since the KB has no entry for its alert. |

An independent grader model scores each answer on several dimensions (004
§6.2). These are the ones this document cites:

| Dimension | Passes when |
| --- | --- |
| **cause** | the answer states the root cause in `truth.yaml` |
| **evidence** | every claim is tied to a tool result that supports it |
| **rivals** | every alternative cause listed in `truth.yaml` got a verdict: confirmed, weakened, refuted, or could-not-check. Since 2026-10-18 it is two dimensions (008 §6.2): *standing* (most likely, not ruled out, ruled out) and *calibration* (the strength given). The gates below were graded on rivals |
| **traps** | the answer avoids the tempting wrong conclusion the scenario was built around |
| **gaps** | evidence the agent could not get is reported as unavailable, not as absent |

A score like **5/6** means a dimension passed in 5 of 6 runs of the same
scenario. Each scenario runs six times because the agent is
nondeterministic. A *batch* is one set of six runs. The *recorded baseline*
is the batch saved in `tests/scenarios/baseline.json`, which a change is
compared against. The agent alone varies by up to 3 runs in 6 between
identical batches, so a single batch shows only a move of 4 or more (004
§6.4).

**The KB-on/KB-off comparison** (004 §10.1, staged as "S3" in 004's plan)
ran the three scenarios twice: once with the KB, and once with
`kb_query.py` and the knowledge base removed from the skill, so the agent
had to form candidates from its own knowledge. §1 starts from its result.

**InferRCA** is a separate Benedat project (repository
`BenedatLLC/InferRCA`, private). It defines a formal language for
root-cause analysis (`designs/rca-language.md` in that repository). Runbooks
are translated into that language and compiled to [clingo](https://potassco.org/clingo/),
an Answer Set Programming solver, which then decides which checks to run
and what can be concluded. This design takes InferRCA's concepts but not
its solver (§3, §10.2). The repository also holds three example runbooks
(crash loop, latency SLO, cascade failure), each with a written translation
into the language. §9 refers to those translations.

Other design documents are cited by number and section: 001 architecture,
002 investigation model, 004 scenario testing, 005 modular architecture
(a draft target), 006 hold-out suite.

---

## 1. Why

The KB-on/KB-off comparison
([004 §10.1](004-scenario-testing.md#101-s3-result-the-knowledge-base-on-and-off-2026-10-15))
found that the knowledge base does not help the agent find causes. What it
does is make the agent rule on alternatives. Without the KB, `cause` did
not move on any scenario, but `rivals` fell from 5/6 to 0/6 on the two
scenarios where the KB had entries (jvm and checkout). The agent raised
fewer alternatives and stopped at the first one that fit.

That effect came from the KB's small structured parts: the would-confirm and
would-refute text in the discriminators, and the `refines` link from
`CrashLoopBackOff`'s "OOM" to `OOMKilled`. Both existed for two alerts out of
81. The rest of the knowledge base is a flat list per alert of hypothesis
strings with free-text evidence hints:

```json
{"alert": "APIClientThrottling",
 "hypotheses": ["excessive client request rate", "low API priority/fairness capacity"],
 "evidence": {"metrics": ["rate(apiserver_request_total{code=\"429\"}[5m])"], ...}}
```

That record names what might be wrong. It does not say how to tell the
candidates apart, which ones can be true together, what each one opens up
when confirmed, or whether this deployment can make the checks at all. The
model is left to invent those things during the investigation, and that is
where it fails.

Three things are wanted, in this order:

1. **A representation** that carries discriminators, refinement and exclusion as
   structure, not prose, so that the build can check them and a script can
   reason over them (§3–§6).
2. **More content**, mined from public Kubernetes debugging guides rather than
   written to fix the scenarios we know about (§8).
3. **A place for runbooks** written by a site's own IT, which are site-specific
   and often keyed by alert (§9).

## 2. Layers

Two layers describe the system, and two say how to diagnose it. Two are
generic and two are specific to one site:

| | Generic (true of any cluster) | Site-specific |
| --- | --- | --- |
| **What the system is** | (not needed) | architecture wiki (kubewiki, `packages/kubewiki/`) |
| **How to diagnose it** | the knowledge base: authored + mined | **runbooks** |

The authored KB has always had one rule: *generic, true of any cluster*.
That stays the rule for the knowledge base. Site-specific *descriptions*
belong in the wiki, as before. Site-specific *diagnostics* ("this alert is
usually the nightly batch job") belong in runbooks, which until now had
nowhere to go.

All diagnostic content, whatever its layer, uses one representation (§3). Each
layer is a separate set of files with its own provenance, merged when the
skill is built (§9.3).

### 2.1 Files and formats

| File | Format | Who reads it |
| --- | --- | --- |
| `docs/rca/knowledge/*.yaml` (authored) | YAML | people, who write and review it; `kb build` |
| `docs/rca/mined/*.yaml` (mined, reviewed) | YAML | people, who review it; `kb build` |
| `docs/rca/mining/sources.yaml` (the mining manifest, §8.2) | YAML | people; `kb mine` |
| `vendor/kb/kubernetes_rca_knowledge_base_v2.json` (the 81 received records) | JSON | `kb build` and `kb mine` only |
| `skills/k8s-rca/knowledge_base.json` (generated) | JSON | `kb_query.py` only, inside the sandbox |
| `.k8srca/kb-mine/` (fetched sources, extractions, drafts; gitignored) | text, JSON, YAML | `kb mine`; drafts are read by the reviewer |

Everything a person writes or reviews is YAML. The two JSON files stay
JSON, which was decided rather than left alone:

- **The generated file has no human reader.** The agent never opens it (the
  coordinator prompt says not to read bundle files whole); `kb_query.py
  lookup` is the readable view of it. And `kb_query.py` ships in the skill
  importing the standard library only, so that it runs in any sandbox. YAML
  would need PyYAML, which our image happens to have only as a k8srca
  dependency. JSON written with sorted keys is also canonical, so a rebuild
  diffs cleanly, where YAML dumpers make style choices and have type traps
  (`no`, `1.10`).
- **The vendored file stays as received**, so a new upstream version replaces
  it whole and `kb build` shows what moved (`vendor/kb/README.md`). A YAML
  copy would be a second thing to keep in step.

What makes the vendored records hard to read is their shape, not their
syntax: flat records with candidate causes packed into `;`-separated
strings. The remedy is converting them into the YAML layers (§8.5). Once all
81 are converted, every file a person opens is YAML under `docs/rca/`, and the
two JSON files are a build input and a build output that nobody needs to.

## 3. The representation

The concepts come from InferRCA's language (see Background):

- facts are kept apart from claims;
- actions are what produce facts;
- rules derive claims from facts;
- an `unknown` result is recorded as a gap;
- exclusion is declared.

InferRCA's surface syntax is not taken over. Here the format is JSON, built
from YAML sources, and **the model interprets it, not a solver** (§10.2).

### 3.1 Concepts

| Concept | Is | Example |
| --- | --- | --- |
| **Symptom** | An entry point: what someone asking can see. | `pod-restarting`, `pod-pending`, `high-latency` |
| **Fact** | An observable statement about the cluster, with parameters. True, false or unknown, never inferred. | `last_exit_code[Container, 137]`, `limit_equals_request[Container, memory]` |
| **Action** | How a fact is observed: a capability it needs, what to call, what to read in the result, and a cost. | k8stools `get_workload_report`, reading the last state of the container |
| **Claim** | A candidate cause. Never observed, only supported or refuted by facts. | `oom-kill`, `memory-limit-too-low`, `memory-leak` |
| **Rule** | The conditions on facts (and on other claims) that confirm a claim, and the conditions that refute it. | below |
| **Refinement** | Confirming one claim opens another set of candidates: what *kind* of OOM. | `oom-kill` → {`memory-limit-too-low`, `memory-leak`, `workload-spike`} |
| **Prevalence** | Whether a sub-cause is a common kind or an uncommon one. Uncommon ones are held back until asked for (§6). | `cache-growth` under `oom-kill` |
| **Exclusion** | Claims that cannot both be the cause. Claims that are not excluded can stack. | `memory-limit-too-low` vs `memory-leak` |
| **Change kind** | A kind of change, when it takes effect, and the actions that can see one (§3.4). | `image`, `config`, `node` |

The distinction that does the work is between a **refinement** and a
**rival**. In the KB-off runs of the jvm scenario, answers "confirmed" both
`oom` and `memory-limit-too-low`, as if they competed and both won. They
are not rivals: one refines the other, and confirming `oom` is where the
question "which kind of OOM?" starts. Two claims are rivals only when they
are siblings under one symptom or refinement. Exclusion says which siblings
cannot be the cause together.

**Planned (008 §4):** two concepts change.
- **Rules carry weights.** A claim is ruled out decisively, as a refute does
  today. Otherwise evidence counts *for* or *against* it, each weighted strong,
  moderate or weak. The claim's standing comes from what holds, and the
  leading sibling is the most likely cause.
- **Facts carry their scope.** A fact states what the record showed and over
  what span, never a whole history the record cannot hold.

### 3.2 A worked entry

This is `CrashLoopBackOff` and `OOMKilled` as converted in
[`docs/rca/knowledge/pod-restarting.yaml`](../docs/rca/knowledge/pod-restarting.yaml),
abridged:

```yaml
symptoms:
  pod-restarting:
    title: Container repeatedly restarts
    claims: [application-crash, bad-config, oom-kill, failed-liveness-probe]
  container-oom-killed:
    title: Container terminated for memory
    claims: [memory-limit-too-low, memory-leak, workload-spike]

alerts:
  CrashLoopBackOff:    {symptom: pod-restarting}
  KubePodCrashLooping: {symptom: pod-restarting, bind: {Pod: labels.pod, ...}}
  OOMKilled:           {symptom: container-oom-killed}

facts:
  last_exit_code:
    params: [Container, Code]
    functional: true          # one value at a time: a container has one last exit code
    says: "the container's last termination exit code is {Code}"
  memory_grows_within_life:
    params: [Container]
    says: memory climbs monotonically within each life rather than spiking

actions:
  read-container-state:
    requires: k8stools
    call: get_workload_report
    read: >-
      Each container's last terminated state: exit code, and `ran_for` -- how
      long that one instance lived, which is not how often the container restarts.
    produces: [last_exit_code, completes_startup_each_life, ...]
    cost: 1
  sample-memory-now:
    requires: k8stools
    call: get_container_metrics
    quality: point
    limits: One sample, taken now. It cannot show growth within a life.
    produces: [memory_limit_plausibly_reached]
  query-memory-series:
    requires: prometheus
    call: query_range
    produces: [memory_grows_within_life, memory_limit_plausibly_reached, ...]
    cost: 3

claims:
  oom-kill:
    title: OOM
    confirm:
      - ["last_exit_code[C, 137]", "not process_logged_fatal_error[C]",
         "memory_limit_plausibly_reached[C]"]
    refute:
      - "last_exit_code[C, Code], Code != 137"
      - "process_logged_fatal_error[C]"
    refined_by: [memory-limit-too-low, memory-leak, workload-spike]
  memory-leak:
    title: memory leak
    confirm:
      - ["lifetime_steady_or_shortening[C]", "completes_startup_each_life[C]",
         "memory_grows_within_life[C]"]
    refute:
      - "not completes_startup_each_life[C]"
      - "lifetime_varies_without_trend[C]"
exclusive:
  - [memory-limit-too-low, memory-leak]
```

How to read it:

- `confirm` and `refute` are lists of alternatives. An alternative that is
  itself a list holds only when all its conditions do.
- `supports` (not shown) is evidence for a claim, but not decisive.
- Capitalised arguments (`C`, `Code`) are variables. `C` is the container
  under investigation, and `Code` is bound by the fact and then compared.

**OOM is confirmed on the exit code, not on the termination `reason`.** This
looks like an omission and is deliberate. In the jvm scenario, `reason` says
`Error` while the exit code says 137, and the scenario measures whether the
agent notices that disagreement. We twice tried telling the agent that
`reason` is unreliable and 137 means OOM: once in the cluster's
architecture notes, once in the KB. Both times `traps` fell from 6/6 to
4/6, because the agent no longer had a reason to flag the discrepancy. The
lesson generalises: a claim should say what evidence would decide a
question, not pre-decide it. The YAML file keeps the history in a comment.

A condition is a fact pattern, optionally negated, optionally compared.
`not` means *checked and found false*, never "not looked at". This reverses
InferRCA's semantics. There, as in most logic programming, `not p` uses
*negation as failure*: it holds whenever `p` has not been established. Here
an unchecked fact is a gap (§4.2), not evidence, so `not` needs a check
that came back false.

Where a condition cannot be stated precisely, it can be prose. InferRCA
cannot allow that, because a solver cannot read prose, but the model can.
The build counts prose conditions (§7) so they stay the exception, and the
first conversion has none.

Every claim needs a `refute`. A claim that can only be confirmed invites the
confirmation-seeking 002 §6 is built against.

**Planned (008 §4.2): `for`, `against` and `rules_out`.**
- `confirm` and `supports` merge into `for`, a list of alternatives each with
  a weight.
- `refute` splits into `rules_out`, which is decisive, and `against`, which is
  weighted.
- `open` computes a claim's standing from those: ruled out, or a support grade
  that holding `against`s lower.
- The `reason`-says-`Error` question above becomes a weak `against` on
  `oom-kill`. That says the observation counts against OOM and belongs in the
  answer, without pre-deciding it. The rule this section draws from the trap
  holds.

The weights are bands of a likelihood ratio (008 §5.2). They are written down
so that a calibration can later replace them with numbers.

### 3.3 Not a decision tree

Debugging *reads* like a tree, and most guides draw one: the learnk8s
flowchart (§8.2) literally is one. The tree is the wrong thing to store, for
two reasons:

- **A tree fixes the order and stops at the first leaf.** Stopping at the first
  fit is the failure we keep measuring. Rules state what would settle each
  candidate; the order comes from cost and availability (§4) at run time.
- **A tree has one path; incidents do not.** A memory leak and a too-low limit
  are exclusive; an OOM kill and a failing liveness probe can both be true. A
  tree encodes neither.

The tree-like part, going from general to specific, is kept as refinement: a
graph of claims, which is what the old `refines` link started.

### 3.4 Timing: changes and onsets

"What changed just before this started?" is the most useful question in an
investigation (SKILL.md, method step 5) and the easiest to get wrong. A
change is evidence of a cause only when two things hold:

1. **The onset is observed.** The first failure itself falls inside what
   events, metrics or history retain. It is not inferred from a restart count
   or a pod's age. Thousands of restarts against an hour of events began
   before the events did.
2. **The change took effect just before the onset**, inside the window in
   which that kind of change takes effect. A change outside the window is
   history, not a trigger.

With no observed onset, any claim that a change triggered the failure is
could-not-check, whatever else changed.

**Why this is in the format.** The first gate of mined content (§11.2) had
answers confirming "application release" as the trigger in the jvm
scenario. The only release was 163 days old, and nothing showed when the
crash loop began. The mined claim said "a deployment change precedes the
restart spike". The authored claims said the same thing three other ways,
none of which required the onset to be seen or the change to be recent:
`limit_or_image_changed_before_onset`, `config_change_precedes_first_failure`
and `node_disruption_near_restarts`. Each claim that coined its own timing
fact could get the rule wrong in its own way.

**The format, in three parts.**

- **Two reserved facts.** `onset_observed[W]` says the onset is observed.
  `changed_before_onset[X, Kind]` says that `Kind` changed and took effect
  just before the failures began. The authored layer defines both.
- **Change kinds** (`changes:` in a layer file). A change kind has a title,
  `takes_effect` (when that kind of change bites), and `seen_by` (the actions
  that can see one). The authored layer has these:
  - `image`: takes effect as the rollout replaces pods. A mutable tag
    re-pushed under the same name changes the digest with no new revision.
  - `resources`: takes effect on each new pod's first start.
  - `spec`: everything else in the pod template; takes effect as the rollout
    replaces pods.
  - `config`: a ConfigMap or Secret edit takes effect when the process next
    reads it, which can be days later. The onset to line it up with is the
    first restart after the edit.

  The mined layer adds `node`. Layers merge change kinds like facts, the
  higher layer winning on a name.
- **Availability per kind.** A kind is observable only by its `seen_by`
  actions, not by every action that sees some change. Rollout history sees
  `image`, `resources` and `spec`. Only an audit log sees `config`. So
  `bad-config` confirmed by a ConfigMap edit is a gap with k8stools alone,
  which the old `config_change_precedes_first_failure` hid: it was
  "produced" by rollout history, which cannot see ConfigMap edits.

`kb_query.py` prints the two conditions above, and the `takes_effect` of
each kind it mentions, wherever a test or claim it shows names a change.

**What it does not cover.**
- *Co-occurrence that is not a change*, such as "kills coincide with a rise
  in load". It has its own facts and needs no onset.
- *Changes whose effect builds up*, such as a release that introduced a
  leak. The onset lags the change by the build-up time. The evidence is then
  the build-up itself (lifetime shortening, memory growth), not the timing.
  `kb_query`'s timing note says so; the format does not model the lag.

The build enforces it (§7).

### 3.5 Scope: what the record retains

*Planned (008 §4.1).*

Facts here are observations, but some are worded as history. The clearest is
`killed_on_every_life`, "killed on every restart including the first". The
Kubernetes API keeps each container's last termination and a count, and no
record keeps every restart. A careful agent will not assert the fact, so the
claims that rest on it cannot be decided. In the eighth and ninth gates
(§11.2), that is how the correct cause was demoted.

The fact's `says` therefore states what was observed and over what:
- `killed_on_every_life` becomes `killed_in_every_retained_life`, which
  covers the last state, the previous instance's, and what the event series
  implies over its span;
- `healthy_period_before_kills` and `completes_startup_each_life` get the
  same treatment.

A build check refuses a fact whose text quantifies over all time ("every",
"always", "never", "first", "since") unless one of its producing actions
declares `scope: history`. No k8stools action does. A restart series over the
pod's whole life would.

This is §3.4's rule, generalised. §3.4 refused to read an onset from a pod's
age, because the record does not reach back to it. This refuses any fact that
claims to reach back further than the record does.

## 4. Actions and availability

### 4.1 Capabilities

Every action `requires` one capability:

| Capability | Present when | Today |
| --- | --- | --- |
| `k8stools` | always (005 §5.1: the core Kubernetes plugin is always enabled) | yes |
| `prometheus` | the deployment enables a metrics plugin | no |
| `tracing`, `logs-backend`, `audit-log` | the deployment enables that plugin | no |
| `human` | never, for the agent | — |

A capability is named for what it provides, not for the product that
provides it, so `prometheus` could be served by any MCP server that speaks
PromQL. The actual tool is the action's `call`, resolved against that
server's tool list.

Availability is **per deployment**. A deployment declares its capabilities;
nothing probes for them (005 §5.4). It works like this:

1. Each MCP server in `k8srca.yaml` `provides` capabilities. The default is
   the server's own name when that is a capability name (`k8stools`,
   `prometheus`).
2. A deployment has the union over the servers its agents are wired to,
   plus `k8stools`, always.
3. The poller passes that set to each sandbox in the `K8SRCA_CAPABILITIES`
   environment variable, and `kb_query.py` reads it.
4. If the variable is unset, `kb_query.py` assumes k8stools alone. That
   errs toward reporting a check as a gap rather than assuming it can be
   made.

An earlier draft wrote the set into the skill bundle instead, and that was
rejected. The bundle is synced once per workspace, and scenario runs share
the production agents and skills while starting their own poller from their
own config. A file in the bundle would describe production in every
scenario. An environment variable set by whichever poller claimed the work
describes the deployment the session actually runs in. The knowledge itself
stays deployment-independent.

### 4.2 Unavailable is a gap, not an absence

An action whose capability is missing is **kept, not dropped**. Dropping it
would make its facts look checked-and-absent, which under `not` reads as
evidence. InferRCA distinguishes "attempted, result unknown" from "known
false" during an investigation. This applies the same distinction before
the investigation starts:

- A fact whose every producing action is unavailable is **unobservable here**.
  It is neither true nor false, so `not` does not hold for it either.
- A claim whose confirm *and* refute conditions all rest on unobservable
  facts starts as **could not check**, and the gap names what would settle
  it ("needs a memory time series: no metrics backend in this deployment").
  That is what the `gaps` dimension and the `could_not_check` verdict in 004
  look for, produced here by the knowledge rather than left to the model to
  notice.
- An action that is available but fails at run time (a tool error, logs
  rotated away) is a gap of the same kind, found later.

`human` actions are never available to the agent. They become recommended
next steps in the answer ("ask the DBA to check lock waits"), the honest form
of a check the agent cannot make.

### 4.3 Alternatives and their quality

One fact can have several producing actions with different requirements.
`memory_limit_plausibly_reached`, for example, can come from a Prometheus
range query or from a single sample via k8stools' `get_container_metrics`.
The best available action is used, and `quality` (`complete`, `partial` or
`point`) records what is lost. A `point` sample can confirm "near the limit
now" but cannot show growth over time, so it cannot refute `memory-leak`.
Where a condition needs a series, a point sample leaves the fact
unobservable for that condition.

The checkout scenario is this situation exactly. One usage sample showed GC
pressure, and no history was available to say since when.

### 4.4 Cost

`cost` is a small integer that orders actions, cheap lookups first, as
InferRCA's `priority` does. It orders the suggestions in §6; it never
removes one.

## 5. Entry points: symptoms, and alerts mapped to them

Knowledge is keyed by **symptom**, because our questions come from people in
Slack ("why is checkout slow?"), not from an alerting system. The vendored
KB is keyed by alert name instead, and that is why itbench-33 got nothing
from it: its alert, `PendingPodsDetected`, is not one of the 81 names.

Alerts still matter. Runbooks are usually written per alert (§9), and 005
§3.3 adds investigations triggered by alerts posted to a channel. So alerts
are a **second entry point that resolves to symptoms**, through a mapping
table:

```yaml
alerts:
  KubePodCrashLooping:            # kube-prometheus's name
    symptom: pod-restarting
    bind: {Pod: labels.pod, Namespace: labels.namespace}
  CrashLoopBackOff:               # the vendored KB's name for the same thing
    symptom: pod-restarting
  PendingPodsDetected:
    symptom: pod-pending
  HighRequestLatency:
    symptom: high-latency
    bind: {Service: labels.service}
```

`bind` copies the alert's labels into the claim's parameters, so an alert
for pod `checkout-7f9c` starts the investigation with `Pod` already set.
This is InferRCA's alert mapping, reduced: an alert maps to a symptom and
binds labels to parameters.

Many alert names map to one symptom, which is what makes runbook alert names
and vendor alert names interchangeable. An alert with no mapping falls back
to `kb_query.py search`, as today. The build reports alert names, from the
vendored KB or from any runbook, that map to nothing.

## 6. How the agent uses it

`kb_query.py` stays the interface (002 §7.2). It changes in three ways:

- `lookup <symptom|alert>` returns each candidate in a line or two, ordered
  cheapest test first. Each line has the candidate's quickest test this
  deployment can make (a refute over a confirm at equal cost) and how many
  sub-causes it has. It then lists the checks those tests need, one row per
  tool. Candidates that only unavailable actions could decide come last,
  marked could-not-check (§4.2). *Built, stage 1; made brief after the
  first mining gate.*
- `claim <id> [<id> ...]` returns a candidate's full criteria: every
  alternative, the `note`, its exclusions and refinements, and its checks.
  The agent reads it for any candidate the quickest test does not settle, and
  for the cause it concludes on. *Built after the first mining gate.*
- `refine <claim>` returns what confirming that claim opens up, in the same
  brief form as `lookup`. *Built, stage 1.* It lists the common sub-causes,
  and names the uncommon ones (`prevalence: uncommon`) without their tests.
  An uncommon one needs a disposition only when the evidence points at it, or
  fits none of the common ones. `refine <claim> --all` tests them too.
  `lookup` counts the two apart: `+3 sub-causes (+3 uncommon)`.

**Why prevalence.** Mining took `oom-kill` from three sub-causes to six. In
the third gate (§11.2), answers that concluded on OOM disposed of the new
ones (cache growth, node memory pressure) and left out memory-leak and
workload-spike. They covered a different three, not more. Naming all six
where the candidates are listed did not help. Each sub-cause shown is one
more the answer has to dispose of. So the likely few are shown, and the rest
wait until the evidence calls for them. Which ones are likely is a reviewer's
judgement (§8.3); the guides rarely say.

**Why `lookup` is two steps.** Once the first family was mined (§11.2),
`lookup CrashLoopBackOff` printed every candidate's full criteria and notes:
13 candidates in 28 KB. Two things showed in the answers:
- Answers repeated the text as if it were an observation. A mined action's
  limits line, "only retained revisions are visible", became "only revision 2
  is retained" in 5 of 12 runs. The history listed revision 1.
- How often a candidate was disposed of followed its position in the list,
  not its relevance. The first four were mentioned in 12/12 answers; later
  ones fell to 3/12 and 0/12.

The brief form keeps the knowledge and prints the notes only for candidates
being pursued. It is about 10 KB, of which 1.4 KB is the vendored record's
evidence and remediation.
- **`open --facts <file>`**: given the facts recorded so far, which claims are
  still open, and which available actions would separate them, cheapest
  first. *Built 2026-10-17, with recorded facts (§6.1).* This is InferRCA's "identify candidate actions"
  step done as a script call the model chooses to make, not as an engine loop
  around the model. It is the guardrail 002 §2 asks scripts to supply: it
  cannot make the model look, but it can say precisely what is left to look
  at.

The facts file is the evidence part of the investigation record (002 §3),
written in the fact vocabulary. How much of the record moves to fact form is
an open question (§12.1). *Since 010, the facts come from code into
`evidence/facts.jsonl`, and `open --workload` reads them (010 §3).*

The SKILL.md rule "account for every candidate" stays, and gets sharper: every
claim `lookup` and `refine` returned needs a verdict, and could-not-check
ones arrive already justified. *Dropped by 010: the short answer names only
what a fact rules out, and the rest appears as next steps (010 §4, §5.1).*

### 6.1 Checking against the evidence record

**Changed ([010](010-facts-and-short-answers.md) §5.2, §6, done 2026-10-23):** the completeness check and "owed" are off
(`review.completeness: false`; the code stays). The plan's two deterministic checks (the leader
is the stated cause; every candidate a fact rules out is named) were not built. The answer checks
of 010 §5.2 replaced completeness on their own (010 §9.3). The light reviewer, which reads the
answer and the facts it cites, is built and off (010 §9.8). The evidence record below is what
010's facts layer writes beside (`evidence/facts.jsonl`).

*Proposed, 2026-10-17. Not built.*

Four gates of measurement (§11.2) showed what better knowledge text cannot
fix. The agent drops candidates it was given, applies a refute the evidence
does not meet, and states things no tool returned. Each of those can be
checked by a script, if the script can see what the tools returned. Today it
cannot: `wrap_mcp_tool` (`src/k8srca/tools.py`) hands each result to the
model and keeps nothing.

**The evidence record.** *Built 2026-10-17* (`src/k8srca/evidence.py`). The
wrapper also writes each result to `evidence/<session>/<n>-<tool>.json` in
the workdir, with its arguments.
- **Contents:** each file holds the text the model saw and the server's
  structured result. The answer check reads the text, and the extractor the
  structured result. A namespace health call is about 7.5 KB.
- **Numbering:** `n` runs across a session's turns, resuming from the files
  already there.
- **What the model sees is unchanged.** It needs no new cluster access: the
  results have already passed k8stools' redaction, and the model has
  already seen them.
- **Lifetime:** the record lives in the session's workspace, with the
  investigation record (002), and is reaped with it.
- **Writing is best-effort.** A failed write is logged, and the tool call
  still succeeds.

Both checks below read it. Neither runs an investigation loop, which is what
§10.2 and 002 §10.3 reject: the model still decides what to look at, and a
check is a script it calls or a pass over its answer.

**`open --facts`: candidates evaluated from facts.** This is §6's planned
command, with the open question of where the facts come from (§12.1)
answered in two parts:
- **Extracted facts.** A script maps fields of the record to facts where a
  field states one outright. Examples are the last exit code, limit equal to
  request, a probe configured, and init containers (k8stools 4.0, #24).
  Each fact carries the call id it came from.
  - **`onset_observed` is not among them with k8stools alone.** #24 first
    asked k8stools for span fields to extract it from. The review cut them,
    because the Kubernetes API keeps only each container's restart count and
    last termination: no earlier terminations, and not when restarts began.
    4.0 says that in its results and points to metrics instead. So the
    extractor takes `onset_observed` only from a restart series (Prometheus'
    `kube_pod_container_status_restarts_total`) or centralized logs. Where
    neither is available, a claim that needs it is could-not-check. That is
    what the jvm truth already says of the release and the onset.
- **Recorded facts.** The model adds the rest, such as what the logs show,
  with the same citation. The reserved timing facts (§3.4) can only be
  extracted, never recorded. That closes the gap the fourth gate showed:
  answers refuted the true cause on an event window they read as the onset.

`open` returns every candidate reached, and the common sub-causes of any that
survive, each with one of these statuses:

| status | when |
| --- | --- |
| confirmed / refuted | an alternative holds; the output names it and its facts |
| conflicting | both sides hold, so a fact is wrong |
| open | the facts are missing; the output names them and the cheapest available check for each |
| could-not-check | only an action that needs an absent capability would decide it |

**Planned (008 §4.3):** these statuses become standings. Confirmed becomes
most likely, at the strength `open` computes. Refuted becomes ruled out.
Open, could-not-check and weakened become *not ruled out*, each with its
reason: facts missing, a capability missing, or an `against` holding.
Could-not-check stops being a verdict and becomes a reason, so a cause that
cannot be confirmed but still ranks first stays most likely.

The logic is three-valued. `not X` holds only when X was observed false, never
because X is missing, or the script brings back the absence-as-evidence that
review removed (§8.3). Prose conditions stay open for the model to decide.

This answers the two failures text could not. The table lists every candidate
reached, so none can be dropped silently, and the script, not the model,
decides whether an alternative holds. Its new risk is systematic: an
extractor bug gives the same wrong verdict in every run, where the model's
mistakes scatter. So the extractor is tested field by field, and scored as a
component eval (`src/k8srca/evals/`): extraction and `open` run over a
scenario's capture, and the result is compared with `truth.yaml`'s rivals.
The eval costs nothing to run. It measures how far extracted facts alone get
before any agent runs.

**Checking the answer.** A script reads the draft answer against the record:
- **Numbers trace to a result.** Every number and duration in the answer
  appears in a saved result, or follows from ones that do. Many graded
  unsupported claims fail this: a one-hour event retention, "2–8 s",
  "13–52 restarts", "23 other workloads".
- **Scope claims meet the namespace data.** "Every other workload is
  healthy", "only", "none" and similar are checked against the saved
  namespace health output, including the `recovering` group k8stools 4.0
  added (#24). That group holds ready workloads with a restart in the last
  24h, which 3.x listed as healthy with only a restart count.

The skill can ask the agent to run it before answering. Agents skip such steps:
they skipped `claim` for the cause they concluded on (§11.2). Alternatively
the worker runs it on the final answer and returns its flags for one
revision, at the cost of one turn. Which of the two to use is decided by
measurement.

**Measured before it is built into the agent:**
- **The answer check runs offline first.** Seventy-one graded runs have their
  unsupported claims recorded, and their sessions' tool results can still be
  read. Running the check over them gives its catch rate against the
  grader's list, and its false alarms, at no cost.
- **Order:**
  1. The evidence record.
  2. The answer check, offline and then in runs.
  3. `open` with recorded facts only.
  4. The extractor, on k8stools 4.0's fields (#24), with `onset_observed`
     from metrics only.

**Offline result: the answer check** (`skills/k8s-rca/answer_check.py`,
2026-10-17). It was run over 59 graded jvm runs (24 baseline, 35 with mined
content), against evidence records rebuilt from their sessions:

| graded unsupported claim | caught |
| --- | --- |
| "every other workload is healthy" | 30/33 (91%) |
| "events are kept about an hour" | 8/11 (73%) |
| "only JVM services" | 4/12 |
| when it began | 13/46 (28%) |
| lifetime stated as cadence | 1/11 |
| other: log content, init containers, QoS | 6/40 (15%) |
| **all** | **62/153 (41%)** |

- **Flag load:** 2.5 flags per run on baseline answers, 5.4 on the longer
  mined-content answers. About 30% of flags match a graded claim.
- **A hand-labelled sample of the rest** (30 scope flags): 8 were the same
  overclaim the grader had not listed, 10 were borderline but worth showing
  (dependencies called healthy with 26 and 14 restarts), and 12 were noise.
  So about 60% of scope flags point at something worth a second look.
- **The first version was much noisier.** It flagged every "all" and
  "every", at 9 flags per run, nearly 80% of them matching nothing graded.
  Restricting scope flags to claims about workloads' health removed most of
  the noise and lost almost nothing it caught.
- **What it cannot reach is about half the graded claims.** These are
  correct numbers carrying a wrong inference ("crash-looping since the
  163-day-old bump", where 163 is real), and wrong facts without numbers (log
  content read from a kubelet message, "no init containers"). k8stools 4.0
  (#24) removes the sources of the last two: a reclaimed log is absent, and
  init containers are listed. The first needs `open`.
- **Next:** wire it into the agent and measure in runs, with the skill asking
  first. If the agent skips it, as with `refine`, then the worker runs it
  on the final answer.

**Offline result: a model reviewer** (2026-10-18). The checks above cannot
reach an inference, so a reviewer was tried: one `claude-sonnet-5-5` call per
answer, given the answer and its evidence record, asked to flag claims that
fail two properties:
- **Valid:** a claim no result states, one a result contradicts, or an
  inference written as observed.
- **Relevant:** a conclusion that rests on no observation.

The prompt includes the timing rule. It was run over 30 graded runs from the
k8stools 4.0.1 and 4.0.2 sittings, which have the worker's own evidence
records:

| arm | graded claims | reviewer | answer check | either | reviewer flags/run |
| --- | --- | --- | --- | --- | --- |
| 4.0.1 baseline | 45 | 60% | 18% | 64% | 8.4 |
| 4.0.1 sixth gate | 45 | 47% | 20% | 60% | 6.2 |
| 4.0.2 baseline | 51 | 53% | 10% | 61% | 7.2 |

- **It reaches the inference.** It caught 45 of the 79 graded "crash-looping
  since the rollout" claims, a kind the answer check cannot see.
- **Its other flags are mostly real.** About a third match a graded claim.
  In a sample of 22 of the rest, about half were problems the grader had not
  listed: a log said to end at the agent banner when it goes on, "there's no
  Prometheus" when namespace health lists one, and a restart count said to
  have "climbed continuously" from one snapshot.
- **It argues against the right cause.** In 14 of 30 runs it disputes OOM
  because the reason is `Error`, not `OOMKilled`. "Likely" rather than
  "confirmed" is fair, and the scenario has a trap for it; an agent that
  acted on the flag by dropping the cause would be worse off.
- **It disputes "killed on every life" in 17 of 30 runs**, since only the
  last termination is recorded. That is a question about the knowledge, not
  the reviewer: memory-limit-too-low's second confirm alternative rests on a
  fact the record shows for one life and the event series' span, not every
  life.
- **Cost:** about 15k tokens in and 1.7k out per answer.
- **Not yet measured:** latency, and precision by hand over a larger sample.
- **Since 010:** it still ships in the skill and has its tests, but SKILL.md
  does not mention it and nothing runs it. 010's answer checks (010 §5.2)
  check figures against cited facts in the host instead.
- **Next:** if it is used, the worker runs it on the final answer. That
  happens alongside a deterministic completeness check against `open`'s
  owed list, and its findings return as one revision turn. The prompt would
  have to separate wording ("confirmed" for "likely") from grounds to drop a
  claim.

**The completeness check, built** (`src/k8srca/review.py`, 2026-10-18). The host runs it on the
final answer, in the Slack orchestrator and the scenario runner alike:
- **Where the rows come from.** Each `kb_query.py open` run writes its rows (id, title, status,
  depth, parent) to `<workdir>/evidence/open/<n>.json`. It does so only when an evidence record
  sits beside the skill, so running from the repo writes nothing.
- **What is owed.** Every candidate in the last `open` on each facts file is owed a disposition,
  and so is every sub-cause it confirmed, refuted or found conflicting. Open sub-causes are not
  owed: there are dozens, and listing each would bury the answer. A claim is addressed when the
  answer names its id or its title.
- **What happens.** If anything is missing, one revision turn names the missing claims with their
  status, and asks for the whole answer again with nothing else changed. The revised answer
  replaces the first; the scenario run keeps the first as `first_answer`. A session that never ran
  `open` is asked to run it. A workspace the host cannot see is not checked.
- **On saved answers**, with `open` rows from the jvm facts, every one of 20 answers owes
  something: 3 to 11 claims, mostly rare candidates such as container-config-error, which one line
  of "not checked" settles. Every run therefore pays a revision turn. `--no-review` turns it off
  for a baseline skill, which has no `open`.

**The reviewer, built** (`review.reviewer`, off by default; 2026-10-18). *Removed 2026-10-22:
replaced by 010's light reviewer, and never on in production (010 §9.8).* It is the offline
reviewer with one change. Certainty is its own kind, `overstated`: the claim stays, and the
revision restates how sure it is. Never flag a supported conclusion as contradicted because
another explanation remains. A flag whose quote is not in the answer is dropped, and a failed
call holds nothing back. Its flags join the completeness findings in the one revision turn.
`scenario run --reviewer` turns it on for a run.

Offline, over the same 30 runs:

| prompt | caught | flags/run | runs where it disputes OOM |
| --- | --- | --- | --- |
| eval (v2) | 53% | 7.3 | 14/30 |
| eval with the built timing rule only | 59% | 8.2 | 16/30 |
| built | 58% | 8.8, of them 2.7 `overstated` | 4/30 |

The ablation separates the two changes:
- **The timing rule raised the catch.** "Crash-looping since the rollout" claims caught rose from
  45 to 56 of 79. The built rule says a revision's age is not an onset either, and that none of
  these says the failures ran continuously.
- **The certainty paragraph removed the disputes.** Runs where the reviewer disputes OOM fell from
  16 to 4 of 30, and the catch held: 80 flags became `overstated`.

Each prompt was run once, so differences of a few claims are noise.


**`open`, built with recorded facts** (2026-10-17). The facts file is one per
failing workload:
- **Syntax:** each line is `fact[args]` (observed true) or `not fact[args]`
  (observed false), with optional `@n` citations of evidence calls.
- **Role arguments take their letter** (`C`, `W`, `P`, `N`, `S`), whatever
  was written there, because the file describes one workload.
- **What is refused:** unknown fact names (with a suggestion), arity errors,
  contradicting lines, and the reserved timing facts. An alternative that
  needs a timing fact is could-not-check until the extractor exists.
- **Functional facts** (one value per subject, such as the exit code) make
  the other values false.
- **Output:** every candidate under the symptom and the common sub-causes of
  each one not refuted, with status, deciding alternative, or missing facts.
  Each needed fact gets one line, with its cheapest available check. That is
  about 9 KB with facts recorded, 14 KB with none.
- **A confirmed sub-cause confirms the claim it refines**, which shows "by:
  its sub-cause X". Before, `oom-kill` could read OPEN beside its confirmed
  memory-limit-too-low. All sub-causes refuted does not refute the claim: the
  listed ones are not all there are.
- **It ends with the dispositions the answer owes**, grouped by status. A
  confirmed sub-cause's refuted siblings get a line of their own, because in
  the sixth gate (§11.2) answers reported the confirmed one and dropped the
  siblings refuted beside it.

**Checked against `truth.yaml`, at no cost.** Run on six facts an agent
could record for `ad` from the jvm capture, `open` refutes application-crash,
failed-liveness-probe, memory-leak and workload-spike, and confirms oom-kill,
all as the truth allows. It leaves bad-config open, where the truth wants a
judgement, and calls application-release could-not-check.

At first it left memory-limit-too-low open, though the truth says confirmed.
Its only confirm alternative was "killed on every life with no healthy period
before the kills", which the capture cannot show either way. The knowledge,
not `open`, was at fault: a limit below the startup footprint is too low
whatever came before. The claim now has a second confirm alternative, killed
on every life without completing startup (2026-10-17), and `open` confirms
it. A container that once ran on the limit and now dies at startup still
fits it. Its footprint grew, and the change that grew it is the trigger, not
a rival.

That fixed `open` and not the agents. Both alternatives start with
`killed_on_every_life`, which a strict agent will not record, since only the
last termination is retained. The eighth and ninth gates lost the cause to it
(§11.2). §3.5 rescopes the fact to the retained lives.

Model-recorded facts will need to cite a call. The record numbers calls, but
the model is not yet told the numbers. Showing them changes what the model
sees, so it waits for step 3.

## 7. Build checks

`k8srca kb build` checks the whole format ([`src/k8srca/kb/knowledge.py`](../src/k8srca/kb/knowledge.py),
`check`). It fails on:

- a fact, claim, action, symptom or capability name that does not resolve;
- a claim with no `refute`;
- a fact with no producing action;
- a `k8stools` action whose `call` is not a k8stools tool (checked against
  k8stools' tool list);
- a refinement cycle, or an `exclusive` pair that is not a pair of siblings;
- **timing** (§3.4):
  - a confirm or refute alternative that names `changed_before_onset`
    without a positive `onset_observed` in the same alternative;
  - a change kind that does not resolve;
  - a change cited as a support;
  - an action that lists `changed_before_onset` in `produces` (a kind's
    `seen_by` says which actions see it);
  - a fact whose name looks like a coined timing fact (`precede`,
    `before_onset`, `changed_before`, `_near_restart` and similar).

It reports, without failing:

- prose conditions;
- claims every rule of which needs a capability the default deployment
  (k8stools only) lacks;
- unmapped alert names.

Once runbooks exist it will also perform the layer merge (§9.3).

## 8. Mining public debugging guides

### 8.1 Why mine

The alternative is to add whatever our scenarios turned out to be missing,
such as a GC-pressure candidate under `HighRequestLatency` for checkout.
That fits the content to the suite, which is the overfitting 006's hold-out
suite exists to detect. Mined content comes from sources we did not choose
by looking at our failures.

The KB-off comparison also says what mining is for. The model already knows
most of the facts these guides contain: it found causes just as well without
the KB. Its weaknesses were *complete candidate lists* and *criteria that
refute*. So extraction targets those two, not explanations.

### 8.2 Sources

These are the sources to start with. Each licence was checked on
2026-10-16. "Vendor" means extracted content may be stored in the repository
with attribution. "Reference" means a source may inform what we write, but
its text is not copied.

| Source | Shape | Good for | Licence |
| --- | --- | --- | --- |
| **Kubernetes documentation**, debugging tasks: [Debug Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/), [Debug Running Pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/), [Debug Services](https://kubernetes.io/docs/tasks/debug/debug-application/debug-service/), [Determine the Reason for Pod Failure](https://kubernetes.io/docs/tasks/debug/debug-application/determine-reason-pod-failure/), [Debug Init Containers](https://kubernetes.io/docs/tasks/debug/debug-application/debug-init-containers/), [Debug a StatefulSet](https://kubernetes.io/docs/tasks/debug/debug-application/debug-statefulset/), [Troubleshooting Clusters](https://kubernetes.io/docs/tasks/debug/debug-cluster/), [Debugging DNS Resolution](https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/) | prose, task-oriented | the core symptoms: pods pending, restarting, unreachable; DNS; nodes | CC BY 4.0 ([kubernetes/website](https://github.com/kubernetes/website)); vendor |
| **kube-prometheus runbooks**: [runbooks.prometheus-operator.dev](https://runbooks.prometheus-operator.dev/), source in [prometheus-operator/runbooks](https://github.com/prometheus-operator/runbooks) | one page per alert: meaning, impact, diagnosis, mitigation | alert mappings (§5) for the alert names most clusters actually fire; candidates per alert | Apache-2.0; vendor |
| **GKE troubleshooting**: [index](https://cloud.google.com/kubernetes-engine/docs/troubleshooting), e.g. [CrashLoopBackOff events](https://cloud.google.com/kubernetes-engine/docs/troubleshooting/crashloopbackoff-events) | prose per symptom, often with decision steps | node, network, storage and autoscaler failures | CC BY 4.0; vendor, keeping only the generic content, not GKE-specific steps |
| **Amazon EKS troubleshooting**: [user guide](https://docs.aws.amazon.com/eks/latest/userguide/troubleshooting.html), [best practices guide](https://docs.aws.amazon.com/eks/latest/best-practices/introduction.html) | prose per error message | node join, networking (CNI), IAM-shaped access failures | CC BY-SA 4.0 ([awsdocs/amazon-eks-user-guide](https://github.com/awsdocs/amazon-eks-user-guide)); vendor, but ShareAlike applies to what we derive, so keep it in its own directory |
| **AKS troubleshooting**: [Azure Kubernetes Service troubleshooting](https://learn.microsoft.com/en-us/troubleshoot/azure/azure-kubernetes/welcome-azure-kubernetes) | prose per symptom and error | connectivity, node, storage, upgrade failures | CC BY 4.0 ([MicrosoftDocs/SupportArticles-docs](https://github.com/MicrosoftDocs/SupportArticles-docs)); vendor |
| **OpenShift troubleshooting**: [openshift-docs `support/troubleshooting`](https://github.com/openshift/openshift-docs/tree/main/support/troubleshooting) (published on docs.redhat.com) | prose per component | pod, node, operator, storage and network investigation | Apache-2.0; vendor |
| **learnk8s visual troubleshooting guide**: [A visual guide on troubleshooting Kubernetes deployments](https://learnk8s.io/troubleshooting-deployments) | a decision tree | which checks matter and in what order (§3.3) | all rights reserved; reference only |
| **k8sgpt analyzers**: [k8sgpt-ai/k8sgpt `pkg/analyzer`](https://github.com/k8sgpt-ai/k8sgpt/tree/main/pkg/analyzer) | Go code, one analyzer per resource kind | checks already concrete enough to bind to k8stools calls | Apache-2.0; vendor |
| **Robusta playbooks**: [robusta-dev/robusta `playbooks/robusta_playbooks`](https://github.com/robusta-dev/robusta/tree/master/playbooks/robusta_playbooks) | Python code, enrichment actions per alert | the evidence gathered per alert, and alert-to-check mappings | MIT; vendor |

The vendored knowledge base is a source too, but not one listed here: each
family reads its own vendored records (§8.5). InferRCA's runbooks are left
out deliberately: they are runbooks, not generic guides (§9.4).

### 8.3 Pipeline

`k8srca kb mine <family>` ([`src/k8srca/kb/mine.py`](../src/k8srca/kb/mine.py))
runs one family, a batch of related symptoms, from
`docs/rca/mining/sources.yaml`. `k8srca kb mine` with no family lists them.

1. **Fetch.** Each source is downloaded once, raw markdown or code where a
   raw form exists, rendered HTML reduced to its article text where not, and
   cached under `.k8srca/kb-mine/sources/` with the date it was retrieved.
   `--refresh` fetches again.
2. **Extract**, one model call per source. The model is given the family's
   scope, the existing vocabulary (every symptom, claim, fact and action
   already in the authored and mined layers, and k8stools' tool names and
   descriptions from the running server), and the source text. It returns
   §3-format items, each with a `quote`: one or two sentences copied from
   the source that the item rests on. **An item whose quote is not in the
   source is dropped**, compared on letters and digits only so markdown and
   line-wrapping do not decide it. The model proposes; the text decides what
   the source said.
3. **Merge**, one model call over every extraction. One claim per cause,
   however many sources named it, with their criteria kept as separate
   alternatives. Every merged item names the extracted items it was made
   from (`from: "source:name, ..."`), and **its citations are copied from
   those items, never written by the merge**. A merged item none of whose
   `from` entries resolves is dropped. The number of sources citing a claim
   is the confidence signal; nothing else is.
4. **Check.** The draft is checked with the build's own checks (§7), in
   place: under the authored layer, beside the rest of the mined layer. If
   anything fails, one repair round sends the failures back to the merge.
5. **Draft.** The result is written to `.k8srca/kb-mine/draft/<family>.yaml`,
   with a header naming every source, its URL, licence and retrieval date,
   and any check that still fails.
6. **Review, then ship.** A person reads the draft, fixes it, and copies it to
   `docs/rca/mined/<family>.yaml`. Committing it is shipping it: `kb build`
   reads `docs/rca/mined/` and nothing under `.k8srca/`. **Nothing mined
   ships unreviewed** because there is no other way in.

What review looks for, most of it learned from the first family:

- **Drafted refutes.** Public guides are almost all confirm-oriented ("if you
  see X, it's Y"), so refute criteria are mostly the model's, drafted from
  the confirm side and marked `refute_drafted: true`. Budget the review
  there.
- **Refutes that rest on silence.** "`not` error-in-the-logs" refutes a
  cause because the logs do not mention it. `not` means checked and found
  false, but logs truncate and rotate: the absence-versus-unavailable
  mistake scenario 8 exists to catch. A refute needs something positive,
  such as the container completing startup.
- **Pre-deciding.** A criterion that says which field or source to believe,
  rather than what evidence decides (§3.2).
- **Provider-specific content** that slipped past the generic rule.
- **Overlapping actions.** The mine report lists every draft action that
  calls a tool another layer's action already calls ("review"). Two reads of
  one tool are often both right; once, a mined action duplicated an authored
  one, and answers echoed its limits line as an observation (§6). The check
  doesn't fail the build, because only a reader can tell which case it is.
- **Timing.** Claims that blame a change should use the shared timing facts
  (§3.4). The prompt says so (rule 8), and the build refuses a coined timing
  fact.
- **Prevalence.** Where a claim has more than three sub-causes, mark the ones
  past the likely few `prevalence: uncommon` (§6). Mining adds sub-causes
  rather than replacing them, so this is where a family's breadth reaches
  the answer.

Extractions are cached by prompt version, model, source digest, family and
vocabulary, so re-running after a fix to the merge costs only the merge.
`--max-usd` (default $10) stops a run that spends past it. The default model
is the one `arch build` synthesises with.

Two implementation limits worth knowing. The output schemas are compiled by
the API into a grammar with a size limit, and the extraction schema is just
under it; `from` is one string rather than a list for that reason, and any
field added to the schema has to be tried against the API, not only the
tests. And a merge reads every extraction in the family at once, so a family
much larger than the first (11 sources, about 50 candidate causes extracted)
may have to be split.

Mined content lives in its own directory, apart from what we author. This
is the same reason `vendor/kb/` and `docs/rca/knowledge/` are kept apart:
what we received must stay distinguishable from what we wrote. Within it,
every item keeps the quotes it came from.

Families are mined, reviewed and measured one at a time (§11).

### 8.4 The cost of breadth

More candidates means more to dispose of: more tool calls, and more claims
an answer can get wrong. Symptom keying (§5) and refinement keep what
`lookup` returns small. It returns the top-level claims for the symptom,
and sub-causes only once their parent is confirmed. The tool-call and cost
columns in 004's reports are where an increase would show.

The first family showed that the number of candidates is not the only cost;
the amount of text is too. Full criteria for 13 candidates made the output
4.5 times longer, and answers echoed it. So `lookup` prints one test per
candidate, and the rest is `claim`'s (§6).

### 8.5 Converting the vendored 81

The vendored records are converted through the same pipeline, not by a
separate route. Each family lists the vendored alerts it converts (`alerts:`
in the manifest). The records of those not yet mapped by any layer are
rendered as one more source for the family, `vendored-kb`, beside the public
guides: one line per hypothesis, "PodRestartSpike candidate cause: OOM",
with the record's evidence fields and without its remediation fields.
Extraction quotes those lines like any other source, so a mined claim can
cite the vendored knowledge base as well as the Kubernetes docs.

What each hypothesis became is an explicit table, `converts`, in the layer
file:

```yaml
converts:
  PodRestartSpike:
    application release: release-regression
    OOM: oom-kill
    node issue: {declined: "a node problem is the nodes family's symptom"}
```

The pipeline fills it in from citations: a claim whose quote contains a
hypothesis's line converts that hypothesis. One that no claim cites
(usually because it is the same cause as an existing claim, which mining
may not redefine) is written as `declined: "TODO: ..."`, for review to point
at the right claim or to decline with a reason. The two alerts stage 1
converted by hand carry the same table in `docs/rca/knowledge/`.

A family is not finished until:

- **every vendored alert it lists maps to a symptom** (§5), so `lookup`
  answers it from the knowledge rather than the legacy record (`kb mine`
  reports one that does not);
- **every hypothesis of every mapped vendored alert is in `converts`**,
  naming a claim the alert's symptom reaches (directly or through
  refinement), or declined with a reason. **This is a build check**: `kb
  build` fails on a missing hypothesis, an undecided `TODO`, or a claim the
  symptom cannot reach, because once an alert is mapped `kb_query` stops
  showing its vendored record, and a hypothesis missing from the table is a
  candidate the agent silently stops weighing. It replaces stage 1's
  title-matching test, which mined claims' names would not pass.

When the last family is done, `kb_query.py`'s legacy path for unconverted
alerts goes, and the vendored file becomes a source the build checks
coverage against, not one it serves from.

The families, in the order they will be mined. The order is by how often an
investigation meets the symptom, not by which scenarios we have:

| Family | Vendored alerts it converts |
| --- | --- |
| `pod-restarting` | CrashLoopBackOff, OOMKilled (done by hand, stage 1), LivenessProbeFailed, StartupProbeFailed, PodRestartSpike, MemoryWorkingSetGrowing |
| `pod-pending` | PodPending, FailedScheduling, ResourceQuotaExceeded, LimitRangeViolation, AdmissionDenied, ReplicaSetFailure |
| `image-and-sandbox` | ErrImagePull, ImagePullBackOff, FailedCreatePodSandbox, ContainerRuntimeDown |
| `rollouts-and-jobs` | DeploymentRolloutStuck, DeploymentUnavailable, ReleaseCorrelatedFailure, StatefulSetFailure, PodPDBBlocked, JobFailed, JobDeadlineExceeded, CronJobMissed, NamespaceTerminating |
| `service-traffic` | ReadinessProbeFailed, ServiceNoEndpoints, ServiceLatencyHigh, HighRequestLatency, Ingress5xx, HighCPUThrottling, WebhookTimeout |
| `storage` | PVCPending, FailedAttachVolume, FailedMount, MultiAttachError, CSIControllerDown, VolumeFull, StorageLatencyHigh |
| `nodes` | NodeNotReady, KubeletDown, NodeMemoryPressure, NodeDiskPressure, NodePIDPressure, NodeCPUHigh, NodeDiskIOHigh, EphemeralStorageHigh, HighFileDescriptors, PodEvicted |
| `networking` | DNSFailure, DNSLatencyHigh, CoreDNSDown, CoreDNSHighErrorRate, NetworkPacketLoss, NetworkPolicyDenied, PodNetworkReceiveErrors, PodNetworkTransmitErrors |
| `scaling` | HPAUnableToScale, HPAMaxReplicas, ClusterAutoscalerBlocked, ClusterAutoscalerCloudError |
| `control-plane` | APIServerDown, APIServerHighErrorRate, APIServerHighLatency, APIServerTooManyRequests, APIClientThrottling, EtcdDown, EtcdHighDBSize, EtcdHighLatency, EtcdNoLeader, ControllerManagerDown, SchedulerDown |
| `access` | RBACForbidden, CertificateExpired, CertificateExpiring |
| `observability` | EventIngestionFailures, LogsPipelineBackpressure, MetricsScrapeFailures, NoScrapeTargets, TraceIngestionFailures, AlertStorm |

That is all 81. The vendored `group` label (`causal_parent`) is not used to
form them: one of its values covers 41 records (`src/k8srca/kb/build.py`).
Many families' claims will need capabilities we do not have yet
(`prometheus` above all), and will arrive as could-not-check (§4.2). That is
the honest state, not a reason to leave them out.

Only three families have scenarios that exercise them today:
`pod-restarting` (jvm), `pod-pending` (itbench-33) and `service-traffic`
(checkout, through `HighRequestLatency`). The
rest pass the build checks and review, and their measurement is the hold-out
suite (006), which is not limited to the symptoms we chose.

## 9. Runbooks

### 9.1 The boundary

A runbook is what only one organisation knows about diagnosing its own
systems:

- its services, dependencies and thresholds ("checkout p99 over 800 ms");
- its history ("this alert is usually the nightly batch job");
- checks outside Kubernetes (a database console, a vendor status page);
- owners, escalation, and remediation procedures.

If a statement would be true of any cluster, it belongs in the knowledge base
and is contributed there. If it describes rather than diagnoses ("checkout
calls payment synchronously"), it belongs in the wiki.

### 9.2 Same format, translated

Runbooks are prose and stay the source of record. Each runbook is
**translated** into the §3 format, with every item pointing back to the
runbook step it came from. InferRCA has already done this exercise three
times: its `runbooks/` directory holds three prose runbooks (crash loop,
latency SLO, cascade failure), and beside each one a `*-translation.md`
that translates it into InferRCA's language and records the decisions and
problems along the way. Those are the closest thing we have to a worked
example of runbook translation.

Runbooks are usually **keyed by alert**. The translation starts from the
alert mapping (§5): the runbook's alert maps to a symptom (an existing one, or
a new site-specific one), and its steps attach there. A runbook for
`CheckoutLatencySLO` becomes `high-latency`, bound to `Service: checkout`.

What translation changes:

- **Commands become actions.** A `kubectl describe pod` step becomes a k8stools
  action. A step that needs access the agent lacks becomes an action requiring
  that capability, or `human` (§4.2). Nothing in a runbook is executed as
  written: the security rule in CLAUDE.md holds, and remediation steps are
  reported as recommendations, never run.
- **Thresholds become comparisons** in conditions, not text in a prompt.
  InferRCA's latency-SLO translation could only express "p99 > 500 ms" by
  hiding the number inside the prompt of the action that checks it. A
  reader could no longer see the threshold, and changing it meant editing
  prompts. Comparisons (`Code != 137`, `p99 > 500`) keep the number in the
  rule.
- **"Usually" becomes cost or order**, never a probability (002 §10.1).

### 9.3 Combining the layers

Layers merge in a fixed order: authored over mined, runbooks over both. A runbook
**can**:

- add claims, facts and actions, including site-specific ones;
- reorder (lower the cost of "check the batch job" so it comes first);
- set thresholds for its own services;
- add alert mappings.

A runbook **cannot** remove or exclude a generic claim. Runbooks list the most
common cause first, and that is exactly how an investigation anchors. The
generic claims are what still get disposed of when the usual cause is not the
cause this time.

Conflicts (a runbook refute condition that contradicts a KB confirm) are
reported by the build, not silently resolved. Every claim keeps its provenance,
and the answer cites it: "per the checkout-latency runbook, step 3" versus
"knowledge base".

Runbooks are per site, so they are deployment content, like the capability
set (§4.1): supplied with the deployment's configuration, not baked into the
generic knowledge. How they reach the sandbox is §12.4.

### 9.4 Testing runbooks

A runbook can only be tested against the system it describes. Our scenarios
run on the OpenTelemetry demo application and have no runbooks. Runbook
evaluation belongs where the runbooks are: the deployment's own scenarios,
recorded and graded the 006 way. Until we have a site's runbooks, InferRCA's
three runbooks serve as stand-ins to exercise the translation and merge, not
to measure anything.

## 10. Rejected alternatives

### 10.1 A decision tree

See §3.3. It stores one order and one path, and it encodes the
stop-at-first-fit behaviour we are trying to remove.

### 10.2 Compiling to clingo and running a solver

This is InferRCA's route. It gives guarantees the model-interpreted format
cannot: the solver considers every rule and derives everything that
follows. It is rejected for now for 002 §10.3's reason. The Managed Agents
platform supplies the investigation loop, and the model handles messy
evidence, which is the part that works today. The format is kept
machine-checkable and close enough to InferRCA's that a solver stays a later
choice, not a rewrite: `open` (§6) is the step a solver would replace.

### 10.3 A separate runbook format

It would need a second interpreter, a second set of build checks, and a merge
across formats. Translation is the one-time cost of keeping a single format,
and it is where a runbook's ambiguities surface, before an investigation
depends on them.

### 10.4 Adding what the scenarios are missing

See §8.1. It is quick, and it fits the knowledge to the suite.

## 11. Measurement

| Change | Gate |
| --- | --- |
| Format, with `CrashLoopBackOff` / `OOMKilled` converted | **Passed, 2026-10-16** (§11.1) |
| Availability (§4) | **Passed, 2026-10-16, without a batch.** With only k8stools, `kb_query.py` prints exactly what it printed after stage 1, which already assumed k8stools alone, so a scenario run had nothing to measure. Tests cover the rest: the poller passes the set, a deployment with `prometheus` sees those checks as available, and an unknown capability is refused at config load |
| Mined content, per family | The build checks and review (§8.3), and the family's completion checks (§8.5). Where a scenario exercises the family, re-run the KB-on/KB-off comparison on it, **judged on cause and evidence**: `rivals` stops being informative once the KB writes the candidate lists the agent is graded against. Then the hold-out suite (006), which is the real test |
| Runbooks | Stand-in runbooks translate, merge and pass §7. No score gate until a site has runbooks and scenarios |

Every gate compares the change against the baseline skill **re-run beside
it in the same sitting**, not against the recorded baseline alone. A single
batch of six resolves a move of four runs or more. Smaller moves are leans
until pooled to twelve runs per arm (004 §6.4).

### 11.1 Stage 1

The gate written at the time was: no dimension falls by two of six on the
three scenarios, and jvm `rivals` stays at 5/6 or above. It failed twice,
on a different dimension each time, and then turned out to be measuring
noise. On the jvm scenario:

| | skill | cause | evidence | rivals | traps | gaps | calls | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recorded baseline | vendored + discriminators | 6/6 | 2/6 | 5/6 | 6/6 | 6/6 | 16 | 0.22 |
| baseline, re-run | the same | 6/6 | 0/6 | 2/6 | 5/6 | 6/6 | 21 | 0.26 |
| stage 1, first batch | converted | 6/6 | 1/6 | 6/6 | 3/6 | 6/6 | 18 | 0.26 |
| stage 1, after the fix below | converted | 6/6 | 0/6 | 4/6 | 5/6 | 6/6 | 21 | 0.31 |

- **The first batch failed `traps` 3/6.** Two answers stated the restart
  interval as the instance lifetime ("SIGKILLed every 2-8 seconds"). These
  are different quantities: *lifetime* is how long one instance runs before
  it is killed; the *restart interval* is how often a new one starts, which
  the kubelet's back-off mostly sets. The original discriminator said
  "time-to-kill", which reads as either. The converted text put "how long
  that life ran" beside it and claimed lifetimes came from events, which do
  not record them. Lifetime and restart interval are now separate in the
  facts and in the actions that observe them. The re-run baseline made the
  same mistake once, so the fix is right on its merits rather than shown by
  the scores.
- **Against the same-sitting baseline the change is level or better** on
  every dimension: `rivals` 4/6 against 2/6, the rest equal. Pooled, the
  converted skill disposed of rivals in 10 of 12 runs; the old one, recorded
  and re-run, 7 of 12. A lean, not a result (004 §6.4).
- **Every failure first blamed on the conversion appeared in the old skill's
  re-run**: OOM sub-causes silently dropped, restart cadence in seconds,
  the span of the events (first to last seen) read as the pod's age, and
  `evidence` near zero.
- checkout and itbench-33 never looked up a converted alert, so their KB
  input was unchanged, and they still moved by up to three runs. That is how
  the noise floor was found.

`kb_query.py` output for a converted alert is about twice as long; jvm cost
rose by about $0.05 a run.

### 11.2 Mining: `pod-restarting`

The first family, mined 2026-10-16 from 11 public guides and the records of
its four unconverted vendored alerts.

| | |
| --- | --- |
| extracted | 273 items from 12 sources; no quote failed verification |
| merged (draft) | 45 claims, 44 with drafted refutes; 92 facts; 41 actions; 2 hypotheses no claim cited |
| shipped (`docs/rca/mined/pod-restarting.yaml`) | 44 claims, 89 facts, 41 actions |
| converted | 6 of 81 vendored alerts (was 2): LivenessProbeFailed, StartupProbeFailed, PodRestartSpike and MemoryWorkingSetGrowing added |
| `lookup pod-restarting` | 13 top-level candidates (was 4); 28 KB of output for `lookup CrashLoopBackOff` (was 6 KB) |
| model spend | about $3 for the merge and its repair round; the extractions' cost went unrecorded |

What review changed is listed in the file's header. The substantive changes:
refutes that rested on silence in the logs now need a positive observation
(§8.3); an intermediate OOM claim that restated what a cgroup OOM is was
dropped; specific causes were nested under general ones to bring the
candidates from 22 to 13 (§8.4); two hypotheses were mapped by hand.

Three pipeline failures surfaced on the way, each now handled: the merge
schema compiled to a grammar the API refused (§8.3); the merge ran out of
output tokens at 64k, thinking included, and is now given 128k and its
output cached; and a truncated response surfaced as a JSON error rather
than as truncation.

**First gate: no better, and some evidence slips traced to the content.**
These are jvm runs, 12 per arm, pooled from two same-sitting batches of 6.
The baseline is the stage-1 skill:

| | cause | evidence | rivals | traps | gaps | unsupported/run | calls | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 12/12 | 3/12 | 9/12 | 10/12 | 12/12 | 2.6 | 25.5 | 0.35 |
| mined | 12/12 | 0/12 | 7/12 | 10/12 | 12/12 | 3.8 | 17.6 | 0.31 |

- **Rivals: noise.** The missed rivals were the same in both arms:
  `bad-config` refuted, memory-leak and workload-spike left out,
  application-crash weakened. A `bad-config` refutation that looked specific
  to the mined arm in the first batch appeared in the baseline in the second.
- **Evidence fails in both arms for the same main reason.** Answers
  generalise "every other workload is healthy", but `accounting` has 578
  restarts. The scenario is hard on this, with or without the mined layer.
- **The extra unsupported claims trace to mined text:**
  - "Only revision 2 is retained" appeared in 5 mined runs and no baseline
    run. It echoes the limits line of `read-deployment-change`, a mined
    duplicate of the authored `read-rollout-history`.
  - "Application release, confirmed as the trigger" appeared once, against
    a release 163 days old.
- **Cost fell slightly** despite the longer lookup.

**Revision.** Three changes, measured together because they move the same
scores:
1. Dropped the duplicate action, and added the overlap review (§8.3).
2. Added timing to the format (§3.4), moving the claims that blamed a change
   onto it. This made `invalid-config-content` undecidable with k8stools
   alone, which it always was.
3. Made `lookup` two steps (§6). The output went from 28 KB to 10 KB.

**Second gate: the targeted slips are gone; rivals still lag.** These are jvm
runs, 12 per arm, from two batches each. The first batch was cut short by an
exhausted API balance, and the rest ran the next day, with both arms in each
sitting. The baseline is the stage-1 skill:

| | cause | evidence | rivals | traps | gaps | unsupported/run | calls (median) | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 12/12 | 2/12 | 12/12 | 10/12 | 12/12 | 2.2 | 13.5 | 0.23 |
| revised | 12/12 | 2/12 | 8/12 | 10/12 | 12/12 | 2.4 | 15 | 0.29 |

- **The echoed claims are gone.** "Only revision 2 is retained" appeared in no
  run. One run in 12 still lined the crash loop up with the 163-day-old
  release. Most answers called the release could-not-check on timing, as
  §3.4 intends.
- **Unsupported claims and evidence are back to the baseline's level**
  (2.4 vs 2.2 per run, and 2/12 each, against 3.8 vs 2.6 and 0 vs 3 in the
  first gate). "Every other workload is healthy" is still the main evidence
  slip in both arms.
- **Rivals: 8/12 against 12/12, a move the noise rule counts as real.** Three
  of the four misses are the same pair: memory-leak and workload-spike, both
  sub-causes of `oom-kill`, left out. The fourth is `bad-config` refuted.
  These are the misses of the first gate (7/12 then), so the revision did not
  cause them, but it did not fix them either. The baseline's own score moved
  from 9/12 to 12/12 between sittings, so the gap is less certain than its
  size suggests.
  - The likely mechanism is in the two-step lookup. The brief form shows
    `+6 sub-causes` without naming them, `claim oom-kill` names them only in
    a pointer to `refine`, and an agent that has concluded on OOM stops there.
    The baseline's lookup lists them where the agent first reads the
    candidates.
- **Cost rose** ($0.29 vs $0.23 median), and one revised run took 60 calls.

**Third gate: naming the sub-causes did not help.** The brief form now names
each candidate's sub-causes, and `claim` prints their quickest tests under the
claim. This is n=12, against the stage-1 skill pooled over three sittings
(004 §6.4), whose drift check this sitting was in its usual spread:

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline, pooled | 30 | 30/30 | 6/30 | 26/30 | 26/30 | 2.3 | 0.23–0.26 |
| sub-causes named | 12 | 11/12 | 1/12 | 6/12 | 9/12 | 3.1 | 0.28 |

- **Rivals: 6/12, no better than the second gate's 8/12.** Five of the six
  misses are still memory-leak and workload-spike left out.
- **The session logs show how the agent read the knowledge.**
  - In three of those five runs the agent never ran `claim oom-kill`. It
    opened another candidate instead (`dependency-unavailable-at-startup`,
    `bad-config`), and concluded on OOM from the lookup line alone.
  - In the other two it did run `claim oom-kill`, either alone or with
    three other claims, and still gave the two no disposition.
  - The sub-causes it did dispose of were mined ones: cache growth, node
    memory pressure and CPU starvation. The stage-1 layer has three
    sub-causes under OOM, and memory leak and workload spike are two of
    them. With six, the two whose test is "never gets past startup" are the
    ones dropped.
- **Breadth looks like the cause, not presentation.** Which sub-causes an
  answer covers moved toward the new ones, rather than covering more of
  them.
- **One cause miss and more unsupported claims** (3.1 per run). Both are
  leans at this n.

**Revision.** Reverted naming the sub-causes, and ranked them instead:
twelve mined sub-causes are now `prevalence: uncommon` (§6), and `oom-kill`
shows the authored three. The other option was to require a disposition for
every sub-cause of the concluded cause. It was set aside because it treats
the symptom: it would make answers longer, and it would not stop the rare
sub-causes from crowding out the likely ones.

**Fourth gate: ranking fixed the sub-cause misses; a timing trap remains.**
The revised arm is n=11, because the account ran out of credit on the twelfth
run. The baseline is the stage-1 skill pooled over four sittings. This
sitting's drift check read rivals 6/6 and traps 3/6. The traps score is low
for the baseline, but within its spread.

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline, pooled | 36 | 36/36 | 8/36 | 32/36 | 29/36 | 2.2 | 0.23–0.30 |
| prevalence | 11 | 10/11 | 1/11 | 8/11 | 9/11 | 3.6 | 0.27 |

- **The crowding is gone.** Memory-leak and workload-spike were left out
  alone in one run of 11, against five of 12 in the third gate. Every run
  that ran `refine oom-kill` disposed of the authored three, except that
  one.
- **Two of the three rival misses came from a trap instead.** Both runs read
  the event window as the onset, and so as "ran fine for ~159 days on this
  same 300Mi limit". That met the refute of `memory-limit-too-low` as the
  agent read it: a healthy period before the kills, with an onset observed.
  - One run refuted the true cause and blamed an image change, the arm's one
    cause miss.
  - The other took a traffic spike for the trigger.
  - The agent opened neither `claim oom-kill` nor `claim memory-limit-too-low`,
    so it never read the limits on onset that `read-events` and §3.4 give. A
    refute that rests on an observed onset is only as good as the agent's
    reading of "observed".
- **Rivals 8/11 against 32/36 is a lean** (73% against 89%) at this n.
- **Unsupported claims have risen at every gate with mined content:** 2.4,
  then 3.1, then 3.6 per run, while the baseline holds at about 2. Each gate
  is a lean on its own, but the direction has been the same three times.
  Most are peripheral overstatements, such as "every other workload is
  healthy", rather than links in the causal chain.
- **Cost:** two runs went past 50 tool calls.

**What the unsupported claims quote.** These are counts per run, over every
graded answer:

| kind | baseline (36) | 2nd gate (12) | 3rd gate (12) | 4th gate (11) |
| --- | --- | --- | --- | --- |
| when it began | 0.39 | 0.75 | 1.25 | 0.73 |
| "every other workload is healthy" | 0.19 | 0.50 | 0.58 | 1.18 |
| "events are kept about an hour" | 0.06 | 0.08 | 0.33 | 0.36 |
| everything else | 0.67 | 1.08 | 0.92 | 1.36 |

Two of these quote the knowledge:
- **"About an hour"** was the authored `read-events` limit, added with timing
  (§3.4). It is wrong where a series recurs: a series keeps its first_seen,
  and `ad`'s events span four days. Answers repeated it, and some reasoned
  from it to an onset.
- **"Every other workload is healthy"** tracks the mined `node-issue` note,
  "restarts on one node while peers elsewhere are stable argue for it". It
  means the workload's own replicas. On a one-node cluster, answers
  surveyed the other 23 workloads instead, and summarised them past
  `accounting`'s 578 restarts.

**Revision.**
- The events limit and the timing rule now say what first_seen bounds,
  rather than giving a duration.
- `node-issue`'s note names the workload's own replicas.
- The timing rule now prints wherever a shown claim rests on the onset, not
  only where one names a change. `refine oom-kill` now carries it, because
  `memory-limit-too-low` is refuted by a healthy period with an observed
  onset.

**Fifth gate: the quoted claims fell; the skipped `refine` remains.** n=10,
against the stage-1 skill pooled over four sittings. There was no drift check
this sitting, since the baseline had held its spread through four.

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline, pooled | 36 | 36/36 | 8/36 | 32/36 | 29/36 | 2.2 | 0.23–0.30 |
| fourth gate | 11 | 10/11 | 1/11 | 8/11 | 9/11 | 3.6 | 0.27 |
| fifth gate | 10 | 10/10 | 1/10 | 6/10 | 8/10 | 2.6 | 0.33 |

- **The two quoted claims fell.** Retention claims went from 0.36 to 0 per
  run, and "every other workload is healthy" from 1.18 to 0.7, which is still
  above the baseline's 0.19. Onset claims fell from 0.73 to 0.5. Unsupported
  claims per run are close to the baseline's again.
- **The onset trap held in 9 of 10 runs**, and cause was 10/10.
- **Rivals 6/10.** Three misses are memory-leak and workload-spike again,
  against one in 11 in the fourth gate. Two of the three runs never ran
  `refine oom-kill`; the third ran it and still left the pair out. In every
  gate since prevalence, most of these misses are runs that skipped the step
  that lists the sub-causes. No text in the knowledge reaches a step that is
  not taken. `open` (§6.1) would: it lists the sub-causes of any surviving
  claim in the same table.
- **Evidence is still 1/10.** The answer check (§6.1) targets it.

**Sixth gate: k8stools 4.0.1, `open --facts` and the answer check.** n=10 for
each arm, run in the same sitting. The new tool output could move the baseline,
so it was measured again rather than pooled.

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline, k8stools 4.0.1 | 10 | 10/10 | 0/10 | 9/10 | 9/10 | 4.5 | 0.22 |
| sixth gate | 10 | 10/10 | 0/10 | 4/10 | 10/10 | 4.5 | 0.41 |

- **Every run ran `open --facts`, and it worked as built.** Seven runs quote
  its verdict, memory-limit-too-low CONFIRMED.
- **No run ran the answer check.** The skill offers it, and nothing makes a
  run take it.
- **Rivals fell to 4/10, and every miss is memory-leak and workload-spike.**
  `open` lists both as REFUTED, directly under the confirmed sibling. The
  answers report the confirmed sub-cause and drop its refuted siblings; only
  one run named the pair. The closing line of `open`'s output, "every claim
  above needs a disposition, sub-causes included", comes about 100 lines
  after the table. The step that lists the sub-causes is now taken; the
  answers still leave them out.
- **Unsupported claims doubled in both arms**, from 2.2 to 4.5 in the
  baseline. The new kind is "it has been crash-looping continuously since the
  rollout 163 days ago": 26 of 45 baseline flags and 21 of 45 revised ones.
  Answers get it from k8stools 4.0's `restarts_per_day` (19.5) and
  `pod_age`: they multiply one by the other to get the 3,200 restarts back,
  and read that as a history. k8stools documents the field as a ratio, not a
  rate. The timing rule says a pod's age and a restart count are not an
  onset; the field does the division for the agent anyway.
- **Cost rose to 0.41 a run**, from 0.33 in the fifth gate. That is `open`
  and the facts file the run writes for it.

**Seventh gate: k8stools 4.0.2 (#26), `open`'s owed list, and a confirmed
sub-cause confirming its claim.** n=10 for each arm, run in the same sitting.
The tool output changed, so the baseline was measured again.

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline, k8stools 4.0.2 | 10 | 10/10 | 0/10 | 10/10 | 8/10 | 5.1 | 0.21 |
| seventh gate | 10 | 10/10 | 0/10 | 7/10 | 9/10 | 3.8 | 0.36 |

- **Dropping `restarts_per_day` did not remove the claim it fed.** 27 of the
  baseline's 51 unsupported claims are still "crash-looping since the
  rollout", against 26 of 45 under 4.0.1. Answers now reach it from the pod's
  age matching its last template change. The field was one route to the
  claim, not its source.
- **Rivals 7/10**, up from 4/10 in the sixth gate and still below the
  baseline's 10/10. Two misses are memory-leak and workload-spike again, and
  one is bad-config. Both runs that dropped the pair read the new line
  "refuted beside the confirmed memory-limit-too-low: memory-leak,
  workload-spike". The owed list reaches the step and not the answer.
- **Unsupported claims 3.8 a run against the baseline's 5.1.** This is the
  first gate below its sitting's baseline. The looping claim is 20 of the 38.
- **Seven runs ran `open --facts`; none ran the answer check.**
- What the knowledge prints is now read and still dropped when the answer is
  written. That is the case for the worker running the completeness check
  and the reviewer on the final answer (§6.1), rather than more text.

**Eighth gate: the completeness check and its revision turn** (§6.1). n=10, against the
seventh gate's 4.0.2 baseline: same capture, same tools, same evening.

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline, k8stools 4.0.2 | 10 | 10/10 | 0/10 | 10/10 | 8/10 | 5.1 | 0.21 |
| seventh gate | 10 | 10/10 | 0/10 | 7/10 | 9/10 | 3.8 | 0.36 |
| eighth gate | 10 | 10/10 | 0/10 | 9/10 | 7/10 | 3.1 | 0.64 |

- **Every run got a revision turn.** Four first answers left 2 to 6 owed claims unnamed. Six had
  not run `open` at all, against three in the seventh gate, and were asked to.
- **The revision did what it asked.** Unnamed owed claims fell from 2–10 per answer to 0–2, and
  memory-leak and workload-spike were dropped in no run.
- **Answers grew by about half,** from 6,300 to 9,300 characters on average, and cost rose to
  0.64 a run. Most of the added text is "not checked" lines for rare candidates.
- **The one rivals miss came from the revision.** Run 3's first answer had OOM from
  memory-limit-too-low, which is right. Asked to run `open`, it recorded strict facts and would
  not assert `killed_on_every_life`, since only the last termination is retained. `open` then
  left both could-not-check, and the revised answer demoted them: "two things my first answer got
  wrong". The offline reviewer raised the same objection in 17 of 30 runs. Both confirm
  alternatives for memory-limit-too-low rest on a fact the record can show only for the last
  life and the event window. It needs a definition the evidence can meet.
- **Unsupported claims 3.1 a run,** the lowest yet. The looping claim is 15 of the 31.
- **Traps 7/10,** within noise of 8/10.

**Ninth gate: the reviewer beside the completeness check** (§6.1, `--reviewer`). n=10, same
capture and tools as the eighth gate, the next morning.

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| eighth gate | 10 | 10/10 | 0/10 | 9/10 | 7/10 | 3.1 | 0.64 |
| ninth gate | 10 | 9/10 | 1/10 | 4/10 | 10/10 | 1.9 | 0.69 |

- **Rivals fell by five, and the revision did it.** Five of the six misses demoted OOM and
  memory-limit-too-low to could-not-check. In all five, the first answer had OOM from
  memory-limit-too-low as the most likely cause; four of them also refuted memory-leak and
  workload-spike, which is right. (The first answers were not re-graded: the account's credit ran
  out mid-regrade. This is read from their text.) The sixth miss, run 5, dropped memory-leak and
  workload-spike, which its one `open` run had left open, so they were not owed.
- **The reviewer pushed the same way as run 3 of the eighth gate.** It raised 8.3 flags a run.
  Its most common objection: "every start" and "every life" are not observed, because only the
  last termination is retained. It also marked OOM `overstated`, because the reason reads Error.
  The prompt says "keep each claim; state its certainty", and the revisions kept the claim but
  stated it as could-not-check. That is the disposition the grader rejects. Seven runs had also
  not run `open` and were asked to, so the knowledge's `killed_on_every_life` objection arrived
  twice, from both checks.
- **Unsupported claims fell to 1.9 a run,** from 3.1, the lowest yet. Traps were 10/10.
- **Answers grew** from 6,100 characters to 10,900 after revision.
- **The reviewer stays off by default.** It cuts unsupported claims, but at this rate it costs
  the correct cause in half the runs. It should not be measured again until `killed_on_every_life`
  has a definition the record can meet. Then both checks would be asking for a fact the evidence
  can give.
- **The wider lesson is 008's.** The grader read "confirmed" as the explanation that survives. The
  knowledge and the reviewer read it as proven. An answer that took the stricter reading lost the
  cause. 008 replaces confirmation with ranking across the answer, the knowledge and the grader.
  It regrades these gates before any prompt changes.

**Tenth gate: the reviewer's timing wording, copied into the skill** (§3.4, `kb_query.py`
`TIMING`). The rule now says a revision's age is not an onset either, and that none of a pod's
age, a revision's age or a restart count says the failures ran continuously. n=10, completeness
check on, reviewer off: the eighth gate's setup with only this changed.

| | runs | cause | evidence | rivals | traps | unsupported/run | $/run (median) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| eighth gate | 10 | 10/10 | 0/10 | 9/10 | 7/10 | 3.1 | 0.64 |
| tenth gate | 10 | 10/10 | 0/10 | 8/10 | 8/10 | 3.5 | 0.76 |

- **The looping claim did not move.** It is counted with one pattern across every gate, over the
  grader's unsupported claims. The pattern is stricter than the reading behind the eighth
  gate's "15 of the 31", so its counts are lower; they compare with each other.

  | | baseline | seventh | eighth | ninth | tenth |
  | --- | --- | --- | --- | --- | --- |
  | looping claims | 18 | 15 | 10 | 3 | 10 |
  | runs with one | 9 | 10 | 6 | 3 | 6 |
- **The answers make the inference the rule names, and say so.** "pod_age and the ReplicaSet
  revision age are both exactly P163D -- this pod has never been replaced; it's been crash-looping
  in place since". Text the agent reads reaches the step, not the answer. That is what §6.1
  found, and it is why the completeness check runs on the answer. Only the ninth gate's reviewer,
  which reads the answer, cut the claim. It also cost the cause.
- **Rivals 8/10 and traps 8/10** are within noise of the eighth gate. The two rivals misses
  dropped memory-leak and workload-spike, which their `open` runs had left open, so they were not
  owed.
- **The wording stays.** It is correct, and it is what 008 §7's reviewer will hold answers to.
  It does not earn its place in the skill by this measurement.

### 11.3 Mining: `request-latency` (010 step 3b)

Mined from seven public guides in 010's step 3b ($5.90) and reviewed in place,
in `docs/rca/mined/request-latency.yaml`; its header lists the review's
changes (010 §9.6). 010 §9.5 planned an authored copy in
`docs/rca/knowledge/`; none was made, so the family reaches
`knowledge_base.json` through the mined layer alone (§9.3). Two facts were
added for it: `runtime_memory_limit_configured` and `cpu_limit_set`.
- **Gate:** 010's, not §11's KB-on/KB-off comparison. checkout went from
  10.7/13 on main to 11.7/13 at cause 3/3 (010 §9.6).
- **Untuned test:** downstream-latency, where the cause is a dependency's CPU
  limit, not the asked-about service's resources. Cause 3/3 (010 §9.12).

### 11.4 Reopened: the families that have scenarios (2026-10-24, owner)

007 is in progress again, for the alert families that the suite's scenarios
reach (§8.5). Each family is its own step, planned and measured on its own,
against 004's checklist baseline and §6.4's rule (004 §10.4).

**What a family can be measured on.** Before this step, the knowledge base
holds nothing better than the vendored record for the alerts these scenarios
raise. `lookup FailedScheduling` returns one vendored hypothesis, "resource
shortage", with "no authored discriminator". `ErrImagePull` and `PVCPending`
look the same. `PendingPodsDetected` (itbench-33's alert) and
`KubePodNotReady` (pvc-pending's and image-pull-typo's) are not in it at all.

| Family | Scenarios it reaches | Order |
| --- | --- | --- |
| `pod-pending` | itbench-33 (`PendingPodsDetected`), pvc-pending (its new pod is Pending) | 1 |
| `image-and-sandbox` | image-pull-typo (`ErrImagePull` on the new pod) | 2 |
| `storage` | pvc-pending (`PVCPending`, the StorageClass) | 4, after the facts step (3) |
| per-node facts, for `open` | pvc-pending's and itbench-33's ruled-out resources and taints (step 3's known limit) | 5, after `storage` (owner, 2026-10-24) |
| `rollouts-and-jobs` | deploy-regression, only if its rollout claims reach it: its question is "crash-looping", which `pod-restarting` answers | 4, reach checked first |
| `service-traffic`, the rest | none known. checkout and downstream-latency raise the latency alerts `request-latency` already converts | decided on judgment, and said so |

`KubePodNotReady` is mapped in the step whose scenarios it is first needed
for. It is the alert of two scenarios whose causes are in two families, so
it maps to a symptom both reach, not to one family's.

#### Step 1 plan: `pod-pending`

**Hypothesis.** Candidates and discriminators for Pending pods let the
agent rule on the alternatives to a Pending pod's cause. It now forms its
own, from one vendored hypothesis or none.

**Expected effect.**
- **Offline, certain:** for both scenarios' alerts, `lookup` returns a claim
  that is the truth's cause, and the truth's ruled-out candidates as claims
  with a refute this capture can meet. Today neither returns either.
- **Live, a lean:** the ruled-out cells move, itbench-33's from 6/9 and
  pvc-pending's from 2/3. Diagnosis is already 3/3 and 2/3, and the
  checklist means are 10.0/12 and 9.3/10.

**Can we see it?**
- **The offline check:** yes, at no cost.
- **The ruled-out lean:** not at n=3. A group of one to three items has no
  measured spread, and process.md §1 says such an effect does not get a live
  run for itself.
- **The live run is the guard.** No REGRESSION under 004 §6.4 on either
  scenario, at n=3. itbench-33's spread is 0, so any drop in its mean flags,
  and its runs are read before the flag is believed (004 §6.4).
- **Recorded beside it:** which `lookup` calls the sessions made, and what
  they printed. A family the agent never looks up cannot help it.

**The work.**
1. **The family in `sources.yaml`:**
   - scope: a pod that is never scheduled or never starts its containers
     for want of a node, a quota or admission;
   - out of scope: image pulls (`image-and-sandbox`) and volumes that
     cannot bind (`storage`);
   - its vendored alerts, from §8.5's table: PodPending, FailedScheduling,
     ResourceQuotaExceeded, LimitRangeViolation, AdmissionDenied and
     ReplicaSetFailure;
   - its sources: the Kubernetes documentation's pod and scheduling pages
     with a vendor licence, chosen and licence-checked by hand (§8.2) before
     the run;
   - `PendingPodsDetected` mapped to its symptom, as `KubePodCrashLooping`
     was for `pod-restarting`.
2. **`k8srca kb mine pod-pending`,** at `--max-usd 10`.
3. **Review** the draft under §8.3's list. Then ship it as
   `docs/rca/mined/pod-pending.yaml` and run `kb build`.
4. **The offline check**, as a test where it can be hermetic. It asks
   `lookup` for each scenario's alert, and reads the result against the
   truth's cause and ruled-out candidates.
5. **The live run:** both scenarios, n=3, `--local-skill k8s-rca`, from a
   worktree.

The instrument is not changed: no checklist, truth or grader.

**Budget: $15, two days.**
- Mining: about $5, with a cap of $10.
- The live run: about $2. That is six sessions at $0.13–0.27, and six
  gradings.
- A re-mine after review, if the merge needs one: up to $3.

**Done:**
- the family shipped, and `kb build` passing;
- the offline check passing on both scenarios;
- n=3 on both with no REGRESSION;
- a result table here, with the lookups the sessions made.

**Stop:**
- **A REGRESSION** on either scenario.
- **Mining over $10.**
- **The review finding the draft can't be shipped without rewriting most of
  it.** That is a pipeline problem, not this family's.
- **Any of process.md §2's tripwires.**

#### Step 1 result (2026-10-24): done

**The family** is `docs/rca/mined/pod-pending.yaml`, eb6efab.
- **Mining:** eight sources, $8.66, against an estimate of $5 and a cap of
  $10. The draft had 52 claims, 113 facts and 41 actions, after one repair
  round.
- **Scope, refined at mining:** a pod waiting on a volume is a candidate
  here (`unbound-persistent-volume-claim`). Why the volume does not bind is
  `storage`'s. Without that candidate, pvc-pending's cause would have none
  in this family.
- **Review changes,** listed in the file's header:
  - the top level cut from 34 candidates to six, with the rest reached by
    refinement (§8.4);
  - `controller-cannot-create-pods` re-pointed to what refuses creation;
  - five refutes that rested on silence replaced: expired events, and an
    audit log that k8stools cannot read;
  - 20 sub-causes marked uncommon;
  - nine `exclusive` pairs dropped, as no longer siblings;
  - `PendingPodsDetected` mapped by hand.
- **Coverage:** six more vendored alerts converted, 14 of 81 in all.

**The offline check** (`tests/test_kb_pod_pending.py`) passes. For both
scenarios, `lookup` offers the truth's cause and every candidate the truth
rules out, each with a refute. The one exception is itbench-33's
`application-fault`, which is no cause of a Pending pod.

**The live run,** n=3 on each scenario from `../k8srca-pending`, against
004's baseline:

| Scenario | | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| itbench-33 | baseline | 3/3 | 10.0/12 | 9/9 | 6/9 | 9/9 | 4/6 | 2/3 | 0.21 |
| | pod-pending | 3/3 | 10.0/12 | 9/9 | 5/9 | 9/9 | 4/6 | 3/3 | 0.27 |
| pvc-pending | baseline | 2/3 | 9.3/10 | 8/9 | 2/3 | 9/9 | 6/6 | 3/3 | 0.17 |
| | pod-pending | 2/3 | 9.7/10 | 8/9 | 3/3 | 9/9 | 6/6 | 3/3 | 0.25 |

- **No REGRESSION:** both scenarios read `noise` under 004 §6.4.
- **The ruled-out lean did not show.** itbench-33 moved one item down and
  pvc-pending one up, as n=3 could not resolve (plan).
  `rules-out-application-fault` is still missed in all three itbench-33
  runs. It is outside this family.
- **Every session reached the family.**
  - **itbench-33:** all three ran `lookup PendingPodsDetected`. Two went on
    to `open pod-pending`, recorded facts about the selector, and ranked
    again.
  - **pvc-pending:** all three searched, then ran `lookup pod-pending` and
    `claim unbound-persistent-volume-claim`.

  In the baseline they had found only `PodPending`'s single vendored
  hypothesis.
- **pvc-pending's miss repeats.** One run in three, both times, says the PVC
  does not exist when its StorageClass does not. The scheduler message reads
  "unbound immediate PersistentVolumeClaims. not found", and those runs
  never read the PVC. That is `storage`'s question (step 3), so nothing is
  changed here: it would be a rule from a run.
- **Found, not changed:**
  - **The facts layer derives none of this family's facts** (010 §3), so
    `open` ranks pod-pending only on facts the agent records.
  - **Agents guessed fact names** before matching the vocabulary. One run
    tried `node_selector_matches_no_node` and `no_node_matches_node_selector`
    before `no_node_matches_node_selector_or_affinity`.

  Deriving the scheduling facts in code would close both.
- **A defect in 004's comparison, fixed.** The baseline pin compared the
  whole skill digest, the scenario's wiki plus the k8s-rca skill under test.
  So any skill change read "skill re-pinned" and was refused, which made the
  baseline useless for the changes it exists to screen. It now pins the wiki
  only (`report.world_skill`, with a test). No verdict changed.
- **Spent:** about $10.60 of $15. Mining $8.66, sessions $1.55, gradings
  about $0.40.

**Against the done criterion:** met. The family is shipped, `kb build`
passes, the offline check passes on both scenarios, there is no REGRESSION,
and this table is written.

#### Step 2 plan: `image-and-sandbox`, and `KubePodNotReady` (2026-10-24, approved)

**Why now for `KubePodNotReady`.** It is image-pull-typo's alert and
pvc-pending's. In the baseline and in step 1, every one of those nine
sessions ran `lookup KubePodNotReady` first. It found nothing, and each
session then searched its way to something else:
- image-pull-typo found `ImagePullBackOff`'s and `ReadinessProbeFailed`'s
  vendored records, and once `DeploymentRolloutStuck`'s;
- pvc-pending found `FailedScheduling`'s and `PVCPending`'s records in
  the baseline, and `pod-pending` in step 1.

§11.4 maps the alert in the first step whose scenarios need it. That is
this one.

**Hypothesis.** Two things:
- `KubePodNotReady` maps to a `pod-not-ready` symptom whose candidates are
  the ways a pod is not ready. That puts the first lookup of both scenarios
  on the right family.
- Candidates and discriminators for images that cannot be pulled let the
  agent rule out a registry outage or an auth failure. Today it forms that
  itself, from vendored records with no discriminator.

**Expected effect.**
- **Offline, certain:**
  - `lookup KubePodNotReady` offers, at the top level, a pod that is not
    scheduled, an image that cannot be pulled, and a pod running but failing
    readiness. These are the three the kube-prometheus runbook names.
  - Refining "not scheduled" reaches `pod-pending`'s six candidates.
  - Refining "image cannot be pulled" reaches this family's candidates,
    including the truth's cause (a tag that does not exist) and its
    ruled-out registry candidate, with a refute.
- **Live, a lean:** image-pull-typo's `rules-out-registry`, missed in 2 of 3
  baseline runs (ruled out 1/3), moves up. Diagnosis is already 3/3 and the
  mean is 8.3/9.

**Can we see it?**
- **The offline check:** yes, at no cost.
- **The registry lean:** not at n=3. It is one item.
- **The live run is the guard.** No REGRESSION under 004 §6.4 on
  image-pull-typo, at spread 1, or on pvc-pending, which the new symptom also
  reaches, at spread 1.
- **Recorded as in step 1:** which lookups the sessions ran. Expected:
  `lookup KubePodNotReady` and then `refine`, without the search that every
  session needed before.

**The work.**
1. **The family in `sources.yaml`.**
   - **Scope:** a pod whose container never starts because its image cannot
     be pulled or its sandbox cannot be created, and a pod not ready for any
     reason, as the entry symptom. Out of scope: a running pod failing
     readiness (`service-traffic`, a claim here with no sub-causes yet), and
     pods never scheduled (`pod-pending`, which `pod-not-ready` refines
     into).
   - **Vendored alerts,** from §8.5's table: ErrImagePull, ImagePullBackOff,
     FailedCreatePodSandbox and ContainerRuntimeDown.
   - **Sources, all fetch URLs checked:**
     - Debug Pods (listed already);
     - the Kubernetes documentation's Images, and Pull an Image from a
       Private Registry (CC-BY-4.0);
     - GKE's Troubleshoot image pulls (CC-BY-4.0);
     - AKS's Cannot pull images from ACR (CC-BY-4.0; provider-specific, so
       reviewed for what generalises);
     - kube-prometheus's KubePodNotReady runbook (Apache-2.0), which states
       the alert's meaning.
2. **`k8srca kb mine image-and-sandbox`,** at `--max-usd 10`.
3. **Review** under §8.3, as in step 1:
   - a small top level;
   - refutes that rest on positive reads;
   - `pod-not-ready`'s three candidates, with the not-scheduled one
     `refined_by` `pod-pending`'s six. A claim may refine into another
     symptom's claims, as `controller-cannot-create-pods` does.
4. **The offline check,** `tests/test_kb_image_and_sandbox.py`, on both
   scenarios, from `KubePodNotReady` through refinement.
5. **The live run:** image-pull-typo and pvc-pending, n=3, from a worktree,
   with the lookups recorded.

The instrument is not changed. The scheduling facts the facts layer does
not derive (step 1's finding) are not in this step.

**Budget: $15, one day.**
- Mining: about $7 for six sources, against step 1's $8.66 for eight, with
  a cap of $10.
- The live run: about $2.
- A re-merge after review: up to $3.

**Done:**
- the family shipped, with `KubePodNotReady` mapped, and `kb build` passing;
- the offline check passing on both scenarios;
- n=3 on both with no REGRESSION;
- a result table with the lookups.

**Stop:** as step 1:
- a REGRESSION on either scenario;
- mining over $10;
- a draft that can't be shipped without rewriting most of it;
- any of process.md §2's tripwires.

#### Step 2 result (2026-10-24): done

**The family** is `docs/rca/mined/image-and-sandbox.yaml`, fffeaea.
- **Mining:** six sources, $8.34. The draft had 66 claims, 93 facts and 35
  actions, and passed its checks after one repair round.
- **Review changes,** listed in the file's header:
  - **`pod-not-ready`'s top level:** cut from 21 candidates to seven. Thirteen
    of the 21 were restart-family claims, out of scope.
  - **`image-pull-failing`'s top level:** cut from 26 to seven.
  - **`pod-not-scheduled`** refines into pod-pending's six.
  - **`readiness-check-failing`** added by hand, citing the runbook's
    sentence. The draft had no claim for the runbook's third state.
  - **Refutes that could never fire dropped:** the registry's log, which no
    cluster tool reads. One of them was on image-pull-typo's ruled-out
    candidate.
  - **Refutes on expired events dropped.**
  - **A 401 no longer rules out a misspelled image.** The claim's own note
    says a wrong name can surface as 401.
  - **Prevalence:** 24 sub-causes marked uncommon.
- **Coverage:** four more vendored alerts converted, 18 of 81 in all.

**The offline check** (`tests/test_kb_image_and_sandbox.py`) passes.
`KubePodNotReady` offers the runbook's three states, and through refinement
reaches both scenarios' causes and every candidate their truths rule out,
each with a refute.

**The live run,** n=3 from `../k8srca-image`, against 004's baseline:

| Scenario | | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| image-pull-typo | baseline | 3/3 | 8.3/9 | 9/9 | 1/3 | 9/9 | 3/3 | 3/3 | 0.12 |
| | step 2 | 3/3 | 8.3/9 | 9/9 | 1/3 | 9/9 | 3/3 | 3/3 | 0.17 |
| pvc-pending | baseline | 2/3 | 9.3/10 | 8/9 | 2/3 | 9/9 | 6/6 | 3/3 | 0.17 |
| | step 2 | 3/3 | 9.7/10 | 9/9 | 2/3 | 9/9 | 6/6 | 3/3 | 0.29 |

- **No REGRESSION.** image-pull-typo reads `=` and pvc-pending `noise`.
- **pvc-pending found the StorageClass in all three runs,** against one miss
  in three in both the baseline and step 1. At n=3 that is a lean, not a
  finding.
- **The registry lean did not show.** `rules-out-registry` is still missed
  in 2 of 3 image-pull-typo runs. The answers rule out the registry less
  often than the knowledge now lets them.
- **The first lookup lands.** All six sessions ran `lookup KubePodNotReady`;
  one searched first. In the nine sessions before, every one had to search
  its way to some other record. The pvc-pending sessions went on to
  `refine pod-not-scheduled` and `claim unbound-persistent-volume-claim`.
- **Recording facts is now where sessions spend their effort.**
  - **Syntaxes tried:** `container_waiting_reason[C]=ImagePullBackOff`,
    `pvc_not_bound[quote-5d664c9767-xg5pk]`, and a fact's English sentence
    in place of its name.
  - **Help:** they ran `fact --help`.
  - **Source:** two read `kb_query.py`'s code.

  This is step 1's finding, larger: the facts layer derives none of these
  families' facts (010 §3), so `open` ranks them only on facts the agent
  writes, and writing them is hard.
- **One FAIL is the closed-world check working.** image-pull-typo run 2 cut
  the bad tag to `2.2.0-em`, a tag not in the capture.
- **Spent:** about $10.10 of $15. Mining $8.34, sessions $1.37, gradings
  about $0.40.

**Against the done criterion:** met. The family is shipped with
`KubePodNotReady` mapped, `kb build` passes, the offline check passes, there
is no REGRESSION, and this table is written.

#### Step 3 plan: the scheduling and image facts, in code (2026-10-24, approved)

**Why before `storage`.** Steps 1 and 2 shipped knowledge that `open` can
rank only on facts the agent records, because the facts layer (010 §3)
derives none of these families' facts. That cost showed in both steps:

| Sessions | `fact` commands, per run | bash calls, per run |
| --- | --- | --- |
| itbench-33, step 1 | 0, 9, 0 | 6, 13, 5 |
| pvc-pending, step 1 | 1, 0, 0 | 21, 7, 12 |
| image-pull-typo, step 2 | 3, 0, 7 | 18, 3, 12 |
| pvc-pending, step 2 | 3, 3, 0 | 22, 17, 11 |

The runs that skip `fact` rank nothing. The runs that use it guess names and
syntax, read `--help`, and read the script's source (step 2's result).
`storage` would add a third family with the same gap. This step is a change
to the agent's code, in 010's layer, for 007's families. 010 is closed, and
this does not reopen it.

**A defect found while planning.** `open` treats any capitalised argument as
a variable (`kb_query.py` `_value`: `a[:1].isupper()`), so a literal is
read as one too. `waiting_reason[C, InvalidImageName]` then holds for a
container waiting in `ImagePullBackOff`. On image-pull-typo's facts, written
by hand as code would derive them, `open KubePodNotReady` ranks
`invalid-image-name` strong and leads with it. The fix belongs in this step,
because derived facts are what would trigger the defect in every session.
- **Who it reaches:** 43 conditions have a bare literal argument
  (`ErrImagePull`, `Always`, `NoSchedule`, `MemoryPressure`), all in
  `image-and-sandbox` and `pod-pending`.
- **Who it doesn't reach:** pod-restarting writes its literals as
  comparisons (`waiting_reason[C, R], R == CrashLoopBackOff`), so it isn't
  affected. Nor are real variables with other names (`R`, `Pct`, `ImgArch`).

**Hypothesis.** When code states the scheduling, pull and claim facts
(below), `open` ranks these families from the evidence record, as it ranks
pod-restarting. The agent then records fewer facts by hand and has less to
get wrong.

**Expected effect.**
- **Offline, certain.** With the scenarios' recorded tool results and no
  agent facts, `open --workload` gives:
  - **image-pull-typo:** the leader is `image-pull-failure`, refined to
    `image-tag-not-found-in-registry` or to the claim the message names.
    `registry-network-or-auth-problem` is ruled out, as is `pod-not-scheduled`.
    Today it ranks nothing: no fact is derived.
  - **pvc-pending:** `pod-not-scheduled`, then
    `unbound-persistent-volume-claim` strong (prototyped on hand-written
    facts).
  - **itbench-33:** `node-selector-or-affinity-matches-no-node` strong, and
    `unbound-persistent-volume-claim` ruled out.
- **Offline, a known limit.** What the truths rule out on resources, taints
  and admission stays open. Those refutes rest on per-node facts
  (`node_requests_exceed_allocatable[N]`, `node_taint_present[N, ...]`), and
  `open --workload` reads only the workload's own facts. The scheduler
  message proves nothing about them: pvc-pending's stops at the claim, and
  itbench-33's cites the control-plane taint, on one node of five. That
  citation makes `untolerated-node-taint` strong beside the true cause, which
  is a knowledge question and not this step's. The step records it and
  changes nothing for it.
- **Live, a lean.** Fewer `fact` commands and bash calls per run. Ruled-out
  cells move up: image-pull-typo's `rules-out-registry` (1/3), and
  itbench-33's (5/9 in step 1).

**Can we see it?**
- **The offline check:** yes, at no cost.
- **The leans:** not at n=3. Bash calls per run range 3–22 within one
  scenario, and each ruled-out group is one to three items.
- **The live run is the guard.** No REGRESSION under 004 §6.4 on any
  scenario the change reaches. itbench-33's spread is 0, so any drop in its
  mean flags, and its runs are read before the flag is believed.
- **Recorded beside it:** `fact` commands and bash calls per run, from the
  sessions, against the table above.
- **The tripwire to watch.** This is the second change aimed at
  `rules-out-registry`, after step 2. If it doesn't move, a third would be
  process.md §2's tripwire, so none follows without a checkpoint.

**The work.**
1. **The literal fix, with a test.**
   - **The rule:** an argument is a variable when it is the fact's own
     parameter name, appears in a comparison, or recurs in the alternative.
     Anything else is a literal.
   - **The build check:** `kb build` lists every literal it finds, so a
     misread can't come back unnoticed. Today the rule misreads one
     variable: `ImgArch`, used alone in
     `image-lacks-node-architecture-variant`. Review renames it to the
     parameter's name, `Arch`.
2. **Record results** (`scenario record-results`) for image-pull-typo and
   pvc-pending, as itbench-33 has. These replays need Docker and no model.
3. **Derivations** in `facts/derive.py`. Each states what one result shows,
   with kb lines in the families' vocabulary:
   - **From a FailedScheduling message** (`workload_report`, `events`,
     `pod_events`):
     - `pod_unscheduled[P]` and `failed_scheduling_event[P]`;
     - `scheduling_message_cites_affinity`, `_taint` and
       `_insufficient_resource`, true when the message cites them. They are
       false only when the message accounts for every node, as "0/5 nodes are
       available: 1 ..., 4 ..." does;
     - `not untolerated_noschedule_taint_on_all_candidate_nodes[P]` when the
       message's taint count is below its node count.
   - **From the pod spec:** `node_selector_or_affinity_set[P]`, true or
     false. The spec is read already; it gains the kb line.
   - **From claim status** (`pvc_summaries`, or the scheduler's "unbound
     immediate PersistentVolumeClaims"): `pvc_not_bound[P]` for the pod
     that mounts the claim.
   - **From a waiting container and its pull events:**
     - `waiting_reason[C, <reason>]` and `pod_scheduled_but_container_waiting[P]`;
     - `image_pull_failure_event[P]`;
     - `image_reference_in_spec[C, <image>]`;
     - the pull message's kind: `pull_message_names_manifest_not_found`,
       `_image_not_found`, `_unauthorized`, `_forbidden` or
       `_connection_failure`. When the message names one, the others are
       stated false, since the registry answered with that one.
   - **Out of scope:** per-node facts, and anything about the registry's
     contents. Code does not infer `image_tag_not_found_in_registry` from
     "manifest unknown": what the message says is the fact.
4. **The reach check.** Replay every scenario's recorded or captured
   results through the derivations, before and after, and diff the facts.
   Any scenario whose facts or `open` ranking change is in the live run. The
   expected reach is the three above. If others come in, they run too and
   the budget is restated before the run.
5. **The offline check,** `tests/test_facts_pending_and_image.py`: the
   expected effect above, on the recorded results, with no agent facts.
6. **SKILL.md:** one line saying these families' facts are recorded by
   code, as pod-restarting's are, so `open --workload` comes first. No other
   instruction changes.
7. **The live run:** the reached scenarios, n=3, `--local-skill k8s-rca`,
   from a worktree, with `fact` commands and bash calls counted.

The instrument is not changed: no checklist, truth or grader.

**Budget: $5, one day.**
- No mining.
- The live run: about $2.70, nine sessions at $0.17–0.29, plus about $0.60
  for the gradings.

**Done:**
- the literal fix, with its test and build check;
- the derivations, with fixtures;
- the offline check passing on all three;
- n=3 on the reached scenarios with no REGRESSION;
- a result table here, with `fact` commands and bash calls against the
  table above.

**Stop:**
- **A REGRESSION** on any reached scenario.
- **The reach check finds more than five scenarios.** That is more than a
  knowledge change and needs its own plan.
- **A derivation that has to guess.** A fact the result can't settle is left
  out (010 §3.3). If one of the three scenarios needs a guessed fact to
  rank, that is a finding, not something to work around.
- **Any of process.md §2's tripwires.**

#### Step 3 result (2026-10-24): done

**The build** is 03d6881.
- **The literal fix.** `kb build` marks capitalised values in each condition
  (`literals`), and `open` binds only the rest as variables. The build lists
  11 such values. `ImgArch` is renamed `Arch` in
  `image-lacks-node-architecture-variant`.
- **Derivations** (`facts/derive.py`):
  - **from FailedScheduling messages**, in `events`, `get_pod_events` and the
    workload report:
    - `pod_unscheduled`, `failed_scheduling_event`;
    - the cites-affinity, cites-taint and cites-insufficient facts;
    - `untolerated_noschedule_taint_on_all_candidate_nodes`;
  - **from the pod spec of a pod with no node:** its selector or affinity, and
    `not pvc_not_bound` when it mounts no claim;
  - **from `get_pvc_summaries`:** `pvc_not_bound`;
  - **from a container that has never run:** its waiting reason, the image,
    and what kind of failure the pull message names. A crash-looping
    container has run, so it stays pod-restarting's.
- **Fixtures** for image-pull-typo and pvc-pending. The recorder now reads
  each namespace's claims and the events of each pod a Warning names. It no
  longer stops on a pod an event names that is gone by capture time.
- **Departure from the plan: no SKILL.md line.** Every session reads
  SKILL.md, so the line would have reached all ten scenarios and tripped the
  stop at more than five. The loop already says to run `open` first.

**The reach check** replayed all ten scenarios and diffed the derived kb
lines. The result was the plan's three and no others:
- image-pull-typo gains 11;
- itbench-33 gains 18;
- pvc-pending gains 4;
- the other seven gain or lose none.

**The offline check** (`tests/test_facts_pending_and_image.py`) passes, as
the plan expected:
- **image-pull-typo:** `open` leads with `image-pull-failure`, then
  `image-not-found-in-registry`, both strong. The registry is ruled out.
- **pvc-pending:** `unbound-persistent-volume-claim` leads, strong.
  `node-selector-or-affinity-matches-no-node` is ruled out.
- **itbench-33:** the selector cause is strong and the unbound claim is ruled
  out. `untolerated-node-taint` is strong too, the known limit.

**The live run,** n=3 from `../k8srca-facts`, against 004's baseline:

| Scenario | | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| image-pull-typo | baseline | 3/3 | 8.3/9 | 9/9 | 1/3 | 9/9 | 3/3 | 3/3 | 0.12 |
| | step 3 | 3/3 | 9.0/9 | 9/9 | 3/3 | 9/9 | 3/3 | 3/3 | 0.09 |
| itbench-33 | baseline | 3/3 | 10.0/12 | 9/9 | 6/9 | 9/9 | 4/6 | 2/3 | 0.21 |
| | step 3 | 3/3 | 11.3/12 | 9/9 | 8/9 | 9/9 | 5/6 | 3/3 | 0.28 |
| pvc-pending | baseline | 2/3 | 9.3/10 | 8/9 | 2/3 | 9/9 | 6/6 | 3/3 | 0.17 |
| | step 3 | 3/3 | 9.0/10 | 9/9 | 0/3 | 9/9 | 6/6 | 3/3 | 0.19 |

- **No REGRESSION.** All three read `noise` under 004 §6.4.
- **`rules-out-registry` met in all three runs,** against one in three in the
  baseline and in step 2. At n=3 that is a lean, not a finding. It is the
  second change aimed at that item (plan), and the first that moved it.
- **itbench-33's checklist mean went from 10.0 to 11.3,** the highest it has
  been. Its ruled-out cell went from 6/9 to 8/9 (5/9 in step 1).
- **pvc-pending's `rules-out-node-resources` was missed in all three runs,**
  against one miss in three in the baseline. This is the known limit showing
  live. `open` prints `insufficient-cluster-resources` as undecided ("nothing
  available here decides it"), and the answers followed it. In the baseline,
  agents had ruled it out by reading node capacity themselves. Diagnosis rose
  to 3/3, so the mean stayed within the spread. The fix is per-node facts that
  `open --workload` can read: a question for the knowledge and the facts
  layer together, not a rule from these runs.
- **The agent records less.** `fact` commands and bash calls per run, against
  the table in the plan:

  | Sessions | `fact` commands, per run | bash calls, per run |
  | --- | --- | --- |
  | image-pull-typo, step 2 | 3, 0, 7 | 18, 3, 12 |
  | image-pull-typo, step 3 | 0, 0, 0 | 3, 2, 2 |
  | itbench-33, step 1 | 0, 9, 0 | 6, 13, 5 |
  | itbench-33, step 3 | 0, 0, 3 | 5, 2, 13 |
  | pvc-pending, step 2 | 3, 3, 0 | 22, 17, 11 |
  | pvc-pending, step 3 | 2, 0, 3 | 8, 3, 17 |

  image-pull-typo's sessions ran `lookup` and `open` and recorded nothing.
- **Recording that remains:**
  - **pvc-pending run 1** recorded `pvc_not_bound` by hand, which code had
    already stated.
  - **pvc-pending run 3** tried to record why the claim is unbound (its
    StorageClass), for which the vocabulary has no fact. It read `fact
    --help`, then `kb_query.py`'s source. That is `storage`'s question.
  - **itbench-33 run 3** passed a claim's name to `fact`.
- **Cost per run fell on image-pull-typo**, from $0.17 in step 2 to $0.09.
- **The FAILs are pass/fail advisories,** as in the baseline:
  - itbench-33's "2 restarts" `numeric_claims` (a possible check defect, not
    investigated);
  - one `must_identify` miss;
  - a `closed_world` catch of `20m-4m57s`, a range an answer formed from two
    event ages.
- **Spent:** about $2.30 of $5. Sessions $1.68, gradings about $0.60.

**Against the done criterion:** met, with the SKILL.md departure above. The
literal fix has its test and build listing. The derivations have fixtures.
The offline check passes on all three. n=3 shows no REGRESSION, and this
table is written.

#### Step 4 plan: `storage` (2026-10-24)

**Why now.** pod-pending's `unbound-persistent-volume-claim` ends with "why
the claim does not bind belongs to the storage family", and there is no such
family yet. In step 3, pvc-pending run 3 tried to record why quote-cache is
unbound (its StorageClass), found no fact for it, and read `kb_query.py`'s
source. The family is the knowledge for that question, and step 3's lesson
is that knowledge should ship with the facts code states for it.

**What the suite can show, said first.** pvc-pending is the only scenario
with a storage cause, and its checklist already meets the cause in 9/9 runs.
No checklist item is expected to move. Its one miss,
`rules-out-node-resources` (0/3 in step 3), belongs to per-node facts (step
5), not to this family. So the live run is a guard, and the step's value is
the family itself: the seven vendored storage alerts converted, and `open`
ranking why a claim does not bind. Most of that value lies outside the suite,
and the step doesn't claim the suite measures it.

**Hypothesis.** With the family mined and the claim's status and events
stated by code, `open` ranks why a claim is unbound from the evidence record.
Agents stop trying to record the StorageClass by hand.

**Expected effect.**
- **Offline, certain.** On pvc-pending's recorded results, with no agent
  facts, `open KubePodNotReady --workload quote` leads with
  `pod-not-scheduled`, refined to `unbound-persistent-volume-claim`, refined
  to the storage claim for a class that does not exist, all three strong.
  These are ruled out:
  - a claim that names no class when the cluster has no default (the claim
    names `fast-ssd`);
  - a provisioner that fails or never answers (the message is that the class
    was not found, so no provisioner ran);
  - no matching static volume.
- **Offline, the new top-level candidate.** `pod-not-ready` gains an eighth
  candidate, a volume that cannot be attached or mounted (FailedMount,
  FailedAttachVolume, Multi-Attach). It is ruled out on both
  `KubePodNotReady` scenarios, by positive reads:
  - pvc-pending: the pod is not scheduled, and volumes attach only to a pod
    on a node;
  - image-pull-typo: the container waits in `ImagePullBackOff`, and the
    kubelet pulls images only after the pod's volumes are mounted.
  Each refute ships only if review finds a source that says it. If none
  does, the candidate stays undecided, which is a finding, not a guess.
- **Live, a lean.** No `fact` command naming the StorageClass or the
  claim's state, against one in three step 3 runs.

**Can we see it?**
- **The offline check:** yes, at no cost.
- **The lean:** not at n=3. It is one run in three.
- **The live run is the guard.** No REGRESSION under 004 §6.4 on the
  scenarios the change reaches:
  - pvc-pending, through the derivations and the refinement;
  - image-pull-typo, through `pod-not-ready`'s new candidate.
  Two items matter most:
  - pvc-pending's `no-invented-class-list`. New knowledge about default
    classes could tempt an answer to list classes that no tool shows.
  - image-pull-typo's `no-unrelated-causes`, which names quote. Its capture
    still holds quote-cache's ProvisioningFailed event, from the scenario
    before it.
- **Recorded beside it:** `fact` commands and bash calls per run, against
  step 3's table.
- **The tripwire to watch.** None fires. No item is aimed at a third time:
  this step aims at no checklist item, and per-node facts are step 5's.

**The work.**
1. **The family in `sources.yaml`.**
   - **Scope:** why a PersistentVolumeClaim does not bind, and why a bound
     volume cannot be attached or mounted:
     - a StorageClass that does not exist;
     - no class named and no default class;
     - a provisioner or CSI driver that fails or is missing;
     - no static volume that matches (capacity, access mode, selector);
     - a topology or zone mismatch;
     - `WaitForFirstConsumer` read as a failure;
     - attach and mount failures, including Multi-Attach;
     - a volume that is full.
   - **Out of scope:**
     - the scheduling causes of a Pending pod, which are pod-pending's (it
       refines into this family through the claim);
     - storage quota (pod-pending's `storage-quota-exhausted`, linked, not
       duplicated);
     - latency from slow storage. That is request-latency's, and
       `StorageLatencyHigh` is converted here only as an entry to it.
   - **Vendored alerts,** from §8.5's table: PVCPending, FailedAttachVolume,
     FailedMount, MultiAttachError, CSIControllerDown, VolumeFull and
     StorageLatencyHigh.
   - **Sources.** Each fetch URL returned 200 on 2026-10-24:
     - Kubernetes documentation (CC-BY-4.0): Persistent Volumes, Storage
       Classes, Dynamic Volume Provisioning, and Change the default
       StorageClass;
     - kube-prometheus runbooks (Apache-2.0): KubePersistentVolumeErrors and
       KubePersistentVolumeFillingUp;
     - GKE's Troubleshoot storage (CC-BY-4.0);
     - AKS's Fail to mount an Azure disk volume (CC-BY-4.0). It is
       provider-specific, so review keeps only what generalises, as with
       step 2's ACR article.
2. **`k8srca kb mine storage`,** at `--max-usd 10`.
3. **Review** under §8.3, as in steps 1 and 2:
   - a small top level;
   - refutes that rest on positive reads;
   - `unbound-persistent-volume-claim` `refined_by` this family's binding
     candidates;
   - `pod-not-ready`'s new candidate, refined into the attach and mount
     claims.
   The vocabulary is fixed in review, before the derivations are written.
   Claim facts are stated over the pod that mounts the claim (`[P]`), as
   `pvc_not_bound[P]` is, so `open --workload` reads them.
4. **One k8stools addition.** `get_pvc_summaries` gains, for each claim
   that is not Bound, its recent events: reason, message, count and type.
   - **Why the tool changes:** the claim's events today come from
     `get_events`, on the claim's own name, and the pods that mount it come
     from `get_pvc_summaries`. Code that derives one result at a time can't
     join them, and a join across results is new machinery in the facts
     layer. Putting the events in the claim's summary follows
     `get_workload_report`, which already carries its pods' events, and
     CLAUDE.md's rule that a missing capability belongs in k8stools.
   - **The mock** answers from the capture's events, so every scenario
     replays the new field with no re-capture.
   - **The release:** a k8stools 4.2.0 release, with your OK, since
     publishing goes outward. k8srca then requires `>=4.2.0`. Production's
     image isn't touched.
5. **Derivations** in `facts/derive.py`, from `get_pvc_summaries`:
   - the class the claim names, or that it names none;
   - what kind of answer its events give:
     - the class was not found;
     - a provisioner error;
     - waiting on an external provisioner;
     - waiting for the first consumer;
     - no volume available and no class set.
     As with pull messages, when the newest message names one kind, the
     other failure kinds are stated false.
   - **From a pod's `FailedMount`, `FailedAttachVolume` and Multi-Attach
     events** (`events`, `get_pod_events`): that the volume failed to attach
     or mount.
   - **Out of scope:**
     - which StorageClasses exist. No tool lists them, and pvc-pending's
       truth records that as a gap (`storage-classes-not-listed`). Adding a
       tool would leave a frozen truth stale, which is process.md §2's
       tripwire. A class's existence is stated only as the message states
       it.
     - provisioner and CSI driver health;
     - the volume list.
   - A claim event read through `get_events` stays without kb lines, so
     image-pull-typo's leftover quote-cache event states nothing about
     email.
6. **`scenario record-results` for pvc-pending again,** with the new field.
   image-pull-typo's quote-cache claim is gone by its capture, so its
   fixtures don't change.
7. **The reach check,** as in step 3: replay all ten scenarios' results
   through the old and new derivations and knowledge, and diff the kb lines
   and `open`'s rankings. The expected reach is the two above. If more come
   in, they run too, and the budget is restated before the run.
8. **The offline check,** `tests/test_kb_storage.py`: the expected effect
   above, on both scenarios' recorded results, with no agent facts.
9. **The live run:** the reached scenarios, n=3, `--local-skill k8s-rca`,
   from a worktree, with `fact` commands and bash calls counted.

The instrument is not changed: no checklist, truth or grader. SKILL.md is not
changed: any line there would reach all ten scenarios (step 3).

**Budget: $15, one day.**
- Mining: about $7 for eight sources, against step 1's $8.66 for eight,
  with a cap of $10.
- The live run: about $1.70. That is six sessions at $0.09–0.20, plus
  about $0.40 for the gradings.
- A re-merge after review: up to $3.

**Done:**
- the family shipped, with `unbound-persistent-volume-claim` refined into
  it, and `kb build` passing;
- k8stools 4.2.0, and the derivations, with fixtures;
- the offline check passing on both scenarios;
- n=3 on the reached scenarios with no REGRESSION;
- a result table here, with `fact` commands and bash calls against step 3's.

**Stop:**
- **A REGRESSION** on any reached scenario.
- **Mining over $10,** or a draft that can't be shipped without rewriting
  most of it.
- **The reach check finds more than five scenarios.**
- **The k8stools release is declined.** The step then needs a new plan for
  the join, not a workaround.
- **A derivation that has to guess.**
- **Any of process.md §2's tripwires.**

#### Step 4 result (2026-10-24)

**k8stools 4.2.0** gives an unbound claim's summary its events (k8stools
#28), and adds the claims to the composites. `get_namespace_health` and
`get_workload_report` now carry each unbound claim an unscheduled pod
mounts, with its newest event.

**Mining** cost $4.24 on the rerun. The interrupted first run's extraction
spend wasn't recorded; it is estimated at $4–5. **Review** was by hand, with
no re-merge; the file's header lists every change:
- **A small top level.** pvc-pending went from 21 candidates to 6, one per
  kind of claim event. volume-mount-failing went from 17 to 7.
- **Facts are keyed on the pod (`[P]`).**
- **Refutes that rested on silence were replaced.**
- **Six claims were dropped,** and nine sub-causes marked uncommon.
- **`pod-not-ready` gains `volume-not-attached-or-mounted`.** Both of its
  refutes have a source (AKS).
- **`kb build` converts 25 of the 81 vendored alerts** (18 before).

**Departures from the plan:**
- **The derivations also read the composites' claim lines.** The plan named
  `get_pvc_summaries` only. It was a good call: no live pvc-pending session
  called `get_pvc_summaries`. Each made one k8stools call,
  `get_namespace_health`, and the claim line there carried the cause.
- **The other event kinds are stated false only when it is safe.** That is
  when the newest event's kind settles them (the class was not found, or no
  class is set), or when every recent event names the same kind. The plan's
  "when the newest message names one kind" would rule out a failing CSI
  provisioner, whose ProvisioningFailed events alternate with the
  controller's ExternalProvisioning events.
- **A claim's missing class is not stated.** k8stools shows an unset class
  and an empty one the same way. Whether a class name is a valid DNS
  subdomain is stated: it is a check on the name alone.

**The reach check** covered all ten scenarios. Four of them had no recorded
results, so their results were recorded into a scratch copy, not the repo.
- **kb lines:** pvc-pending gains 7. The other nine gain or lose none.
- **`open`'s rankings** change on pvc-pending and image-pull-typo, and on
  itbench-33. The plan didn't expect itbench-33: PendingPodsDetected opens
  `pod-not-ready` too, so it gains the new candidate as one more ruled-out
  line and nothing else changes. Under the plan it joined the live run, at a
  restated cost of about $2.50.

**The offline check** (`tests/test_kb_storage.py`) passes, as expected:
- **pvc-pending:** `open` leads with `pod-not-scheduled` →
  `unbound-persistent-volume-claim` → `requested-storage-class-does-not-exist`,
  all strong.
  - The other five binding claims are ruled out, along with the
    invalid-class-name claim and the volume candidate.
  - Before this step the chain ended on `volume-node-affinity-conflict`, weak.
- **image-pull-typo:** the volume candidate is ruled out.

**The live run,** n=3 from `../k8srca-storage`, against 004's baseline, with
step 3's row for comparison:

| Scenario | | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| image-pull-typo | baseline | 3/3 | 8.3/9 | 9/9 | 1/3 | 9/9 | 3/3 | 3/3 | 0.12 |
| | step 3 | 3/3 | 9.0/9 | 9/9 | 3/3 | 9/9 | 3/3 | 3/3 | 0.09 |
| | step 4 | 3/3 | 8.7/9 | 9/9 | 3/3 | 8/9 | 3/3 | 3/3 | 0.08 |
| itbench-33 | baseline | 3/3 | 10.0/12 | 9/9 | 6/9 | 9/9 | 4/6 | 2/3 | 0.21 |
| | step 3 | 3/3 | 11.3/12 | 9/9 | 8/9 | 9/9 | 5/6 | 3/3 | 0.28 |
| | step 4 | 3/3 | 11.3/12 | 9/9 | 9/9 | 9/9 | 4/6 | 3/3 | 0.28 |
| pvc-pending | baseline | 2/3 | 9.3/10 | 8/9 | 2/3 | 9/9 | 6/6 | 3/3 | 0.17 |
| | step 3 | 3/3 | 9.0/10 | 9/9 | 0/3 | 9/9 | 6/6 | 3/3 | 0.19 |
| | step 4 | 3/3 | 9.0/10 | 9/9 | 0/3 | 9/9 | 6/6 | 3/3 | 0.08 |

- **No REGRESSION.** All three scenarios read `noise` under 004 §6.4.
- **The two items to watch were met in every run:** pvc-pending's
  `no-invented-class-list` and image-pull-typo's `no-unrelated-causes`.
- **image-pull-typo run 3 missed the `no-service-down` trap.** Its answer
  said the old pod was "masking the outage". That is wording this step
  didn't touch, and step 3 met the trap in 3 of 3.
- **pvc-pending missed `rules-out-node-resources` in all three runs,** as in
  step 3. That item belongs to step 5.
- **The lean was met.** No pvc-pending session recorded a fact by hand.
  Each answered from `get_namespace_health` and `open`, in 5–6 tool calls
  against 8–17 bash calls in step 3, at $0.08 a run against $0.19.
- **The FAILs are pass/fail advisories,** the same ones as in step 3:
  itbench-33's "2 restarts" `numeric_claims` on ad and cart.
- **Facts recorded by the agent, and bash calls,** per run:

  | Sessions | facts recorded by the agent | bash calls |
  | --- | --- | --- |
  | image-pull-typo, step 3 | 0, 0, 0 (`fact` commands) | 3, 2, 2 |
  | image-pull-typo, step 4 | 0, 0, 0 | 4, 3, 4 |
  | itbench-33, step 3 | 0, 0, 3 (`fact` commands) | 5, 2, 13 |
  | itbench-33, step 4 | 6, 0, 0 | 11, 8, 5 |
  | pvc-pending, step 3 | 2, 0, 3 (`fact` commands) | 8, 3, 17 |
  | pvc-pending, step 4 | 0, 0, 0 | 3, 4, 4 |

  Step 4 counts the facts in the session's facts log written `by: agent`.
  Step 3 counted the `fact` commands; one command can write several facts.
  The 6 in itbench-33's run 1 are the node-selector facts, which are step 5's
  ground.
- **Spent on step 4:** about $11 of $15:
  - mining: $4.24, plus an estimated $4–5 for the interrupted run;
  - sessions: $1.33;
  - gradings: about $0.50.

**Against the done criterion:** met. The family ships, refined into from
`unbound-persistent-volume-claim`, and `kb build` passes. k8stools 4.2.0 is
required. The derivations have fixtures. The offline check passes on both
scenarios. n=3 shows no REGRESSION, and this table is written. No tripwire
fired.

#### Step 5 plan: what the scheduler's message settles, and one join over nodes (2026-10-24, approved)

**Why now.** This is step 3's known limit, set as step 5 on 2026-10-24.
- **pvc-pending's `rules-out-node-resources`** was missed in 6 of 6 runs in
  steps 3 and 4, against 1 miss in 3 in the baseline. `open` prints
  `insufficient-cluster-resources` as undecided, "nothing available here
  decides it", and the answers follow it.
- **itbench-33's `untolerated-node-taint` reads strong.** The message cites
  the control-plane taint on one node of five, and the knowledge reads any
  cited taint as strong. The truth rules it out.

**What the outline assumed, and what was found.** The outline was
"per-node facts": allocatable against requests, taints and pressure, read
by `open --workload`. Two findings change the plan.
1. **Neither item is settled by node data.** Both truths rule on the
   scheduler's message:
   - pvc-pending: "the scheduler gives the unbound claim, not resources, as
     the reason";
   - itbench-33: "the scheduler's message names the reason for every node".

   What the knowledge lacks is how to read that message. The Kubernetes
   documentation (*Scheduling Framework*, CC-BY-4.0) states it:
   - **Filter:** "If any filter plugin marks the node as infeasible, the
     remaining plugins will not be called for that node." So each node's
     reason in the message is the first filter that rejected it. A cited
     taint shows the taint rejected that node first. It does not show that
     the node would take the pod without the taint.
   - **PreFilter:** "If a PreFilter plugin returns an error, the scheduling
     cycle is aborted." A message whose reason carries no node count ("0/1
     nodes are available: pod has unbound immediate PersistentVolumeClaims")
     rejects the pod before any node is weighed. Nothing about a node is why
     it is Pending.
2. **The resources on each node can't be replayed.** Whether a pod fits a
   node needs the requests already committed there, by every pod in every
   namespace. The captures hold only the application namespace's pods:
   `default`'s 28 for pvc-pending, `otel-demo`'s 23 for itbench-33. A
   replay would undercount what kube-system commits and state "fits" from a
   guess, which is the stop below. Re-capturing would leave frozen truths
   stale (process.md §5).

   So committed requests per node are out of this step. They are a k8stools
   feature for live clusters, which the suite can't measure, decided on
   judgment if they are wanted.

**Hypothesis.** With the scheduler's message read as the documentation
says, and the selector checked against every node's labels, `open` rules on
resources and taints as the truths do, from what the record holds.

**The work.**
1. **The source.** *Scheduling Framework* goes in `sources.yaml`
   (CC-BY-4.0, `use: vendor`, fetch URL returning 200 on 2026-10-24). The
   knowledge edits cite it with the two quotes above.
2. **One fact, in code:** `scheduling_rejected_before_nodes_weighed[P]`.
   - Stated true from a FailedScheduling message whose reason after "0/N
     nodes are available:" carries no node count.
   - Stated false from one whose per-node reasons account for every node.
   - Otherwise left out.
3. **Knowledge, by hand, in `pod-pending.yaml`,** with the header noting
   each edit:
   - **`insufficient-cluster-resources`** is ruled out by
     `scheduling_rejected_before_nodes_weighed[P]`. A second refute drops
     its node condition: `[failed_scheduling_event[P], not
     scheduling_message_cites_insufficient_resource[P, Resource]]`, which
     the derivation states only when the message accounts for every node.
     The step 1 refute required `not node_requests_exceed_allocatable[N]`
     too, which nothing can state for an unscheduled pod.
   - **`untolerated-node-taint`:**
     - the message-only `for` falls from strong to weak, citing Filter;
     - it gains a refute, `no_node_matches_node_selector_or_affinity[P]`:
       when the selector admits no node, the tainted ones included, no taint
       is why the pod waits;
     - and `scheduling_rejected_before_nodes_weighed[P]`.
   - **`node-selector-or-affinity-matches-no-node`** is ruled out by
     `scheduling_rejected_before_nodes_weighed[P]`.
4. **One k8stools addition, the join over nodes.** For a pod with no node,
   the composites' `unscheduled` line, or a field beside it, gains how many
   nodes its nodeSelector and required node affinity admit:
   `"0 of 5 nodes match its nodeSelector and required node affinity"`.
   - **Why in k8stools:** as in step 4, the pod's selector and the nodes'
     labels come from two results, and the facts layer derives one result
     at a time. The live sessions call the composites, not
     `get_node_summaries`: pvc-pending's made one call in step 4.
   - **The mock** answers from the captured node labels and pod specs, which
     both scenarios hold, so there is no re-capture.
   - **The release:** a GitHub issue for the k8stools agent, then 4.3.0 with
     your OK. k8srca then requires `>=4.3.0`.
   - **The derivation:** `no_node_matches_node_selector_or_affinity[P]`,
     true at 0 and false above it. Nothing is stated when the pod sets no
     selector and no affinity: pod-pending's
     `not node_selector_or_affinity_set[P]` already covers that.
5. **`scenario record-results`** for itbench-33 and pvc-pending, with the
   new field.
6. **The reach check,** as in steps 3 and 4: all ten scenarios, the kb lines
   and `open`'s rankings, old against new. The edits are to pod-pending
   claims, which `pod-not-ready` reaches through `pod-not-scheduled`. So the
   expected reach is pvc-pending, itbench-33 and perhaps image-pull-typo,
   where `pod-not-scheduled` is already ruled out. If more come in, they
   run, and the budget is restated first.
7. **The offline check,** in `tests/test_facts_pending_and_image.py`, which
   asserts today's limit (`untolerated-node-taint` strong) and changes with
   it:
   - pvc-pending: `insufficient-cluster-resources`, `untolerated-node-taint`
     and `node-selector-or-affinity-matches-no-node` are ruled out, and the
     leading chain is unchanged;
   - itbench-33: `untolerated-node-taint` and
     `insufficient-cluster-resources` are ruled out, and the selector cause
     stays strong.
8. **The live run:** the reached scenarios, n=3, `--local-skill k8s-rca`,
   from a worktree, with facts recorded by the agent and bash calls counted.

The instrument is not changed: no checklist, truth or grader. SKILL.md is
not changed.

**Expected effect.**
- **Offline, certain:** the two checks in item 7.
- **Live, a lean:** pvc-pending's `rules-out-node-resources` goes from 0/6
  (steps 3 and 4) to 2/3 or better. Answers have followed `open`'s
  standings in every step so far.
- **Live, none:** itbench-33's `no-taint-as-cause` is already met in 9 of 9
  runs. The taint edit corrects what `open` says, which the suite does not
  score.

**Can we see it?**
- **The offline check:** yes, at no cost.
- **The lean:** a move from 0/6 to 2/3 or 3/3 is visible at n=3 as a lean,
  not a finding.
- **The guard:** no REGRESSION under 004 §6.4 on the reached scenarios.
  The item to watch is itbench-33's `rules-out-node-resources`, met in 9 of
  9 runs in step 4. Its refute changes shape, so a drop there is the
  regression to look for.

**Tripwires, said first.**
- **A rule from a run (process.md §2.3).** The knowledge edits are aimed at
  a checklist item that failed in runs. Against that:
  - the gap was found offline, in step 3's plan, before those runs;
  - each edit cites the scheduler's documented behaviour, not a run;
  - each applies to every FailedScheduling message, not to these two
    scenarios.

  Still, you decide whether that clears it. If it does not, the knowledge
  edits wait for a mining pass over the scheduling sources, and this step
  ships only the fact and the join.
- **The third change for one thing:** no. This is the first change aimed at
  `rules-out-node-resources`. Step 3 moved it as a side effect.

**Budget: $5, one day,** plus the wait for the k8stools release. No mining.
The live run is about $2.50 for three scenarios at n=3, as in step 4.

**Done:**
- the fact, the join and the edits, with fixtures, and `kb build` passing;
- k8stools 4.3.0 required;
- the offline check passing on both scenarios;
- n=3 on the reached scenarios with no REGRESSION;
- a result table here.

**Stop:**
- **A REGRESSION** on any reached scenario.
- **The reach check finds more than five scenarios.**
- **The k8stools addition is declined.** Then the step ships the fact and
  the edits it can, and the taint refute waits.
- **A derivation that has to guess.**
- **Any of process.md §2's tripwires.**

#### Step 5 result (2026-10-25): done

**Built as planned.**
- k8stools 4.3.0 (#29) adds `placement` to both composites. It also counts
  the admitted nodes whose taint the pod doesn't tolerate.
- The fact, the placement derivation and the edits in `pod-pending.yaml`
  are in, citing *Scheduling Framework*.

**The reach check:**
- **kb lines** change on pvc-pending, itbench-33 and image-pull-typo. The
  image-pull-typo line is about quote, which email's ranking doesn't read.
- **Rankings** change on pvc-pending and itbench-33 only. So those two
  scenarios ran.

**The offline check passes:**
- **pvc-pending:** resources, taint and selector are ruled out, and the
  leading chain is unchanged.
- **itbench-33:** the chain now goes on to
  `node-selector-or-affinity-matches-no-node`, strong, and resources and
  taint are ruled out. Before, the taint tied the selector at strong.

**The live run,** n=3 from `../k8srca-step5`, with step 4's rows for
comparison:

| Scenario | | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| itbench-33 | step 4 | 3/3 | 11.3/12 | 9/9 | 9/9 | 9/9 | 4/6 | 3/3 | 0.28 |
| | step 5 | 3/3 | 11.3/12 | 9/9 | 8/9 | 9/9 | 5/6 | 3/3 | 0.15 |
| pvc-pending | step 4 | 3/3 | 9.0/10 | 9/9 | 0/3 | 9/9 | 6/6 | 3/3 | 0.08 |
| | step 5 | 3/3 | 9.3/10 | 9/9 | 1/3 | 9/9 | 6/6 | 3/3 | 0.10 |

- **No REGRESSION.** Both read `noise` under 004 §6.4.
- **The lean was not met.** pvc-pending's `rules-out-node-resources` was
  met 1 time in 3, against the 2 in 3 expected. itbench-33 missed it once,
  against 3 of 3 met in step 4.
- **Why: the offline check didn't read what the live sessions read.**
  `open` printed `insufficient-cluster-resources` as weak in all six
  sessions, against ruled out offline.
  - **pvc-pending's sessions read only `get_namespace_health`.** Its
    `unscheduled` line carries the scheduler's message ("Unschedulable: 0/1
    nodes are available: pod has unbound immediate
    PersistentVolumeClaims"), but `_rollout` states only
    `pod_unscheduled[P]` from it. The new fact came only from `get_events`
    and `get_workload_report`. `record-results` calls those tools, so the
    fixtures and the offline check have them. These live sessions never
    called them.
  - **itbench-33's sessions ran `open` before reading the workload
    reports:** at 01:51:04 in run 1, against 01:51:05 for the reports. They
    never ran it again. Its capture predates 4.1.0, so its `unscheduled`
    line holds no message to read from.
  - **The taint was ruled out live,** in all three itbench-33 sessions,
    from `get_namespace_health`'s `placement` line. Live, that line is what
    made the difference.
- **`must_identify` missed `invalid-node` in 2 itbench-33 runs.** It's a
  pass/fail advisory and not a checklist item. Those sessions didn't read
  the pod spec, and `placement` gives the count, not the selector.
- **The agent recorded no fact by hand in any run.** Bash calls per run:
  itbench-33 5, 6, 5 (step 4: 11, 8, 5); pvc-pending 3, 6, 5 (step 4: 3, 4,
  4). Tool calls on itbench-33 fell from 18–26 to 9–11.
- **Spent:** about $1.10 of $5: sessions $0.76, gradings about $0.35,
  replays free.

**Against the done criterion:** met. The fact, the join and the edits have
fixtures, and `kb build` passes. 4.3.0 is required. The offline check
passes on both scenarios. n=3 shows no REGRESSION, and this table is
written. The lean, which is not a done criterion, was not met, for the
reason above. No tripwire fired.

**The owner's decision: complete the implementation and run pvc-pending
again.**
- **The fix,** 03f4517: `_rollout` reads the scheduler's message on each
  `unscheduled` line with `scheduling_kb`, as it reads a FailedScheduling
  event's. A line from before 4.1.0 ("reason not recorded") states
  nothing.
- **The offline check gains a case on `get_namespace_health`'s result
  alone,** as the live sessions read it. Resources and taint are ruled out
  there. On the old code the case fails: resources read weak.
- **The second arm,** pvc-pending at n=3 from `../k8srca-step5` at 03f4517:

| Scenario | | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pvc-pending | step 5 | 3/3 | 9.3/10 | 9/9 | 1/3 | 9/9 | 6/6 | 3/3 | 0.10 |
| | second arm | 3/3 | 10.0/10 | 9/9 | 3/3 | 9/9 | 6/6 | 3/3 | 0.09 |

- **The lean is met:** `rules-out-node-resources` 3 of 3, against 2 of 3
  expected. It reads `noise` under 004 §6.4, as n=3 must for one item.
- **One `numeric_claims` advisory, a false positive.** Run 3's "62 times"
  is the claim's ProvisioningFailed event count, cited from `get_events`,
  not a restart count.
- **Spent:** about $1.50 of $5 in all; this arm's sessions $0.27.

**Step 5 is done.** This was the second change aimed at
`rules-out-node-resources`, the last process.md allows without a
checkpoint, and none is needed. itbench-33's capture predates 4.1.0, so
the fix doesn't reach it, and it was not run again.

#### Step 6 plan: when a change came, and when the failures began (2026-10-25, approved with (a))

**Why now.** §11.4 has `rollouts-and-jobs` next, "reach checked first". The
check, offline on the 4.3.0 recordings, says the family doesn't reach:
- **deploy-regression asks why payment is crash-looping.** It opens
  `pod-restarting`, as liveness-probe-wrong-port does. No rollout alert is
  raised. `DeploymentRolloutStuck` and the family's other alerts are not in
  either question.
- **The rollout claims these two scenarios need are already
  pod-restarting's.** `bad-config` and `application-release` turn on
  `onset_observed[W]` and `changed_before_onset[W, Kind]`, and code states
  neither. So `open CrashLoopBackOff` gives "no leading cause" on both, with
  `application-release` "could-not-check".
- **What the record holds:** `get_workload_report`'s `last_change`.
  - deploy-regression: revision 3, 22m35s ago, `env[PAYMENT_PORT]: changed`.
    The one failing pod is 22m36s old, with 9 restarts.
  - liveness-probe-wrong-port: revision 3, 10m53s ago, `livenessProbe: - ->
    {...}`. The one failing pod is 10m54s old, with 7 restarts.

  Code turns this into a statement with no kb line.
- **A wrong fact, in both.** `restart_report` states `not
  onset_observed[W]` from any row already restarting at its first sample.
  - In deploy-regression the rows are the old pod and the new one:
    - The old pod, gone since the rollout, had 16 restarts long before the
      metrics begin, and none in 85 hours.
    - The new pod was born inside the window. Its count was 1 at its first
      sample, 39 seconds into its life.
  - Neither row says when payment's failures began. In liveness the old pod
    does the same.
  - deploy-regression's one baseline miss is the trap this fact feeds,
    `no-old-onset` ("began before the recent revision"), met 2 of 3.

So step 6 is pod-restarting's change and onset facts in code. On judgment,
`rollouts-and-jobs` isn't mined: no scenario reaches it.

**Hypothesis.** With the change and its timing stated from `last_change`,
and no false "onset before the record", `open` places a recent revision as
the truths do. Answers on deploy-regression then stop dating the failure
from before it.

**The work.**
1. **Change and onset, from `get_workload_report`.** For a workload whose
   `last_change` is known (not `reused`, not `rollout_restart`, with
   `changes` listed), when every instance with restarts belongs to a pod
   created with the change (pod age within 60s of the change's age), and the
   report's events reach back to that pod's creation:
   - `onset_observed[W]`: the failures began in a pod whose whole life the
     record holds;
   - `changed_before_onset[W, Kind]`, for each kind the diff names:
     - `image` for `.image`;
     - `resources` for `.resources`;
     - `spec` for env, command, args, probes, volumes and the rest of the
       pod template;
   - `not changed_before_onset[W, Kind]` for the kinds the diff doesn't
     name, among those three.

   `config` (a ConfigMap or Secret edit) isn't in the pod template, so
   nothing is stated about it. Otherwise nothing is stated at all, which
   covers a change hours older than the pods, an older pod still failing,
   or a rollback.
2. **The metrics' onset, corrected.** `restart_report` states `not
   onset_observed[W]` only from a row that both:
   - restarted in the window;
   - has its first sample within a minute of the oldest retained one, so
     the series began before the window.

   A pod born inside the window, or one not failing in it, says nothing
   about the workload's onset.
3. **No knowledge edit, unless you choose one.** With item 1,
   `application-release` turns strong on both scenarios through its spec
   arm (`onset_observed`, `changed_before_onset[W, spec]`). `bad-config`
   needs `logs_name_invalid_setting[C]` too, which the agent records from
   the logs. So offline, with no agent facts, deploy-regression leads with
   "application release, strong", and `bad-config` stays open with its
   check, the logs. That is right as far as it goes: the revision is the
   when, and the logs name the what. The options:
   - **(a) as mined:** no edit. A release and a bad setting can both read
     strong, and the answer weighs them.
   - **(b) the spec arm falls to moderate,** noting that env, probe and
     limit changes are how bad-config, probe and resource causes arrive:
     the release dates them, and the narrower claim names them. Decided on
     judgment, with no source to cite.

   I recommend (a): the step then changes no knowledge, and the measurement
   says whether the overlap costs anything.

   **Owner, 2026-10-25: (a).**
4. **Fixtures:** `scenario record-results` for deploy-regression and
   liveness-probe-wrong-port. They have no `results/` today; mine are
   scratch copies.
5. **The reach check,** as before: all ten scenarios, kb lines and `open`
   rankings, old against new. The expected reach is these two. image-pull-typo's
   and pvc-pending's new pods have no restarts, so item 1 skips them. Item 2
   leaves jvm-oom-with-metrics' fact in place: ad's series starts 19s after
   the oldest retained sample, and it restarts 327 times in the window. If more come in, they run, and
   the budget is restated first.
6. **The offline check,** a new test file:
   - deploy-regression: `application-release` strong, with no conflicting
     standing, and `onset_observed[W]` with no `not onset_observed[W]`;
   - liveness-probe-wrong-port: the same for shipping;
   - unit tests for item 1's skips (an old change, an older failing pod, a
     reused ReplicaSet), and for item 2's two skips.
7. **The live run:** the reached scenarios, n=3, `--local-skill k8s-rca`,
   from a worktree.

The instrument is not changed: no checklist, truth or grader. SKILL.md is
not changed.

**Known limit, left out.** liveness's probe facts:
- `probe_failures_before_restarts[C]` and `kubelet_logged_probe_restart[C]`
  are in the record ("Liveness probe failed: ... connection refused", and
  "failed liveness probe, will be restarted");
- `probe_target_not_served[C]` would need the probe's port against the
  declared ports in `get_pod_spec`, and the refusal in the events. That is a
  join across two results, and declared ports are informational.

Liveness misses only `cause-unavailable` today, which the probe facts don't
reach. So they would move `open` and nothing the suite scores: a later
step, decided on judgment.

**Expected effect.**
- **Offline, certain:** item 6.
- **Live, a lean:** deploy-regression's `no-old-onset` holds at 3 of 3,
  against 2 of 3 in the baseline. Its tool calls fall from 33–45 toward
  liveness's 9–19, as itbench-33's fell in step 5 once `open` had a leader.
- **Live, none expected:** both checklists are at 13.7/14 and 8.3/9.
  Liveness's misses are `cause-unavailable`, which this doesn't reach.

**Can we see it?**
- **Offline:** yes, at no cost.
- **The trap at n=3:** only as a lean. 2/3 to 3/3 is one run.
- **Tool calls:** a fall from a mean of 39 to near 20 is visible at n=3
  against the baseline's spread (33–45). A smaller fall isn't.
- **The guard:** no REGRESSION under 004 §6.4. The item to watch is
  `cause-recent-revision` on both, met 6 of 6 today. A leader of
  "application release" shouldn't lose it. If it does, option (b) is the
  checkpoint's question.

**Tripwires, said first.**
- **A rule from a run (process.md §2.3).** Item 2 changes a derivation
  whose wrong output feeds a trap that failed once. Against that: the
  defect was found offline, by reading the rows; the correction is what the
  fact says ("the first failure is in what the record retains"); and it
  applies to every metrics row, not to payment. **Cleared by the owner,
  2026-10-25.**
- **The third change for one thing:** no. This is the first change aimed at
  `no-old-onset` and at these two scenarios' ranking.

**Budget: $5, one day.** No mining and no k8stools change. The live run is
about $2.70: deploy-regression at about $0.60 a run, liveness at about
$0.20, n=3 each, plus grading.

**Done:**
- items 1 and 2 with fixtures, and `kb build` passing;
- the offline check passing on both scenarios;
- n=3 on the reached scenarios with no REGRESSION;
- a result table here.

**Stop:**
- **A REGRESSION** on any reached scenario.
- **The reach check finds more than four scenarios.**
- **A derivation that has to guess:** for instance, a pod's creation that the
  record doesn't hold.
- **Any of process.md §2's tripwires.**

#### Step 6 result (2026-10-25): done

**Built as planned, with (a): no knowledge edit.** a827b49.
- **`get_workload_report`** states `onset_observed[W]` and
  `changed_before_onset[W, image|resources|spec]` from `last_change`. It
  does so when every failing pod was created with the change (within 60s)
  and the event record reaches back to their creation.
- **`restart_report`** states `not onset_observed[W]` only from a series
  that restarted in the window and began before it.
- **Fixtures:** `results/` for deploy-regression and
  liveness-probe-wrong-port, which had none.

**The reach check:**
- **kb lines** change in seven scenarios. deploy-regression and liveness
  gain the change and onset lines. In the other five, only false `not
  onset_observed[W]` lines go, on healthy workloads whose old pods
  restarted before the metrics begin and not since.
- **Rankings** change on the two planned scenarios, and on one more:
  downstream-latency's frontend-proxy. Its only two facts were the false
  ones, so `open` now says "no facts recorded" where it listed every
  candidate as undecided. Neither the truth nor the checklist names
  frontend-proxy, and the cause, product-catalog's CPU limit, isn't
  reached, since that pod doesn't restart. **Owner: not run.**

**The offline check passes** (`tests/test_facts_change_onset.py`). Both
scenarios open with `application-release` strong, where they had no
leading cause. deploy-regression's `bad-config` stays open, with the logs
as its check.

**The live run,** n=3 from `../k8srca-step6`, against the baseline:

| Scenario | | Diagnosis | Checklist | Cause | Ruled out | Traps | Gaps | Next | Calls | $/run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| deploy-regression | baseline | 3/3 | 13.7/14 | 12/12 | 6/6 | 14/15 | 6/6 | 3/3 | 33–45 | 0.60 |
| | step 6 | 3/3 | 13.3/14 | 12/12 | 6/6 | 13/15 | 6/6 | 3/3 | 17–34 | 0.35 |
| liveness | baseline | 3/3 | 8.3/9 | 10/12 | 3/3 | 6/6 | 3/3 | 3/3 | 9–19 | 0.20 |
| | step 6 | 3/3 | 8.7/9 | 11/12 | 3/3 | 6/6 | 3/3 | 3/3 | 18–21 | 0.26 |

- **No REGRESSION.** Both read `noise` under 004 §6.4.
- **The lean on the trap is met:** `no-old-onset` 3 of 3, against 2 of 3.
- **Tool calls fell partly.** deploy-regression's mean went from 39 to 25
  and its cost per run from $0.60 to $0.35. The plan called a fall to
  about 20 visible and a smaller one not, so this is a lean, not a finding.
  Liveness's rose, from 9–19 to 18–21.
- **A new miss, outside this change:** `no-ad-errors-as-payment`, 2 of 3,
  against 0 in the baseline.
  - Both misses are in the follow-up ("is anything else affected?"). The
    agent read frontend's logs and quoted `ECONNREFUSED
    10.106.129.133:8080`, which is ad's address, as failed calls to payment.
  - The new facts say nothing about frontend, addresses or the follow-up.
    At n=3 a move of 2 can't be told from noise.
  - Answering it with a rule would be a rule from a run (process.md §2.3),
    so nothing is changed here. It goes on the list to watch.
- **The answers lead as `open` does.** All three deploy-regression answers
  gave bad config as very likely, citing the revision's `PAYMENT_PORT`
  change and the logs. All three liveness answers named the probe that
  revision 3 added.
- **Two `numeric_claims` advisories on liveness run 1, both false
  positives.** "21" and "6" are the Unhealthy and Killing event counts,
  cited from `get_pod_events`.
- **Spent:** about $2.20 of $5: sessions $1.83, grading about $0.35.

**Against the done criterion:** met. Items 1 and 2 have fixtures, and `kb
build` passes with the knowledge unchanged. The offline check passes on both
scenarios. n=3 shows no REGRESSION, and this table is written. No tripwire
fired. The new trap miss is one to watch, not a change to make.

## 12. Open questions

1. **How much of the investigation record becomes facts?** `open` needs facts
   in the vocabulary. Making the model write all evidence that way may cost
   reasoning quality (002 §11.1's worry about structured output). Start with
   `open` taking whatever facts the model chooses to record. §6.1 proposes
   extracting the mechanical facts from an evidence record instead, with the
   timing facts extracted only. *Settled by 010 (§3):* code derives the facts
   from seven tools' results, and the agent writes only what needs reading
   text, such as a log, with `kb_query.py fact`.
2. **Prose conditions.** How many survive mining, and whether they are a
   permanent part of the format or debt to pay down.
3. ~~**The vendored 81.**~~ *Decided 2026-10-16:* all of them go through the
   mining pipeline as one more source per family (§8.5), and the legacy path
   goes when the last family is done. Until then it serves the unconverted
   alerts (79 today), as 002 §8 proposed.
4. **Runbook delivery.** Whether runbooks come in through the deployment
   config or as a plugin (005 §5) with its own evals.
5. **A runbook that is wrong.** It outranks the KB on order, never on what gets
   disposed of. That may still not be enough when a runbook's threshold is
   simply stale.
6. **Weights per mined claim.** Mining (§8) extracts conditions, not how much
   each counts, and the guides rarely say. Under 008 §4.2 a mined condition needs
   a weight. The reviewer of a mined family may have to supply it, or the
   extractor could propose one from the guide's wording ("usually", "rules
   out"), to be reviewed.

## Glossary

| Term | Meaning |
| --- | --- |
| **alternative** | One entry in a claim's `confirm` or `refute` list. Any one alternative holding is enough (§3.2). |
| **batch** | One set of six runs of a scenario. |
| **capability** | What an action needs from the deployment: `k8stools`, `prometheus`, … (§4.1). |
| **change kind** | A kind of change (`image`, `config`, `node`, …): when it takes effect, and the actions that can see one (§3.4). |
| **claim** | A candidate cause (§3.1). Called a *hypothesis* in the vendored KB and a *rival* in `truth.yaml`. |
| **could-not-check** | A verdict for a claim nothing available can decide. It is a gap, not a refutation (§4.2). Under 008 it becomes a reason a claim is not ruled out, no longer a verdict. |
| **discriminator** | Evidence that tells candidate causes apart. Formerly prose in `docs/rca/discriminators.yaml`; now `confirm`/`refute` rules. |
| **disposition** / **verdict** | What the answer concludes about a claim: confirmed, weakened, refuted, or could-not-check. Under 008, a *standing* and a *strength*. |
| **gap** | Evidence that could not be obtained, said so rather than treated as absent. |
| **k8stools** | The read-only MCP server through which all cluster access goes (CLAUDE.md). |
| **lean** | A move too small for one batch to resolve (fewer than four runs in six). |
| **onset** | When the failures began. *Observed* only when the first failure itself is in what events, metrics or history retain (§3.4). |
| **prevalence** | Whether a sub-cause is a common or uncommon kind of its parent; uncommon ones wait until asked for (§6). |
| **refinement** | A claim whose confirmation opens a further set of claims, e.g. `oom-kill` → which kind of OOM (§3.1). |
| **rival** | A claim that competes with another as *the* cause: a sibling under the same symptom or refinement. |
| **scope** | What span of the cluster's history a fact was observed over: the retained terminations, the event window, the log tail (§3.5, planned). |
| **standing** | Planned (008): most likely, not ruled out, or ruled out. |
| **strength** | Planned (008): strong, moderate or weak, for a claim not ruled out. |
| **symptom** | What the person asking can see; the entry point to the knowledge (§5). |
| **vendored** | Received from elsewhere and kept unedited, under `vendor/`. |
