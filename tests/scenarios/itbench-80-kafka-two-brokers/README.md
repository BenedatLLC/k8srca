# itbench-80-kafka-two-brokers

**Discriminates:** ranking two causes on one path. Kafka's two replicas break
checkout's writes from the start; a Chaos Mesh partition joins 20 minutes
later. The newer, louder fault is not the one users feel. It also tests
severity: checkout is up, but checking out fails.

## Where it came from

[ITBench-Lite](https://huggingface.co/datasets/ibm-research/ITBench-Lite)
(Apache 2.0), SRE snapshot Scenario-80. The OTel demo 2.1.3 (chart 0.38.6)
on a 5-node kops cluster in AWS, namespace `otel-demo`, installed about
17:22:33. ITBench's fault is a Chaos Mesh `Schedule` that spawns a
`NetworkChaos` partition between checkout and Kafka every minute from
17:54:00, each lasting 55 seconds.

Only in this snapshot, Kafka's Deployment runs `replicas: 2` (004 §12.3).
ITBench doesn't mention it.

## How the capture was made

`k8s.json` comes from k8stools 4.4.0's ITBench import path (k8stools#30):

```bash
k8s-capture-state --from-itbench DIR --max-log-lines 10000
```

It uses the last of the snapshot's six pulls, 18:02:44, so `captured_at` is
18:02:48. Notes on it:
- **The log cap is 10,000 lines per container, not the default, on purpose.**
  Checkout's 6,486 lines then fit whole, back to its first record at
  17:33:41, so the capture shows the write failures predate the partition.
  frontend-proxy, the load generator, product-catalog and both Kafka pods hit
  the cap. Their logs start at 17:45 (frontend-proxy), 17:48 (skk8d) and
  17:49 (xclnk).
- Logs are OTel SDK records, not container stdout. frontend, accounting,
  fraud-detection, email, flagd, image-provider, otel-collector, postgresql
  and valkey-cart have none.
- There are no previous-instance logs. That costs ad's killed instance.
- Custom resource definitions are recorded, but not their instances. Events
  are recorded, including Chaos Mesh's, so the partition shows through its
  events, not its object.
- There are no metrics-server readings, so usage is unknown.

With the wiki, the capture and the skill go to the scenario data repository
when published (issue #7). `k8srca scenario fetch` puts them here.
`scenario.yaml` pins no digest yet. The wiki is not built yet. 004 §12.3
says to build its skill from the last pull before the partition (17:54:00),
because no pull predates the Kafka replicas.

## What is in the capture that matters

Times below are the snapshot's. The replay shifts them to "now", so the agent
sees them relative to capture.

| | |
|---|---|
| Deployment `kafka` | 2/2, one ReplicaSet (revision 1, desired 2). Both pods are 40m15s old, ready, with no restarts. `get_namespace_health` lists it under healthy |
| Kafka env (both pods) | `KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092`, `KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka:9093`. Both addresses are the Service |
| Service `kafka` | ClusterIP 100.71.78.255, ports 9092 and 9093, both pods ready endpoints |
| Kafka logs | Both log as `QuorumController id=1` and write their own KRaft snapshots, at different offsets. skk8d logs `Sent auto-creation request for Set(orders)` within a millisecond of checkout's metadata requests. xclnk's controller answers `orders ... TOPIC_ALREADY_EXISTS` 0.4 ms later each time. The reverse happens for `__consumer_offsets`, about 620 times a minute. Both log `STALE_BROKER_EPOCH` throughout |
| checkout log, 17:33:41–17:54:00 | 604 orders and 604 failed writes. Every ~11 s a metadata retry finds `orders` leaderless and ends in `Request was for a topic or partition that does not exist on this broker`. The rest is `circuit breaker is open`. No successful write is logged anywhere in the log |
| checkout log, from 17:54:37 | `i/o timeout`, `broken pipe`, and `run out of available brokers` once (17:57:41). The errors come in bursts (0 to 102 a minute), close to one per order overall |
| checkout pod | Running, ready, 0 restarts, endpoint ready. It logs `payment went through` / `order placed` for 887 orders, about 30 a minute, to the last second |
| frontend-proxy log (17:45:17–18:02:48) | 513 of 513 `POST /api/checkout` end in `504 UT` after 15.0 s, about 30 a minute, at the same rate before and after 17:54. `GET /api/data` returns 500 17:52:12–17:59:44 and 504 until 18:00:20. 7 `503 UC` in 10,000 lines |
| chaos-mesh events | `Schedule/otel-demo-checkout-kafka-network-partition` spawns a NetworkChaos every minute from 17:54:00. Each one is applied to checkout and both Kafka pods, and reports `TimeUp` 55 s later. The last was spawned at 18:02:00, so one is active at capture |
| otel-demo events | 51 `PodNetworkChaos` `Updated` events for the same three pods, and ad's second start |
| ad | OOMKilled (exit 137) at 17:52:12 after 29m37s. Its new instance logs `Ad service started` at 17:59:04. Its memory limit is 450Mi, request 225Mi |
| default log tail | `get_logs_for_pod_and_container` returns the last 1,000 lines. For checkout that starts at 17:57:50, after the partition: 9 i/o timeouts and 3 `does not exist`. The agent has to read further back to see the failures predate it |

## Where ITBench's ground truth is wrong or incomplete

Its `ground_truth.yaml` names one root cause, the NetworkChaos. It says
checkout "continues processing orders" and that order events don't reach
Kafka. The capture differs in three ways:

1. **It misses the cause users feel.** Kafka's two replicas break every
   order's write from checkout's first record, 20 minutes before the first
   partition. The 504s at the proxy run at the same rate before and after it.
2. **Its user impact is too mild.** Checkout does keep placing orders, but
   every checkout request in the proxy's window times out at 15 s. Users are
   charged for orders they're told failed.
3. **It misses ad.** ad's OOM kill and seven-minute restart cause the
   `/api/data` errors from 17:52 to 18:00. Those are probably part of the
   `RequestErrorRate` the alerts report. frontend's alert went active at
   17:53:31, frontend-proxy's at 17:34:31. The alert file is not in the
   capture and the agent never sees it.

ITBench's alerts in `ground_truth.yaml` (`KafkaConnectionClosureRate`,
`RequestLatency` on checkout) aren't the ones its alert file shows firing.
The question uses the firing ones.

So the truth here ranks the split brokers most likely and the partition as a
second fault, not ruled out. 004 §12.3 planned the partition as invisible.
It isn't: its events are in the capture.

## Re-recording

The source cluster is gone, so it can't be re-recorded. If k8stools' import
changes, regenerate `k8s.json` from the ITBench snapshot (the 18:02:44 pull,
`--max-log-lines 10000`) and re-review `truth.yaml` and `checklist.yaml`.
The log windows above move with the cap. Then update `captured_at` and the
digests.
