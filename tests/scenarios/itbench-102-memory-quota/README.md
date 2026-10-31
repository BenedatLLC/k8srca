# itbench-102-memory-quota

**Discriminates:** a workload with no pods at all. Nothing crashed and there
is no container to read; the cause is a Deployment condition and FailedCreate
events, and the number in them has to be read in milli-units.

## Where it came from

[ITBench-Lite](https://huggingface.co/datasets/ibm-research/ITBench-Lite)
(Apache 2.0), SRE Scenario-102. The OTel demo 2.1.3 (chart 0.38.6) on the same
kind of 5-node kops cluster in AWS as the other ITBench imports (each
snapshot on its own nodes), namespace `otel-demo`. The fault
injector created a ResourceQuota `otel-demo-memory` with `hard: {memory:
1503238553600m}` (1.4Gi) at 17:54:21, then scaled the `ad` Deployment to 0
at 17:54:31 and back to 1 at 17:54:41, so ad had to create a pod under the
quota. The quota is already below the 1901Mi the namespace's running pods
request, so the creation is refused.

## How the capture was made

`k8s.json` is k8stools 4.4.0's ITBench import of the snapshot's last pull
(`results/` recorded on 4.4.1's replay):

```
k8s-capture-state --from-itbench DIR --max-log-lines 10000
```

with DIR the Scenario-102 snapshot (k8stools#30). It picks the last of the
five pulls (pulled from 17:59:03; the capture's `captured_at` is
17:59:06) and filters events and logs to it.

- Logs are OTel SDK records, not container stdout. 7 of the 20 otel-demo pods
  have none: frontend, email, flagd, image-provider, otel-collector,
  postgresql, valkey-cart. Previous-instance logs are not imported.
- `--max-log-lines 10000` caps frontend-proxy, load-generator and
  product-catalog; frontend-proxy's log starts at 17:44:35, ten minutes
  before the fault. A default `get_logs_for_pod_and_container` call returns
  the last 1000 lines (about 90 seconds, all after the fault); seeing the
  pre-fault `/api/data` 200s needs a larger `tail`.
- No metrics-server readings: usage is unknown, which the composites say.
- The ResourceQuota is in the raw snapshot (last pull only) but not in the
  capture: k8stools has no quota tool. The agent sees the quota only through
  the Deployment's `ReplicaFailure` condition and the ReplicaSet's
  FailedCreate events, which carry its name, the request, the usage and the
  limit.

The capture and the wiki live in the scenario data repository
(`BenedatLLC/k8srca-scenario-data`, issue #7) after `k8srca scenario publish`;
`k8srca scenario fetch` puts them here. `scenario build-wiki` built
the wiki from this capture with the `live_cluster` and `change_history`
sources only (004 §12.1), and `truth.yaml` pins its digest. A legacy skill, if wanted, comes from the
17:54:03 pull, eighteen seconds before the quota.

## What is in the capture that matters

| | |
|---|---|
| ad Deployment | 0/1 ready; `ReplicaFailure=True (FailedCreate)`: `pods "ad-554b849958-v9dqt" is forbidden: exceeded quota: otel-demo-memory, requested: memory=225Mi, used: memory=1901Mi, limited: memory=1503238553600m`; `Available=False` since 4m25s; `Progressing=True (NewReplicaSetAvailable)` from 35m ago, which reads healthy |
| ad ReplicaSet | `ad-554b849958`, revision 1, the only one, 35 minutes old; desired 1, current 0 |
| events | `Scaled down replica set ad-554b849958 from 1 to 0`, `Deleted pod: ad-554b849958-p226w` and `Killing` at 4m35s; `Scaled up ... from 0 to 1` at 4m25s; ten FailedCreate records (16 refusals) from 4m25s to 1m41s |
| the limit | `1503238553600m` = 1,503,238,553.6 bytes = 1.4Gi, about 1434Mi; `used` 1901Mi is the exact sum of the 20 running otel-demo pods' memory requests |
| ad Service | 0 ready endpoints |
| frontend-proxy log | from 17:54:34 (3s after the scale-down) every `GET /api/data` returns 500 `via_upstream` from frontend, each after a 308 redirect from `/api/data/`: 266 of 2819 non-redirect requests, 9.4% (8.6% counting the 308s); checkout, cart, products and recommendations stay 200 |
| background | three `503 UC` in 10,000 lines (17:47, 17:50, 17:56) on `/api/products` and `/api/recommendations`, in every snapshot of this install |
| bystander | flagd rollout restart (revision 2) 33 minutes before, visible in workload history (`restartedAt` annotation; revision 1's ReplicaSet at 0), not in the captured events; healthy |
| nodes | 5, all Ready, no MemoryPressure |
| traffic | all from the load generator (`python-requests`), which logs no errors |

## Where ITBench's ground truth differs from the capture

Its `ground_truth.yaml` gets the cause right (a memory quota on `otel-demo`),
and its user impact (frontend and frontend-proxy request errors) matches the
capture. Where it differs:

1. **"Deployment cannot create new ReplicaSet."** No new ReplicaSet was
   needed or attempted. The existing revision-1 ReplicaSet was scaled 1 -> 0
   -> 1 and cannot create its pod.
2. **"Preventing pod scheduling"** and "ReplicaSet cannot schedule pods". The
   refusal is at admission: no pod object is created, so nothing reaches the
   scheduler. There is no Pending pod and no FailedScheduling event.
3. **It does not say the quota is already exceeded.** Its condition ("Memory
   quota prevents new pod scheduling when deployment tries to scale up") reads
   as if ad's pod is the one that does not fit. The limit (1.4Gi) is 467Mi
   below what is already requested, so any otel-demo pod that has to be
   recreated is refused, and the fix needs the quota at 2126Mi or more.
4. **Alerts.** It lists `NoRequestsDetected` for ad; at 17:59:18 only the two
   `RequestErrorRate` alerts (frontend 5.09, frontend-proxy 2.50) are in
   alerting state.
5. **flagd.** It lists flagd's Service and pod as entities, with no
   propagation. flagd's only event in the raw events is a rollout restart half an hour
   before the fault (the capture keeps only workload history of it); here it is a trap.

## Re-recording

It cannot be re-recorded; the source cluster is gone. If the importer or
k8stools changes, regenerate `k8s.json` from the ITBench snapshot (the last
pull) with the command above, then re-review `truth.yaml` (its
`captured_at`, counts and ages) and `checklist.yaml`, rebuild the wiki and
re-pin its digest.
