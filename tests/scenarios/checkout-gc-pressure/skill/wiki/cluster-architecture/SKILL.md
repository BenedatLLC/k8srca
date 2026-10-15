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
python3 wiki.py blast <component>   everything that depends on it, transitively
python3 wiki.py path <from> <to>    how one reaches the other
python3 wiki.py list                every component
```

Nothing here is live. Replicas, readiness, the image actually running, restarts
and events come from the cluster itself, at the moment you need them.
