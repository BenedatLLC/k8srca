# What this cluster is

Operator notes describing the components and how they fit together. They record
what neither the live cluster nor the rendered charts can state: what each part
is *for*, and what happens to the system when it fails.

**Scope.** These notes describe the system. They deliberately contain no
diagnostic guidance — no "check this first", no ranked causes, no advice on
reading evidence. Generic root-cause knowledge lives in the `k8s-rca` skill,
which is where it applies to any cluster; mixing it in here would duplicate it
in a place that only covers this one.

**These are the most likely facts here to go stale.** The cluster and the charts
are re-read on every `k8srca arch build`; this text is only as current as the
last person to edit it. Where it disagrees with observed state, the observation
is right.

## What the application is

A synthetic e-commerce application, deployed to demonstrate OpenTelemetry
instrumentation. No real customer transacts through it and no data is lost when
it fails.

## Where load comes from

`load-generator` is the only source of traffic. All request volume is synthetic
and roughly constant; there are no users, no diurnal pattern and no external
callers. When `load-generator` stops, the whole application goes quiet.

## What each kind of dependency means

The graph from `arch_query.py deps` is derived from environment variables, so it
records what a service is configured to reach. What the loss of each kind of
dependency actually does to the system:

- **`flagd` — feature flags.** Nearly every service reads flag values from it,
  giving it the widest fan-in in the graph. Callers fall back to compiled-in
  default values when it is unreachable, so the application continues to serve
  with default behaviour. Flag *values* are not visible to this skill, and
  changing one leaves no trace in any workload revision.
- **`kafka` — asynchronous messaging.** `checkout` produces order events;
  `accounting` and `fraud-detection` consume them. Nothing in the
  browse-and-purchase path waits on a consumer, so when kafka or a consumer is
  unavailable the purchase path continues and the consumers fall behind.
- **`valkey-cart` and `postgresql` — datastores.** `cart` reads and writes
  `valkey-cart`; the persisting services use `postgresql`. A dependent service
  cannot serve without its datastore.
- **Telemetry — `otel-collector`, `jaeger`, `prometheus`, `grafana`,
  `opensearch`.** These receive and store signals emitted by the application.
  Nothing in the application's request path depends on them, and when they are
  unavailable the application behaves normally while its telemetry is lost.

## How the workloads are provisioned

Single replica for most services, so a pod is a service. No autoscaling: replica
counts are what the chart declares. Everything is in the `default` namespace.

Memory limits are set per service by the chart and are generally equal to the
request. `ad`, `fraud-detection` and `accounting` are JVM services, and the
chart sets no JVM heap flags for them, so each JVM sizes its heap from the
cgroup it is given.
