#!/usr/bin/env python3
"""Query the Kubernetes RCA knowledge base.

Loading the whole base costs ~25K tokens of context for information that is
almost entirely irrelevant to any one incident. Query it instead.

  kb_query.py lookup CrashLoopBackOff     candidate causes, the quickest test of each
  kb_query.py lookup pod-restarting       the same, by symptom
  kb_query.py claim oom-kill bad-config   a candidate's full criteria, notes and checks
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


#: Timing (007 §3.4): the facts every claim says a change with.
CHANGE_FACT, ONSET_FACT = "changed_before_onset", "onset_observed"


def change_kind(K: dict, c: dict) -> str | None:
    """The kind of change a condition names, if it names one."""
    if c.get("fact") == CHANGE_FACT and len(c.get("args") or []) == 2:
        return c["args"][1] if c["args"][1] in K.get("changes", {}) else None
    return None


def fmt_condition(K: dict, c: dict) -> str:
    fact = K["facts"].get(c.get("fact") or "")
    if not fact:
        return c["text"]
    compare = [tuple(t) for t in c.get("compare") or []]
    text = fact["says"].strip()
    for param, arg in zip(fact["params"], c["args"]):
        if arg in K.get("changes", {}) and c["fact"] == CHANGE_FACT:
            arg = K["changes"][arg]["title"]
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


def _producers(K: dict, c: dict) -> list[tuple[str, dict]]:
    """The actions that can observe a condition. A change is seen by the
    actions its kind names, not by everything that sees some change."""
    kind = change_kind(K, c)
    if kind:
        return [(n, K["actions"][n]) for n in K["changes"][kind]["seen_by"]
                if n in K["actions"]]
    return [(n, a) for n, a in K["actions"].items() if c["fact"] in a["produces"]]


def _missing(K: dict, alternative: list[dict], have: set[str]) -> list[str]:
    """Capabilities an alternative needs that this deployment lacks."""
    needs = set()
    for c in alternative:
        if not c.get("fact"):
            continue
        producers = _producers(K, c)
        if not any(a["requires"] in have for _, a in producers):
            needs |= {a["requires"] for _, a in producers}
    return sorted(needs)


def _cost(K: dict, alternative: list[dict], have: set[str]) -> int | None:
    """What deciding an alternative here costs: its dearest condition, each
    by its cheapest available action. None when it cannot be decided here."""
    worst = 0
    for c in alternative:
        if not c.get("fact"):
            worst = max(worst, 2)  # prose: the model's call, at an unknown price
            continue
        costs = [a["cost"] for _, a in _producers(K, c) if a["requires"] in have]
        if not costs:
            return None
        worst = max(worst, min(costs))
    return worst


def quickest(K: dict, cid: str, have: set[str]) -> tuple[int | None, str, list[dict]]:
    """The cheapest test of a claim this deployment can make: a refute
    alternative over a confirm one at the same cost (002 §6)."""
    c, best = K["claims"][cid], None
    for rank, (label, alts) in enumerate((("ruled out if", c["refute"]),
                                          ("confirmed if", c["confirm"]))):
        for alt in alts:
            cost = _cost(K, alt, have)
            if cost is not None and (best is None or (cost, rank, len(alt)) < best[0]):
                best = ((cost, rank, len(alt)), label, alt)
    return (best[0][0], best[1], best[2]) if best else (None, "", [])


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


def sub_causes(K: dict, c: dict) -> tuple[list[str], list[str]]:
    """A claim's sub-causes: the common ones, and the uncommon ones held back.

    Every sub-cause shown is one more the answer must dispose of. With six
    under OOM, answers covered the rare ones and dropped the likely ones
    (007 §11.2), so the uncommon ones wait until they are asked for."""
    subs = c.get("refined_by") or []
    rare = [s for s in subs if K["claims"][s].get("prevalence") == "uncommon"]
    return [s for s in subs if s not in rare], rare


RARE_RULE = ("an uncommon one needs a disposition only when the evidence points at "
             "it, or fits none of the common ones")


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
        common, rare = sub_causes(K, c)
        subs = ", ".join(K["claims"][s]["title"] for s in common)
        # Accepting a cause is not the end of it when it is a family: "OOM" has
        # kinds, and which kind it is has its own evidence. Without this the
        # investigation stops at the family name.
        out.append(f"      NARROWS FURTHER: if this survives, `kb_query.py refine {cid}` "
                   f"lists its sub-causes ({subs}), and each of those needs a "
                   f"disposition too. They refine this cause; they are not its rivals."
                   + (f" It also lists {len(rare)} uncommon ones: {RARE_RULE}." if rare else ""))
    if c.get("refines_alert"):
        out.append(f"      NARROWS FURTHER: if this survives, `kb_query.py lookup "
                   f"{c['refines_alert']}` lists its sub-causes, and each of those "
                   f"needs a disposition too")
    rivals = [b if a == cid else a for a, b in K.get("exclusive", []) if cid in (a, b)]
    if rivals:
        out.append("      cannot both be the cause: "
                   + ", ".join(K["claims"][r]["title"] for r in rivals))
    return out


def _gives(K: dict, c: dict) -> str:
    """What observing a condition's fact tells you, values left open."""
    kind = change_kind(K, c)
    if kind:
        return fact_says(K, CHANGE_FACT).replace("<kind>", K["changes"][kind]["title"])
    return fact_says(K, c["fact"])


