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
arch_query.py drift                where declared and observed disagree
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
- **documented** — from runbooks and upstream docs. Why it is shaped this way
  and what to do when it breaks. The most likely to be stale.

When they disagree, that is a **finding, not noise**. A chart declaring two
replicas while one is running, or a documented limit that does not match the
deployed one, is drift worth reporting. `arch_query.py drift` lists it.

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
