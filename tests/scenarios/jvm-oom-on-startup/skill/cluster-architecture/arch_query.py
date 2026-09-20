#!/usr/bin/env python3
"""Query what is deployed in this cluster.

  arch_query.py service checkout   everything known about one service
  arch_query.py deps checkout      what it calls, and what calls it
  arch_query.py blast ad           what degrades if this service fails
  arch_query.py drift              where declared and observed disagree
  arch_query.py list               every service, one line each
  arch_query.py sources            what this was built from, and when

Self-contained: stdlib only, reads architecture.json from its own directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DATA = Path(__file__).with_name("architecture.json")


def load() -> dict:
    if not DATA.exists():
        sys.exit(f"architecture not built: {DATA} missing (run `k8srca arch build`)")
    return json.loads(DATA.read_text())


def fmt_fact(key: str, entry: dict) -> str:
    line = f"  {key:16} {entry['value']}"
    if entry["source"] != "observed":
        line += f"   [{entry['source']}]"
    if entry.get("conflicts"):
        others = "; ".join(f"{c['source']}={c['value']}" for c in entry["conflicts"])
        line += f"\n  {'':16} DRIFT: {others}"
    return line


def cmd_service(db: dict, args) -> int:
    svc = db["services"].get(args.name)
    if not svc:
        near = [n for n in db["services"] if args.name.lower() in n.lower()]
        print(f"no service {args.name!r}." + (f" did you mean: {', '.join(near[:8])}" if near
                                              else " try: arch_query.py list"))
        return 1
    print(f"# {svc['name']}  (namespace {svc['namespace']})")
    for key in sorted(svc["facts"]):
        print(fmt_fact(key, svc["facts"][key]))
    if svc["depends_on"]:
        print(f"  {'calls':16} {', '.join(svc['depends_on'])}")
    if svc["called_by"]:
        print(f"  {'called by':16} {', '.join(svc['called_by'])}")
    for n in svc["notes"]:
        print(f"\n  note [{n['source']}] {n['origin']}:\n    {n['text']}")
    return 0


def cmd_deps(db: dict, args) -> int:
    svc = db["services"].get(args.name)
    if not svc:
        print(f"no service {args.name!r}")
        return 1
    print(f"{args.name} calls    : {', '.join(svc['depends_on']) or 'nothing recorded'}")
    print(f"{args.name} called by: {', '.join(svc['called_by']) or 'nothing recorded'}")
    print("\nNOTE: derived from configuration, not from observed traffic. It shows what\n"
          "this service *can* call, not what it called during an incident.")
    return 0


def cmd_blast(db: dict, args) -> int:
    """Transitive callers: everything that will look broken if this fails."""
    if args.name not in db["services"]:
        print(f"no service {args.name!r}")
        return 1
    affected, frontier = set(), [args.name]
    while frontier:
        current = frontier.pop()
        for caller in db["services"].get(current, {}).get("called_by", []):
            if caller not in affected:
                affected.add(caller)
                frontier.append(caller)
    if not affected:
        print(f"nothing recorded as calling {args.name} -- a failure there should not\n"
              f"produce symptoms elsewhere, though the dependency graph is configuration-\n"
              f"derived and may be incomplete.")
        return 0
    print(f"if {args.name} fails, these may show symptoms (transitive callers):\n")
    for name in sorted(affected):
        direct = args.name in db["services"][name]["depends_on"]
        print(f"  {name:24} {'calls it directly' if direct else 'indirectly'}")
    return 0


def cmd_changes(db: dict, args) -> int:
    """When this workload last changed, from the cluster's ReplicaSet history.

    "Nothing has changed" is a real answer: it rules out recent-regression
    hypotheses rather than leaving them open.
    """
    svc = db["services"].get(args.name)
    if not svc:
        print(f"no service {args.name!r}")
        return 1
    facts = svc["facts"]
    last = facts.get("last_changed", {}).get("value")
    revs = facts.get("revisions", {}).get("value")
    what = facts.get("last_change_was", {}).get("value")
    if last is None:
        print(f"{args.name}: no revision history recorded.\n"
              f"Either the change_history source is not configured, or this is not a\n"
              f"Deployment (StatefulSets and DaemonSets keep history differently).")
        return 0
    print(f"{args.name}:")
    print(f"  last changed   {last}")
    print(f"  revisions      {revs}")
    if what:
        print(f"  that change    {what}")
    else:
        print("  that change    no tracked field differed (image, resources, replicas)")
    print("\nIf this is old, a recent regression is not the explanation and should be\n"
          "ruled out rather than left open. Note this covers workload spec changes\n"
          "only -- a ConfigMap edit or a feature flag toggle leaves no revision.")
    return 0


def cmd_drift(db: dict, args) -> int:
    found = 0
    for name, svc in sorted(db["services"].items()):
        for key, entry in sorted(svc["facts"].items()):
            if entry.get("conflicts"):
                found += 1
                others = "; ".join(f"{c['source']}={c['value']}" for c in entry["conflicts"])
                print(f"  {name}.{key}: {others}")
    if not found:
        srcs = {s.get("type") for s in db.get("sources", [])}
        print("no drift found." + ("" if len(srcs) > 1 else
              f"\nNOTE: only one source ({', '.join(srcs) or 'none'}) was used, so drift"
              "\ncannot be detected -- it needs at least two to compare."))
    return 0


def cmd_list(db: dict, args) -> int:
    for name, svc in sorted(db["services"].items()):
        img = svc["facts"].get("image", {}).get("value", "")
        tag = str(img).rsplit(":", 1)[-1] if img else ""
        print(f"  {name:26} {tag:22} calls={len(svc['depends_on']):2} "
              f"called_by={len(svc['called_by'])}")
    print(f"\n{len(db['services'])} services")
    return 0


def cmd_sources(db: dict, args) -> int:
    print(f"built at {db.get('built_at')}")
    for s in db.get("sources", []):
        print(f"  {s.get('type'):14} {s.get('origin','')} {s.get('namespaces','')}")
    print("\nThis is a snapshot. For anything that may have changed since, check the\n"
          "live cluster with your Kubernetes tools rather than trusting this file.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, needs_arg in [("service", cmd_service, True), ("deps", cmd_deps, True),
                                ("blast", cmd_blast, True), ("changes", cmd_changes, True),
                                ("drift", cmd_drift, False),
                                ("list", cmd_list, False), ("sources", cmd_sources, False)]:
        sp = sub.add_parser(name)
        if needs_arg:
            sp.add_argument("name")
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    return args.fn(load(), args)


if __name__ == "__main__":
    sys.exit(main())
