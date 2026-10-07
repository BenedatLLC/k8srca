---
name: cluster-architecture
description: What this deployment is made of - its components, how they connect, what each is for, and what it is declared to run. No live state; ask the cluster for that.
---

# Deployment knowledge graph

Start with `index.md`: the system on one page, with a diagram and every
component. Each component has a page under `components/`. `sources.md` says
what this was built from; `log.md` what changed between builds.

For structure, run `wiki.py` next to this file rather than tracing links:

```
python3 wiki.py deps <component>    what it connects to, and what connects to it
python3 wiki.py blast <component>   what fails with it, and what only degrades
python3 wiki.py path <from> <to>    how one reaches the other
python3 wiki.py kind <kind>         every component of a kind
python3 wiki.py list                every component
```

`graph.json` is `wiki.py`'s data file, not a document: do not read it whole.
Every statement on a page cites where it came from: `docs` (the publisher's
documentation), `chart` (its declared configuration), `env` and `config` (the
variable or configuration key that names a connection), `derived` (computed
from the connections).

Nothing here is live. Replicas, readiness, the image actually running,
restarts, events and what changed recently (a workload's revision history)
come from the cluster itself, at the moment you need them. Where a page's
declared configuration differs from what the cluster runs, the cluster is
right about now and the difference is drift.
