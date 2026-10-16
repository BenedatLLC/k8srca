# Design 007 — Diagnostic knowledge: representation, sources and runbooks

**Status:** draft, 2026-10-15. Nothing here is built. Supersedes the target
format in [002 §8](002-investigation-model.md#8-from-knowledge-base-to-playbooks),
which it extends rather than contradicts.

Companion to [002](002-investigation-model.md) (how the agent reasons) and
[004](004-scenario-testing.md) (how we measure it). 002 says the agent forms
candidates, gathers evidence and disposes of every candidate. This says what the
knowledge it does that with looks like, where it comes from, and how a site's own
runbooks fit alongside it.

---

## 1. Why

The S3 measurement ([004 §10.1](004-scenario-testing.md#101-s3-result-the-knowledge-base-on-and-off-2026-10-15))
says the knowledge base does not help the agent find causes; it makes the agent
rule on alternatives. Without it, rivals fell from 5/6 to 0/6 on the two
scenarios where it had entries, because the agent raised fewer alternatives and
stopped at the first that fit.

The parts that produced that effect are the small structured parts: the
would-confirm / would-refute text in `docs/rca/discriminators.yaml` and the
`refines` edge from `CrashLoopBackOff`'s "OOM" to `OOMKilled`. Both exist for two
alerts out of 81. The rest of the knowledge base is a flat list per alert of
hypothesis strings with free-text evidence hints:

```json
{"alert": "APIClientThrottling",
 "hypotheses": ["excessive client request rate", "low API priority/fairness capacity"],
 "evidence": {"metrics": ["rate(apiserver_request_total{code=\"429\"}[5m])"], ...}}
```

That record names what might be wrong. It does not say how to tell the
candidates apart, which ones can be true together, what each one opens up
when confirmed, or whether this deployment can make the checks at all. That
leaves the model to invent those things during the investigation, and that is
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
| **What the system is** | (not needed) | architecture wiki (kubewiki) |
| **How to diagnose it** | the knowledge base: authored + mined | **runbooks** |

The rule `discriminators.yaml` enforces today, *generic: true of any cluster*,
stays the rule for the knowledge base. Site-specific *descriptions* belong in
the wiki, as before. Site-specific *diagnostics* ("this alert is usually the
nightly batch job") belong in runbooks, which until now had nowhere to go.

All diagnostic content, whatever its layer, uses one representation (§3). Each
layer is a separate set of files with its own provenance, merged when the
skill is built (§9.3).

## 3. The representation

Concepts come from InferRCA's RCA language (`designs/rca-language.md` in the
InferRCA repository):
facts are kept apart from claims, actions are what produce facts, rules
derive claims from facts, an `unknown` result is recorded as a gap, and
exclusion is declared. The surface syntax is not taken over. Here the format is
JSON, built from YAML sources, and **the model interprets it, not a solver**
(§10.2).

### 3.1 Concepts

| Concept | Is | Example |
| --- | --- | --- |
| **Symptom** | An entry point: what someone asking can see. | `pod-restarting`, `pod-pending`, `high-latency` |
| **Fact** | An observable statement about the cluster, with parameters. True, false or unknown, never inferred. | `last_termination_reason[Container, OOMKilled]`, `limit_equals_request[Container, memory]` |
| **Action** | How a fact is observed: a capability it needs, what to call, what to read in the result, and a cost. | k8stools `get_workload_report`, reading the last state of the container |
| **Claim** | A candidate cause. Never observed, only supported or refuted by facts. | `oom-kill`, `memory-limit-too-low`, `memory-leak` |
| **Rule** | The conditions on facts (and on other claims) that confirm a claim, and the conditions that refute it. | below |
| **Refinement** | Confirming one claim opens another set of candidates: what *kind* of OOM. | `oom-kill` → {`memory-limit-too-low`, `memory-leak`, `workload-spike`} |
| **Exclusion** | Claims that cannot both be the cause. Claims that are not excluded can stack. | `memory-limit-too-low` vs `memory-leak` |

The distinction that does the work is between a **refinement** and a
**rival**. In the KB-off jvm runs, `oom` and `memory-limit-too-low` were both
"confirmed", as if they competed and both won. They are not rivals:
one refines the other, and confirming `oom` is where the question "which kind?"
starts. Two claims are rivals only when they are siblings under one symptom
or refinement, and exclusion says which siblings cannot be the cause together.

### 3.2 A worked entry

`CrashLoopBackOff` and `OOMKilled` today, rewritten. Abridged, in YAML source
form:

```yaml
symptoms:
  pod-restarting:
    summary: A container restarts repeatedly.
    claims: [oom-kill, application-crash, failed-liveness-probe, bad-config]

facts:
  last_termination_reason:  {params: [Container, Reason], functional: true}
  limit_equals_request:     {params: [Container, Resource]}
  heap_sizing_flags_set:    {params: [Container]}
  healthy_period_before_kills: {params: [Container]}
  limit_or_image_changed_before_onset: {params: [Container]}
  memory_usage_series:      {params: [Container]}

actions:
  read-container-last-state:
    produces: [last_termination_reason]
    requires: k8stools
    call: get_workload_report        # or get_pod_container_statuses
    read: The last terminated state of each container -- reason and exit code.
    cost: 1
  read-container-spec:
    produces: [limit_equals_request, heap_sizing_flags_set]
    requires: k8stools
    call: get_pod_spec
    read: >-
      Resources and env/args. Heap flags: -Xmx / -XX:MaxRAMPercentage for a
      JVM, GOMEMLIMIT for Go, --max-old-space-size for Node.
    cost: 1
  read-rollout-history:
    produces: [limit_or_image_changed_before_onset]
    requires: k8stools
    call: get_workload_history
    cost: 2
  query-memory-series:
    produces: [memory_usage_series, healthy_period_before_kills]
    requires: prometheus
    call: query_range
    read: container_memory_working_set_bytes over the incident window.
    cost: 3
  sample-memory-now:
    produces: [memory_usage_series]
    requires: k8stools
    call: get_container_metrics
    read: One usage sample -- current, not a history.
    quality: point                   # §4.3
    cost: 1

claims:
  oom-kill:
    confirm: [last_termination_reason[C, OOMKilled]]
    refute:  ["last_termination_reason[C, R], R != OOMKilled"]
    refined_by: [memory-limit-too-low, memory-leak, workload-spike]
  memory-limit-too-low:
    confirm:
      - limit_equals_request[C, memory]
      - not heap_sizing_flags_set[C]
      - not healthy_period_before_kills[C]
    refute:
      - healthy_period_before_kills[C]
      - not limit_or_image_changed_before_onset[C]
    note: >-
      Killed at or near the limit on every restart, the first one included.
      A runtime with no heap sizing sizes itself from the cgroup.
  memory-leak:
    confirm: [memory_usage_series[C] shows growth to the limit between restarts]
    refute:  [memory_usage_series[C] is flat until the kill]
exclusive:
  - [memory-limit-too-low, memory-leak]
```

A condition is a fact pattern, optionally negated (`not`: checked and false,
or never checked — the gap rule in §4.2 says which), optionally compared. Where
that cannot be said precisely, a condition can be prose
(`memory_usage_series[C] shows growth…`). This is the escape hatch InferRCA
lacks and the model does not need, and the build reports it (§7) so it stays
the exception. Every claim needs a `refute`; one that can only be confirmed
invites the confirmation-seeking 002 §6 is built against.

### 3.3 Not a decision tree

Debugging *reads* like a tree, and most guides draw one (the learnk8s flowchart
is literally one). The tree is the wrong thing to store, for two reasons:

- **A tree fixes the order and stops at the first leaf.** Stopping at the first
  fit is the failure we keep measuring. Rules state what would settle each
  candidate; the order comes from cost and availability (§4) at run time.
- **A tree has one path; incidents do not.** A memory leak and a too-low limit
  are exclusive; an OOM kill and a failing liveness probe can both be true. A
  tree encodes neither.

The tree-like part, going from general to specific, is kept as refinement:
a graph of claims, which is what `refines` started.

## 4. Actions and availability

### 4.1 Capabilities

Every action `requires` one capability:

| Capability | Present when | Today |
| --- | --- | --- |
| `k8stools` | always (005 §5.1: `k8s-core` is always enabled) | yes |
| `prometheus` | the deployment enables a metrics plugin | no |
| `tracing`, `logs-backend`, … | the deployment enables that plugin | no |
| `human` | never, for the agent | — |

A capability is named by what it provides, not by which product provides it,
so `prometheus` could be served by any MCP server speaking PromQL. The actual tool
is the action's `call`, resolved against the plugin's tool manifest.

Availability is **per deployment**. Which capabilities a deployment has is
declared, not probed (005 §5.4): `k8srca sync` knows the MCP servers it wired
into the agents, and writes them into the skill bundle as `capabilities.json`.
The knowledge itself stays deployment-independent; only that file differs.
A scenario run takes its capabilities from the scenario's `sources`, so a
k8stools-only scenario sees the same availability as a k8stools-only deployment.

### 4.2 Unavailable is a gap, not an absence

An action whose capability is missing is **kept, not dropped**. Dropping it
would make its facts look checked-and-absent, which under `not` reads as
evidence. That is InferRCA's distinction between "attempted, unknown" and
"known false", moved to before the investigation starts:

- A fact whose every producing action is unavailable is **unobservable here**.
  It is neither true nor false, so `not` does not hold for it either.
- A claim whose confirm *and* refute conditions all rest on unobservable facts
  starts as **could not check**, and the gap names what would settle it
  ("needs a memory time series: no metrics backend in this deployment"). That is
  the `gaps` dimension and the `could_not_check` disposition in 004, produced by
  the knowledge rather than left to the model to notice.
- An action that is available but fails at run time (a tool error, logs
  rotated away) is a gap of the same kind, found later.

`human` actions are never available to the agent. They become recommended next
steps in the answer ("ask the DBA to check lock waits"), the honest form of a
check the agent cannot make.

### 4.3 Alternatives and their quality

One fact can have several producing actions with different requirements:
`memory_usage_series` from a Prometheus range query, or a single sample from
k8stools' `get_container_metrics`. The best available one is used, and
`quality` records what is lost. A `point` sample can confirm "near the limit
now" but cannot show growth over time, so it cannot refute `memory-leak`. Where
a condition needs a series, a point sample leaves the fact unobservable for
that condition. This is the checkout-gc-pressure situation: a usage sample
showed GC pressure, and no history was available to say since when.

### 4.4 Cost

`cost` is a small integer ordering (cheap lookups first), as InferRCA's
`priority` is. It orders the suggestions in §6; it never removes one.

## 5. Entry points: symptoms, and alerts mapped to them

Knowledge is keyed by **symptom**, because our questions come from people in
Slack ("why is checkout slow?"), not from Alertmanager. itbench-33 found no
entry because the knowledge base is keyed by alert names and
`PendingPodsDetected` was not one of them.

Alerts still matter. Runbooks are usually written per alert (§9), and 005
§3.3 adds alert-triggered investigations. So alerts are a **second entry point
that resolves to symptoms**, through a mapping table:

```yaml
alerts:
  KubePodCrashLooping:            # kube-prometheus
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

This is InferRCA's alert mapping, reduced: an alert maps to a symptom and
binds labels to parameters. Many alert names map to one symptom, which is
what makes runbook alert names and vendor alert names interchangeable. An alert
with no mapping falls back to `search`, as today. The build reports the
vendored KB's 81 alert names and any runbook's alerts that map to nothing.

## 6. How the agent uses it

`kb_query.py` stays the interface (002 §7.2). It changes in three ways:

- `lookup <symptom|alert>` returns the claims, their discriminating facts,
  and for each fact the cheapest *available* action, with unavailable claims
  already marked could-not-check (§4.2).
- `refine <claim>` returns what confirming that claim opens.
- **`open --facts <file>`**: given the facts recorded so far, which claims are
  still open, and which available actions would separate them, cheapest
  first. This is InferRCA's "identify candidate actions" step done as a script
  call the model chooses to make, not as an engine loop around it. It is the
  guardrail 002 §2 asks scripts to supply: it cannot make the model look, but
  it can say precisely what is left to look at.

The facts file is the investigation record's evidence (002 §3), written in
the fact vocabulary. How much of the record moves to fact form is an open
question (§12.1).

The SKILL.md rule "account for every candidate" stays, and gets sharper: every
claim `lookup` and `refine` returned needs a disposition, and could-not-check
ones arrive already justified.

## 7. Build checks

The build does what `kb build` does for `discriminators.yaml` today, over the
whole format. It fails on:

- a fact, claim, action, symptom or capability name that does not resolve;
- a claim with no `refute`;
- a fact with no producing action;
- a `k8stools` action whose `call` is not a k8stools tool (checked against
  the tool manifest `tools.py` already hashes);
- a refinement cycle, or an `exclusive` pair that is not a pair of siblings.

It reports, without failing: prose conditions, claims every rule of which needs
an unavailable capability under the default deployment (k8stools only), and
unmapped alert names. It also builds the layer merge (§9.3).

## 8. Mining public debugging guides

### 8.1 Why mine

The alternative is adding what our scenarios turned out to be missing, such
as a GC-pressure candidate under `HighRequestLatency`. That fits the content to
the suite, which is what 006 exists to detect. Mined content comes from sources
we did not choose by looking at our failures.

The KB-off arm also says what mining is for. The model already knows most of
the facts these guides contain, and it found causes without the KB. Its
weaknesses were *complete candidate lists* and *criteria that refute*. So
extraction targets those two, not explanations.

### 8.2 Sources

Licences checked per source before anything is vendored:

| Source | Shape | Good for |
| --- | --- | --- |
| Kubernetes docs: debug pods, services, running pods, clusters | prose, task-oriented | the core symptoms |
| kube-prometheus runbooks (runbooks.prometheus-operator.dev) | per alert | alert mappings (§5), candidates |
| GKE / EKS / AKS / OpenShift troubleshooting | prose per symptom | node, network, storage |
| learnk8s troubleshooting flowchart | a decision tree | checks and their order (§3.3) |
| k8sgpt analyzers, Robusta playbooks | code | actions already concrete enough to bind |

### 8.3 Pipeline

1. **Extract** each source with a model into the §3 format. Every item carries
   provenance: URL, retrieval date, and the sentence it came from.
2. **Merge** across sources by symptom and claim. The number of sources
   that independently list a claim is kept as a confidence signal.
3. **Bind actions** to capabilities; an action no capability provides is kept,
   marked so (§4.2).
4. **Check** (§7), then **review by hand**. Nothing mined ships unreviewed.

Mined content is vendored into its own directory, apart from what we author,
for the reason `vendor/kb/` and `discriminators.yaml` are kept apart today:
what we received must stay distinguishable from what we wrote.

Public guides are almost all confirm-oriented ("if you see X, it's Y"). Refute
criteria will mostly have to be written: drafted by a model from the confirm
side and the exclusions, then reviewed. Budget the review there.

### 8.4 The cost of breadth

More candidates means more to dispose of: more tool calls, more claims an
answer can get wrong. Symptom keying (§5) and refinement keep what `lookup`
returns small: the top-level claims for the symptom, with sub-causes only once
their parent is confirmed. The tool-call and cost columns in 004 are where an
increase would show.

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

Runbooks are prose and stay the source of record. They are **translated** into
the §3 format, a translation per runbook, with each item pointing back to
the runbook step it came from. InferRCA's `runbooks/*-translation.md` are this
exercise done three times.

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
- **Thresholds become comparisons** in conditions, not text in a prompt
  (InferRCA's latency translation found the cost of burying them).
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

Runbooks are per site, so they are deployment content, like `capabilities.json`:
supplied with the deployment's configuration, merged by `sync` into that
deployment's skill bundle.

### 9.4 Testing runbooks

A runbook can only be tested against the system it describes. Our targeted
scenarios run on the OTel demo and have no runbooks. Runbook evaluation
belongs where the runbooks are: the deployment's own scenarios, recorded and
graded the 006 way. Until we have a site's runbooks, InferRCA's three runbooks
(crash loop, latency SLO, cascade failure) serve as stand-ins to exercise the
translation and merge, not to measure anything.

## 10. Rejected alternatives

### 10.1 A decision tree

§3.3. It stores one order and one path, and it encodes the stop-at-first-fit
behaviour we are trying to remove.

### 10.2 Compiling to clingo and running a solver

InferRCA's route. It gives guarantees (complete, exhaustive) that the
model-interpreted format cannot. It is rejected for now, for 002 §10.3's
reason: CMA supplies the loop and the model handles messy evidence, which is the
part that works today. The format is kept machine-checkable and close enough
to InferRCA's that this stays a later choice, not a rewrite: `open` (§6) is
the step a solver would replace.

### 10.3 A separate runbook format

It would need a second interpreter, a second set of build checks, and a merge
across formats. Translation is the one-time cost of keeping a single format,
and it is where a runbook's ambiguities surface, before an investigation
depends on them.

### 10.4 Adding what the scenarios are missing

§8.1. It is quick, and it fits the knowledge to the suite.

## 11. Measurement

| Change | Gate |
| --- | --- |
| Format, with `CrashLoopBackOff` / `OOMKilled` converted | No dimension falls by ≥2 of 6 on the three scenarios; jvm rivals stays ≥5/6 |
| Availability (§4) | A k8stools-only run marks memory-series claims could-not-check; checkout `gaps` does not fall |
| Mined content | Re-run the KB on/off comparison, **judged on cause and evidence**: rivals is circular once the KB writes the lists. Then the hold-out suite (006), which is the real test |
| Runbooks | Stand-in runbooks translate, merge and pass §7. No score gate until a site has runbooks and scenarios |

## 12. Open questions

1. **How much of the investigation record becomes facts?** `open` needs facts
   in the vocabulary. Making the model write all evidence that way may cost
   reasoning quality (002 §11.1's worry about structured output). Start with
   `open` taking whatever facts the model chooses to record.
2. **Prose conditions.** How many survive mining, and whether they are a
   permanent part of the format or debt to pay down.
3. **The vendored 81.** Convert them all through the mining pipeline as one more
   source, or keep `kb_query.py`'s legacy path for unconverted alerts, as
   002 §8 proposes.
4. **Runbook delivery.** Whether runbooks come in through the deployment
   config or as a plugin (005 §5) with its own evals.
5. **A runbook that is wrong.** It outranks the KB on order, never on what gets
   disposed of. That may still not be enough when a runbook's threshold is
   simply stale.
