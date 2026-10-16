#!/usr/bin/env python3
"""Query the Kubernetes RCA knowledge base.

Loading the whole base costs ~25K tokens of context for information that is
almost entirely irrelevant to any one incident. Query it instead.

  kb_query.py lookup CrashLoopBackOff     candidate causes, what decides each, how to check
  kb_query.py lookup pod-restarting       the same, by symptom
  kb_query.py refine oom-kill             the sub-causes a confirmed cause opens up
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


def available() -> set[str]:
    """What this deployment can observe with (design 007 §4.1).

    The sandbox is told in K8SRCA_CAPABILITIES. Absent -- an older poller, or
    this script run by hand -- it is k8stools alone, which every deployment
    has; a check needing anything else is then reported as one that cannot be
    made, which is the safe direction to be wrong in.
    """
    import os

    raw = os.environ.get("K8SRCA_CAPABILITIES", "")
    return {"k8stools"} | {c.strip() for c in raw.split(",") if c.strip()}


def load() -> dict:
    if not KB.exists():
        sys.exit(f"knowledge base not found at {KB}")
    return json.loads(KB.read_text())


def fmt_alert(a: dict, *, brief: bool = False, hypotheses: bool = True) -> str:
    out = [f"# {a['alert']}  ({a.get('resource_type') or '?'}, severity={a.get('severity')})",
           f"symptom: {a.get('symptom')}"]
    if a["hypotheses"] and hypotheses:
        out.append("\ncandidate causes (these are HYPOTHESES -- discriminate, do not assume):")
        for h in a["hypotheses"]:
            out.append(f"  - {h}")
            if not brief:
                # Saying so matters: an invented criterion is only as good as the
                # invention, and the agent should know which kind it is holding
                # (002 §11.3).
                out.append("      no authored discriminator -- you will have to form "
                           "one, and should say so")
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


# --- the authored knowledge (design 007) -----------------------------------------
#
# A converted alert or symptom is answered from here: each candidate with what
# would confirm and refute it, and which checks this deployment can make.


def fmt_condition(K: dict, c: dict) -> str:
    fact = K["facts"].get(c.get("fact") or "")
    if not fact:
        return c["text"]
    compare = [tuple(t) for t in c.get("compare") or []]
    text = fact["says"].strip()
    for param, arg in zip(fact["params"], c["args"]):
        # A variable compared only by != reads as what it is not: "exit code is
        # not 137". Anything else keeps its comparison.
        excluded = [v for var, op, v in compare if var == arg and op == "!="]
        if excluded and all(op == "!=" for var, op, _ in compare if var == arg):
            value = "not " + excluded[0] if len(excluded) == 1 else \
                "neither " + " nor ".join(excluded)
            compare = [t for t in compare if t[0] != arg]
        else:
            value = arg
        text = text.replace("{" + param + "}", value)
    if compare:
        text += ", where " + " and ".join(" ".join(t) for t in compare)
    return f"NOT ({text})" if c.get("negated") else text


def fact_says(K: dict, name: str) -> str:
    """A fact's sentence with its value parameters left open."""
    fact = K["facts"][name]
    text = fact["says"].strip()
    for param in fact["params"]:
        text = text.replace("{" + param + "}", f"<{param.lower()}>")
    return text


def _producers(K: dict, fact: str) -> list[tuple[str, dict]]:
    return [(n, a) for n, a in K["actions"].items() if fact in a["produces"]]


def _missing(K: dict, alternative: list[dict], have: set[str]) -> list[str]:
    """Capabilities an alternative needs that this deployment lacks."""
    needs = set()
    for c in alternative:
        if not c.get("fact"):
            continue
        producers = _producers(K, c["fact"])
        if not any(a["requires"] in have for _, a in producers):
            needs |= {a["requires"] for _, a in producers}
    return sorted(needs)


def fmt_alternatives(K: dict, label: str, alts: list[list[dict]], have: set[str]) -> list[str]:
    out = []
    for i, alt in enumerate(alts):
        lead = f"{label}:" if i == 0 else "or:".rjust(len(label) + 1)
        line = f"      {lead} " + " AND ".join(fmt_condition(K, c) for c in alt)
        missing = _missing(K, alt, have)
        if missing:
            line += f"  [cannot be checked here: needs {', '.join(missing)}]"
        out.append(line)
    return out


def fmt_claim(K: dict, cid: str, have: set[str]) -> list[str]:
    c = K["claims"][cid]
    out = [f"  - {c['title']}  [{cid}]"]
    out += fmt_alternatives(K, "confirmed when", c["confirm"], have)
    out += fmt_alternatives(K, "refuted when", c["refute"], have)
    if c.get("supports"):
        out.append("      for, not decisive: "
                   + "; ".join(fmt_condition(K, s) for s in c["supports"]))
    if c.get("note"):
        out.append(f"      note: {c['note'].strip()}")
    if c.get("refined_by"):
        subs = ", ".join(K["claims"][s]["title"] for s in c["refined_by"])
        # Accepting a cause is not the end of it when it is a family: "OOM" has
        # kinds, and which kind it is has its own evidence. Without this the
        # investigation stops at the family name.
        out.append(f"      NARROWS FURTHER: if this survives, `kb_query.py refine {cid}` "
                   f"lists its sub-causes ({subs}), and each of those needs a "
                   f"disposition too. They refine this cause; they are not its rivals.")
    if c.get("refines_alert"):
        out.append(f"      NARROWS FURTHER: if this survives, `kb_query.py lookup "
                   f"{c['refines_alert']}` lists its sub-causes, and each of those "
                   f"needs a disposition too")
    rivals = [b if a == cid else a for a, b in K.get("exclusive", []) if cid in (a, b)]
    if rivals:
        out.append("      cannot both be the cause: "
                   + ", ".join(K["claims"][r]["title"] for r in rivals))
    return out


