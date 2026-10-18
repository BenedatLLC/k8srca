# Design 009 — Metrics: Prometheus in the cluster, the suite and the agent

**Status:** proposed, 2026-10-18. Nothing here is built. The findings in §2 were
read from the demo cluster on benben the same day.

Companion to [004 §3.3](004-scenario-testing.md#33-prometheus--a-real-server-on-a-captured-block-first-class)
(replaying a captured TSDB block), [007 §4](007-knowledge-representation.md#4-actions-and-availability)
(capabilities: the `prometheus` actions the knowledge already has) and
[008](008-conclusions-under-incomplete-evidence.md) (strength follows what can
be observed).

---

## 1. Why

Every scenario's largest gap is a metric:
- jvm cannot see whether memory reaches the limit, or whether the loop ran
  continuously.
- checkout has one usage sample and no latency.
- itbench-33 cannot measure user impact.

The knowledge base already has the actions for these (`query-memory-series`,
the restart series), each requiring the `prometheus` capability, which no
deployment declares. 008 makes this worth measuring rather than merely nice:
- with metrics, the truth's strength ranges move up;
- a scenario with metrics tests the side of calibration no gate has
  exercised yet: does the agent raise its strength when the evidence allows?

This design covers three parts:
- what the cluster has to provide;
- how the agent reaches it;
- how a scenario captures and replays it.

## 2. What the demo cluster has today

The opentelemetry-demo chart (0.40.7) ships Prometheus 3.9.0, and someone has
added a kube-state-metrics job to its scrape config. Read on 2026-10-18,
through k8stools and Prometheus' own read-only HTTP API:

| Property | Value | Consequence |
| --- | --- | --- |
| Scrape interval | 1m, every job | An 8-second life gets a sample only when one lands in it |
| Retention | 7d (`--storage.tsdb.retention.time=7d`) | The oldest sample was 7.1 days old. jvm's loop is older than that, so its onset stays unobserved |
| Storage | `emptyDir` | Survives a container restart, not a pod rescheduled. The pod is 181 days old, with 14 container restarts |
| Memory limit | 400Mi | 45,500 head series; fine at 1m, tight at 15s |
| kube-state-metrics | kube-system, its own helm release, scraped every 1m | Restart counters, last termination reason and exit code, limits: the series that matter most |
| Duplicate scrape | kube-state-metrics is scraped twice: its own job, and `kubernetes-service-endpoints` through the `prometheus.io/scrape` annotation | Every KSM series appears twice with different `job` labels. A query without `job=` double-counts |
| cAdvisor | scraped through the API server's node proxy | **Pod-level cgroups only: no `container` label.** The docker runtime under minikube does not report per-container cgroups. For single-container pods, pod level is enough |
| node-exporter, pushgateway | absent / no target | Node memory pressure comes from k8stools, not metrics |
| Admin API | off | No TSDB snapshot endpoint. That is right for production, and §5 does not need one |
| Network | ClusterIP only | Nothing outside the cluster can reach it |

**What it says about jvm** (`ad`, the retained 7 days):

| Question | Answer from metrics | Today's capture |
| --- | --- | --- |
| Does memory reach the limit? | `max_over_time(container_memory_working_set_bytes{pod=~"ad-.*"}[7d])` is **300.0 MiB**, the limit exactly; the median is 1.8 MiB, between lives | not observable |
| Is the reason really `Error`? | `kube_pod_container_status_last_terminated_reason` reads **OOMKilled in 3,695 of 10,058 retained minutes, Error in 6,363** | `Error`, at one instant |
| Did it loop continuously? | **8 to 14 restarts in every hour of the 7 days**, with kube-state-metrics up for all 10,080 minutes | "looping since the rollout" was an inference, unsupported |
| When did it start? | before retention began | not observable |
| OOM kill counter | `container_oom_events_total` is **0** at pod level, through thousands of kills | — |

The last row is a trap the knowledge must carry, not a finding. A pod cgroup's
OOM counter does not count kills of the container inside it. A zero there is
not "checked and found false", the meaning `not` has in 007 §3.2. The action
that reads it has to say so, or the counter refutes OOM on every cluster
shaped like this one.

The reason series also turns the jvm scenario's main trap around. With
metrics, the `Error` reason is a property of the instant, not of the
failure. The right answer cites the series and is strong on OOM. Without
metrics it stays the discrepancy the trap asks the answer to flag.

## 3. What a cluster must provide

This is what a deployment needs before it declares `prometheus` (007 §4.1),
written for any site, not only ours:

1. **kube-state-metrics, scraped once.** The restart counter, last terminated
   reason and exit code, resource limits and requests, pod phase. These
   decide the most questions per byte.
2. **cAdvisor, container level where the runtime gives it.** Working set,
   CPU usage, and CPU throttling (`container_cpu_cfs_throttled_periods_total`),
   which checkout's `cpu-throttling` candidate needs. Pod level is a weaker
   fallback; the action says which it got.
3. **A scrape interval short against the failures it must see.** 15s for
   kube-state-metrics and cAdvisor. At 1m, most 8-second lives go unsampled,
   and memory peaks are seen only by luck. jvm's 300.0 MiB was seen because
   there were thousands of lives to be lucky in.
4. **Retention longer than the incidents it explains**, 14 days at least, on
   storage that survives the pod being rescheduled.
5. **The admin API off.** Nothing here needs it.
6. **A read-only path to the HTTP API** for the MCP server (§4).

### 3.1 On benben

A values overlay for the demo's `prometheus` sub-chart:
- `server.retention: 14d`;
- a PVC for `/data`;
- memory limit 1Gi;
- a 15s global scrape interval (the two `-slow` jobs keep their 5m);
- kube-state-metrics scraped once: the static job is cleared, and the
  `prometheus.io/scrape` annotation route stays, so KSM series carry
  `job="kubernetes-service-endpoints"`;
- a NodePort service for §4's tunnel, on 30090.

It lives in [`cluster/prometheus-values.yaml`](../cluster/prometheus-values.yaml),
with the steps in `docs/cluster-setup.md`, "Adding metrics". It was checked
by rendering the chart offline: every setting lands, and the extra flags still
carry no lifecycle or admin API. Applying it replaces the Prometheus pod onto a
new, empty volume, so the week in the old `emptyDir` is lost. A
person applies it with `helm upgrade`, as they installed the chart. k8srca
never runs helm against a cluster (CLAUDE.md), and this is no exception: the
overlay is configuration for the cluster's owner, not something k8srca
applies.

Container-level cAdvisor series on minikube need the containerd runtime
(`minikube start --container-runtime=containerd`). That means rebuilding the
cluster, and every existing capture is of the docker one, so it is not part
of this step. Pod level carries every scenario we have: each failing pod has
one container.

## 4. How the agent reaches it

**A Prometheus MCP server, as a peer of k8stools.** It is one `mcp:` entry with
`provides: [prometheus]`, and the poller passes the capability to the
sandbox, as 007 §4.1 built. Two network paths were considered:

- **Through k8stools and the API server's service proxy.** This keeps "all
  cluster access through k8stools" literal. But it needs `services/proxy`,
  which reaches every HTTP endpoint of every service. RBAC can narrow it to
  GET on one named service, but the read-only property then rests on an RBAC
  rule, not on the tool surface (CLAUDE.md, reason 2). Not adopted.
- **Directly, over a tunnel** (adopted). Prometheus is not the Kubernetes API
  and holds no cluster credential. Prometheus is exposed on a NodePort.
  `cluster.py`'s tunnel, the same mechanism the API server uses, carries it
  to the MCP container. The URL is site topology and goes in `.env`
  (`K8SRCA_PROMETHEUS_URL`), as the API server's tunnel does. Many sites
  already run Prometheus outside the cluster, and they configure only the URL.

**Read-only by construction.** The server exposes query endpoints only:
- `query` and `query_range`;
- `series`, `labels` and `metadata`;
- `targets`, for the scrape interval;
- `status/runtimeinfo`, for retention.

No admin endpoints, no remote write. 001 §8.1's rule for k8stools carries
over.

### 4.1 Ours, in `packages/`

Existing Prometheus MCP servers pass PromQL through and return raw JSON. The
k8stools lesson (its 3.0 triage tools, #13) is that results need their
Kubernetes semantics attached, or the model reads them wrong:
- a pod-level series has no `container` label, and says so;
- a series scraped every 60s says a life shorter than that may have no
  sample;
- a pod cgroup's OOM counter says it does not count a container's kills;
- a range that starts before retention says so, so a missing onset reads as
  unobserved, not as "it started then";
- KSM duplicated under two jobs is reported, not summed.

So it is ours: `packages/promtools`. Like kubewiki, it is a standalone package
that never imports k8srca, with its own tests and its own boundary test (no
Kubernetes client, no admin endpoints). It has two parts:
- **the MCP server:** query tools, plus a few composites shaped like
  k8stools' triage tools. For example, `container_memory_report` gives
  working-set peaks against limit, sample count and interval, per life
  where the counters allow.
- **`promtools capture`:** §5's export.

**Should it be its own repository?** k8stools is. Start in `packages/` while
its interface settles, as kubewiki did. Move it out when another project
wants it.

## 5. Scenarios: capture and replay

004 §3.3 planned a TSDB snapshot. It needs the admin API, which §3 keeps off,
and a snapshot is the whole database: 45,500 series to answer questions about
a few pods. Instead:

- **Capture by query.** `promtools capture` runs, for each metric on an
  allowlist and each scenario namespace, an instant query with a range
  selector at the capture moment: `name{namespace="default"}[<retention>]`.
  A range selector returns raw samples, not interpolated ones, so nothing
  is lost. It uses the read-only API only, and writes OpenMetrics.
- **Same moment as the k8stools capture.** `scenario record` runs both, and
  `truth.yaml` pins the metrics capture's digest and end time beside
  `captured_at`. A truth read against one but not the other is refused, as a
  skill form is (004 §6.3).
- **Stored as a block.** `promtool tsdb create-blocks-from openmetrics` at
  record time. Gorilla compression keeps an allowlisted week to a few MB, where
  OpenMetrics text would be tens.
- **Replayed by a real Prometheus**, in `scenario/sources.py`, beside the
  k8stools replay container: no scraping, the block mounted read-only, and
  the timestamps shifted so the capture ends at run start. That is the same
  rebasing k8stools applies to ages. The scenario's agent gets the promtools
  MCP server pointed at it, so the capability is declared exactly when the
  scenario has metrics.

The allowlist starts with what §2 used and §3 requires:
- kube-state-metrics: restarts, last terminated reason and exit code,
  limits and requests, pod phase;
- cAdvisor: working set, CPU usage, CPU throttling;
- `up`, so a scrape gap reads as a gap.

The rest of the database is not captured. A query for something outside the
allowlist returns nothing at replay. To keep "no data" from reading as "not
there", the replay's `metadata` lists only allowlisted names.

**Scenarios:** jvm stays as it is. `jvm-oom-with-metrics` is a new recording,
taken with the §3.1 overlay applied, with its own truth:
- cause strength: strong;
- memory-leak and workload-spike ruled out on data;
- `reason-says-error` rewritten for the reason series;
- a new trap, the pod-level OOM counter at zero.

checkout follows once throttling and latency are captured.

## 6. The knowledge

The `prometheus` actions exist, but nothing has run them. They need:
- **`call` resolved against promtools' tools**, as k8stools' are.
- **`limits` text that carries §2's traps:** pod-level series, the sample
  interval against lifetime, the pod-level OOM counter, retention against
  onset.
- **`scope: history` (008 §4.1) for what a series really covers,** and only
  that. A restart series covers back to the start of retention, not the
  pod's whole life. So `onset_observed` comes from it only when the first
  restart falls inside retention, and jvm's does not.

## 7. Staging

| Step | What | Gate |
| --- | --- | --- |
| 1 | §3.1 overlay on benben, applied by its owner (*written*) | `promtools` (or `curl`) reads 15s KSM and cAdvisor series, 14d retention configured |
| 2 | `packages/promtools`: the read-only server and capture | its own tests; capture of benben round-trips through `promtool` and a replay Prometheus, and the same queries give the same answers |
| 3 | Replay in `scenario/sources.py`; `scenario record` captures both; truth pins both | hermetic tests with a tiny generated block |
| 4 | `jvm-oom-with-metrics` recorded, truth written | `holdout doctor`-style lint over the truth (006) |
| 5 | The KB's `prometheus` actions run (§6); n=10 on the new scenario | calibration on the new truth: strong, where jvm alone allows moderate |

Steps 1–2 do not touch the skill. They can run while 008's steps 3–4 settle
what strength means. Step 5 waits for 008's step 4.

## 8. Open questions

1. **How much to capture.** A week of an allowlist is a few MB as a block.
   Two weeks at 15s is four times that per series. Whether blocks go in git
   or in LFS depends on the allowlist that survives step 5.
2. **Latency for checkout.** Its spans reach Prometheus as OTLP metrics
   (`traces_span_metrics_duration_milliseconds_bucket`, the demo's span
   metrics). Whether those carry checkout's server latency at a useful
   resolution has not been checked.
3. **containerd.** Container-level series would let multi-container pods be
   answered at container level. That means rebuilding the cluster; worth it
   when a scenario needs it.
4. **Production.** A site's Prometheus may need auth (a bearer token, mTLS).
   That makes promtools a credential holder, the k8stools pattern again: one
   container holds it, and nothing else does.
