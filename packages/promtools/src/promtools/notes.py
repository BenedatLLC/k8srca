"""What a result does not say, attached to the result (design 009 §4.1).

Raw PromQL results read wrong in predictable ways, and each was seen on the
first cluster this was pointed at (009 §2):

- cAdvisor under some runtimes reports pod cgroups only, with no `container`
  label: a figure for the whole pod, not the container.
- A pod cgroup's OOM counter does not count kills of the containers inside
  it. It read 0 through thousands of kills, which reads as "checked, no OOM".
- A series scraped every minute says nothing about an 8-second life between
  two scrapes.
- A range that starts before the oldest retained sample shows when retention
  began, not when the series did.
- kube-state-metrics scraped by two jobs puts every series in twice.

k8stools attaches notes like these to its results (its 3.0 triage tools), and
answers stopped misreading them. These are the same thing for metrics.
"""

from __future__ import annotations

import re
from collections import defaultdict

POD_SLICE = re.compile(r"kubepods.*-pod[0-9a-f_]+\.slice$|kubepods/.*pod[0-9a-f-]+$")

#: Labels that identify what a series is about, as opposed to who scraped it.
SUBJECT = ("namespace", "pod", "container", "node", "resource", "reason", "condition", "status")


def duration(text: str) -> float:
    """Seconds in a PromQL duration (`90s`, `15m`, `7d`, `1h30m`)."""
    units = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"not a duration: {text!r}")
    return sum(float(n) * units[u] for n, u in parts)


def pod_level(labels: dict) -> bool:
    """A cAdvisor series for a pod's cgroup rather than a container's."""
    if not labels.get("__name__", "").startswith("container_"):
        return False
    if labels.get("container") not in (None, "", "POD"):
        return False
    return bool(labels.get("pod")) or bool(POD_SLICE.search(labels.get("id", "")))


def for_series(series: list[dict], *, intervals: dict[str, float] | None = None,
               oldest: float | None = None, start: float | None = None) -> list[str]:
    """Notes for a set of result series, each given by its labels."""
    notes: list[str] = []
    names = {s.get("__name__", "") for s in series}
    if any(pod_level(s) for s in series):
        notes.append(
            "pod-level cgroup: these container_* series have no `container` label, so each "
            "covers every container in the pod together, not one container. For a "
            "single-container pod that is the container's figure plus a little overhead.")
    if "container_oom_events_total" in names and any(pod_level(s) for s in series):
        notes.append(
            "container_oom_events_total at pod level does NOT count kills of the containers "
            "inside the pod: it reads 0 through any number of container OOM kills. A zero "
            "here is not evidence against OOM; use the termination reason series "
            "(kube_pod_container_status_last_terminated_reason) instead.")
    if "kube_pod_container_status_last_terminated_reason" in names:
        notes.append(
            "kube_pod_container_status_last_terminated_reason holds the reason of the most "
            "recent termination, one sample per scrape. Counting its samples measures how "
            "long each reason was the latest one, not how many kills had it; restarts "
            "closer together than the scrape interval are not all seen.")
    if "kube_pod_container_status_restarts_total" in names:
        notes.append(
            "kube_pod_container_status_restarts_total is a count since the pod started. A "
            "range shows restarts back to the oldest retained sample, not the pod's whole "
            "life; a value already above zero at the first sample means restarts began "
            "before it.")
    jobs_by_subject = defaultdict(set)
    for s in series:
        jobs_by_subject[(s.get("__name__"),) + tuple(s.get(k) for k in SUBJECT)].add(s.get("job"))
    dup = sorted({j for js in jobs_by_subject.values() if len(js) > 1 for j in js if j})
    if dup:
        notes.append(
            f"the same series is scraped by more than one job ({', '.join(dup)}): sums and "
            f"counts across jobs double-count. Filter on one job.")
    jobs = sorted({s.get("job") for s in series if s.get("job")})
    known = {j: intervals[j] for j in jobs if intervals and j in intervals}
    if known:
        shown = ", ".join(f"{j} every {_fmt(v)}" for j, v in known.items())
        notes.append(
            f"scraped {shown}: a container life or a spike shorter than the interval may "
            f"fall between two samples and not appear at all; a peak is the highest value "
            f"sampled, not necessarily the highest reached.")
    if oldest is not None and start is not None and start < oldest:
        notes.append(
            f"the range starts {_fmt(oldest - start)} before the oldest retained sample "
            f"(retention): nothing before {_iso(oldest)} is observable. A series that is "
            f"already non-zero there began earlier, at a time this cannot show.")
    return notes


def _fmt(seconds: float) -> str:
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= n and seconds % n == 0 or seconds >= 10 * n:
            return f"{seconds / n:g}{unit}" if seconds % n == 0 else f"{seconds / n:.1f}{unit}"
    return f"{seconds:g}s"


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
