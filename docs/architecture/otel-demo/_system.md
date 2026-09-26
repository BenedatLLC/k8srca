# How this cluster is meant to work

Operator notes, not upstream documentation. Written by hand against the
OpenTelemetry demo as deployed in the `default` namespace of this cluster, to
record the things neither the live cluster nor the rendered charts can state:
what a component is *for*, and what "broken" means for it.

**These are the most likely facts here to go stale.** The cluster and the charts
are re-read on every `k8srca arch build`; this text is only as current as the
last person to edit it. Treat a disagreement with observed state as a reason to
trust the observation and fix the note.

## What the system is

A synthetic e-commerce application, deployed as a demonstration of
OpenTelemetry instrumentation. Nobody's order is lost when it breaks. That
matters for triage: **there is no customer impact to weigh against the cost of
investigating**, and no reason to prefer a fast mitigation over an accurate
diagnosis.

## The one traffic source

`load-generator` is the only thing that drives the application. All request
volume is synthetic and roughly constant. Two consequences worth stating
plainly, because both are easy to get wrong from cluster state alone:

- **A "traffic spike" is not a normal event here.** If a failure hypothesis
  depends on load increasing, check `load-generator` before accepting it — and
  note that a service which never reaches readiness has served no traffic at
  all, synthetic or otherwise.
- **A quiet service is suspicious, not healthy.** Steady synthetic load means
  request volume near zero usually indicates something upstream is failing, not
  that users went away.

## Which dependencies are load-bearing

The dependency graph in `arch_query.py deps` is derived from environment
variables, so it shows what a service *can* reach. It cannot distinguish a
dependency whose loss is fatal from one whose loss is invisible. Here:

- **`flagd` is soft.** Nearly every service reads feature flags from it, so it
  has the widest fan-in in the graph — and a flagd outage does not take the
  application down: callers fall back to default flag values. A wide blast
  radius in `arch_query.py blast flagd` therefore overstates the consequence.
  Do not read "many services depend on it" as "many services fail without it".
- **`kafka` is asynchronous.** `checkout` produces to it; `accounting` and
  `fraud-detection` consume. If kafka is unavailable the consumers stall and
  fall behind, but the browse-and-buy path keeps working. Symptoms appear in the
  consumers, minutes later, and look unrelated to the cause.
- **The datastores are hard.** `valkey-cart` backs `cart`, and `postgresql`
  backs the services that persist. Losing one breaks its dependant directly.
- **Telemetry is not the application.** `otel-collector`, `jaeger`,
  `prometheus`, `grafana` and `opensearch` observe the system. Their failure
  degrades what you can *see* and breaks nothing a user would notice — but it
  can make an unrelated incident much harder to investigate, so their health is
  worth checking early for that reason rather than as a suspect.