def fmt_checks(K: dict, claims: list[str], have: set[str]) -> list[str]:
    """The checks that decide these claims, grouped by action, cheapest first."""
    used = []
    for cid in claims:
        c = K["claims"][cid]
        for cond in [x for alt in c["confirm"] + c["refute"] for x in alt] + c["supports"]:
            if cond.get("fact") and cond["fact"] not in used:
                used.append(cond["fact"])
    rows = []
    for name, a in K["actions"].items():
        gives = [f for f in a["produces"] if f in used]
        if gives:
            rows.append((a["requires"] not in have, a["cost"], name, a, gives))
    out, unavailable_header = ["\nhow to check, cheapest first:"], False
    for missing, _, name, a, gives in sorted(rows):
        if missing and not unavailable_header:
            out.append("\nnot available in this deployment -- a candidate resting only on "
                       "these is could-not-check, never refuted:")
            unavailable_header = True
        call = a.get("call") or name
        out.append(f"  {call}  ({a['requires']})" + (f" -- {a['read'].strip()}" if a.get("read") else ""))
        if a.get("quality") in ("point", "partial"):
            out.append(f"      {a['quality'].upper()}: {a.get('limits', '').strip()}")
        out.append("      tells you whether: " + "; ".join(fact_says(K, f) for f in gives))
    return out


def fmt_symptom(K: dict, sid: str, have: set[str], *, via: str | None = None) -> str:
    s = K["symptoms"][sid]
    out = [f"# {sid}: {s['title']}" + (f"   (alert {via} maps here)" if via else ""),
           "\ncandidate causes (these are HYPOTHESES -- discriminate, do not assume):"]
    for cid in s["claims"]:
        out += fmt_claim(K, cid, have)
    out += fmt_checks(K, s["claims"], have)
    return "\n".join(out)


def cmd_lookup(kb: dict, args) -> int:
    K, have = kb.get("knowledge") or {}, available()
    if args.alert in K.get("symptoms", {}):
        print(fmt_symptom(K, args.alert, have))
        return 0
    if args.alert in K.get("alerts", {}):
        print(fmt_symptom(K, K["alerts"][args.alert]["symptom"], have, via=args.alert))
        legacy = kb["alerts"].get(args.alert)
        if legacy:
            # The vendored record's evidence, remediation and correlations still
            # apply; its hypotheses are the ones above.
            print("\n" + fmt_alert(legacy, hypotheses=False).split("\n", 2)[-1])
        return 0
    if args.alert in K.get("claims", {}):
        print("\n".join(fmt_claim(K, args.alert, have) + fmt_checks(K, [args.alert], have)))
        return 0
    a = kb["alerts"].get(args.alert)
    if not a:
        names = list(kb["alerts"]) + list((kb.get("knowledge") or {}).get("symptoms", {}))
        near = [n for n in names if args.alert.lower() in n.lower()]
        print(f"no alert named {args.alert!r}." + (f" did you mean: {', '.join(near[:8])}" if near else
              " try: kb_query.py search <symptom text>"))
        return 1
    print(fmt_alert(a))
    return 0


def cmd_refine(kb: dict, args) -> int:
    K, have = kb.get("knowledge") or {}, available()
    c = K.get("claims", {}).get(args.claim)
    if not c:
        print(f"no claim named {args.claim!r}. claims: {', '.join(sorted(K.get('claims', {})))}")
        return 1
    if not c.get("refined_by"):
        if c.get("refines_alert"):
            print(f"{args.claim} narrows into alert {c['refines_alert']}: "
                  f"`kb_query.py lookup {c['refines_alert']}`")
            return 0
        print(f"{args.claim} has no recorded sub-causes.")
        return 0
    out = [f"# sub-causes of {c['title']} [{args.claim}]",
           "they refine it, not rival it: each needs a disposition, and "
           "confirming one says which kind it is.\n"]
    for sub in c["refined_by"]:
        out += fmt_claim(K, sub, have)
    out += fmt_checks(K, c["refined_by"], have)
    print("\n".join(out))
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
    K = kb.get("knowledge") or {}
    symptoms = []
    for sid, sym in K.get("symptoms", {}).items():
        haystack = " ".join([sid, sym["title"]] + [K["claims"][c]["title"] for c in sym["claims"]]
                            + [a for a, m in K.get("alerts", {}).items() if m["symptom"] == sid]).lower()
        if all(w in haystack for w in needle.split()):
            symptoms.append((sid, sym))
    for sid, sym in symptoms:
        print(f"symptom {sid}: {sym['title']} -- `kb_query.py lookup {sid}`")
    if symptoms:
        print()
    if not hits:
        if symptoms:
            return 0
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

    s = sub.add_parser("lookup", help="an alert, symptom or cause: candidates and checks"); s.add_argument("alert"); s.set_defaults(fn=cmd_lookup)
    s = sub.add_parser("refine", help="the sub-causes a confirmed cause opens up")
    s.add_argument("claim"); s.set_defaults(fn=cmd_refine)
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
