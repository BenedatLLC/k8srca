"""Deterministic checks (design 004 §6.2).

These run before any grader, cost nothing, and never flake. They exist because
a capture is a *closed world*: every pod, container, image and namespace the
agent could legitimately cite is in the file, so anything it names that is not
there was invented. Against a live cluster you can only ask whether an answer
sounds right; here you can ask whether every entity it names exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .model import ScenarioDir


@dataclass
class Finding:
    check: str
    detail: str


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)
    #: Reported but not gating. Some signals are worth surfacing on every run
    #: and worth failing none: they measure the weather rather than the answer.
    advisories: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings

    def fail(self, check: str, detail: str) -> None:
        self.findings.append(Finding(check, detail))

    def advise(self, check: str, detail: str) -> None:
        self.advisories.append(Finding(check, detail))


# A Kubernetes *generated* name: a workload name followed by controller-assigned
# segments, e.g. `ad-5547bd5bd9-v65gj` or the replica set `ad-5547bd5bd9`.
#
# Checking every bare word against the capture is the obvious approach and is
# unusable: `ad`, `cart`, `checkout` and `frontend` are ordinary English, and a
# grader that flags them cries wolf on every answer until it is ignored. What is
# distinctive is the controller's hash segment -- five or more characters mixing
# letters and digits -- which essentially never occurs in prose. So the strict
# check is scoped to names carrying one. That is also where fabrication actually
# shows up: an agent that invents a pod invents a plausible-looking full name.
_NAME_TOKEN = re.compile(r"\b[a-z0-9]+(?:[-.][a-z0-9]+)+\b")
_HASH_SEGMENT = re.compile(r"^(?=[a-z0-9]{5,})(?=.*[a-z])(?=.*[0-9])[a-z0-9]+$")

# `repo/name:tag` or `host/repo/name:tag`. Tags are cited often and mistyped
# easily, and an image that is not in the capture is a hard fabrication.
_IMAGE_TOKEN = re.compile(r"\b[a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)+:[A-Za-z0-9._-]+\b")


def _looks_generated(token: str) -> bool:
    """True when a token carries a controller-assigned hash segment."""
    parts = token.split("-")
    return len(parts) >= 2 and any(_HASH_SEGMENT.match(p) for p in parts[1:])


def capture_entities(capture: dict) -> dict[str, set[str]]:
    """Every name the agent could legitimately cite, by kind."""
    out: dict[str, set[str]] = {k: set() for k in
                                ("pod", "container", "namespace", "node", "workload", "image")}

    def names(items: Iterable[Any], key: str = "name") -> set[str]:
        return {i[key] for i in items or [] if isinstance(i, dict) and i.get(key)}

    out["namespace"] |= names(capture.get("namespaces"))
    out["node"] |= names(capture.get("nodes"))
    for kind in ("deployments", "replicasets", "services", "statefulsets",
                 "configmaps", "cronjobs", "jobs", "pvcs"):
        out["workload"] |= names(capture.get(kind))

    for pod in capture.get("pods") or []:
        summary = pod.get("summary") or {}
        if summary.get("name"):
            out["pod"].add(summary["name"])
        for cs in pod.get("container_statuses") or []:
            if cs.get("container_name"):
                out["container"].add(cs["container_name"])
            if cs.get("image"):
                out["image"].add(cs["image"])
    return out


def closed_world(answer: str, capture: dict) -> CheckResult:
    """Fail on any entity the answer names that the capture does not contain."""
    result = CheckResult()
    known = capture_entities(capture)
    all_names = set().union(*known.values())

    cited = {t for t in _NAME_TOKEN.findall(answer) if _looks_generated(t)}
    for token in sorted(cited - all_names):
        # An image reference contains '/' and ':' and is handled below; a bare
        # generated name that matches nothing is a fabricated object.
        if "/" in token or ":" in token:
            continue
        result.fail("closed_world",
                    f"answer names {token!r}, which is not in the capture")

    for image in sorted(set(_IMAGE_TOKEN.findall(answer)) - known["image"]):
        result.fail("closed_world",
                    f"answer cites image {image!r}, which is not in the capture")
    return result


# "1907 restarts", "restarted 1907 times", "restart count of 1907"
_RESTARTS = re.compile(
    r"(?:(\d[\d,]*)\s*(?:restarts|times)|restart count(?:\s+of)?\s*[:=]?\s*(\d[\d,]*))",
    re.I)


#: How far back to look for the object a number is about.
_ATTRIBUTION_WINDOW = 90


def numeric_claims(answer: str, capture: dict) -> CheckResult:
    """Fail on a restart count attributed to an object the capture disagrees with.

    Narrow twice over. A number is only checkable when we know which object and
    field it belongs to, and restart counts are the case where prose reliably
    says so -- they are also what a crash-loop answer cites most.

    The attribution requirement is the second narrowing, and it was learned the
    hard way. A correct answer reasoned "154 days of continuous looping at the
    5m backoff cap would produce on the order of ~44,000 restarts; `ad` has
    2,165" -- deriving a bound in order to argue the looping is intermittent.
    Flagging every "<N> restarts" failed that answer on its best sentence. A
    number with no object named near it is not a claim about any container, so
    it is left alone; the rubric grader sees the capture and can judge context
    that a regex cannot.
    """
    result = CheckResult()
    entities = capture_entities(capture)
    names = {n.lower() for n in entities["pod"] | entities["container"] | entities["workload"]}
    counts = {cs.get("restart_count")
              for pod in capture.get("pods") or []
              for cs in pod.get("container_statuses") or []}
    counts.discard(None)
    if not counts or not names:
        return result
    for match in _RESTARTS.finditer(answer):
        raw = match.group(1) or match.group(2)
        value = int(raw.replace(",", ""))
        if value in counts:
            continue
        window = answer[max(0, match.start() - _ATTRIBUTION_WINDOW):match.start()].lower()
        attributed = next((n for n in names
                           if re.search(r"(?<![a-z0-9-])" + re.escape(n) + r"(?![a-z0-9-])",
                                        window)), None)
        if attributed is None:
            continue
        result.fail("numeric_claims",
                    f"answer claims {value} restarts for {attributed!r}; no container "
                    f"in the capture has that count")
    return result


def must_identify(answer: str, terms: list[str]) -> CheckResult:
    """Fail when the answer never names something the cause turns on.

    Matched on word boundaries rather than as bare substrings. Truth files name
    short things -- a container called `ad`, a limit of `300Mi` -- and plain
    `in` would find `ad` inside "read", "load" and "already", passing an answer
    that never mentions the container at all.
    """
    result = CheckResult()
    for term in terms:
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
        if not re.search(pattern, answer, re.I):
            result.fail("must_identify", f"answer never mentions {term!r}")
    return result


def required_tools(called: Iterable[str], required: list[str]) -> CheckResult:
    """Fail when a tool the scenario exists to provoke was never called."""
    result = CheckResult()
    seen = {_unprefixed(name) for name in called}
    for tool in required:
        if _unprefixed(tool) not in seen:
            result.fail("required_tools", f"{tool} was never called")
    return result


def _unprefixed(name: str) -> str:
    """Drop a worker tool prefix so `k8s_get_events` matches `get_events`."""
    return name.split("_", 1)[1] if name.startswith("k8s_") else name


def budget(tool_calls: int, usd: float, limits) -> CheckResult:
    """Gate on tool calls; report cost.

    Cost is advisory because it does not measure what a budget is for. Across
    13 runs of one unchanged scenario, tool calls varied 2.3x (19-44, sd 7.7)
    while cost varied 3.4x ($0.25-$0.85, sd $0.16) -- and the run with the
    tightest call spread had the widest cost spread. They decouple: cost tracks
    thinking and delegation, not how many times the agent reached for a tool.

    A dollar cap derived from either therefore fails correct answers on an
    expensive day, which happened repeatedly before this. Tool calls remain a
    gate because running away is a real failure mode and the count is what
    004 §5's stopping scenario is about.
    """
    result = CheckResult()
    if tool_calls > limits.max_tool_calls:
        result.fail("budget",
                    f"{tool_calls} tool calls exceeds max_tool_calls={limits.max_tool_calls}")
    if usd > limits.max_usd:
        result.advise("budget",
                      f"${usd:.4f} over the ${limits.max_usd:.2f} advisory (cost is not "
                      f"a gate: it varies ~3x run to run on identical input)")
    return result


def run_all(sd: ScenarioDir, answer: str, called: Iterable[str],
            tool_calls: int, usd: float) -> CheckResult:
    """Every deterministic check, in the order that fails most cheaply first."""
    capture = sd.capture()
    combined = CheckResult()
    for part in (
        required_tools(called, sd.scenario.requires_tools),
        budget(tool_calls, usd, sd.scenario.budget),
        must_identify(answer, sd.truth.cause.must_identify),
        closed_world(answer, capture),
        numeric_claims(answer, capture),
    ):
        combined.findings.extend(part.findings)
        combined.advisories.extend(part.advisories)
    return combined
