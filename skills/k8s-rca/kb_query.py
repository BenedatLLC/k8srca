#!/usr/bin/env python3
"""Query the Kubernetes RCA knowledge base.

Loading the whole base costs ~25K tokens of context for information that is
almost entirely irrelevant to any one incident. Query it instead.

  kb_query.py lookup CrashLoopBackOff     candidate causes, the quickest test of each
  kb_query.py lookup pod-restarting       the same, by symptom
  kb_query.py claim oom-kill bad-config   a candidate's full criteria, notes and checks
  kb_query.py refine oom-kill             the sub-causes a cause that survives opens up
  kb_query.py open CrashLoopBackOff --facts ad.facts
                                          every candidate's standing from the facts so far
  kb_query.py search "pod restart"        find candidate alerts by symptom or cause
  kb_query.py related OOMKilled           alerts that co-occur with this one
  kb_query.py promql PodPending           just the PromQL evidence queries
  kb_query.py list --category storage     browse by category or group

Self-contained: stdlib only, reads knowledge_base.json from its own directory.
"""

from __future__ import annotations

import argparse
import difflib
import fcntl
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

KB = Path(__file__).with_name("knowledge_base.json")
#: In a session the skill is <workdir>/skills/k8s-rca and the evidence record
#: <workdir>/evidence (007 §6.1). `open` leaves its rows there for the check the
#: host runs on the final answer; with no record beside it, it writes nothing.
EVIDENCE = Path(__file__).resolve().parents[2] / "evidence"


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


GRADE = {"weak": 1, "moderate": 2, "strong": 3}
GRADE_NAME = {v: k for k, v in GRADE.items()}


def weighted(c: dict) -> list[tuple[str, str, list[dict]]]:
    """A claim's alternatives as (part, weight, conditions): for and against
    by their weight, rules_out as decisive (008 §4.2)."""
    return ([("for", w["weight"], w["when"]) for w in c["for"]]
            + [("against", w["weight"], w["when"]) for w in c.get("against") or []]
            + [("rules_out", "decisive", a) for a in c["rules_out"]])


def quickest(K: dict, cid: str, have: set[str]) -> tuple[int | None, str, list[dict]]:
    """The cheapest test of a claim this deployment can make: a rules_out
    alternative over a `for` at the same cost (002 §6), and a stronger `for`
    over a weaker one. A weak `for` is a lean, not a test: it settles nothing."""
    c, best = K["claims"][cid], None
    tests = [(0, "ruled out if", a) for a in c["rules_out"]]
    tests += [(4 - GRADE[w["weight"]], f"{w['weight']} for it if", w["when"])
              for w in c["for"] if w["weight"] != "weak"]
    for rank, label, alt in tests:
        cost = _cost(K, alt, have)
        if cost is not None and (best is None or (cost, rank, len(alt)) < best[0]):
            best = ((cost, rank, len(alt)), label, alt)
    return (best[0][0], best[1], best[2]) if best else (None, "", [])


def fmt_alternatives(K: dict, label: str, alts: list[list[dict]], have: set[str],
                     weights: list[str] | None = None) -> list[str]:
    out = []
    for i, alt in enumerate(alts):
        lead = f"{label}:" if i == 0 else "or:".rjust(len(label) + 1)
        weight = f"({weights[i]}) " if weights else ""
        line = f"      {lead} {weight}" + " AND ".join(fmt_condition(K, c) for c in alt)
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


RARE_RULE = ("an uncommon one needs a place of its own only when the evidence points at "
             "it, or fits none of the common ones")


def fmt_claim(K: dict, cid: str, have: set[str]) -> list[str]:
    c = K["claims"][cid]
    out = [f"  - {c['title']}  [{cid}]"]
    out += fmt_alternatives(K, "for it", [w["when"] for w in c["for"]], have,
                            [w["weight"] for w in c["for"]])
    if c.get("against"):
        out += fmt_alternatives(K, "against it", [w["when"] for w in c["against"]], have,
                                [w["weight"] for w in c["against"]])
    out += fmt_alternatives(K, "ruled out when", c["rules_out"], have)
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
                   f"place in the answer too. They refine this cause; they are not its rivals."
                   + (f" It also lists {len(rare)} uncommon ones: {RARE_RULE}." if rare else ""))
    if c.get("refines_alert"):
        out.append(f"      NARROWS FURTHER: if this survives, `kb_query.py lookup "
                   f"{c['refines_alert']}` lists its sub-causes, and each of those "
                   f"needs a place in the answer too")
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


