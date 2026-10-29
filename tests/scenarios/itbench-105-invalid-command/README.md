# itbench-105-invalid-command

**Discriminates:** cause against symptom. Four services alert, and none of
them is the one that broke: product-catalog cannot start, and its callers only
report it, mostly by what they stop logging.

## Where it came from

[ITBench-Lite](https://huggingface.co/datasets/ibm-research/ITBench-Lite)
(Apache 2.0), SRE snapshot Scenario-105. The OTel demo 2.1.3 (chart 0.38.6) on
a 5-node kops cluster in AWS, namespace `otel-demo`. At 17:54:13 the fault
injector gave the `product-catalog` Deployment `command: ["invalid-command"]`
(revision 2), and around it scaled the Deployment to 0 and back to 1, which
removed the revision-1 pod. Picked in 004 §12.3.

## How the capture was made

`k8s.json` is k8stools 4.4.0's ITBench import (`results/` recorded on 4.4.1's
replay) of the snapshot's last pull
(pulled 17:59:05, `captured_at` 17:59:09):

```
k8s-capture-state --from-itbench DIR --max-log-lines 10000
```

That serves the raw API objects through fake clients and runs k8stools' own
`capture_state` against them, with the clock pinned and events normalised
(k8stools#30). Logs are OTel SDK records the services exported, not container
stdout: 8 of 21 otel-demo pods have none (frontend, product-catalog, email,
flagd, …), and previous-instance logs are not imported. frontend-proxy's tail
is capped at 10,000 lines, which reaches back to 17:44:32 and so covers the
whole incident.

Once published, the capture and the wiki live in the scenario data repository,
and `k8srca scenario fetch` puts them here (issue #7). The wiki is built from
this capture with `k8srca scenario build-wiki itbench-105-invalid-command`,
with the `live_cluster` and `change_history` sources only (`scenario.yaml`);
its digest is pinned in `truth.yaml` after the build.

## What is in the capture that matters

| | |
|---|---|
| revision 2 (`product-catalog-fcf7564cf`, 296s old) | only change: `containers[product-catalog].command` added, `["invalid-command"]`; image unchanged |
| `product-catalog-fcf7564cf-vjj7l` | CrashLoopBackOff, 5 restarts; last termination exit 128, StartError, `exec: "invalid-command": executable file not found in $PATH`; `started_at`/`ran_for` None (the process never ran) |
| events | Failed (OCI runtime create failed) 6× on vjj7l, 2× on the first new pod `2fvnx`; at 17:54:22 both ReplicaSets "Scaled down … from 1 to 0" and the revision-1 pod `7c7f8b68dc-wzpwm` is killed; new ReplicaSet back to 1 at 17:54:30 |
| Deployment / Service | 0/1 ready, `Available=False` since 17:54:30; `Progressing=True NewReplicaSetAvailable` (stale, misleading); Service `product-catalog` 0 ready, 1 not ready |
| frontend-proxy access log | from 17:54:21.878: `GET /api/products/{id}` 500 ×1392, `GET /api/recommendations` 500 ×269, `POST /api/checkout` 500 ×157, none succeeding; `/`, `/api/cart`, `/api/data` (ads) still 200. Before: no 500s, 3 `503 UC` in 6,780 requests |
| load-generator log | "Checkout completed …" for each of the 157 checkouts answered 500: Locust logs it whatever the status (a trap) |
| checkout log | 716 PlaceOrder → "order placed" before 17:54:21; after, 157 PlaceOrder and no "order placed". All INFO; nothing mentions product-catalog |
| recommendation log | "Receive ListRecommendations" until 17:54:18, then nothing; no error lines |
| downstream | payment, shipping, quote, currency, accounting and fraud-detection all stop logging at 17:54:20–21: no orders reach them |
| callers | frontend, frontend-proxy, checkout, recommendation: revision 1 since install, Running, 0 restarts |
| fraud-detection | OOMKilled once (exit 137) at 17:44:16 after an 18-minute run, restarted at once; ten minutes before the fault and unrelated |
| `503 UC` | 5 in frontend-proxy's tail (3 before, 2 after the change): the background trickle every ITBench snapshot shows |

## Where ITBench's ground truth differs from the capture

Its fault (invalid command, product-catalog) and its core chain (catalog down,
checkout fails, payment/shipping/email/currency/accounting/fraud-detection get
nothing) agree with the capture. Three things do not:

1. **Most of the alerts it lists are not the ones firing.** It lists
   PodCrashLoopBackOff for product-catalog, RequestErrorRate for checkout
   only, and NoRequestsDetected for six downstream services. In the
   snapshot's alert file (17:59:15) only RequestErrorRate is firing, for
   frontend, frontend-proxy, checkout and recommendation -- the four the
   scenario's question names. Only its checkout RequestErrorRate matches.
2. **frontend, frontend-proxy and recommendation are missing from its
   propagation.** Three of the four alerting services are not in the chain;
   the capture shows product pages and recommendations failing as hard as
   checkout.
3. **"Checkout service experiencing connection failures to product-catalog"**
   is not in any log. Checkout logs nothing about product-catalog; its
   failure shows only as orders that start and never complete. `truth.yaml`
   makes quoting such an error a trap.

004 §12.1's table lists 105 under "Empty ground truth"; this snapshot's
`ground_truth.yaml` is not empty.

## Things the replay gets wrong

- k8stools labels exit 128 "application-defined"; here it is the runtime's
  StartError (a trap in `truth.yaml`).
- Replay rebases absolute timestamps (container `finished_at`, log record
  times) to the replay's clock; ages and envoy's own bracketed timestamps are
  unaffected.

## Re-recording

It cannot be re-recorded; the source cluster is gone. If the importer or
k8stools changes, regenerate `k8s.json` from the snapshot's last pull with the
command above, re-review `truth.yaml` and `checklist.yaml` against it, and
rebuild and re-pin the wiki.
