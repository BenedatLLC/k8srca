"""Render the deterministic wiki (docs/design.md §3, migration step 2).

Everything here is computed from the collected model: no model calls, no
prose. Pages carry what the inputs state -- the component inventory, its
workload type, the raw edges with their evidence, the declared configuration
from the pinned chart, and the documentation's lead verbatim with its
citation. Synthesis (step 3) adds prose and edge kinds on top.

No observed state is written. What k8stools can answer live -- replicas,
readiness, the running image -- is left to the agent to ask for.

No timestamps either, so an unchanged deployment produces an identical wiki
and nothing downstream (a skill upload, a diff) sees a change that is not
there. The one dated thing is `log.md`, which gains an entry only when the
graph changed.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from . import __version__
from .model import Architecture

#: Output format of graph.json and the pages. Bumped when their meaning changes.
FORMAT = 1

TEMPLATES = Path(__file__).parent / "templates"

#: Kinds the synthesis stage assigns (§3.2); until then, every component is this.
UNCLASSIFIED = "unclassified"

#: Sources the wiki draws on. change_history is not one: what changed recently is
#: state, and the agent reads it live (k8stools' get_replicaset_summaries).
WIKI_SOURCES = {"live_cluster", "chart_repo", "docs"}

#: Facts from the chart worth stating as declared configuration.
DECLARED_FACTS = ("image", "replicas", "resources", "probes", "ports")


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------

def graph(arch: Architecture) -> dict:
    """The machine-readable wiki: components, edges with evidence, docs, sources."""
    components = {}
    for name, s in sorted(arch.services.items()):
        declared = {}
        for key in DECLARED_FACTS:
            for f in s.facts.get(key) or []:
                if f.source == "declared":
                    declared[key] = {"value": f.value, "origin": f.origin}
                    break
        components[name] = {
            "namespace": s.namespace,
            "workload": s.workload,
            "aliases": list(s.aliases),
            "kind": UNCLASSIFIED,
            "declared": declared,
            "docs": [{"text": n.value, "origin": n.origin} for n in s.notes],
        }
    edges = []
    for name, s in sorted(arch.services.items()):
        for target, evidence in sorted(s.edges.items()):
            edges.append({
                "from": name, "to": target, "kind": UNCLASSIFIED,
                "evidence": sorted(({"var": e.var, "source": e.source, "origin": e.origin,
                                     "via": e.via} for e in evidence),
                                   key=lambda e: (e["source"], e["var"], e["origin"])),
            })
    return {
        "format": FORMAT,
        "components": components,
        "edges": edges,
        "general_docs": [{"text": g.value, "origin": g.origin} for g in arch.general],
        "sources": [s for s in arch.sources if s.get("type") in WIKI_SOURCES],
    }


def callers(g: dict, name: str) -> list[str]:
    return sorted({e["from"] for e in g["edges"] if e["to"] == name})


def callees(g: dict, name: str) -> list[str]:
    return sorted({e["to"] for e in g["edges"] if e["from"] == name})


# ---------------------------------------------------------------------------
# Diagrams (§3.6): drawn from the graph, never written
# ---------------------------------------------------------------------------

def _node(name: str) -> str:
    """A Mermaid-safe node id; the label keeps the real name."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def system_diagram(g: dict) -> str:
    groups: dict[str, list[str]] = {}
    typed = any(c["kind"] != UNCLASSIFIED for c in g["components"].values())
    for name, c in g["components"].items():
        groups.setdefault(c["kind"] if typed else (c["workload"] or "Service only"), []).append(name)
    lines = ["```mermaid", "flowchart LR"]
    for group in sorted(groups):
        # Prefixed: a group may share its name with a component in it (the
        # load-generator kind holds load-generator), and Mermaid rejects a
        # subgraph whose id is one of its own nodes.
        lines.append(f'  subgraph group_{_node(group)}["{group}"]')
        lines += [f'    {_node(n)}["{n}"]' for n in sorted(groups[group])]
        lines.append("  end")
    lines += [f"  {_node(e['from'])} {_arrow(e)} {_node(e['to'])}" for e in g["edges"]]
    lines.append("```")
    return "\n".join(lines)


def _arrow(e: dict) -> str:
    """Labelled with the edge's kind once synthesis has one; soft edges dashed."""
    kind = e.get("kind")
    label = f"|{kind}|" if kind and kind != UNCLASSIFIED else ""
    return f"-.->{label}" if e.get("soft") else f"-->{label}"


