"""Design 015 step 2b: the search hit rate on the queries agents issued. No model, offline.

    uv run python tests/kb_eval/measure_2b.py [--half report|build|all] [--show-misses]

Labels: tests/kb_eval/labels-2b.json (committed before the first run). The search is run now
on each query text, with the default --limit, against today's knowledge_base.json.
  hit        the wanted entry (or an `also_ok` one) appears in the output
  no match   the output is 'no match ...'
  wrong      the output lists entries but not the wanted one
  none-ok    nothing exists for the query, and search returned no match
  none-spurious  nothing exists for the query, and search listed entries anyway
"""

import argparse
import io
import json
import sys
import types
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "k8s-rca"))
import kb_query  # noqa: E402


def run(kb, text):
    buf = io.StringIO()
    with redirect_stdout(buf):
        kb_query.cmd_search(kb, types.SimpleNamespace(text=text, limit=5))
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--half", default="report")
    ap.add_argument("--show-misses", action="store_true")
    a = ap.parse_args()
    data = json.loads((Path(__file__).parent / "labels-2b.json").read_text())["instances"]
    kb = kb_query.load()
    rows = []
    for x in data:
        if a.half != "all" and x["half"] != a.half:
            continue
        out = run(kb, x["text"])
        w = x["lab"]["wanted"]
        ok = {w, *x["lab"].get("also_ok", [])} - {"none"}
        nomatch = out.startswith("no match")
        if w == "none":
            v = "none-ok" if nomatch else "none-spurious"
        elif nomatch:
            v = "no match"
        elif any(i in out for i in ok):
            v = "hit"
        else:
            v = "wrong"
        rows.append((x, v))
    c = Counter(v for _, v in rows)
    n = len(rows)
    wanted = [r for r in rows if r[0]["lab"]["wanted"] != "none"]
    print(f"half={a.half}: {n} queries ({len({r[0]['text'].lower() for r in rows})} distinct)")
    print(f"  something exists to find: {len(wanted)}")
    for v in ("hit", "no match", "wrong"):
        k = sum(1 for _, vv in wanted if vv == v)
        print(f"    {v:9} {k:3} ({100 * k / max(len(wanted), 1):.0f}%)")
    nn = n - len(wanted)
    print(f"  nothing exists for it: {nn}")
    for v in ("none-ok", "none-spurious"):
        print(f"    {v:14} {c[v]:3} ({100 * c[v] / max(nn, 1):.0f}%)")
    byq = Counter()
    for x, v in rows:
        if x["lab"]["wanted"] != "none" and v != "hit":
            byq[(x["text"].lower(), x["lab"]["wanted"], v)] += 1
    if a.show_misses:
        for (t, w, v), k in byq.most_common():
            print(f"    {k}x {t!r} wanted {w}: {v}")


if __name__ == "__main__":
    main()
