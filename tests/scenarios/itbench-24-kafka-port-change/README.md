# itbench-24-kafka-port-change

**Discriminates:** reading a caller's log. checkout's log names Kafka in
every error line, but the fault is checkout's own setting, and Kafka is
healthy. It is the log-reading half of a pair with
`itbench-80-kafka-two-brokers` (004 §12.3).

## Where it came from

[ITBench-Lite](https://huggingface.co/datasets/ibm-research/ITBench-Lite)
(Apache 2.0), SRE Scenario-24. The OTel demo 2.1.3 (chart 0.38.6) on a 5-node
kops cluster in AWS, namespace `otel-demo`. At about 17:49:57 the fault
injector patched the `checkout` Deployment's `KAFKA_ADDR` from `kafka:9092` to
`kafka:9999`, which rolled it to revision 2.

## How the capture was made

`k8s-capture-state --from-itbench DIR --max-log-lines 10000` (k8stools 4.4.0,
k8stools#30), on the snapshot's **last pull**, 17:57:07 (`captured_at`
17:57:11, about 7 minutes into the incident). One pull, not a merge, with
events and logs cut at it (004 §12.1).

- **Logs are OTel SDK records, not container stdout/stderr.** frontend, email,
  flagd, image-provider, otel-collector, postgresql and valkey-cart have none.
  Whatever checkout printed to stderr when it exited is not here.
- **Previous-instance logs aren't imported.** checkout's first instance (which
  also exited with code 2) shows as "not recorded".
- frontend-proxy and load-generator hit the 10,000-line cap; their logs start
  at 17:42:43 and 17:37:56, both before the fault.
- No metrics, HPAs, Jobs or NetworkPolicies are recorded; the tools say so.

The capture, and the wiki once `k8srca scenario build-wiki` has built it, live
in the scenario data repository (issue #7) once published with
`k8srca scenario publish itbench-24-kafka-port-change`; `k8srca scenario
fetch` puts them here. The wiki is built from the capture with only the
`live_cluster` and `change_history` sources: our charts and
`docs/architecture/otel-demo` notes describe our install, not ITBench's.
`truth.yaml` pins its digest after that build and a re-read.

## What is in the capture that matters

Times are relative to `captured_at`, 17:57:11.

| | |
|---|---|
| checkout revision 2 | 434s old (17:49:57); the only change is `containers[checkout].env[KAFKA_ADDR]`. Values aren't shown by `get_workload_history`; the pod spec shows `kafka:9999` |
| kafka Service | ClusterIP 100.66.162.182, ports 9092 and 9093; Kafka's listeners are `PLAINTEXT://:9092,CONTROLLER://:9093`; accounting and fraud-detection use `kafka:9092` |
| `wait-for-kafka` | checkout's init container runs `nc -z kafka 9092`; it completed at once at 17:49:59 |
| checkout log (2nd instance) | 17:53:36 to 17:55:37: four `Failed to connect to broker kafka:9999: dial tcp 100.66.162.182:9999: i/o timeout` (INFO), then `ERROR kafka: client has run out of available brokers to talk to`, then `service config: ... kafkaBrokerSvcAddr:kafka:9999`, then `starting to listen on tcp: "[::]:8080"`. Nothing after |
| checkout container | `restart_count` 1; both instances Terminated, exit 2, reason Error, after 216s and 206s; the second finished 9s before capture. BackOff event 8s before. No probes; limit 512Mi |
| old pod | `checkout-5dccddf8bb-9d9c8` deleted 431s before capture, a second after the new container started; revision 1's ReplicaSet is at 0 |
| checkout Deployment | ready 0/1, `Available=False` (MinimumReplicasUnavailable) since 8s; `Progressing=True` "ReplicaSet checkout-849d65649b has successfully progressed" |
| checkout Service | 0 ready, 1 not ready endpoint |
| frontend-proxy log | `POST /api/checkout`: all 200 until 17:49:59, every one 500 from 17:50:02 (259 of 259, ~35 a minute). Nine `503 UC` on other routes in 10,000 requests, eight of them before the change |
| load-generator log | "Checkout completed for user ..." for each of those 259 failed requests |
| payment, shipping, quote, currency | ~40 orders a minute until 17:49:59; since then one payment charge at 17:53:35 and one at 17:57:02, each within a second of a checkout exit |
| accounting, fraud-detection | logs stop at 17:49:59; both 1/1, 0 restarts |
| kafka | 1/1 Running, 0 restarts, revision 1 (40 minutes); no WARN or ERROR in its log |
| bystanders | a `VolumeFailedDelete` warning on a PV in `default`, 18 minutes old; old kube-system restarts (4.6 days). Neither touches otel-demo |

## Where ITBench's ground truth is wrong

It names the right entity and the right fault, `KAFKA_ADDR` on port 9999.
Its propagation chain is where it goes wrong:

1. **"Checkout service cannot publish order events to Kafka, but continues
   processing orders."** The capture shows checkout down: both instances
   exited with code 2, the old pod is gone, the Deployment is
   `Available=False`, and every checkout request since the change returned
   500. Only two orders reached payment in seven minutes. An agent that
   follows ITBench here would understate the impact as delayed accounting.
2. **The alerts it lists** (`KafkaConnectionClosureRate`, `RequestLatency` on
   checkout) aren't the ones the snapshot recorded firing: `RequestErrorRate`
   for frontend (active from 17:51:19) and for frontend-proxy (active from
   17:41:19, eight minutes **before** the fault, from the background 503s).
   The scenario's question names those two.

`truth.yaml` is written against the capture, and "checkout is still
serving" is a trap.

## Re-recording

It cannot be re-recorded; the source cluster is gone. If k8stools' import
changes, regenerate `k8s.json` from the ITBench snapshot (the last pull,
17:57:07) with the command above, re-review `truth.yaml` against it, and
update `capture.captured_at` and the scenario's digest. The last pull before
the fault is 17:47:07, if a pre-fault architecture skill is ever wanted.