def neighbourhood_diagram(g: dict, name: str) -> str:
    lines = ["```mermaid", "flowchart LR", f'  {_node(name)}["{name}"]']
    for e in g["edges"]:
        if e["to"] == name:
            lines.append(f'  {_node(e["from"])}["{e["from"]}"] {_arrow(e)} {_node(name)}')
        if e["from"] == name:
            lines.append(f'  {_node(name)} {_arrow(e)} {_node(e["to"])}["{e["to"]}"]')
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _cite(origin: str, source: str) -> str:
    return f"[{source}: {origin}]"


def _cites(cites: list[str]) -> str:
    """Synthesis citations (`docs:x`, `env:V`, `review`) as page citations."""
    out = []
    for c in cites:
        if c == "review":
            out.append("[review: review.yaml]")
        else:
            kind, _, ref = c.partition(":")
            out.append(f"[{kind}: {ref}]")
    return " ".join(out)


def _statements(items: list[dict]) -> list[str]:
    return [f"{s['text'].strip()} {_cites(s.get('cites') or [])}".strip() for s in items]


def _fmt(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k} {_fmt(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def component_page(g: dict, name: str) -> str:
    c = g["components"][name]
    out = [f"# {name}", ""]
    kind_cite = f" {_cites(c['kind_cites'])}" if c.get("kind_cites") else ""
    out.append(f"**Kind:** {c['kind']}{kind_cite}  ")
    out.append(f"**Workload:** {c['workload'] or 'none (a Service with no workload)'}  ")
    out.append(f"**Namespace:** {c['namespace']}  ")
    if c.get("aliases"):
        out.append("**Also reached as:** " + ", ".join(f"`{a}`" for a in c["aliases"])
                   + " (headless Service)  ")
    edges = [e for e in g["edges"] if e["from"] == name]
    if edges:
        out.append("**Connects:** " + "; ".join(
            f"[{e['to']}]({e['to']}.md) via {e['kind']}{' (soft)' if e.get('soft') else ''}"
            for e in edges) + "  ")
    called_by = callers(g, name)
    if called_by:
        out.append("**Called by:** " + ", ".join(f"[{n}]({n}.md)" for n in called_by) + "  ")
    out += ["", neighbourhood_diagram(g, name), ""]

    for heading, key in (("Purpose", "purpose"), ("If it fails", "if_it_fails")):
        if c.get(key):
            out += [f"## {heading}", ""]
            for s in _statements(c[key]):
                out += [s, ""]

    if edges:
        out += ["## Connections", ""]
        for e in edges:
            why = ", ".join(f"{'config ' if ev.get('via') == 'config' else ''}`{ev['var']}` "
                            f"({ev['source']}, {ev['origin']})" for ev in e["evidence"])
            kind = "" if e["kind"] == UNCLASSIFIED else f" ({e['kind']}" + \
                (", soft" if e.get("soft") else "") + ")"
            out.append(f"- **{e['to']}**{kind}: named by {why}" if why
                       else f"- **{e['to']}**{kind}: added in review")
        out.append("")

    out += ["## Documentation", ""]
    if c["docs"]:
        for d in c["docs"]:
            out += [_quote(d["text"]), "", _cite(d["origin"], "docs"), ""]
    else:
        out += ["No documentation page names this component. [derived: docs source]", ""]

    if c["declared"]:
        out += ["## Declared configuration", ""]
        for key, f in c["declared"].items():
            out.append(f"- {key}: {_fmt(f['value'])} {_cite(f['origin'], 'chart')}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())


def index_page(g: dict) -> str:
    out = ["# System index", "",
           "What this deployment is made of and how its parts connect. This wiki holds",
           "no live state: for what is running now, ask the cluster.", ""]
    if g.get("overview"):
        out += ["## Overview", ""]
        for s in _statements(g["overview"]):
            out += [s, ""]
    if g["general_docs"]:
        out += ["## From the documentation", ""]
        for d in g["general_docs"]:
            out += [_quote(d["text"]), "", _cite(d["origin"], "docs"), ""]
    out += ["## Structure", "", system_diagram(g), ""]
    out += ["## Components", ""]
    groups: dict[str, list[str]] = {}
    typed = any(c["kind"] != UNCLASSIFIED for c in g["components"].values())
    for name, c in g["components"].items():
        key = c["kind"] if typed else (c["workload"] or "Service only")
        groups.setdefault(key, []).append(name)
    for group in sorted(groups):
        out.append(f"**{group}:** " + ", ".join(f"[{n}](components/{n}.md)"
                                               for n in sorted(groups[group])))
        out.append("")
    out += ["Built from the sources in [sources.md](sources.md); what changed between",
            "builds is in [log.md](log.md).", ""]
    return "\n".join(out)


def sources_page(g: dict) -> str:
    out = ["# Sources", "", "What this wiki was built from.", ""]
    for s in g["sources"]:
        if s.get("pinned"):
            out.append(f"- **{s.get('type')}**: {s['pinned']}")
            continue
        detail = ", ".join(f"{k}={v}" for k, v in sorted(s.items()) if k != "type")
        out.append(f"- **{s.get('type')}**: {detail}")
    out.append("")
    return "\n".join(out)


SKILL_MD = """---
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
"""

AGENTS_MD = """# Deployment knowledge graph

This directory describes a deployment's components and how they connect. Read
`index.md` first; see `SKILL.md` for how to query it. It holds no live state.
"""


# ---------------------------------------------------------------------------
# Writing, and the change log
# ---------------------------------------------------------------------------

def _changes(old: dict | None, new: dict) -> list[str]:
    if old is None:
        return [f"first build: {len(new['components'])} components, {len(new['edges'])} edges"]
    out = []
    for label, a, b in (("component", set(old["components"]), set(new["components"])),
                        ("edge", {(e["from"], e["to"]) for e in old["edges"]},
                         {(e["from"], e["to"]) for e in new["edges"]})):
        out += [f"added {label} {_name(x)}" for x in sorted(b - a)]
        out += [f"removed {label} {_name(x)}" for x in sorted(a - b)]
    changed = sorted(n for n in set(old["components"]) & set(new["components"])
                     if old["components"][n] != new["components"][n])
    out += [f"changed {n}" for n in changed]
    if not out and old != new:
        out.append("changed sources or documentation")
    return out


def _name(x: Any) -> str:
    return f"{x[0]} -> {x[1]}" if isinstance(x, tuple) else str(x)


def merge(g: dict, synthesis: dict | None) -> dict:
    """Fold a validated (and reviewed) synthesis into the graph."""
    if not synthesis:
        return g
    g["overview"] = synthesis.get("overview") or []
    for c in synthesis.get("components") or []:
        comp = g["components"].get(c["name"])
        if comp is None:
            continue
        comp.update(kind=c["kind"], kind_cites=c.get("kind_cites") or [],
                    purpose=c.get("purpose") or [], if_it_fails=c.get("if_it_fails") or [])
        for e in c.get("edges") or []:
            for edge in g["edges"]:
                if edge["from"] == c["name"] and edge["to"] == e["to"]:
                    edge.update(kind=e["kind"], soft=bool(e.get("soft")),
                                cites=e.get("cites") or [])
    return g


def write(arch: Architecture, dest: Path, today: date | None = None,
          synthesis: dict | None = None, review: Any = None, g: dict | None = None,
          synthesis_errors: list[str] | None = None) -> dict:
    """Write the wiki into `dest`, logging what changed since the last build there.

    `g`, if given, is the graph synthesis was run on (review applied); it is
    rebuilt from `arch` otherwise.
    """
    if g is None:
        g = graph(arch)
        if review is not None:
            g = review.adjust(g)
    g = merge(g, synthesis)
    previous = None
    if (dest / "graph.json").exists():
        try:
            previous = json.loads((dest / "graph.json").read_text())
        except json.JSONDecodeError:
            previous = None
    log = (dest / "log.md").read_text() if (dest / "log.md").exists() else "# Change log\n"
    changes = _changes(previous, g)
    if changes:
        entry = f"\n## {(today or date.today()).isoformat()}\n\n" + "\n".join(f"- {c}" for c in changes)
        header, _, rest = log.partition("\n")
        log = header + "\n" + entry + "\n" + rest

    comp_dir = dest / "components"
    if comp_dir.exists():
        for stale in comp_dir.glob("*.md"):
            if stale.stem not in g["components"]:
                stale.unlink()
    comp_dir.mkdir(parents=True, exist_ok=True)
    for name in g["components"]:
        (comp_dir / f"{name}.md").write_text(component_page(g, name))
    (dest / "graph.json").write_text(json.dumps(g, indent=2, sort_keys=True) + "\n")
    (dest / "index.md").write_text(index_page(g))
    (dest / "sources.md").write_text(sources_page(g))
    (dest / "log.md").write_text(log)
    (dest / "SKILL.md").write_text(SKILL_MD)
    (dest / "AGENTS.md").write_text(AGENTS_MD)
    query = dest / "wiki.py"
    query.write_text((TEMPLATES / "wiki_query.py").read_text())
    query.chmod(0o755)
    manifest = {"dkgg": __version__, "format": FORMAT, "synthesised": bool(synthesis)}
    if synthesis_errors:
        # Written, but marked failing: `dkgg check` reports these.
        manifest["synthesis_errors"] = synthesis_errors
    (dest / "dkgg.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return g