def fmt_checks(K: dict, conditions: list[dict], have: set[str],
               caveats: bool = True) -> list[str]:
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
            if caveats and a.get("quality") in ("point", "partial"):
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
                       "these is not ruled out, with this gap named:")
            unavailable_header = True
        reads = " ".join(r if r.endswith(".") else r + "." for r in row["read"])
        out.append(f"  {call}  ({requires})" + (f" -- {reads}" if reads else ""))
        out += [f"      {lim}" for lim in row["limits"]]
        out.append("      tells you whether: " + "; ".join(row["gives"]))
    return out


def _conditions(K: dict, claims: list[str]) -> list[dict]:
    return [x for cid in claims for _, _, alt in weighted(K["claims"][cid]) for x in alt]


TIMING = ("\ntiming: a change is evidence of a cause only when (1) the onset is "
          "observed -- the first failure itself is in what events, metrics or history "
          "retain. An event series' first_seen says the failures were already "
          "happening then, not that they began then; a pod's age, a revision's age and "
          "a restart count are not an onset either, and none of them says the "
          "failures ran continuously over that span -- and (2) the change "
          "took effect just before it. A change outside that window is history, not a "
          "trigger. With no observed onset, a change-as-trigger cannot be checked. A "
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
        needs = sorted({n for part, _, a in weighted(c) if part != "against"
                        for n in _missing(K, a, have)})
        out.append(f"      cannot be decided here (needs {', '.join(needs) or 'a person'}): "
                   f"not ruled out unless other evidence settles it")
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
    # What the timing paragraph and the per-check caveats warned about is a
    # fact's scope now, or a horizon fact (010 §4).
    out += fmt_checks(K, tested, have, caveats=False)
    out.append("\n`kb_query.py claim <id> [<id> ...]` gives a candidate's full criteria, "
               "notes and checks.")
    return out


def widened(K: dict, sid: str) -> tuple[str, str | None]:
    """The symptom to open for `sid`, and why, when that is another one.

    A symptom whose candidates are all one claim's sub-causes is that claim,
    taken as settled. OOMKilled is how a termination ended: evidence for
    oom-kill, one candidate for why a container restarts. Opened on its own it
    drops oom-kill's rivals, and answers starting from it never placed
    application-crash (009 §7.5). So it opens the symptom the claim is a
    candidate of, where its candidates are oom-kill's sub-causes."""
    S, claims = K.get("symptoms", {}), K.get("claims", {})
    mine = set(S[sid]["claims"])
    for cid, c in claims.items():
        if mine and mine <= set(c.get("refined_by") or []):
            for other, o in S.items():
                if other != sid and cid in o["claims"]:
                    return other, (f"{sid}'s candidates are all sub-causes of {cid}, one "
                                   f"candidate for {other}. Opened as {other}, so that "
                                   f"{cid}'s rivals get a place too. {sid}'s candidates are "
                                   f"{cid}'s sub-causes (`refine {cid}`).")
    return sid, None


def fmt_symptom(K: dict, sid: str, have: set[str], *, via: str | None = None) -> str:
    asked = sid
    sid, why = widened(K, sid)
    s = K["symptoms"][sid]
    maps = f"alert {via} maps to {asked}" if via else asked
    out = [f"# {sid}: {s['title']}" + (f"   ({maps}, which widens to it)" if why else
                                       f"   (alert {via} maps here)" if via else "")]
    if why:
        out.append(why)
    out += ["\ncandidate causes (these are HYPOTHESES -- discriminate, do not assume), "
           "cheapest to test first:"]
    out += fmt_candidates(K, s["claims"], have)
    return "\n".join(out)


def fmt_full(K: dict, claims: list[str], have: set[str]) -> str:
    out = []
    for cid in claims:
        out += fmt_claim(K, cid, have)
    conds = _conditions(K, claims)
    return "\n".join(out + fmt_checks(K, conds, have) + fmt_timing(K, conds))


# ------------------------------------------------------------------ open
#
# Design 007 §6.1, 008 §4.2: how the facts so far stand on each candidate, and
# what is left to look at. The model chooses to run it; it decides nothing
# about what to investigate. Three-valued: a fact not recorded is unknown,
# never false, so absence is not evidence.

T, U, F = 2, 1, 0
ROLES = {"C", "W", "P", "N", "S"}  # the container, workload, pod, node, service
RESERVED = (ONSET_FACT, CHANGE_FACT)  # extracted from the evidence record only
ROLE_NAMES = {"C": "C", "Container": "C", "W": "W", "Workload": "W", "P": "P", "Pod": "P",
              "N": "N", "Node": "N", "S": "S", "Service": "S"}


def role_params(K: dict, name: str) -> list[str | None]:
    """Each parameter's role letter, or None for a value (Code, Reason, ...)."""
    return [ROLE_NAMES.get(p) for p in K["facts"][name]["params"]]


FACTS_LOG = "facts.jsonl"  # the facts layer's record (010 §3.1)


def _workload_rows(log: Path, workload: str) -> list[dict]:
    """The facts recorded for `workload` (`ad` or `default/ad`)."""
    rows = []
    if not log.exists():
        return rows
    for line in log.read_text().splitlines():
        try:
            f = json.loads(line)
        except ValueError:
            continue
        parts = f.get("subject", "").split("/")
        if len(parts) >= 3 and workload in (parts[2], f"{parts[0]}/{parts[2]}"):
            rows.append(f)
    return rows


def workload_facts(log: Path, workload: str) -> tuple[list[str], list[str]]:
    """The knowledge-base lines the facts layer, and then the agent, recorded
    for `workload` (`ad` or `default/ad`)."""
    derived, agent = [], []
    for f in _workload_rows(log, workload):
        (agent if f.get("by") == "agent" else derived).extend(f.get("kb") or [])
    return derived, agent


def no_workload_facts(log: Path, workload: str) -> str:
    """Why `open --workload` has nothing to rank. Facts with no knowledge-base
    line are sentences only, such as a replica count: `facts` lists them, but
    no claim reads them, so "no facts recorded" would contradict that list."""
    rows = _workload_rows(log, workload)
    if not rows:
        return f"no facts recorded for workload {workload!r} in {log}"
    ids = ", ".join(f"[{f['id']}]" for f in rows if f.get("id"))
    return (f"nothing to rank for workload {workload!r}: its {len(rows)} recorded fact(s) "
            f"({ids}) state no fact the knowledge base's claims read. The workload's report "
            f"(get_workload_report) states most of those; or record one read from text "
            f"with `fact`.")


def cmd_fact(kb: dict, args) -> int:
    """Record an observation read from text, such as a log, that the facts
    layer cannot state (010 §3.4). It is footnoted like any other fact."""
    K = kb.get("knowledge") or {}
    _, problems = parse_facts(K, args.kb)
    if problems:
        print("\n".join(problems))
        return 1
    log = Path(args.facts_log) if args.facts_log else EVIDENCE / FACTS_LOG
    ns, _, name = args.workload.rpartition("/")
    log.parent.mkdir(parents=True, exist_ok=True)
    # The host appends derived facts to the same file: lock it, and number past
    # the highest id in it rather than its line count (k8srca #9).
    with log.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        fid = f"F{next_fact_id(fh.read())}"
        entry = {"id": fid, "subject": f"{ns or '?'}/Workload/{name}",
                 "key": args.kb.split("[")[0], "value": not args.kb.startswith("not "),
                 "statement": args.says, "scope": "", "kb": [args.kb], "by": "agent",
                 "source": {"n": args.source} if args.source is not None else {}}
        fh.write(json.dumps(entry) + "\n")
    print(f"[{fid}] {args.says}")
    return 0


def next_fact_id(text: str) -> int:
    """One past the highest `F<n>` id in a facts log's text."""
    top = 0
    for line in text.splitlines():
        try:
            m = re.fullmatch(r"F(\d+)", str(json.loads(line).get("id") or ""))
        except (ValueError, AttributeError):
            continue
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


def parse_facts(K: dict, text: str, derived: bool = False) -> tuple[dict, list[str]]:
    """`fact[args]` is observed true, `not fact[args]` observed false. Role
    letters (C, W, ...) stand for the one workload the file describes.
    `derived` is the facts layer's lines, which may hold the facts reserved to
    the evidence record (010 §3.3)."""
    store, problems = {}, []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].split(" @", 1)[0].strip()
        if not line:
            continue
        neg = line.startswith("not ")
        body = line[4:].strip() if neg else line
        if "[" not in body or not body.endswith("]"):
            problems.append(f"line {n}: {raw.strip()!r} is not fact[args]")
            continue
        name, args = body[:-1].split("[", 1)
        name, args = name.strip(), tuple(a.strip() for a in args.split(",") if a.strip())
        fact = K["facts"].get(name)
        if fact is None:
            near = difflib.get_close_matches(name, list(K["facts"]), n=4, cutoff=0.5)
            problems.append(f"line {n}: no fact {name!r}" + (f" (did you mean {', '.join(near)}?)" if near else ""))
        elif name in RESERVED and not derived:
            problems.append(f"line {n}: {name} is not recorded by hand: when the failures "
                            f"began comes from the evidence record (007 §3.4, §6.1)")
        elif len(args) != len(fact["params"]):
            problems.append(f"line {n}: {name} takes {len(fact['params'])} arguments "
                            f"({', '.join(fact['params'])}), not {len(args)}")
        else:
            # The file describes one workload, so a role argument is its letter
            # whatever was written there: `[ad, 137]` and `[Container, 137]` are `[C, 137]`.
            args = tuple(r or a for r, a in zip(role_params(K, name), args))
            if store.get((name, args), not neg) != (not neg):
                problems.append(f"line {n}: {raw.strip()!r} contradicts an earlier line; "
                                f"keep the one the evidence shows")
                continue
            store[(name, args)] = not neg
    return store, problems


