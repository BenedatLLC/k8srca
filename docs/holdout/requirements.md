# Hold-out suite: what it should cover

For the hold-out author ([design 006](../../designs/006-holdout-suite.md)).
These are requirements, not scenarios: what to build is yours to choose, and
we will not know what you chose. Read
[targeted-scenarios.md](targeted-scenarios.md) first — that is what to avoid.

## The application

- **Not the OpenTelemetry demo, and not Online Boutique.** The OTel demo
  descends from Online Boutique and shares its services and call graph.
- **Tell us its name**, and nothing else about the suite. We commit to never
  building a targeted scenario on it.
- Prefer one with **a Helm chart** (the agent's architecture wiki is richest
  when it can read the declared configuration), **eight or more workloads**,
  **more than one language**, and **at least one stateful component** (a
  database or queue with a PVC). Candidates to check, not endorsements:
  DeathStarBench's hotel-reservation or social-network, TeaStore, Sock Shop.
- One application is enough to start. A second later is worth more than more
  scenarios on the first.

## Size

Four to eight scenarios to start. Fewer than four, and one odd scenario is a
quarter of the signal.

## Coverage

Each scenario should exist to discriminate one way of reasoning well or badly.
Between them, cover at least five of these, and include the three marked
**required**:

| Reasoning | What a scenario looks like |
| --- | --- |
| **Declining (required)** | A question about something healthy, asked while something unrelated is visibly broken — or with nothing broken at all. The right answer is "I can't find a cause here", with what was checked. |
| **Stopping (required)** | The answer is a few tool calls deep. Over-investigation (cost, tool calls) is the failure. |
| **Unavailable evidence (required)** | The decisive evidence cannot be read through the agent's tools (below). The right answer says so instead of concluding from absence. |
| Contradicting evidence | An observation that fits the obvious hypothesis badly. Does the answer flag it or smooth it over? |
| Widening | The symptom is on one object, the cause on another the symptom does not name. |
| Cause vs symptom | Several things are broken because one is. |
| Change correlation | A recent rollout or config change explains it, visible in ReplicaSet revisions. |
| Severity | Something is wrong but not as wrong as it looks (or the reverse). |

Vehicles not on our list, to start from (more than you need, on purpose): a
NetworkPolicy blocking traffic, a missing or rotated Secret, a Service whose
selector or port no longer matches its pods, a ConfigMap key a container
expects but no longer finds, an init container that never completes, a failing
Job or CronJob, a node cordoned or tainted mid-rollout, a StatefulSet stuck on
one ordinal, a ServiceAccount losing a permission the app needs, DNS
misconfiguration, a disk filling up.

## What the agent can see

The agent reads the cluster only through k8stools (4.1.0 or later). A
replayed capture holds:
- **Workloads:** namespaces, nodes, pods, Deployments, ReplicaSets with their
  revision history, StatefulSets, DaemonSets, Jobs and CronJobs.
- **Pods in detail:** spec and status, init containers included, and current
  and previous logs, 200 lines per container.
- **Networking:** Services with their EndpointSlices, and Ingresses.
- **Configuration and storage:** ConfigMaps (keys and sizes; values
  redacted), PVCs, and HPAs.
- **Events.**
- **One resource-usage sample** per container and node, if the cluster runs
  metrics-server.
- **Custom resources' status conditions.**

Not captured: metrics over time, traces, Secrets' contents, NetworkPolicies,
ResourceQuotas, RBAC and StorageClasses. Kubernetes itself keeps no restart
history either: only each container's restart count and last termination.
So "when did this start?" is often unanswerable from the capture, and the
agent is told so.

A scenario whose only evidence is outside that list can be answered only by
saying it cannot be answered. That is a good *unavailable evidence* scenario
and an unfair any-other kind. Know which one you are writing.

## How to author

The full guide is [authoring.md](authoring.md). The parts that matter
most:

- **Break a real cluster, then capture it.** Do not write cluster state by
  hand: you will only encode failures you already understand, and the answer
  will be over-determined. Real breakage produces the details nobody would
  invent, and those are the best tests.
- **Ask the question an SRE would ask.** Name the symptom ("checkout requests
  are failing"), never the cause.
- **Write the truth from the capture, not from what you did to the cluster.**
  Every one of our truth errors was a note written from a mental model of the
  fault that the capture contradicted: a service called "normal history" that
  was actually being OOM-killed, two crash loops called independent that
  shared their only distinguishing feature. If the capture shows more than you
  broke, the truth says what the capture shows.
- **Calibrate, then seal.** Run each scenario a few times and read the answers
  only for mistakes in your truth. When a dimension disagrees with itself
  across runs, suspect the truth before the agent. Then seal: after that the
  truth changes only by a recorded decision.

## What you send us, and what you never send

Per release, the score report the kit writes: opaque ids, per-dimension pass
counts, tool calls, cost, unsupported-claim counts. Never answers, grader
notes, questions, scenario names, check findings, or a description of what
broke. If a drop is serious enough that we jointly decide to look at a
scenario, it leaves the hold-out set and you write a replacement.
