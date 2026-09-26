---
name: cluster-architecture
description: What is actually deployed in this cluster - services, images, resource limits, probes, and which services call which. Use when a question involves a specific workload, or when you need to know what else is affected by a failing service.
---

# Cluster architecture

Facts about *this* cluster, as opposed to Kubernetes in general. Use it to turn
a symptom into a blast radius, and to spot configuration that explains a
failure.

## Query it, do not read it

`arch_query.py` sits next to this file. **Run it.**

```
arch_query.py service checkout     everything known about one service
arch_query.py deps checkout        what it calls, and what calls it
arch_query.py blast ad             what degrades if this service fails
arch_query.py changes ad           when it last changed, and what changed
arch_query.py drift                where declared and observed disagree
arch_query.py intent               what the system is for, and what breaks it
arch_query.py list                 every service, one line each
arch_query.py sources              what this was built from, and when
```

`architecture.json` next to it is the script's **data file**, not a document.
Do not `cat` it, and do not pipe it through `json.tool`: it holds every
service in the cluster and reading it spends the context you need for the
investigation itself. `arch_query.py service <name>` returns the same facts
for one service in a few lines.

## Every fact carries its source

Three kinds, and the difference matters:

- **observed** — read from the live cluster at build time. Authoritative about
  what is running. Says nothing about intent.
- **declared** — from charts or manifests. What the system is supposed to be,
  including things not currently deployed.
- **documented** — hand-written operator notes. Why it is shaped this way
  and what to do when it breaks. The most likely to be stale.

When they disagree it *may* be a finding, and `arch_query.py drift` lists it —
but read it with care, because most of what it reports here is not
discriminating. A whole-chart version bump shows up as separate drift on every
service it touched: one fact with a multiplier, true of the healthy services
too. Drift is worth citing when it is **specific to the service you are
investigating** and plausibly connected to the symptom. "Every service shows the
same skew" is a property of the deployment, not an explanation of a failure, and
an answer that leans on it is reaching.

## Intent: what a component is for

`arch_query.py intent` gives the system-level notes — which dependencies are
load-bearing, what the traffic source is, what "broken" means for each kind of
component — and `arch_query.py service <name>` ends with any note for that one
service.

**This is the only source here that states intent rather than deriving
structure**, and it answers the questions that change a diagnosis: a dependency
with wide fan-in whose loss is nonetheless invisible, an asynchronous consumer
whose symptoms appear minutes after and somewhere else, a component that
observes the system rather than serving it. `deps` and `blast` cannot tell you
any of that — they show reach, and intent tells you what reach costs.

It is also the only source that can be **wrong rather than stale**: the cluster
and the charts are re-read on every build, these notes are as current as the
last person to edit them. Where a note disagrees with observed state, trust the
observation.

## "What changed?" — ask this early

`arch_query.py changes <service>` answers it from the cluster's own ReplicaSet
history: when the workload last changed and what differed.

**A long-unchanged workload is a finding.** If nothing has been applied for
months, a recent regression is not the explanation, and you should rule it out
explicitly rather than leaving it open as unchecked. This is usually the
cheapest hypothesis to eliminate, so ask it before exhausting telemetry.

It covers workload spec changes only. A ConfigMap edit, a feature-flag toggle,
or a change in traffic leaves no revision behind — so "unchanged" narrows the
field, it does not close it.

## Two things this cannot tell you

- **It is a snapshot.** Built at the timestamp in `arch_query.py sources`. If
  an investigation concerns something that changed recently, check the live
  cluster with your Kubernetes tools rather than trusting this.
- **Dependencies are configured, not observed.** The graph comes from
  environment variables naming other services. It shows what a service *can*
  call, not what it called during the incident.

## Using it in an investigation

When a service is failing, `blast` tells you what else will look broken. Work
*down* the dependency graph toward the cause rather than treating each
symptomatic service as its own problem — a failing dependency produces
symptoms in everything upstream of it.

When a service is failing on its own, its `resources` and `probes` are usually
the first things worth looking at. A container with no probes configured cannot
be restart-looping because of a failed health check, which rules out a whole
family of explanations immediately.