def _value(K: dict, store: dict, c: dict, bind: dict) -> list[tuple[int, dict]]:
    """A condition's value under each way of binding its open variables."""
    name, args = c["fact"], [bind.get(a, a) for a in c["args"]]
    # A capitalised argument is a variable unless the build marked it a value
    # (`ErrImagePull` in `waiting_reason[C, ErrImagePull]`).
    open_vars = [a for a in args if a[:1].isupper() and a not in ROLES
                 and a not in (c.get("literals") or ())]
    out = []
    for (fname, fargs), truth in store.items():
        if fname != name or len(fargs) != len(args):
            continue
        b = dict(bind)
        if all(a == f or (a in open_vars and b.setdefault(a, f) == f) for a, f in zip(args, fargs)):
            v = T if truth else F
            for var, op, lit in c.get("compare") or []:
                x = b.get(var)
                if x is None:
                    v = U
                elif not {"==": x == lit, "!=": x != lit}.get(op, False):
                    v = F if truth else U
            out.append(((F if v == T else T if v == F else U) if c["negated"] else v, b))
    if K["facts"][name].get("functional") and not open_vars:
        # One value per subject: last_exit_code[C, 137] makes [C, 0] false.
        key = tuple(args[:-1])
        if any(fn == name and fa[:-1] == key and fa[-1] != args[-1] and t
               for (fn, fa), t in store.items()) and not any(b == bind for _, b in out):
            out.append((T if c["negated"] else F, bind))
    return out or [(U, bind)]