def fmt_checks(K: dict, conditions: list[dict], have: set[str]) -> list[str]:
    """The checks that decide these conditions, one row per tool, cheapest first.

    Actions sharing a tool are one call for the agent, so they are one row:
    what to read in it and what it tells you, merged."""
    rows: dict[tuple[str, str], dict] = {}
    for c in conditions:
        if not c.get("fact"):
            continue
        for name, a in _producers(K, c):
            key = (a.get("call") or name, a["requires"])
            row = rows.setdefault(key, {"cost": a["cost"], "read": [], "limits": [],
                                        "gives": []})
            row["cost"] = min(row["cost"], a["cost"])
            if a.get("read") and a["read"].strip() not in row["read"]:
                row["read"].append(a["read"].strip())
            if a.get("quality") in ("point", "partial"):
                lim = f"{a['quality'].upper()}: {a.get('limits', '').strip()}"
                if lim not in row["limits"]:
                    row["limits"].append(lim)
            if _gives(K, c) not in row["gives"]:
                row["gives"].append(_gives(K, c))
    out, unavailable_header = ["\nhow to check, cheapest first:"], False
    order = sorted(rows.items(), key=lambda kv: (kv[0][1] not in have, kv[1]["cost"], kv[0]))
    for (call, requires), row in order:
        if requires not in have and not unavailable_header:
            out.append("\nnot available in this deployment -- a candidate resting only on "
                       "these is could-not-check, never refuted:")
            unavailable_header = True
        reads = " ".join(r if r.endswith(".") else r + "." for r in row["read"])
        out.append(f"  {call}  ({requires})" + (f" -- {reads}" if reads else ""))
        out += [f"      {lim}" for lim in row["limits"]]
        out.append("      tells you whether: " + "; ".join(row["gives"]))
    return out


def _conditions(K: dict, claims: list[str]) -> list[dict]:
    return [x for cid in claims for x in
            [y for alt in K["claims"][cid]["confirm"] + K["claims"][cid]["refute"] for y in alt]
            + K["claims"][cid]["supports"]]


TIMING = ("\ntiming: a change is evidence of a cause only when (1) the onset is "
          "observed -- the first failure itself is in what events, metrics or history "
          "retain. An event series' first_seen says the failures were already "
          "happening then, not that they began then; a pod's age and a restart count "
          "are not an onset either -- and (2) the change "
          "took effect just before it. A change outside that window is history, not a "
          "trigger. With no observed onset, a change-as-trigger is could-not-check. A "
          "change whose effect builds up (a leak, a growing queue) lags its onset by the "
          "build-up; the evidence is then the build-up, not the timing. The same holds "
          "for a healthy period before the failures: without an observed onset, \"it "
          "ran fine until N days ago\" is not observed either.")


def fmt_timing(K: dict, conditions: list[dict]) -> list[str]:
    """The timing rule, wherever a condition names a change or rests on the
    onset. Printed only with changes, it missed answers that refuted a cause
    on "ran fine until 4 days ago", read from an event window (007 §11.2)."""
    kinds, onset = [], False
    for c in conditions:
        kind = change_kind(K, c)
        if kind and kind not in kinds:
            kinds.append(kind)
        onset = onset or c.get("fact") == ONSET_FACT
    if not kinds:
        return [TIMING] if onset else []
    out = [TIMING, "when each kind of change takes effect:"]
    for kind in kinds:
        ch = K["changes"][kind]
        out.append(f"  - {ch['title']}: {' '.join(ch['takes_effect'].split())}")
    return out


