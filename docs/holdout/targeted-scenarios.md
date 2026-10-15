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

## Built

| Scenario | Vehicle | What it discriminates |
| --- | --- | --- |
| `jvm-oom-on-startup` | JVM with memory limit == request and no heap flags, killed in seconds on every start; reports `reason: Error`, not `OOMKilled` | Evidence that contradicts the hypothesis; onset that cannot be dated |
| `itbench-33-invalid-node-selector` | Deployment updated to a `nodeSelector` no node matches; new pods Pending, previous revision still serving (imported from ITBench-Lite) | Severity: a blocked rollout behind a healthy revision is not an outage |
| `checkout-gc-pressure` | "Why is checkout slow?": checkout's Go runtime above its `GOMEMLIMIT` near a tight memory limit, burning CPU, while unrelated services crash-loop, get OOM-killed and fill logs with errors | Finding a quiet lead among loud unrelated breakage, at the right confidence |

## Planned: authored on our cluster

| Scenario | Vehicle | What it discriminates |
| --- | --- | --- |
| `nothing-is-wrong` | "Why is checkout slow?" while unrelated services crash-loop, with checkout's own path made clean first | Declining to find a cause |
| `probe-too-aggressive` | Readiness/liveness probe with a 1 s timeout on a slow starter | Broken app vs broken health check |
| `image-pull-typo` | Misspelled image tag | Stopping: the answer is three calls deep |
| `pvc-pending-no-storageclass` | PVC naming a StorageClass that does not exist | Widening: the answer is on another object |
| `deploy-regression` | Deployment updated to a broken image ~20 min before, previous ReplicaSet intact | Tying a failure to a change |
| `innocent-bystander` | Service B fails because service A is down | Cause vs symptom |
| `evidence-unavailable` | Crash loop whose previous-instance logs are gone | "No error logged" vs "the logs are gone" |

## Planned: imported from ITBench-Lite

| ITBench | Vehicle | Status |
| --- | --- | --- |
| 16 | `shipping` given `QUOTE_ADDR` with port 0000 | next |
| 24 | `checkout` given `KAFKA_ADDR` with port 9999 | next |
| 34 | `valkey` password enabled, `cart` not updated | convertible |
| 102 | Namespace `ResourceQuota` blocks pod creation | convertible |
| 38 | Misconfigured HPA | needs a k8stools HPA tool |
| 2, 4–9, 11–15 | `flagd` feature-flag ConfigMap edits | deferred: symptoms are in metrics and traces |
| 17–19, 21, 22, 25, 29, 35, 80, 81, 83, 91 | Chaos Mesh faults | undecided |

If you draw on ITBench too, use a scenario not in this table, and convert it
onto your application rather than the OTel demo.
