# itbench-33-invalid-node-selector

**Discriminates:** severity. A blocked rollout behind a healthy previous
revision is not an outage, and the summary tools make it look like nothing at
all.

## Where it came from

[ITBench-Lite](https://huggingface.co/datasets/ibm-research/ITBench-Lite)
(Apache 2.0), SRE snapshot `v0.2-B96DF826-…/Scenario-33`. The OTel demo 2.1.3
(chart 0.38.6) on a 5-node kops cluster in AWS, namespace `otel-demo`. At
17:54:51 and 17:54:59 the fault injector patched the `ad` and `cart`
Deployments to add `nodeSelector: {kubernetes.io/hostname: invalid-node}`.

This is the first scenario authored from someone else's capture rather than by
breaking our own cluster (004 §4.1). The breakage is still real -- ITBench
injected it into a live cluster and recorded what followed -- so the §4.1
argument holds; what we lose is the ability to re-make it.

## How the capture was made

ITBench ships raw API objects (`k8s_objects_raw.tsv`, the OTel k8sobjects
receiver pulling every 5 minutes), an event watch stream and OTel application
logs. These were converted to a k8stools capture by serving the raw objects
through fake API clients and running k8stools' own `capture_state` against
them, with its clock pinned to the snapshot. The converter is not in this
repository: it imports the `kubernetes` client, which k8srca may not
(CLAUDE.md), and belongs in k8stools as an import path beside
`k8s-capture-state` (004 §12.1).

- `k8s.json` -- the last pull, 18:04:40, ~10 minutes into the incident.
- `skill/cluster-architecture` -- `k8srca arch build` run against a replay of
  the **17:54:40 pull, before the fault**, with only the `live_cluster` and
  `change_history` sources. Our charts and `docs/architecture/otel-demo` notes
  describe our install, not this one (ITBench's `ad` has a 450Mi limit; our
  note says 300Mi), so pinning them would hand the agent false facts.

## What is in the capture that matters

| | |
|---|---|
| `ad-dd7f45975-mw86p`, `cart-5b597c6db5-pkzkh` | Pending, no node, `nodeSelector: kubernetes.io/hostname: invalid-node` |
| FailedScheduling | "0/5 nodes are available: 1 node(s) had untolerated taint {node-role.kubernetes.io/control-plane: }, 4 node(s) didn't match Pod's node affinity/selector" |
| ReplicaSets | ad and cart each have revision 1 (1 ready) and revision 2 (0 ready), created 589s / 581s before capture |
| Deployment summaries | 1 total, 1 ready, 1 up-to-date, 1 available -- looks finished, is not |
| old pods | `ad-554b849958-42mdm`, `cart-755465879b-bgb2p` Running 1/1, logging live requests to the last second |
| nodes | 5, all Ready; only the control plane is tainted; no node is named `invalid-node` |
| `progressDeadlineSeconds` | 600, and the rollout is 589s old, so the Deployments have not yet been marked ProgressDeadlineExceeded (not visible through k8stools either way) |
| logs | OTel SDK logs, not container stdout: 9 of 23 pods have none (frontend, email, flagd, valkey-cart, …) |

## Where ITBench's ground truth is wrong

Its `ground_truth.yaml` names only `ad` as the root cause and propagates it as
"ad service has no healthy endpoints" leading to frontend errors. The capture
disagrees on both counts:

1. **`cart` is the same fault.** Identical selector, created 8 seconds later.
   Its own `recommended_actions` says to fix "ad and cart deployments".
2. **Nothing is down.** Both Endpoints objects have a ready address, both old
   pods serve traffic, the only alert in alerting state is
   `PendingPodsDetected` ("current: 2"), and there is no error- or warn-level
   log line in 110K records.

ITBench's precision-at-full-recall scoring would therefore penalise an agent
for naming cart and reward one that claims an outage. `truth.yaml` here is
written against the capture instead, and the outage claim is a trap.

## Re-recording

It cannot be re-recorded; the source cluster is gone. If the converter or
k8stools changes, regenerate both files from the ITBench snapshot (the
18:04:40 pull for `k8s.json`, the 17:54:40 pull for the skill), then re-review
`truth.yaml` and update both digests.
