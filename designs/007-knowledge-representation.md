# Design 007 — Diagnostic knowledge: representation, sources and runbooks

**Status:** draft, 2026-10-15. Stage 1 landed 2026-10-16: the format, its
build checks, `kb_query.py lookup`/`refine`, and `CrashLoopBackOff`/`OOMKilled`
converted, in `docs/rca/knowledge/` (§11.1). Stage 2, availability, landed
the same day: MCP servers declare what they `provide`, and the poller tells
each sandbox (§4.1). The other 79 alerts in the knowledge base are not
converted yet; that is the mining stage (§8). Supersedes the target
format in [002 §8](002-investigation-model.md#8-from-knowledge-base-to-playbooks),
which it extends rather than contradicts.

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
It has two parts:

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

`k8srca kb build` merges both parts into `skills/k8s-rca/knowledge_base.json`,
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
| **rivals** | every alternative cause listed in `truth.yaml` got a verdict: confirmed, weakened, refuted, or could-not-check |
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
| **Exclusion** | Claims that cannot both be the cause. Claims that are not excluded can stack. | `memory-limit-too-low` vs `memory-leak` |

The distinction that does the work is between a **refinement** and a
**rival**. In the KB-off runs of the jvm scenario, answers "confirmed" both
`oom` and `memory-limit-too-low`, as if they competed and both won. They
are not rivals: one refines the other, and confirming `oom` is where the
question "which kind of OOM?" starts. Two claims are rivals only when they
are siblings under one symptom or refinement. Exclusion says which siblings
cannot be the cause together.

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

- `lookup <symptom|alert|claim>` returns the claims, their discriminating
  facts, and for each fact the cheapest *available* action. Claims that only
  unavailable actions could decide are already marked could-not-check
  (§4.2). *Built, stage 1.*
- `refine <claim>` returns what confirming that claim opens up. *Built,
  stage 1.*
- **`open --facts <file>`**: given the facts recorded so far, which claims are
  still open, and which available actions would separate them, cheapest
  first. *Not built yet.* This is InferRCA's "identify candidate actions"
  step done as a script call the model chooses to make, not as an engine loop
  around the model. It is the guardrail 002 §2 asks scripts to supply: it
  cannot make the model look, but it can say precisely what is left to look
  at.

The facts file is the evidence part of the investigation record (002 §3),
written in the fact vocabulary. How much of the record moves to fact form is
an open question (§12.1).

The SKILL.md rule "account for every candidate" stays, and gets sharper: every
claim `lookup` and `refine` returned needs a verdict, and could-not-check
ones arrive already justified.

## 7. Build checks

`k8srca kb build` checks the whole format ([`src/k8srca/kb/knowledge.py`](../src/k8srca/kb/knowledge.py),
`check`). It fails on:

- a fact, claim, action, symptom or capability name that does not resolve;
- a claim with no `refute`;
- a fact with no producing action;
- a `k8stools` action whose `call` is not a k8stools tool (checked against
  k8stools' tool list);
- a refinement cycle, or an `exclusive` pair that is not a pair of siblings.

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

Two are deliberately left out. The vendored KB is not a mining source
because it is already in; whether to re-derive it through this pipeline is
§12.3. InferRCA's runbooks are runbooks, not generic guides (§9.4).

### 8.3 Pipeline

1. **Extract** each source with a model into the §3 format. Every item
   carries provenance: URL, retrieval date, and the sentence it came from.
2. **Merge** across sources by symptom and claim. The number of sources that
   independently list a claim is kept as a confidence signal.
3. **Bind actions** to capabilities. An action no capability provides is
   kept, and marked as such (§4.2).
4. **Check** (§7), then **review by hand**. Nothing mined ships unreviewed.

Mined content is vendored into its own directory, apart from what we
author. This is the same reason `vendor/kb/` and `docs/rca/knowledge/` are
kept apart: what we received must stay distinguishable from what we wrote.

Public guides are almost all confirm-oriented ("if you see X, it's Y").
Refute criteria will mostly have to be written: drafted by a model from the
confirm side and the exclusions, then reviewed. Budget the review there.

Mining proceeds in batches by symptom family (scheduling, image pull,
storage, networking, control plane, …), not one alert at a time. Each batch
is measured as §11 says.

### 8.4 The cost of breadth

More candidates means more to dispose of: more tool calls, and more claims
an answer can get wrong. Symptom keying (§5) and refinement keep what
`lookup` returns small. It returns the top-level claims for the symptom,
and sub-causes only once their parent is confirmed. The tool-call and cost
columns in 004's reports are where an increase would show.

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
| Mined content | Re-run the KB-on/KB-off comparison, **judged on cause and evidence**. `rivals` stops being informative once the KB writes the candidate lists the agent is graded against. Then the hold-out suite (006), which is the real test |
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

## 12. Open questions

1. **How much of the investigation record becomes facts?** `open` needs facts
   in the vocabulary. Making the model write all evidence that way may cost
   reasoning quality (002 §11.1's worry about structured output). Start with
   `open` taking whatever facts the model chooses to record.
2. **Prose conditions.** How many survive mining, and whether they are a
   permanent part of the format or debt to pay down.
3. **The vendored 81.** Convert them all through the mining pipeline as one more
   source, or keep `kb_query.py`'s legacy path for unconverted alerts, as
   002 §8 proposes. Today 79 take the legacy path.
4. **Runbook delivery.** Whether runbooks come in through the deployment
   config or as a plugin (005 §5) with its own evals.
5. **A runbook that is wrong.** It outranks the KB on order, never on what gets
   disposed of. That may still not be enough when a runbook's threshold is
   simply stale.

## Glossary

| Term | Meaning |
| --- | --- |
| **alternative** | One entry in a claim's `confirm` or `refute` list. Any one alternative holding is enough (§3.2). |
| **batch** | One set of six runs of a scenario. |
| **capability** | What an action needs from the deployment: `k8stools`, `prometheus`, … (§4.1). |
| **claim** | A candidate cause (§3.1). Called a *hypothesis* in the vendored KB and a *rival* in `truth.yaml`. |
| **could-not-check** | A verdict for a claim nothing available can decide. It is a gap, not a refutation (§4.2). |
| **discriminator** | Evidence that tells candidate causes apart. Formerly prose in `docs/rca/discriminators.yaml`; now `confirm`/`refute` rules. |
| **disposition** / **verdict** | What the answer concludes about a claim: confirmed, weakened, refuted, or could-not-check. |
| **gap** | Evidence that could not be obtained, said so rather than treated as absent. |
| **k8stools** | The read-only MCP server through which all cluster access goes (CLAUDE.md). |
| **lean** | A move too small for one batch to resolve (fewer than four runs in six). |
| **refinement** | A claim whose confirmation opens a further set of claims, e.g. `oom-kill` → which kind of OOM (§3.1). |
| **rival** | A claim that competes with another as *the* cause: a sibling under the same symptom or refinement. |
| **symptom** | What the person asking can see; the entry point to the knowledge (§5). |
| **vendored** | Received from elsewhere and kept unedited, under `vendor/`. |