def _alternative(K: dict, store: dict, alt: list[dict]) -> tuple[int, list[dict]]:
    """Kleene AND over the conditions, at the best binding: the value, and the
    conditions still unknown there."""
    best = (F, alt)
    def walk(i, bind, value, unknown):
        nonlocal best
        if value == F or (value, -len(unknown)) <= (best[0], -len(best[1])) and value < T and i == len(alt):
            return
        if i == len(alt):
            if (value, -len(unknown)) > (best[0], -len(best[1])):
                best = (value, unknown)
            return
        c = alt[i]
        if not c.get("fact"):
            walk(i + 1, bind, min(value, U), unknown + [c])
            return
        for v, b in _value(K, store, c, bind):
            walk(i + 1, b, min(value, v), unknown + ([c] if v == U else []))
    walk(0, {}, T, [])
    return best


@dataclass
class Standing:
    """A claim's standing from the facts so far (008 §4.2)."""

    #: ruled-out, conflicting, strong, moderate, weak, open or could-not-check.
    status: str
    #: Its support after `against`, 0 to 3: what ranks it among its siblings.
    grade: int = 0
    #: The alternatives that decided it: the `for` that holds, or the rules_out.
    decided: list = field(default_factory=list)
    #: The cheapest alternatives still unknown that would move it.
    missing: list = field(default_factory=list)
    #: `against` alternatives that hold, as (weight, conditions).
    against: list = field(default_factory=list)
    #: Lifted to this grade by a sub-cause.
    via: str | None = None


