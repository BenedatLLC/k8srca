# itbench-31-network-policy-decoy

**Discriminates:** restraint with a decoy. A NetworkPolicy named
`frontend-block-all-ports`, minutes old, selects the alerting proxy's
upstream, and it blocks nothing. The alert is the install's background
trickle of 503s, at the same rate before the policy as after.
`nothing-is-wrong` tests restraint with no decoy; this one has a story ready
to adopt, and the tool that refutes it.

## Where it came from

[ITBench-Lite](https://huggingface.co/datasets/ibm-research/ITBench-Lite)
(Apache 2.0), SRE Scenario-31. The OTel demo 2.1.3 (chart 0.38.6) on the same
5-node kops cluster in AWS (the same setup as the other ITBench imports,
each on its own nodes), with Cilium as the CNI,
namespace `otel-demo`. The fault injector created a NetworkPolicy at
17:51:21:

```yaml
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: frontend
      app.kubernetes.io/name: frontend
      opentelemetry.io/name: frontend
  policyTypes: [Ingress]
  ingress: [{}]
```

`ingress: [{}]` is one rule with no `from` and no `ports`, which matches all
traffic: it allows all ingress. A deny-all would be `ingress: []` (or no
`ingress` key). So the fault had no effect.

## How the capture was made

`k8s.json` is k8stools 4.4.1's ITBench import of the snapshot's last pull:

```
k8s-capture-state --from-itbench DIR --max-log-lines 10000
```

with DIR the Scenario-31 snapshot. It picks the last of six pulls
(captured_at 17:57:16), 5m55s after the policy.

- Logs are OTel SDK records, not container stdout. frontend, email, flagd,
  image-provider, otel-collector, postgresql and valkey-cart have none.
- `--max-log-lines 10000` caps frontend-proxy, load-generator and
  product-catalog; frontend-proxy's log starts at 17:42:19, nine minutes
  before the policy. A default `get_logs_for_pod_and_container` call returns
  the last 1000 lines (about 90 seconds, all after the policy). Comparing
  before and after needs `tail` of about 5,800 or more (or all 10,000): the
  last 503 before the policy is at 17:48:41, and there is none from there to
  17:54:11, so any shorter tail shows 503s only after the policy (tail=5000
  starts at 17:49:44 and has none before it). This is the scenario's
  sharpest trap.
- Replayed log lines carry a shifted replay-clock prefix and Envoy's
  original `[2025-12-15T…]` time. Placing the policy (age 5m55s) on Envoy's
  timeline means anchoring on the last log line, which is within seconds of
  capture.
- No metrics-server readings and no Prometheus capture.
- The NetworkPolicy is in the capture (k8stools 4.4's `networkpolicies`), and
  `get_networkpolicy_summaries` reports it with `ingress_effect: allows all
  ingress` and the one pod it selects. No event records its creation.

The capture and the wiki live in the scenario data repository
(`BenedatLLC/k8srca-scenario-data`, issue #7) after `k8srca scenario publish`;
`k8srca scenario fetch` puts them here. `scenario build-wiki` built the wiki
from this capture with the `live_cluster` and `change_history` sources only
(004 §12.1), and `truth.yaml` pins its digest.

## What is in the capture that matters

| | |
|---|---|
| the policy | `frontend-block-all-ports`, 355s old, Ingress, one empty rule; `ingress_effect: allows all ingress`; selects `frontend-675fd7b5c5-8qrfh` |
| frontend-proxy log | 10,000 lines, 17:42:19 to 17:57:16, every one routed to frontend: 9168 × 200, 817 × 308, 15 × `503 UC` (`upstream_reset_before_response_started{connection_termination}`). About 670 requests a minute throughout |
| the 503s | 9 in the nine minutes before the policy, 6 in the six after; on /api/products, /api/recommendations, /, /api/cart and /api/data/; the last three at 17:56:49–50 |
| frontend | Running, 1/1 ready, 0 restarts, revision 1 for 40 minutes; no log captured |
| otel-demo | every Deployment available; no events; no error lines in any captured log but the proxy's 503s |
| bystanders | fraud-detection OOMKilled once ~35 minutes before capture, after a 219s life, running since; flagd rollout restart (revision 2) ~38 minutes before; two FailedScheduling events in `data-recorders` ~22 minutes before, from a volume-claim update conflict, both pods started within seconds |
| traffic | all from the load generator (`python-requests`), except a once-a-minute `GET /` from otel-collector (`Go-http-client`), always 200 |

## The alert

The snapshot's alert file has `RequestErrorRate` firing for frontend-proxy
only, active from 17:57:13 by Prometheus's clock, about 20 seconds after the
burst of three 503s at 17:56:49–50 (the alert file itself is stamped
17:56:55, so the clocks differ by some seconds). Its description says the
rate "is above 0 (current: 0.0333)", so the rule apparently fires on any
error rate above zero, and the trickle fires it by itself. 0.0333 is not a
ratio (the log's is 0.0015); it reads as errors per second, about 2 a
minute. In Scenario-24's snapshot the same alert was
active eight minutes before that fault, for the same reason. The agent has no
alert tool: the question names the alert, and the rule is not in the capture.

The raw snapshot, which the agent cannot see, confirms it over a longer
window (17:28 to 18:01): 22 `503 UC` in the 23 minutes before the policy and
9 in the 10 after, about 0.9 a minute in both, with latency unchanged.

## Where ITBench's ground truth differs from the capture

Its `ground_truth.yaml` says the policy "blocks all ingress traffic to
frontend pods", that frontend is "completely unavailable", and that
frontend-proxy cannot reach it. None of that happened:

1. **The policy allows all ingress.** An empty rule matches everything.
2. **frontend is serving.** The proxy routes about 670 requests a minute to
   it after the policy, as before, and gets 200s for all but the trickle.
3. **The alert is the background.** The 503s are the `503 UC` trickle in
   every snapshot of this install (004 §12.3), at an unchanged rate.

## Re-recording

It cannot be re-recorded; the source cluster is gone. If the importer or
k8stools changes, regenerate `k8s.json` from the ITBench snapshot (the last
pull) with the command above, then re-review `truth.yaml` (its
`captured_at`, counts and ages) and `checklist.yaml`, rebuild the wiki and
re-pin its digest.
