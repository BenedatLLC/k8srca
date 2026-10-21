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

**Before the overlay replaced the volume,** the retained week was exported
raw (12.3M samples over about 1,600 allowlisted series), with a k8stools
capture at the same moment. That was 2026-10-18T19:42:36Z, kept as
`jvm-oom-with-metrics`. Built into blocks by `promtool` and served by a stock
Prometheus, it answered six comparison queries identically to the live server.
That is §5's capture done once by hand. The heavy export queries likely cost
the old 400Mi Prometheus one restart: its probes timed out under them.

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
- kube-state-metrics scraped once. The install's own static job stays (it is
  the release's only user value), and the default `kubernetes-service-endpoints`
  job drops KSM ahead of its annotation rule. KSM series keep
  `job="kube-state-metrics"`, as in the exported week;
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
| 1 | §3.1 overlay on benben, applied by its owner (*applied 2026-10-18*: retention 2w or 15GiB, every job at 15s, one KSM job, admin API refused, `/-/reload` 403) | `promtools` (or `curl`) reads 15s KSM and cAdvisor series, 14d retention configured |
| 2 | `packages/promtools`: the read-only server and capture (*started 2026-10-18*: client, notes, nine tools, capture; 46 tests; smoke-tested on benben) | its own tests; capture of benben round-trips through `promtool` and a replay Prometheus, and the same queries give the same answers |
| 3 | Replay in `scenario/sources.py`; `scenario record` captures both; truth pins both (*built 2026-10-19*, §7.2) | hermetic tests with a tiny generated block |
| 4 | `jvm-oom-with-metrics` recorded, truth written (*done 2026-10-19 with ~31h of metrics*, §7.3; a full week is issue #3) | `holdout doctor`-style lint over the truth (006) |
| 5 | The KB's `prometheus` actions run (§6); n=10 on the new scenario | calibration on the new truth: strong, where jvm alone allows moderate |

Steps 1–2 do not touch the skill. They can run while 008's steps 3–4 settle
what strength means. Step 5 waits for 008's step 4.

### 7.1 promtools, first cut (2026-10-18)

- **`client.py`** sends GET only, to nine named query endpoints. A path off the
  list is refused before anything is sent. The boundary test parses the package
  for HTTP calls outside the client, write methods, and strings naming admin,
  lifecycle or write endpoints.
- **Tools:** seven passthrough tools, plus `memory_report` and `restart_report`.
  Every result carries `notes` (`notes.py`) for the §2 cases. On benben,
  `memory_report` gives `ad` 299.7 of 300 MiB at pod level, and says the pod's
  OOM counter of 0 is not evidence. `restart_report` gives 6,889 restarts at
  the first retained sample, so the onset is not observed.
- **Per-hour figures need two hours of history.** With eleven minutes retained
  after the overlay, a partial hour read as "no restarts". It now gives none,
  and says why.
- **Capture** reads six-hour pieces, drops node labels (refusing a drop that
  would merge series), and streams gzipped OpenMetrics family by family. 30
  minutes of benben came to 868 series and 46,695 samples. The allowlist plus
  dropped node labels halves the series count of the hand export, and the block
  is about 3.3 bytes a sample.
- **Size, for §8.1, measured on the exported week** (1m scrapes, built as
  blocks):

  | family | samples | 2h blocks | one block | one block, zeros trimmed |
  | --- | --- | --- | --- | --- |
  | span metrics (3; not on the allowlist) | 3.68M | 20.9 MB | 15.7 MB | — |
  | cAdvisor memory and CPU | 1.12M | 6.7 MB | | |
  | `kube_pod_status_phase` | 1.37M | 2.3 MB | 1.10 MB | 0.23 MB |
  | `kube_pod_status_ready`, node condition | 0.95M | 1.8 MB | 0.77 MB (ready) | 0.27 MB |
  | the three reason families | 0.33M | 1.1 MB | | |

  My first estimate, 115 MB for a 15s week, scaled bytes per sample from a
  14-minute block, where index overhead dominates. It was wrong. Two things
  matter, and both are now settled:
  - **The one-hot zeros.** Phase gives each pod one series per phase, all but
    one reading 0. On the allowlist, phase, ready and node condition were more
    than half the bytes. The capture drops their 0 samples (`ONE_HOT`). Exactly
    one state holds at a time, so nothing is lost. That cuts phase by 79% and
    ready by 65%. The reason families need no trim: kube-state-metrics emits
    only the current reason, and they hold no zeros.
  - **Block length.** `promtool`'s default 2h blocks repeat the index 84 times
    in a week. `--max-block-duration=168h` halves phase and cuts the span
    metrics by a quarter. Step 3's replay build uses it.

  The allowlist's week at 1m is then a few MB. A 15s week is the measurement
  to make once benben has one (2026-10-25): constant series cost almost
  nothing more at 15s, and changing ones about four times as much.
- **Packaging:** a workspace member and a dev dependency of k8srca, not a
  runtime one. k8srca reaches it over MCP, as its own container, so it stays
  out of the sandbox image.

### 7.2 Replay, record and pin (2026-10-19)

- **The metrics server is declared, then switched on.** `k8srca.yaml` has a
  `metrics:` block with the promtools server, its tool groups and its agent
  wiring:
  - the coordinator gets `memory_report` and `restart_report`;
  - the investigator gets everything.

  It is off (`enabled: false`). `K8SRCA_METRICS=1` turns it on. The scenario
  runner sets that for a scenario with a metrics capture, both for its own
  sync and for the poller, which forwards it into the sandbox. The sandbox's
  config is baked into its image. A scenario without metrics, or a deployment
  without Prometheus, declares no `prometheus` capability. Production turns it
  on with `enabled: true` once promtools runs there.
- **The replay** (`sources.metrics_replay`):
  1. Shift the capture so it ends when the replay starts, which is the
     rebasing k8stools applies.
  2. Build one block with the Prometheus image's `promtool`
     (`--max-block-duration=168h`).
  3. Serve it from a Prometheus whose configuration has no scrape jobs.
  4. Put promtools in front, built from `packages/promtools` as
     `k8srca/promtools:<source digest>` (`docker/Dockerfile.promtools`),
     aliased `promtools` on the scenario network.
- **Three fidelity fixes the first end-to-end run found:**
  - **Time-valued samples:** last-terminated time, start times, and the
    lowest retained timestamp. They are shifted in value as well as in
    sample time. Otherwise "last terminated at" reads a year before now.
  - **The retention edge is the capture's.** The live Prometheus kept 14
    days and a capture takes 7. The replay raises
    `prometheus_tsdb_lowest_timestamp_seconds` to the capture's start.
    Otherwise promtools reads an uncaptured week as a week without restarts,
    and could call an onset observed.
  - **Scrape intervals.** A replay has no targets, so `promtools serve`
    takes `--scrape-interval JOB=DURATION` from the capture's manifest.
    Without it every series read as 60s.
- **Stored as gzipped OpenMetrics, not as a block.** §5 planned a block, but a
  block cannot be shifted, and the shift happens at replay. Building takes
  seconds at this size. Whether a 15s week needs block storage, and so a dump
  and rebuild at replay, is step 4's to measure (§8.1).
- **Record and pin.** `scenario record` also captures metrics when
  `K8SRCA_PROMETHEUS_URL` is set. It needs `--namespace`, and its `end` is the
  k8stools capture's own `captured_at`, so the two are one moment. `truth.yaml`
  pins `capture.metrics: {end, digest}`. The runner refuses:
  - a truth not written against the metrics;
  - a capture changed since it was;
  - a metrics end that is not `captured_at`;
  - a pin with no source.
- **Tests:** 22 hermetic ones, which check the commands as built, never run
  docker, and generate a capture. `K8SRCA_TEST_DOCKER=1` adds one that builds a
  real block from a tiny capture and queries it at the shifted time. An
  end-to-end run of the whole replay, with promtools answering
  `restart_report` over MCP, was done by hand.

### 7.3 jvm-oom-with-metrics, first recording (2026-10-19)

- **Recorded fresh.** The 2026-10-18 recording predates the oldest retained
  sample: the volume was replaced when the overlay went on. So this is a new
  capture with about 31h of 15s metrics: 696 series and 4.8M samples. A full
  week is issue #3, on or after 2026-10-25.
- **What the metrics changed in the truth:**
  - **The cause is strong, and only strong.** The working set is at the 300Mi
    limit from the first sample of every life and flat until the kill. Every
    termination in the reason series is OOMKilled.
  - **memory-leak is ruled out on data,** since nothing climbs within a life.
    workload-spike is ruled out on a steady 9–17 kills an hour, with weak
    accepted for the missing request rate (a gap).
  - **jvm's two traps tied to its moment do not carry over.** The reason reads
    OOMKilled throughout, and the previous log is present.
  - **New traps:** the pod-level OOM counter at zero, and fraud-detection's
    sampled peak below its limit, from 2s lives scraped every 15s.
  - **Lives are not jvm's seconds.** The running series shows lives from under
    a scrape to several minutes, so the cadence trap is rewritten.
- **The replay reports the live retention** (`2w`, from the manifest), not its
  own. Reading the capture through promtools found that one too.
- **Lint** (`holdout.lint`): clean but for the hold-out-only rule that a
  scenario declare its own architecture sources. Ours inherit k8srca.yaml's,
  as jvm does.
- **Size.** 31h is 20.7 MB as gzipped OpenMetrics and 4.2 MB as a compressed
  block (17s to build), so a week is about 100 MB against about 20 MB. The
  repository's `.git` is 25 MB. Hence §7.4.
- **Hours are counted back from the report's end.** `restart_report` used to
  count per-hour figures with a subquery, which aligns its buckets to the
  clock. A replay is shifted by a different amount each run, so the same
  samples gave 9–16 one run and 8–14 the next. It now uses a range query
  aligned to whole hours back from `end`, and two replays agree.

### 7.4 Stored as blocks, in a data repository (2026-10-19)

- **Where.** A metrics capture is a TSDB block, packed as a tarball whose bytes
  depend only on the block's files: sorted, no times or owners, no name in the
  gzip header. It lives in `BenedatLLC/k8srca-scenario-data` (public, like
  this repository) as `metrics/<digest>.tar.gz`, outside this repository's
  history. A re-recorded capture would otherwise stay in that history for
  good.
- **What stays here.** The scenario commits `metrics/manifest.json`, the
  window, retention and intervals. `truth.yaml` pins the tarball's digest. The
  tarball's own path, `metrics/block.tar.gz`, is gitignored.
- **No LFS.** Nothing clones the data repository to run a scenario. The runner
  fetches one file by digest from its raw URL, or from a local checkout named
  by `K8SRCA_SCENARIO_DATA`, and checks the digest. GitHub's per-file limit is
  100 MB, against about 20 MB for a week.
- **At replay.** A block cannot be shifted. So it is dumped back to OpenMetrics
  once per digest (`promtool tsdb dump-openmetrics`, about 16s at 31h), into
  `.k8srca/scenario/metrics-cache/`, and each run shifts and rebuilds from
  that, as before. Recording seeds the cache with the text it built the block
  from.
- **`k8srca scenario publish <id>`** commits the block to a checkout of the
  data repository under its digest (`--push` to push).
- **Known labels only.** `promtools capture` refuses a label name outside
  `LABELS` (plus `app_kubernetes_io_*`), with `--allow-label` to admit one
  once checked. The data is published unredacted, and the k8stools capture's
  redaction has no counterpart here.

### 7.5 Step 5, first measurement (2026-10-20)

The KB's `prometheus` actions now call `memory_report`, `restart_report` and
`query_range`, with §2's traps as their limits. The full arm was n=10 on
`jvm-oom-with-metrics` with the reviewer on, plus a regression check at n=5 on
`jvm-oom-on-startup`:

| scenario | cause | evidence | standing | calibration | traps | gaps |
| --- | --- | --- | --- | --- | --- | --- |
| with-metrics (n=10) | 9 | 1 | 2 | 6 | 7 | 4 |
| jvm, this arm (n=5) | 4 | 1 | 4 | 4 | 5 | 5 |
| jvm, last arm (n=5) | 5 | 2 | 5 | 4 | 4 | 5 |

jvm moved by 1 at most: no large effect. The gate was not met: calibration is
6/10, not strong. Every run called both reports, and none called
`query_range`. What failed:

- **The leak is left open.** The truth rules memory-leak out on the series:
  within each life the working set starts at the limit and stays flat. The
  reports give the peak and median, not the shape within a life, and the KB has
  no ruling-out on it. `memory_grows_within_life` is only in a `for`. So the
  ruling-out rests on "never reaches serving", from one 8s life's logs. The
  reviewer rightly rejects that in runs 2 and 9, which then reopen the leak
  and lower the cause to moderate.
- **"7 days".** The reports say `window: 7d` when about 31 hours are retained.
  Answers repeat it ("every hour of the week"), and the reviewer flags it.
- **Gaps.** Six runs never say that load cannot be measured.

**Second arm.** `memory_report` gained `lives`. Each life runs from
kube-state-metrics' start time to its recorded termination, and the report
gives its lifetime and whether the working set climbs within it. On the
capture: 327 lives, lasting 3s to 464s. 214 have two or more samples, and 182
of those start at the limit. The other 32 reach it within 31s, which is
startup. Both reports now say the window they cover (30.9h of the 7d asked
for). They read the retention edge and the limits over the window, since an
instant read of a replay's captured series is stale five minutes in. In the
KB, `not memory_grows_within_life` rules out a leak and a spike.

| scenario | cause | evidence | standing | calibration | traps | gaps |
| --- | --- | --- | --- | --- | --- | --- |
| with-metrics, second arm (n=10) | 10 | 9 | 2 | 7 | 8 | 6 |
| jvm, second arm (n=10) | 10 | 2 | 9 | 9 | 10 | 10 |

Every dimension rose on with-metrics. Evidence went from 1 to 9: the window
is now stated correctly. jvm is level: its first five had evidence 0/5, and
extended to ten it is 2/10, the usual slips (continuity since the rollout,
the count of retained lives). Merged. The gate is still not met. Calibration
is 7/10, and standing stays at 2/10, now because application-crash goes
unnamed rather than because the leak is left open. One FAIL came from the
grader: `numeric_claims` refused "327 restarts over the 30.9h retained",
because the capture holds only lifetime totals. With a metrics capture, that
check now only advises.

**Third arm: application-crash unnamed.** In 8 of 10 second-arm sessions the
agent ran `lookup OOMKilled` and `open OOMKilled`; every jvm session started
from `CrashLoopBackOff`. This capture's last reason reads OOMKilled where
jvm's reads Error. `container-oom-killed`'s candidates are exactly oom-kill's
sub-causes, so oom-kill's rivals never came up and nothing owed them a place.
`kb_query.py` now opens a symptom whose candidates are one claim's sub-causes
as that claim's symptom. At n=5 (scaled second arm in brackets): cause 5 (5),
evidence 2 (4.5), standing 4 (1), calibration 4 (3.5), traps 5 (4), gaps 0
(3). Standing is a result. But evidence and gaps fell by 2 or more, so it was
not merged. The owed list grew from 1–2 claims to 6–9, the restart family
jvm also owes. The revision turn took answers from 4–6 KB to 7–9 KB, with
more slips in them. Every run ruled the spike out on memory shape, and none said request
rate cannot be measured.

**Fourth arm: owe less.** `kb_query.py` and the reviewer now owe a claim only
if the facts decided it or the authored knowledge names it, plus the decided
sub-causes of a supported claim. A mined candidate nothing decided is offered,
not owed. That cut a typical `open OOMKilled` from 17 owed claims to 8. At
n=5, with the third arm and the scaled second arm in brackets: cause 5 (5, 5),
evidence 1 (2, 4.5), standing 4 (4, 1), calibration 3 (4, 3.5), traps 4 (5,
4), gaps 4 (0, 3). jvm at n=5 against its scaled n=10: 5/1/5/5/5/5 (5/1/4.5/
4.5/5/5). Gaps came back and standing held, but evidence stayed low, so it
was not merged. Of the four evidence failures, one ("32 do climb to the limit
within 31s") is memory_report's own `lives` output: the grader's reference
has no Prometheus capture, so that one is the grader's blind spot. Two state a
retention of 5–6 or 10 days, where `covered()` reports 30.9h. The fourth
groups other OOMing workloads by a pod-template age the capture doesn't show.

## 8. Open questions

1. **How much to capture.** *Settled for now (§7.4): blocks, in a data
   repository, fetched by digest.* A week of benben's allowlist at 15s is
   about 20 MB. If the allowlist grows, trimming constant series is the next
   lever.
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