def status(K: dict, store: dict, cid: str, have: set[str]) -> Standing:
    """The rule 008 §4.2 states, in its order: ruled out if a rules_out holds;
    otherwise the highest `for` that holds; each `against` that holds lowers
    it, strong by two grades and moderate by one. A weak `against` is listed
    and lowers nothing. A strong `for` and a rules_out together are a
    conflict: a fact is wrong."""
    c = K["claims"][cid]
    fors = [(GRADE[w["weight"]], _alternative(K, store, w["when"]), w["when"]) for w in c["for"]]
    ruled = [(_alternative(K, store, a), a) for a in c["rules_out"]]
    against = [(w["weight"], _alternative(K, store, w["when"]), w["when"])
               for w in c.get("against") or []]
    held = sorted(((g, a) for g, (v, _), a in fors if v == T), key=lambda ga: -ga[0])
    no = [a for (v, _), a in ruled if v == T]
    if no and held and held[0][0] == GRADE["strong"]:
        return Standing("conflicting", decided=[held[0][1]] + no)
    if no:
        return Standing("ruled-out", decided=no)
    against_held = [(w, a) for w, (v, _), a in against if v == T]
    grade = held[0][0] if held else 0
    grade -= sum({"strong": 2, "moderate": 1}.get(w, 0) for w, _ in against_held)
    # What would still move it: a rules_out, or a `for` stronger than what holds.
    live = [(u, a) for (v, u), a in ruled if v == U]
    live += [(u, a) for g, (v, u), a in fors if v == U and g > (held[0][0] if held else 0)]
    # Timing facts come from the evidence record, which is not extracted yet:
    # an alternative that needs one cannot be decided from recorded facts.
    decidable = [(u, a) for u, a in live if _cost(K, u, have) is not None
                 and not any(x.get("fact") in RESERVED for x in u)]
    decidable.sort(key=lambda ua: (_cost(K, ua[0], have), len(ua[0])))
    missing = [u for u, _ in decidable[:2]]
    if grade > 0:
        return Standing(GRADE_NAME[grade], grade, [held[0][1]], missing, against_held)
    if not decidable:
        return Standing("could-not-check", 0, [held[0][1]] if held else [],
                        [u for u, _ in live][:2], against_held)
    return Standing("open", 0, [held[0][1]] if held else [], missing, against_held)


def _cheapest(K: dict, c: dict, have: set[str]) -> tuple[int, str] | None:
    """The cheapest available check of a condition: (cost, tool)."""
    here = sorted((a["cost"], a.get("call") or n) for n, a in _producers(K, c)
                  if a["requires"] in have)
    return here[0] if here else None


