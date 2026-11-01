# Targeted scenarios: what we build, so the hold-outs do not

For the hold-out author ([design 006](../../designs/006-holdout-suite.md)).
These are the scenarios the agent's developers build, read and tune against.
A hold-out should not repeat one. We update this list when the plan changes,
and tell you; a hold-out you already wrote that later collides with a new
entry stays — you were first.

**All of these run on the OpenTelemetry demo**, which the hold-outs will not
(see [requirements](requirements.md)). Reusing a *failure class* on your
application is allowed and useful — it tests whether what we learned
generalises. Reusing a *vehicle* (the specific mechanism, below) is what to
avoid: at least half of the suite should use a vehicle not on this list.

## Built: authored on our cluster

| Scenario | Vehicle | What it discriminates |
| --- | --- | --- |
| `jvm-oom-on-startup` | JVM with memory limit == request and no heap flags, killed in seconds on every start; reports `reason: Error`, not `OOMKilled` | Evidence that contradicts the hypothesis; onset that cannot be dated |
| `jvm-oom-with-metrics` | The same JVM start-up kills, recaptured with a week of Prometheus metrics | Reading memory over time; an old rollout is not the onset |
| `checkout-gc-pressure` | "Why is checkout slow?": checkout's Go runtime above its `GOMEMLIMIT` near a tight memory limit, burning CPU, while unrelated services crash-loop, get OOM-killed and fill logs with errors | Finding a quiet lead among loud unrelated breakage, at the right confidence |
| `nothing-is-wrong` | "Is cart failing?" while unrelated services crash-loop, with cart's own path clean | Declining to find a cause |
| `image-pull-typo` | Deployment updated to a misspelled image tag | Stopping: the answer is a few calls deep |
| `pvc-pending-no-storageclass` | Deployment updated to mount a new PVC naming a StorageClass that does not exist | Widening: the answer is on another object |
| `deploy-regression` | Deployment updated to an env var holding an invalid port; crash loop with exit code 0 | Tying a failure to a change |
| `liveness-probe-wrong-port` | Deployment updated to add a liveness probe on a port the app does not listen on | Broken app vs broken health check |
| `downstream-latency` | "Why are pages slow?": a dependency's Deployment updated to add a 10m CPU limit, throttled since | Latency whose cause is a dependency's resources |

## Built: imported from ITBench-Lite

| Scenario | Vehicle | What it discriminates |
| --- | --- | --- |
| `itbench-24-kafka-port-change` | checkout's `KAFKA_ADDR` changed to a port the Kafka Service does not expose | A change to a client's address for a dependency |
| `itbench-31-network-policy-decoy` | A NetworkPolicy created minutes before the alert that blocks nothing, beside a steady background trickle of 503s | Declining: a recent change that made no difference; a steady rate is background |
| `itbench-33-invalid-node-selector` | Deployment updated to a `nodeSelector` no node matches; new pods Pending, previous revision still serving | Severity: a blocked rollout behind a healthy revision is not an outage |
| `itbench-80-kafka-two-brokers` | Kafka's Deployment running 2 replicas of a single-node broker: two brokers with the same id behind one Service | Cause vs symptom: a dependency's replicas, not the client |
| `itbench-102-memory-quota` | A namespace ResourceQuota refusing a pod after its Deployment was scaled to 0 and back | Widening: the cause is a namespace object, not the workload |
| `itbench-105-invalid-command` | Deployment updated to a `command` the image does not contain | Tying a failure to a change |

## Planned

| Scenario | Vehicle | What it discriminates |
| --- | --- | --- |
| `evidence-unavailable` (from ITBench 34) | valkey's password enabled at runtime and cart not updated: no revision, no restart, no auth error logged | "No error logged" vs "the evidence is not recorded" |

## Considered, not decided

These may be built on our cluster. Treat them as taken if you can choose
something else.

| Scenario | Vehicle | What it discriminates |
| --- | --- | --- |
| a load spike | The load generator's users raised until a service is OOM-killed or slows under load | A workload spike as the real cause, not a rival to rule out |
| Pending pods | Pods that cannot be scheduled: requests no node can fit, or a taint nothing tolerates | The next family of the knowledge base after restarting pods |

## ITBench-Lite: what else we looked at

| ITBench | Vehicle | Status |
| --- | --- | --- |
| 16 | `shipping` given `QUOTE_ADDR` with port 0000 | dropped: no log states it |
| 20 | product-catalog image changed to one that does not exist | dropped: near `image-pull-typo` |
| 81 | Chaos Mesh partition, shipping → quote | dropped |
| 38 | Misconfigured HPA | needs a k8stools HPA tool |
| 2, 4–9, 11–15 | `flagd` feature-flag ConfigMap edits | deferred: symptoms are in metrics and traces |
| 17–19, 21, 22, 25, 29, 35, 83, 91 | Chaos Mesh faults | deferred: symptoms are in metrics and traces |

If you draw on ITBench too, use a scenario not in these tables, and convert it
onto your application rather than the OTel demo.
