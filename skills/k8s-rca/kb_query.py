#!/usr/bin/env python3
"""Query the Kubernetes RCA knowledge base.

Loading the whole base costs ~25K tokens of context for information that is
almost entirely irrelevant to any one incident. Query it instead.

  kb_query.py lookup CrashLoopBackOff     full record: hypotheses, evidence, remediation
  kb_query.py search "pod restart"        find candidate alerts by symptom or cause
  kb_query.py related OOMKilled           alerts that co-occur with this one
  kb_query.py promql PodPending           just the PromQL evidence queries
  kb_query.py list --category storage     browse by category or group

Self-contained: stdlib only, reads knowledge_base.json from its own directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KB = Path(__file__).with_name("knowledge_base.json")


def load() -> dict:
    if not KB.exists():
        sys.exit(f"knowledge base not found at {KB}")
    return json.loads(KB.read_text())


def fmt_alert(a: dict, *, brief: bool = False) -> str:
    out = [f"# {a['alert']}  ({a.get('resource_type') or '?'}, severity={a.get('severity')})",
           f"symptom: {a.get('symptom')}"]
    if a["hypotheses"]:
        out.append("\ncandidate causes (these are HYPOTHESES -- discriminate, do not assume):")
        out += [f"  - {h}" for h in a["hypotheses"]]
    if brief:
        return "\n".join(out)
    ev = a.get("evidence") or {}
    for kind in ("events", "logs", "metrics", "traces"):
        if ev.get(kind):
            out.append(f"\n{kind} to check:")
            out += [f"  - {e}" for e in ev[kind]]
    if a.get("promql"):
        out.append("\npromql (needs a metrics backend -- not available in v1):")
        out += [f"  - {q}" for q in a["promql"]]
    if a.get("dependency_checks"):
        out.append("\ndependency checks:")
        out += [f"  - {d}" for d in a["dependency_checks"]]
    if a.get("remediation"):
        out.append(f"\nremediation (SUGGEST ONLY; priority={a.get('remediation_priority')}, "
                   f"automation_safety={a.get('automation_safety')}):")
        out += [f"  - {r}" for r in a["remediation"]]
    if a.get("related"):
        out.append(f"\nrelated alerts: {', '.join(a['related'])}")
    return "\n".join(out)


def cmd_lookup(kb: dict, args) -> int:
    a = kb["alerts"].get(args.alert)
    if not a:
        near = [n for n in kb["alerts"] if args.alert.lower() in n.lower()]
        print(f"no alert named {args.alert!r}." + (f" did you mean: {', '.join(near[:8])}" if near else
              " try: kb_query.py search <symptom text>"))
        return 1
    print(fmt_alert(a))
    return 0


def cmd_search(kb: dict, args) -> int:
    needle = args.text.lower()
    hits = []
    for name, a in kb["alerts"].items():
        haystack = " ".join([
            name, a.get("symptom") or "", " ".join(a["hypotheses"]),
            a.get("category") or "", " ".join(a.get("remediation") or []),
        ]).lower()
        if all(w in haystack for w in needle.split()):
            hits.append(a)
    if not hits:
        print(f"no match for {args.text!r}. try fewer words, or: kb_query.py list")
        return 1
    print(f"{len(hits)} match(es):\n")
    for a in hits[: args.limit]:
        print(fmt_alert(a, brief=True))
        print()
    if len(hits) > args.limit:
        print(f"... {len(hits) - args.limit} more; narrow the search or use lookup")
    return 0


def cmd_related(kb: dict, args) -> int:
    a = kb["alerts"].get(args.alert)
    if not a:
        print(f"no alert named {args.alert!r}")
        return 1
    if not a["related"]:
        print(f"{args.alert} has no recorded correlations.\n"
              f"NOTE: the correlation graph is sparse -- 46 of 81 alerts have none. "
              f"Absence here is not evidence that nothing is related.")
        return 0
    print(f"alerts that co-occur with {args.alert}:\n")
    for name in a["related"]:
        other = kb["alerts"][name]
        print(f"  {name:28} {other.get('symptom')}")
        for h in other["hypotheses"][:3]:
            print(f"      - {h}")
    return 0


def cmd_promql(kb: dict, args) -> int:
    a = kb["alerts"].get(args.alert)
    if not a:
        print(f"no alert named {args.alert!r}")
        return 1
    if not a.get("promql"):
        print(f"{args.alert}: no PromQL recorded")
        return 0
    for q in a["promql"]:
        print(q)
    return 0


def cmd_list(kb: dict, args) -> int:
    alerts = kb["alerts"].values()
    if args.category:
        alerts = [a for a in alerts if (a.get("category") or "") == args.category]
    if args.group:
        alerts = [a for a in alerts if (a.get("group") or "") == args.group]
    alerts = sorted(alerts, key=lambda a: a["alert"])
    if not alerts:
        cats = sorted({a.get("category") for a in kb["alerts"].values() if a.get("category")})
        print("no match. categories: " + ", ".join(cats))
        return 1
    for a in alerts:
        print(f"  {a['alert']:30} {a.get('category') or '':22} {a.get('symptom') or ''}")
    print(f"\n{len(alerts)} alert(s)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("lookup", help="full record for an alert"); s.add_argument("alert"); s.set_defaults(fn=cmd_lookup)
    s = sub.add_parser("search", help="find alerts by symptom or cause text")
    s.add_argument("text"); s.add_argument("--limit", type=int, default=5); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("related", help="alerts that co-occur with this one"); s.add_argument("alert"); s.set_defaults(fn=cmd_related)
    s = sub.add_parser("promql", help="PromQL evidence queries"); s.add_argument("alert"); s.set_defaults(fn=cmd_promql)
    s = sub.add_parser("list", help="browse alerts")
    s.add_argument("--category"); s.add_argument("--group"); s.set_defaults(fn=cmd_list)

    args = p.parse_args()
    return args.fn(load(), args)


if __name__ == "__main__":
    sys.exit(main())