def ranking(K: dict, groups: dict, st: dict, have: set[str]) -> tuple[list[str], set[str]]:
    """008 §4.2 rule 5: among siblings, the highest support leads. A tie at the
    top is no leading cause, with the check that would separate them."""
    out, leads = [], set()
    for label, members in groups.items():
        ranked = [m for m in members if st[m].grade > 0]
        if not ranked:
            continue
        top = max(st[m].grade for m in ranked)
        first = [m for m in ranked if st[m].grade == top]
        if len(first) == 1:
            leads.add(first[0])
            out.append(f"  {label}: {first[0]} leads ({GRADE_NAME[top]})")
            continue
        checks = sorted({c for m in first for u in st[m].missing for x in u
                         if x.get("fact") and x["fact"] not in RESERVED
                         and (c := _cheapest(K, x, have))})
        sep = f"; cheapest check that could separate them: {checks[0][1]}" if checks else \
            "; nothing available here separates them"
        out.append(f"  {label}: no leading cause -- {', '.join(first)} tie at "
                   f"{GRADE_NAME[top]}{sep}")
    return out, leads


DECIDED = ("strong", "moderate", "ruled-out", "conflicting")
SUPPORTED = ("strong", "moderate", "weak", "conflicting")


def owed_ids(K: dict, rows, parent) -> set[str]:
    """The claims an answer owes a place (009 §7.5).

    Every candidate the facts decided, and every one the authored knowledge
    names; below them, the decided sub-causes of a claim with support, which
    are the rivals beside the leader. A mined candidate nothing decided, or a
    sub-cause decided under an open claim, is offered and not owed: owing all
    of them took the restart family's owed list to 17, and the revisions that
    named them all carried more slips than they fixed."""
    status = {cid: st.status for _, cid, st in rows if cid is not None}
    out = set()
    for depth, cid, st in rows:
        if cid is None:
            continue
        if depth == 0:
            authored = K["claims"][cid].get("source", "").startswith("knowledge/")
            if st.status in DECIDED or authored:
                out.add(cid)
        elif st.status in DECIDED and status.get(parent.get(cid)) in SUPPORTED:
            out.add(cid)
    return out


STATUSES = ("strong", "moderate", "weak", "conflicting", "ruled-out", "open",
            "could-not-check")


def save_open(K: dict, sid: str, facts: str | None, rows, parent, leads) -> None:
    """This run's standings, for the completeness check on the final answer.
    Best-effort, like the evidence record: the agent's output never depends
    on it."""
    if not EVIDENCE.is_dir():
        return
    try:
        due = owed_ids(K, rows, parent)
        d = EVIDENCE / "open"
        d.mkdir(exist_ok=True)
        n = 1 + max((int(p.stem) for p in d.glob("*.json") if p.stem.isdigit()), default=0)
        doc = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "symptom": sid,
               "facts": facts and Path(facts).name,
               "rows": [{"id": cid, "title": K["claims"][cid]["title"], "status": st.status,
                         "depth": depth, "parent": parent.get(cid), "leads": cid in leads,
                         "via": st.via, "owed": cid in due}
                        for depth, cid, st in rows if cid is not None]}
        tmp = d / f".{n:04d}.tmp"
        tmp.write_text(json.dumps(doc))
        os.replace(tmp, d / f"{n:04d}.json")
    except OSError:
        pass


