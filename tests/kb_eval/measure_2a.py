"""Design 015 step 2a: candidate recall and false-leader rate, offline, no model.

    uv run python tests/kb_eval/measure_2a.py [--json out.json]

Labels are tests/kb_eval/labels-2a.yaml, committed before the first run. For each case:
`lookup`'s candidates (recall, rank, clutter) and `open`'s leader on the facts the facts
layer derives from the scenario's recorded results. Definitions are at the foot of the labels.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "k8s-rca"
SCEN = ROOT / "tests" / "scenarios"
sys.path.insert(0, str(SKILL))
import kb_query  # noqa: E402

from k8srca.facts.derive import derive  # noqa: E402
from k8srca.facts.log import FactLog  # noqa: E402

LEAD = re.compile(r"^most likely: (.*)$", re.M)
ITEM = re.compile(r"\[([\w-]+)\], (\w[\w-]*)")


def reachable(K, top):
    seen, order = set(), []
    def go(cid):
        if cid in seen:
            return
        seen.add(cid); order.append(cid)
        common, rare = kb_query.sub_causes(K, K["claims"][cid])
        for c in list(common) + list(rare):
            go(c)
    for c in top:
        go(c)
    return seen


def open_leader(K, sid, case, wd: Path, scen: str):
    """Run `open` in this process. Returns its text, the printed `most likely` chain, the
    leader `ranking` computed among the symptom's own candidates (added after the first run,
    when the two disagreed: see design 015 §10.3), and the number of facts."""
    import io
    import types
    from contextlib import redirect_stdout

    log = FactLog(wd)
    for p in sorted((SCEN / scen / "results").glob("*.json")):
        e = json.loads(p.read_text())
        if e.get("is_error"):
            continue
        r = e.get("structured") or json.loads(e["text"][0])
        log.add(derive(e["tool"].split("_", 1)[1], e["arguments"], r), {"tool": e["tool"], "n": e["n"]})
    seen = {}
    orig = kb_query.ranking

    def spy(KK, groups, st, have):
        out, leads = orig(KK, groups, st, have)
        label = next(iter(groups))  # the symptom's own candidates come first
        line = next((x for x in out if x.startswith(f"  {label}: ")), "")
        mm = re.search(r": ([\w-]+) leads \((\w+)\)", line)
        seen["top"] = [(mm.group(1), mm.group(2))] if mm else []
        return out, leads
    kb_query.ranking = spy
    kb_query.EVIDENCE = wd / "evidence-out"
    try:
        buf = io.StringIO()
        ns = types.SimpleNamespace(symptom=sid, workload=case["workload"], facts=None,
                                   facts_log=str(log.path))
        with redirect_stdout(buf):
            rc = kb_query.cmd_open(kb_query.load(), ns)
    finally:
        kb_query.ranking = orig
    out = buf.getvalue()
    m = LEAD.search(out)
    chain = ITEM.findall(m.group(1)) if m else []
    return out, chain, seen, len(log.facts)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--json")
    args = ap.parse_args()
    labels = yaml.safe_load((Path(__file__).parent / "labels-2a.yaml").read_text())["cases"]
    K = kb_query.load()["knowledge"]
    rows = []
    for scen, case in labels.items():
        sid = case["entry_symptom"]
        top = list(K["symptoms"][sid]["claims"])
        truth = set(case["truth_claims"]); acc = truth | set(case.get("accept", []))
        reach = reachable(K, top)
        rank = next((i + 1 for i, c in enumerate(top) if c in acc), None)
        recall = "top" if rank else ("reachable" if acc & reach else "absent")
        claim = [K["claims"][c] for c in truth if c in K["claims"]]
        authored = any(c.get("for") and c.get("rules_out") for c in claim)
        with tempfile.TemporaryDirectory() as d:
            out, chain, seen, nfacts = open_leader(K, sid, case, Path(d), scen)
        lead_ids = [c for c, _ in chain]
        grade = chain[-1][1] if chain else None
        if not chain:
            verdict = "no leader"
        elif acc & set(lead_ids):
            verdict = "correct"
        else:
            verdict = "false leader"
        top_lead = [c for c, _ in seen.get("top", [])]
        tgrade = seen["top"][0][1] if seen.get("top") else None
        if not top_lead:
            tv = "no leader"
        elif acc & (set(top_lead) | reachable(K, top_lead)):
            tv = "correct"
        else:
            tv = "false leader"
        rows.append(dict(scenario=scen, top_leader=top_lead, top_grade=tgrade, top_verdict=tv, symptom=sid, recall=recall, rank=rank, clutter=len(top),
                         facts=nfacts, leader=lead_ids, grade=grade, verdict=verdict,
                         authored=authored, truth=sorted(truth)))
    print("PRINTED leader (the `most likely:` line):")
    print(f"{'scenario':34}{'symptom':32}{'recall':10}{'rank':5}{'of':4}{'facts':6}{'verdict':13}leader (grade)")
    for r in rows:
        print(f"{r['scenario']:34}{r['symptom']:32}{r['recall']:10}{str(r['rank']):5}{r['clutter']:<4}{r['facts']:<6}"
              f"{r['verdict']:13}{'; '.join(r['leader']) or '-'} ({r['grade']})")
    n = len(rows)
    for lab, sel in (("all", rows), ("authored truth claim", [r for r in rows if r["authored"]]),
                     ("unauthored truth claim", [r for r in rows if not r["authored"]])):
        if not sel:
            print(lab, "n=0"); continue
        c = lambda v: sum(r["verdict"] == v for r in sel)
        print(f"{lab:24} n={len(sel):2} top={sum(r['recall']=='top' for r in sel)} "
              f"reachable={sum(r['recall']=='reachable' for r in sel)} absent={sum(r['recall']=='absent' for r in sel)}"
              f" | correct={c('correct')} false={c('false leader')} none={c('no leader')}")
    print("\nTOP-GROUP leader (what `ranking` computed among the symptom's own candidates):")
    for r in rows:
        print(f"{r['scenario']:34}{r['top_verdict']:13}{'; '.join(r['top_leader']) or '-'} ({r['top_grade']})"
              + ("" if r["top_leader"] == r["leader"][:1] else "   <- differs from printed"))
    for g in ("moderate", "strong", "weak"):
        pass
    print("printed false leaders by grade:", {g: sum(r["verdict"]=="false leader" and r["grade"]==g for r in rows) for g in ("strong","moderate","weak")})
    print("top-group verdicts:", {v: sum(r["top_verdict"]==v for r in rows) for v in ("correct","false leader","no leader")})
    print("printed differs from top-group in", sum(r["top_leader"] != r["leader"][:1] for r in rows), "of", len(rows))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
