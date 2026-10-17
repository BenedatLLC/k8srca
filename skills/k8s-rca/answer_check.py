#!/usr/bin/env python3
"""Check a draft answer against the tool results it rests on (design 007 §6.1).

    answer_check.py <answer.md> [--evidence DIR] [--json]

Two checks, each a list of things to verify before sending, not a verdict:

  numbers  every count, duration and quantity in the answer should appear in
           some tool result, or follow from ones that do. One that appears in
           none is either arithmetic (show it) or not observed (drop it).
  scope    "every other workload is healthy", "only", "none": a claim about
           everything needs a result that covers everything. For workloads,
           the ones namespace health shows restarting are listed beside the
           claim: its `recovering` group, and restart counts on `healthy`
           lines (older results put both there).

DIR is the session's evidence record: evidence/<session>/ in the workdir,
one JSON file per tool call. By default it is found from
$ANTHROPIC_SESSION_ID, or is the only session under ./evidence.

Stdlib only: it runs in the sandbox beside kb_query.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- evidence

_ISO = re.compile(r"\bP(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?\b")
_GO = re.compile(r"\b(?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s\b")
_INT = re.compile(r"(?<![\w.])\d+(?![\w.])")


def load_evidence(root: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(root.glob("*.json"))]


def _texts(evidence: list[dict]) -> list[str]:
    return [t for e in evidence for t in e.get("text", [])]


def _list_lengths(node, out: set[int]) -> None:
    if isinstance(node, list):
        out.add(len(node))
        for x in node:
            _list_lengths(x, out)
    elif isinstance(node, dict):
        for x in node.values():
            _list_lengths(x, out)


class Haystack:
    """What the tool results contain, as numbers, durations and quantities."""

    def __init__(self, evidence: list[dict]):
        text = "\n".join(_texts(evidence))
        self.text = text
        self.ints = {int(m) for m in _INT.findall(text)}
        self.durations: list[float] = []
        for m in _ISO.finditer(text):
            if any(m.groups()):
                d, h, mi, s = (float(x or 0) for x in m.groups())
                self.durations.append(d * 86400 + h * 3600 + mi * 60 + s)
        for m in _GO.finditer(text):
            h, mi, s = (float(x or 0) for x in m.groups())
            self.durations.append(h * 3600 + mi * 60 + s)
        self.lengths: set[int] = set()
        for t in _texts(evidence):
            try:
                _list_lengths(json.loads(t), self.lengths)
            except ValueError:
                pass
        self.quantities = [b for q in _QTY.finditer(text) if (b := to_bytes(q.group())) is not None]


# ---------------------------------------------------------------- the answer

_QTY = re.compile(r"\b(\d+(?:\.\d+)?)\s?([KMGT]i|[kMGT])B?\b")
_FACTOR = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
           "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}


def to_bytes(token: str) -> float | None:
    m = _QTY.search(token)
    return float(m.group(1)) * _FACTOR[m.group(2)] if m else None


UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
         "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
         "d": 86400, "day": 86400, "days": 86400, "w": 604800, "week": 604800, "weeks": 604800}
_UNIT = "|".join(sorted(UNITS, key=len, reverse=True))
_APPROX = re.compile(r"(?:~|≈|about|around|roughly|approximately|nearly|almost|some)\s*$", re.I)
# 3d20h, 5m0s, 1h30m: compact durations, before the single-unit form.
_COMPACT = re.compile(r"\b(?:\d+d)?(?:\d+h)?(?:\d+m(?!i))?(?:\d+s)?\b")
_DURATION = re.compile(
    rf"(?P<a>\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(?P<b>\d+(?:\.\d+)?))?\s*(?P<u>{_UNIT})\b", re.I)
_MULTI = re.compile(rf"\d+(?:\.\d+)?\s*(?:{_UNIT})\b(?:,?\s*(?:and\s+)?\d+(?:\.\d+)?\s*(?:{_UNIT})\b)+",
                    re.I)
_QUANTITY = re.compile(r"\b\d+(?:\.\d+)?\s?(?:[KMGT]i(?:B)?|[kMG]B?)\b")
_COUNT = re.compile(r"(?<![\w.:/-])(\d{1,3}(?:,\d{3})+|\d+)(?:\s*(?:-|–)\s*(\d+))?(?![\w.:/%-])")
# Not quantities: dates, times, versions, ratios, revision and exit-code labels.
_SKIP = re.compile(r"\d{4}-\d\d-\d\d|\d\d?:\d\d|\bv?\d+\.\d+\.\d+|\b\d+/\d+\b|"
                   r"\b(?:revision|rev|exit(?: code)?|code|signal|SIG\w*|port|§)\s*#?\d+|"
                   r"\d+(?:\.\d+)?\s*%", re.I)


# Advice is not a claim about the cluster: "try 512Mi" needs no result.
_ADVICE = re.compile(r"\b(try|raise|increase|bump|lower|reduce|set|consider|recommend\w*|"
                     r"should|suggest\w*|e\.g\.|for example)\b", re.I)


def sentences(answer: str) -> list[str]:
    out = []
    for line in answer.splitlines():
        line = line.strip().lstrip("#>*-• ").strip()
        if line and not line.startswith("```"):
            out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z`*])", line) if s.strip()]
    return out


def _count_found(v: int, hay: Haystack) -> bool:
    """Exact, or a rounding of a large count: "~3600" for 3613."""
    return v in hay.ints or (v >= 100 and any(abs(v - n) <= 0.02 * n for n in hay.ints if n >= 100))


def _duration_found(secs: float, unit: float, approx: bool, hay: Haystack) -> bool:
    """In the answer's own unit, "163 days" is 163d16h truncated, "8s" is 8s; or
    within a relative tolerance, wider after "~" or "about"."""
    n, tol = secs / unit, secs * (0.35 if approx else 0.15)
    return any(int(d / unit) == n or round(d / unit) == n or abs(secs - d) <= tol
               for d in hay.durations)


def check_numbers(answer: str, hay: Haystack) -> list[dict]:
    flags = []
    for sent in sentences(answer):
        if _ADVICE.search(sent):
            continue
        masked = _SKIP.sub(lambda m: " " * len(m.group()), sent)
        # "exit 137, not 143": what was not observed needs no result.
        masked = re.sub(r"\b(?:not|nor|rather than|instead of)\s+\d+", lambda m: " " * len(m.group()), masked)
        masked = re.sub(r"\b(?:an|a|one)\s+(second|minute|hour|day|week)\b", r"1 \1", masked)
        missing: list[str] = []
        for m in _ISO.finditer(masked):  # the answer quoting a result: PT4M40S
            if any(m.groups()):
                d, h, mi, sec = (float(x or 0) for x in m.groups())
                if not _duration_found(d * 86400 + h * 3600 + mi * 60 + sec, 1, False, hay):
                    missing.append(m.group())
                masked = masked.replace(m.group(), " " * len(m.group()), 1)
        for m in _COMPACT.finditer(masked):
            tok = m.group()
            if len(re.findall(r"\d+[dhms]", tok)) >= 2:  # 3d20h, not a lone "8s"
                secs = sum(int(n) * UNITS[u] for n, u in re.findall(r"(\d+)([dhms])", tok))
                if not _duration_found(secs, 60, True, hay):
                    missing.append(tok)
                masked = masked.replace(tok, " " * len(tok), 1)
        for m in _MULTI.finditer(masked):
            parts = re.findall(rf"(\d+(?:\.\d+)?)\s*({_UNIT})\b", m.group(), re.I)
            secs = sum(float(n) * UNITS[u.lower()] for n, u in parts)
            if not _duration_found(secs, UNITS[parts[-1][1].lower()], True, hay):
                missing.append(m.group().strip())
            masked = masked.replace(m.group(), " " * len(m.group()), 1)
        for m in _DURATION.finditer(masked):
            unit = UNITS[m.group("u").lower()]
            approx = bool(_APPROX.search(masked[:m.start()]))
            for n in filter(None, (m.group("a"), m.group("b"))):
                if not _duration_found(float(n) * unit, unit, approx, hay):
                    missing.append(m.group().strip())
                    break
        masked = _DURATION.sub(lambda m: " " * len(m.group()), masked)
        for m in _QUANTITY.finditer(masked):
            b = to_bytes(m.group())
            if b is not None and not any(abs(b - q) <= 0.05 * q for q in hay.quantities):
                missing.append(m.group())
        masked = _QUANTITY.sub(lambda m: " " * len(m.group()), masked)
        for m in _COUNT.finditer(masked):
            for n in filter(None, (m.group(1), m.group(2))):
                v = int(n.replace(",", ""))
                # Small numbers are in every result; they decide nothing here.
                if v >= 10 and v not in hay.lengths and not _count_found(v, hay):
                    missing.append(m.group().strip())
                    break
        if missing:
            flags.append({"check": "numbers", "sentence": sent, "missing": missing})
    return flags


_UNIVERSAL = re.compile(
    r"\b(every|all|each|only|none|no other|nothing else|any other|everything else|the rest|"
    r"sole|entire|whole)\b|\b(?:the\s+)?other\s+(?:~\s*)?\d+\b", re.I)
_WORKLOADS = re.compile(r"\b(workloads?|services?|deployments?|pods?|namespace|apps?|"
                        r"siblings?|stack|everything else)\b", re.I)
_HEALTHY = re.compile(r"\b(healthy|fine|stable|unaffected|affected|impacted|normal|steady|"
                      r"broken|failing|not (?:crash|restart)\w*)\b", re.I)
_RESTARTS = re.compile(r"^(\S+)\s+(\d+)/(\d+)(?:,\s*(\d+)\s+restarts?)?")


_EXCEPT = re.compile(r"\b(every|each|all)\s+(start|restart|attempt|time|life|instance|"
                     r"candidate|run|cycle|replica)\w*", re.I)


def restarting(evidence: list[dict]) -> list[tuple[str, str]]:
    """Ready workloads that have restarted, as namespace health lists them:
    `recovering` (a restart in its last 24h, k8stools >= 4.0) first, most
    recent first, then `healthy` lines that carry a restart count."""
    recovering: dict[str, str] = {}
    older: dict[str, int] = {}
    for e in evidence:
        if not e.get("tool", "").endswith("get_namespace_health"):
            continue
        for t in e.get("text", []):
            try:
                body = json.loads(t)
            except ValueError:
                continue
            if not isinstance(body, dict):
                continue
            for w in body.get("recovering", []):
                last = w.get("last_termination") or {}
                detail = ", ".join(x for x in (
                    f"{w['restarts']} restarts" if w.get("restarts") is not None else "",
                    f"last exit {last['exit_code']}" if last.get("exit_code") is not None else "",
                    last.get("reason", "")) if x)
                recovering.setdefault(w["workload"], f"recovering: {detail}")
            for line in body.get("recovering_more", []):
                m = _RESTARTS.match(line)
                if m:
                    recovering.setdefault(m.group(1), "recovering: " + line[len(m.group(1)):].strip(" ,"))
            for line in body.get("healthy", []):
                m = _RESTARTS.match(line)
                if m and m.group(4):
                    older[m.group(1)] = max(older.get(m.group(1), 0), int(m.group(4)))
    out = list(recovering.items())
    out += [(w, f"{n} restarts") for w, n in sorted(older.items(), key=lambda kv: -kv[1])
            if w not in recovering]
    return out


def check_scope(answer: str, evidence: list[dict]) -> list[dict]:
    """Claims about every workload, and workloads named as healthy, beside
    what namespace health says about their restarts."""
    flags, listed = [], restarting(evidence)
    short = {w.split("/")[-1]: (w, n) for w, n in listed}
    for sent in sentences(answer):
        healthy = _HEALTHY.search(sent)
        universal = _UNIVERSAL.search(_EXCEPT.sub(" ", sent))
        # Only claims about workloads' health: "every start", "no events
        # recorded" and the like were most of the noise, offline (007 §6.1).
        if universal and healthy and _WORKLOADS.search(sent):
            flags.append({"check": "scope", "sentence": sent,
                          "against": [f"{w} {d}" for w, d in listed[:8]]})
        elif healthy:
            named = [short[n] for n in short if re.search(rf"`?\b{re.escape(n)}\b`?", sent)]
            if named:
                flags.append({"check": "scope", "sentence": sent,
                              "against": [f"{w} {d}" for w, d in named]})
    return flags


# ---------------------------------------------------------------- cli

def find_evidence(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    base = Path("evidence")
    sid = os.environ.get("ANTHROPIC_SESSION_ID")
    if sid and (base / sid).is_dir():
        return base / sid
    subs = [p for p in base.iterdir() if p.is_dir()] if base.is_dir() else []
    return subs[0] if len(subs) == 1 else None


def check(answer: str, evidence: list[dict]) -> list[dict]:
    return check_numbers(answer, Haystack(evidence)) + check_scope(answer, evidence)


def render(flags: list[dict]) -> str:
    if not flags:
        return "no flags: every number traces to a result, and no claim covers everything."
    out = []
    nums = [f for f in flags if f["check"] == "numbers"]
    if nums:
        out.append("NUMBERS in no tool result -- show the arithmetic, or drop them:")
        for f in nums:
            out.append(f"  - {', '.join(f['missing'])}   in: {f['sentence'][:160]}")
    scope = [f for f in flags if f["check"] == "scope"]
    if scope:
        out.append("\nCLAIMS ABOUT EVERYTHING -- cite the result that covers every case:")
        for f in scope:
            out.append(f"  - {f['sentence'][:200]}")
            if f.get("against"):
                out.append("      namespace health shows these restarting, though ready: "
                           + "; ".join(f["against"]))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("answer")
    ap.add_argument("--evidence")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = find_evidence(args.evidence)
    if root is None or not root.is_dir():
        print("no evidence record found: pass --evidence DIR", file=sys.stderr)
        return 2
    flags = check(Path(args.answer).read_text(), load_evidence(root))
    print(json.dumps(flags, indent=1) if args.json else render(flags))
    return 0


if __name__ == "__main__":
    sys.exit(main())