def cmd_open(kb: dict, args) -> int:
    K, have = kb.get("knowledge") or {}, available()
    sid = args.symptom
    if sid in K.get("alerts", {}):
        sid = K["alerts"][sid]["symptom"]
    if sid not in K.get("symptoms", {}):
        print(f"no symptom or converted alert {args.symptom!r}. symptoms: "
              f"{', '.join(K.get('symptoms', {}))}")
        return 1
    sid, why = widened(K, sid)
    store, problems = {}, []
    log = Path(args.facts_log) if args.facts_log else EVIDENCE / FACTS_LOG
    if getattr(args, "workload", None):
        derived, agent = workload_facts(log, args.workload)
        if not derived and not agent:
            print(no_workload_facts(log, args.workload))
            return 1
        store, problems = parse_facts(K, "\n".join(derived), derived=True)
        more, refused = parse_facts(K, "\n".join(agent))
        store.update(more)
        problems += refused
    if args.facts:
        more, refused = parse_facts(K, Path(args.facts).read_text())
        store.update(more)
        problems += refused
    out = [f"# {sid}: {len(store)} facts" + (f", {len(problems)} lines refused" if problems else "")]
    out += [f"  REFUSED {p}" for p in problems]
    if why:
        out.append(why)
    seen, rows, wanted = set(), [], []
    parent, st, groups = {}, {}, {f"{sid}'s candidates": list(K["symptoms"][sid]["claims"])}

    def visit(cid, depth):
        if cid in seen:
            return None
        seen.add(cid)
        here = st[cid] = status(K, store, cid, have)
        rows.append((depth, cid, here))
        if here.status not in ("ruled-out", "conflicting"):
            common, rare = sub_causes(K, K["claims"][cid])
            for sub in common:
                parent.setdefault(sub, cid)
                visit(sub, depth + 1)
            if common:
                groups[f"{cid}'s sub-causes"] = [x for x in common if parent.get(x) == cid]
            if rare:
                rows.append((depth + 1, None, rare))
            # A sub-cause's support lifts its claim to the same grade (008 §4.2
            # rule 4), which answers otherwise reported as open beside it.
            best = max(((st[x].grade, x) for x in common if x in st), default=(0, None))
            if best[0] > here.grade:
                st[cid] = here = Standing(GRADE_NAME[best[0]], best[0], [], [], here.against,
                                          via=best[1])
                rows[[r[1] for r in rows].index(cid)] = (depth, cid, here)
    for cid in K["symptoms"][sid]["claims"]:
        visit(cid, 0)
    ranks, leads = ranking(K, groups, st, have)
    ids = fact_ids(log, args.workload) if getattr(args, "workload", None) else {}
    out += fmt_open(K, rows, parent, leads, ranks, ids, have)
    save_open(K, sid, args.facts, rows, parent, leads)
    print("\n".join(out))
    return 0


def fact_ids(log: Path, workload: str) -> dict[tuple[str, bool], list[str]]:
    """(fact name, negated) -> the ids of the recorded facts that state it, so
    `open` can say which facts a standing rests on."""
    out: dict[tuple[str, bool], list[str]] = {}
    if not log.exists():
        return out
    for line in log.read_text().splitlines():
        try:
            f = json.loads(line)
        except ValueError:
            continue
        parts = f.get("subject", "").split("/")
        if len(parts) < 3 or workload not in (parts[2], f"{parts[0]}/{parts[2]}"):
            continue
        for kb in f.get("kb") or []:
            neg = kb.startswith("not ")
            name = (kb[4:] if neg else kb).split("[", 1)[0].strip()
            ids = out.setdefault((name, neg), [])
            if f.get("id") and f["id"] not in ids:
                ids.append(f["id"])
    return out


def _cited(conds: list[dict], ids: dict) -> str:
    """Conditions as the facts that hold them: `[F4]`, or the condition's text
    where no recorded fact states it."""
    parts = []
    for c in conds:
        found = ids.get((c.get("fact"), bool(c.get("negated"))))
        parts.append("".join(f"[{i}]" for i in found) if found else c["text"])
    return ", ".join(dict.fromkeys(parts))


def _check(K: dict, st: "Standing", have: set[str]) -> str:
    """The cheapest check that would decide a standing, in a few words."""
    for alt in st.missing:
        for c in alt:
            if c.get("fact") and c["fact"] not in RESERVED and (found := _cheapest(K, c, have)):
                says = " ".join(K["facts"][c["fact"]]["says"].split())
                for param, arg in zip(K["facts"][c["fact"]]["params"], c.get("args") or []):
                    if arg not in ROLES:
                        says = says.replace("{" + param + "}", arg)
                return f"{says} -- {found[1]}"
    return "nothing available here decides it"