def fmt_brief(K: dict, cid: str, have: set[str]) -> tuple[list[str], list[dict]]:
    """A candidate in one or two lines: its quickest test here, and whether it
    has sub-causes. The rest is `claim`'s, read when it is pursued."""
    c = K["claims"][cid]
    common, rare = sub_causes(K, c)
    subs = (f"   +{len(common)} sub-causes" if common else "") + (
        f" (+{len(rare)} uncommon)" if rare else "")
    out = [f"  - {c['title']}  [{cid}]{subs}"]
    cost, label, alt = quickest(K, cid, have)
    if alt:
        out.append(f"      {label}: " + " AND ".join(fmt_condition(K, x) for x in alt))
    else:
        needs = sorted({n for a in c["confirm"] + c["refute"] for n in _missing(K, a, have)})
        out.append(f"      cannot be decided here (needs {', '.join(needs) or 'a person'}): "
                   f"could-not-check unless other evidence settles it")
    return out, alt


def fmt_candidates(K: dict, claims: list[str], have: set[str]) -> list[str]:
    """Candidates cheapest to test first, then the checks those tests need."""
    def key(cid):
        cost = quickest(K, cid, have)[0]
        return (cost is None, cost or 0)
    out, tested = [], []
    for cid in sorted(claims, key=key):  # stable: a layer's order within a cost
        lines, alt = fmt_brief(K, cid, have)
        out += lines
        tested += alt
    out += fmt_checks(K, tested, have)
    out += fmt_timing(K, _conditions(K, claims))
    out.append("\nEach candidate needs a disposition: confirmed, weakened, refuted or "
               "could-not-check. `kb_query.py claim <id> [<id> ...]` gives a candidate's "
               "full criteria, notes and checks: read it for any candidate you cannot "
               "settle by the test above, and for the cause you conclude on.")
    return out


def fmt_symptom(K: dict, sid: str, have: set[str], *, via: str | None = None) -> str:
    s = K["symptoms"][sid]
    out = [f"# {sid}: {s['title']}" + (f"   (alert {via} maps here)" if via else ""),
           "\ncandidate causes (these are HYPOTHESES -- discriminate, do not assume), "
           "cheapest to test first:"]
    out += fmt_candidates(K, s["claims"], have)
    return "\n".join(out)


def fmt_full(K: dict, claims: list[str], have: set[str]) -> str:
    out = []
    for cid in claims:
        out += fmt_claim(K, cid, have)
    conds = _conditions(K, claims)
    return "\n".join(out + fmt_checks(K, conds, have) + fmt_timing(K, conds))


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
        print(fmt_full(K, [args.alert], have))
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


def cmd_claim(kb: dict, args) -> int:
    K, have = kb.get("knowledge") or {}, available()
    unknown = [c for c in args.claims if c not in K.get("claims", {})]
    if unknown:
        print(f"no claim named {', '.join(map(repr, unknown))}. "
              f"claims: {', '.join(sorted(K.get('claims', {})))}")
        return 1
    print(fmt_full(K, args.claims, have))
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
    common, rare = sub_causes(K, c)
    out = [f"# sub-causes of {c['title']} [{args.claim}]",
           "they refine it, not rival it: each needs a disposition, and "
           "confirming one says which kind it is.\n"]
    if args.all:
        out += fmt_candidates(K, common + rare, have)
        if rare:
            out.append(f"\nuncommon: {', '.join(rare)} -- {RARE_RULE}.")
    else:
        out += fmt_candidates(K, common, have)
        if rare:
            out.append(f"\nuncommon, not shown: "
                       + ", ".join(f"{K['claims'][r]['title']} [{r}]" for r in rare)
                       + f". {RARE_RULE[0].upper()}{RARE_RULE[1:]}: "
                       f"`kb_query.py refine {args.claim} --all` tests them too.")
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
    s = sub.add_parser("claim", help="candidates' full criteria, notes and checks")
    s.add_argument("claims", nargs="+"); s.set_defaults(fn=cmd_claim)
    s = sub.add_parser("refine", help="the sub-causes a confirmed cause opens up")
    s.add_argument("claim")
    s.add_argument("--all", action="store_true", help="the uncommon sub-causes too")
    s.set_defaults(fn=cmd_refine)
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
