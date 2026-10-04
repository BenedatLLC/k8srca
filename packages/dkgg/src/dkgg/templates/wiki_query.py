#!/usr/bin/env python3
"""Query this deployment knowledge graph.

    wiki.py deps <component>     what it connects to, and what connects to it
    wiki.py blast <component>    everything that depends on it, transitively
    wiki.py path <from> <to>     how one reaches the other
    wiki.py kind <kind>          every component of a kind
    wiki.py list                 every component

Reads graph.json next to this file. Needs only Python 3; no packages.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

GRAPH = Path(__file__).with_name("graph.json")


def load() -> dict:
    if not GRAPH.exists():
        sys.exit(f"{GRAPH} is missing: this wiki has not been built")
    return json.loads(GRAPH.read_text())


def _need(g: dict, name: str) -> None:
    if name not in g["components"]:
        close = [n for n in g["components"] if name in n or n in name]
        hint = f" Did you mean: {', '.join(sorted(close))}?" if close else ""
        sys.exit(f"no component named {name!r}.{hint} (`wiki.py list` shows them all)")


def deps(g: dict, name: str) -> None:
    _need(g, name)
    out = [e for e in g["edges"] if e["from"] == name]
    into = [e for e in g["edges"] if e["to"] == name]
    print(f"{name}  ({g['components'][name]['kind']})")
    print("  connects to: " + (", ".join(f"{e['to']} via {e['kind']}" for e in out) or "nothing"))
    print("  called by:   " + (", ".join(f"{e['from']} via {e['kind']}" for e in into) or "nothing"))


#: Edge kinds through which a failure never propagates: the caller lags, loses
#: telemetry, or loses one route, but keeps serving. On any other edge a failure
#: propagates unless the edge is soft (the caller carries on without it: flag
#: defaults, a cache it can bypass). A cache the caller cannot bypass is not soft,
#: and its failure is the caller's.
CONTAINED = {"async-event": "its events stall (asynchronous)", "load": "loses synthetic traffic",
             "route": "loses a route to it", "telemetry": "loses telemetry"}
SOFT = {"feature-flags": "falls back to defaults", "cache": "runs without the cache"}


def blast(g: dict, name: str) -> None:
    """What happens upstream if this fails, by how each caller depends on it."""
    _need(g, name)
    fails: dict[str, int] = {}
    other: dict[str, str] = {}
    queue = deque([(name, 0)])
    while queue:
        node, hops = queue.popleft()
        for e in g["edges"]:
            if e["to"] != node or e["from"] == name or e["from"] in fails:
                continue
            kind = e.get("kind", "unclassified")
            if kind not in CONTAINED and not e.get("soft"):
                fails[e["from"]] = hops + 1
                other.pop(e["from"], None)
                queue.append((e["from"], hops + 1))
            elif e["from"] not in other:
                other[e["from"]] = CONTAINED.get(kind) or SOFT.get(kind) or f"degrades (soft {kind})"
    if not fails and not other:
        print(f"nothing depends on {name}")
        return
    if fails:
        print(f"if {name} fails, these fail with it:")
        for node, hops in sorted(fails.items(), key=lambda x: (x[1], x[0])):
            print(f"  {node}  ({'directly' if hops == 1 else f'{hops} hops away'})")
    if other:
        print("and these keep running, affected:")
        for node, effect in sorted(other.items()):
            print(f"  {node}  ({effect})")


def path(g: dict, start: str, end: str) -> None:
    _need(g, start)
    _need(g, end)
    prev: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == end:
            break
        for e in g["edges"]:
            if e["from"] == node and e["to"] not in prev:
                prev[e["to"]] = node
                queue.append(e["to"])
    if end not in prev:
        print(f"{start} does not reach {end}")
        return
    hops, node = [], end
    while node is not None:
        hops.append(node)
        node = prev[node]
    print(" -> ".join(reversed(hops)))


def by_kind(g: dict, kind: str) -> None:
    names = sorted(n for n, c in g["components"].items() if c["kind"] == kind)
    print("\n".join(names) if names else f"no component of kind {kind!r}")


def listing(g: dict) -> None:
    for name, c in sorted(g["components"].items()):
        print(f"{name:<28} {c['kind']:<14} {c['workload'] or '-'}")


def main(argv: list[str]) -> int:
    g = load()
    commands = {"deps": (deps, 1), "blast": (blast, 1), "path": (path, 2),
                "kind": (by_kind, 1), "list": (listing, 0)}
    if not argv or argv[0] not in commands or len(argv) - 1 != commands[argv[0]][1]:
        print(__doc__.strip())
        return 2
    fn, _ = commands[argv[0]]
    fn(g, *argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