def fmt_open(K: dict, rows, parent, leads, ranks, ids, have) -> list[str]:
    """Three things (010 §4): the leader and the facts behind it; what is ruled
    out, and by which fact; the open candidates, each with its cheapest check."""
    title = lambda cid: K["claims"][cid]["title"]  # noqa: E731
    st = {cid: s for _, cid, s in rows if cid is not None}
    top = [cid for d, cid, _ in rows if d == 0 and cid in leads]
    chain = []
    for cid in top[:1]:
        chain.append(cid)
        while True:
            subs = [c for c in leads if parent.get(c) == chain[-1]]
            if not subs:
                break
            chain.append(subs[0])
    out = []
    if chain:
        out.append("most likely: " + "; within it, ".join(
            f"{title(c)} [{c}], {st[c].status}" for c in chain))
        for c in chain:
            for alt in st[c].decided[:1]:
                out.append(f"  {c} rests on: {_cited(alt, ids)}")
            for w, alt in st[c].against:
                note = "say in a sentence why it still stands" if w == "weak" else "lowers it"
                out.append(f"  against {c} ({w}; {note}): {_cited(alt, ids)}")
            if st[c].missing and st[c].status != "strong":
                out.append(f"  {c} would be settled by: {_check(K, st[c], have)}")
    else:
        out.append("most likely: no leading cause")
        out += [ln for ln in ranks if "no leading cause" in ln]
    # Ruled out: the candidates, and the sub-causes of a claim with support,
    # which are the rivals beside it (as owed_ids decides). A sub-cause ruled
    # out under an undecided claim says nothing about that claim. Undecided
    # ones only among the candidates and the leader's sub-causes: the rest
    # are dozens.
    scope = {None} | set(chain)
    ruled = [(cid, s) for d, cid, s in rows if cid is not None and s.status == "ruled-out"
             and (d == 0 or st[parent[cid]].grade > 0)]
    if ruled:
        out.append("ruled out:")
        out += [f"  {title(c)} [{c}]: {_cited(s.decided[0], ids) if s.decided else '?'}"
                for c, s in ruled]
    conflicts = [cid for _, cid, s in rows if cid is not None and s.status == "conflicting"]
    for c in conflicts:
        out.append(f"conflicting: {title(c)} [{c}] is both supported and ruled out: one "
                   f"fact is wrong; check them")
    undecided = [(cid, s) for d, cid, s in rows if cid is not None and cid not in chain
                 and s.status not in ("ruled-out", "conflicting")
                 and (d == 0 or parent.get(cid) in scope)]
    if undecided:
        out.append("not decided, each with the cheapest check that would decide it:")
        for c, s in undecided:
            out.append(f"  {title(c)} [{c}] ({s.status}): {_check(K, s, have)}")
    return out


def cmd_facts(kb: dict, args) -> int:
    """The facts recorded so far, as the answer cites them (010 §5.2)."""
    log = Path(args.facts_log) if args.facts_log else EVIDENCE / FACTS_LOG
    if not log.exists():
        print(f"no facts recorded yet ({log})")
        return 0
    for line in log.read_text().splitlines():
        try:
            f = json.loads(line)
        except ValueError:
            continue
        parts = f.get("subject", "").split("/")
        if args.workload and args.workload not in (parts[2] if len(parts) > 2 else "",
                                                   "/".join(parts[:3:2])):
            continue
        scope = f" ({f['scope']})" if f.get("scope") else ""
        print(f"[{f.get('id')}] {f.get('statement')}{scope}")
    return 0


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
           "they refine it, not rival it: each needs a place in the answer, and "
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
    s = sub.add_parser("open", help="every candidate's status from the facts so far")
    s.add_argument("symptom", help="a symptom or a converted alert, e.g. CrashLoopBackOff")
    s.add_argument("--facts", help="the facts recorded so far, one per line")
    s.add_argument("--workload", help="read the facts recorded for this workload "
                                      "(ad, or default/ad) from the evidence record")
    s.add_argument("--facts-log", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_open)
    s = sub.add_parser("facts", help="the facts recorded so far, with the ids to cite")
    s.add_argument("--workload", help="only this workload's (ad, or default/ad)")
    s.add_argument("--facts-log", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_facts)
    s = sub.add_parser("fact", help="record an observation read from text, e.g. a log")
    s.add_argument("workload", help="ad, or default/ad")
    s.add_argument("kb", help="the fact, e.g. 'not process_logged_fatal_error[C]'")
    s.add_argument("--says", required=True, help="the sentence a footnote prints")
    s.add_argument("--from", dest="source", type=int, help="the tool result it was read from")
    s.add_argument("--facts-log", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_fact)
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
