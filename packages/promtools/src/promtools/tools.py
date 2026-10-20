"""The tools, as plain functions over a client (design 009 §4.1).

Each returns a dict with the result and its `notes` (notes.py). server.py
exposes them over MCP; tests call them directly against a fake Prometheus.

Two kinds:
- **Passthrough** -- `query`, `query_range`, `series`, `label_values`,
  `metadata`, `targets`, `runtime_info`. PromQL in, series out, with notes
  from the labels of what came back.
- **Reports** -- `memory_report` and `restart_report`. The questions an RCA
  asks of a failing pod, answered with the queries that answer them
  correctly: one job where kube-state-metrics is scraped twice, the scrape
  interval beside every peak, the retention edge beside every count.
"""

from __future__ import annotations

import re
import time as _time
from typing import Any

from . import notes as N
from .client import PromClient

#: Series returned before the rest are cut; the agent narrows its query.
MAX_SERIES = 100
#: Points per series in a range result.
MAX_POINTS = 1500
MiB = 2 ** 20

KSM_RESTARTS = "kube_pod_container_status_restarts_total"
KSM_REASON = "kube_pod_container_status_last_terminated_reason"
KSM_LIMITS = "kube_pod_container_resource_limits"
WORKING_SET = "container_memory_working_set_bytes"
OOM_EVENTS = "container_oom_events_total"
KSM_STARTED = "kube_pod_container_state_started"
KSM_ENDED = "kube_pod_container_status_last_terminated_timestamp"
#: A life whose first sample is at this share of the limit or above started there.
NEAR_LIMIT = 0.9


class Tools:
    def __init__(self, client: PromClient, intervals: dict[str, float] | None = None):
        self.client = client
        self._intervals: dict[str, float] | None = None
        #: Per job, for a Prometheus with no targets to say: a replay, which
        #: scrapes nothing, serving what a live one scraped.
        self.declared = dict(intervals or {})

    # -- context every result needs -----------------------------------------
    def intervals(self) -> dict[str, float]:
        """Scrape interval per job, from the active targets."""
        if self._intervals is None:
            out: dict[str, float] = {}
            for t in self.client.targets().get("activeTargets", []):
                job, iv = t.get("labels", {}).get("job"), t.get("scrapeInterval")
                if job and iv:
                    out[job] = N.duration(iv)
            self._intervals = {**self.declared, **out}
        return self._intervals

    def oldest(self, at: float | None = None) -> float | None:
        """The oldest retained sample, from Prometheus' own TSDB metric, at `at`
        (default: now). Its latest value over a day, not an instant read: in a
        replay the metric is a captured series, and an instant read finds it
        stale five minutes after the capture's end."""
        r = self.client.query("min(last_over_time(prometheus_tsdb_lowest_timestamp_seconds[1d]))",
                              at)["result"]
        return float(r[0]["value"][1]) if r else None

    def covered(self, window: str, end: float) -> dict:
        """The window asked for, cut to what is retained: a report over "7d"
        of 31 retained hours covers 31 hours, and says so."""
        w = N.duration(window)
        oldest = self.oldest(end)
        span = end - max(end - w, oldest or end - w)
        if span >= w - 1:
            return {"window": window}
        return {"window": N._fmt(round(span)), "window_asked": window,
                "retained_from": N._iso(end - span)}

    def lives(self, sel: str, ksm: str, window: str, end: float) -> dict[str, list]:
        """Each life of each container, as (start, end), from kube-state-metrics:
        the start times it reported while a life ran, and the end times of the
        terminations it recorded. A life with no recorded end is still running."""
        def raw(metric: str) -> dict[tuple, list[float]]:
            out: dict[tuple, set[float]] = {}
            for r in self.client.query(f"{metric}{{{sel}{ksm}}}[{window}]", end)["result"]:
                key = (r["metric"].get("pod"), r["metric"].get("container"))
                out.setdefault(key, set()).update(float(v) for _, v in r.get("values", []))
            return {k: sorted(v) for k, v in out.items()}

        starts, ends = raw(KSM_STARTED), raw(KSM_ENDED)
        found: dict[tuple, list[tuple[float, float]]] = {}
        for key, ss in starts.items():
            es = ends.get(key, [])
            for s in ss:
                e = next((x for x in es if x > s), None)
                if e is not None:
                    found.setdefault(key, []).append((s, e))
        return found

    def _notes(self, series: list[dict], start: float | None = None,
               at: float | None = None) -> list[str]:
        return N.for_series(series, intervals=self.intervals(),
                            oldest=self.oldest(at) if start is not None else None, start=start)

    # -- passthrough -----------------------------------------------------------
    def query(self, promql: str, time: float | None = None) -> dict:
        data = self.client.query(promql, time)
        result, cut = _cut(data.get("result"))
        start = _range_start(promql, time)
        labels = [r["metric"] for r in result] if isinstance(result, list) else []
        return {"result_type": data.get("resultType"), "result": result,
                **({"cut": cut} if cut else {}), "notes": self._notes(labels, start, time)}

    def query_range(self, promql: str, start: float, end: float, step: str | float) -> dict:
        step_s = N.duration(step) if isinstance(step, str) else float(step)
        if (end - start) / step_s > MAX_POINTS:
            raise ValueError(f"{(end - start) / step_s:.0f} points per series is more than "
                             f"{MAX_POINTS}: use a larger step")
        data = self.client.query_range(promql, start, end, step_s)
        result, cut = _cut(data.get("result"))
        return {"result_type": data.get("resultType"), "result": result,
                **({"cut": cut} if cut else {}),
                "notes": self._notes([r["metric"] for r in result], start, end)}

    def series(self, match: list[str], start: float | None = None,
               end: float | None = None) -> dict:
        found, cut = _cut(self.client.series(match, start, end))
        return {"series": found, **({"cut": cut} if cut else {}), "notes": self._notes(found)}

    def label_values(self, label: str, match: list[str] | None = None) -> dict:
        return {"values": self.client.label_values(label, match)}

    def metadata(self, metric: str | None = None) -> dict:
        return {"metadata": self.client.metadata(metric)}

    def targets(self) -> dict:
        rows = [{"job": t["labels"].get("job"), "instance": t["labels"].get("instance"),
                 "health": t.get("health"), "scrape_interval": t.get("scrapeInterval"),
                 "last_error": t.get("lastError") or None}
                for t in self.client.targets().get("activeTargets", [])]
        return {"targets": rows}

    def runtime_info(self) -> dict:
        info = self.client.runtime_info()
        oldest = self.oldest()
        return {"retention": info.get("storageRetention"), "started": info.get("startTime"),
                "oldest_sample": N._iso(oldest) if oldest else None,
                "scrape_intervals": {j: N._fmt(v) for j, v in sorted(self.intervals().items())},
                "notes": ["nothing before oldest_sample is observable: an onset, a first "
                          "restart or a healthy period before it cannot be seen here"]}

    # -- reports -----------------------------------------------------------------
    def ksm_job(self, namespace: str) -> tuple[str | None, list[str]]:
        """The one job to read kube-state-metrics from, and a note if there were more."""
        jobs = sorted({s.get("job") for s in self.client.series(
            [f'{KSM_RESTARTS}{{namespace="{_q(namespace)}"}}'])} - {None})
        if not jobs:
            return None, []
        pick = "kube-state-metrics" if "kube-state-metrics" in jobs else jobs[0]
        note = ([f"kube-state-metrics is scraped by {len(jobs)} jobs ({', '.join(jobs)}); "
                 f"read from {pick} only, so nothing is counted twice"] if len(jobs) > 1 else [])
        return pick, note

    def memory_report(self, namespace: str, pod: str, window: str = "7d",
                      end: float | None = None) -> dict:
        """Working-set peaks against the memory limit, per container where the
        runtime reports containers and per pod where it does not."""
        end = end or _time.time()
        w = N.duration(window)
        sel = f'namespace="{_q(namespace)}",pod=~"{_q(pod)}"'
        job, notes = self.ksm_job(namespace)
        ksm = f',job="{_q(job)}"' if job else ""

        def by_subject(promql: str) -> dict[tuple, tuple[dict, float]]:
            out = {}
            for r in self.client.query(promql, end)["result"]:
                m = r["metric"]
                out[(m.get("pod"), m.get("container") or None)] = (m, float(r["value"][1]))
            return out

        peak = by_subject(f"max_over_time({WORKING_SET}{{{sel}}}[{window}])")
        median = by_subject(f"quantile_over_time(0.5, {WORKING_SET}{{{sel}}}[{window}])")
        samples = by_subject(f"count_over_time({WORKING_SET}{{{sel}}}[{window}])")
        oom = by_subject(f"increase({OOM_EVENTS}{{{sel}}}[{window}])")
        # The last limit in the window, not an instant read, which goes stale
        # five minutes after a pod is gone or a replay's capture ends.
        limits = by_subject(f'max by (pod, container) (last_over_time('
                            f'{KSM_LIMITS}{{{sel},resource="memory"{ksm}}}[{window}]))')
        raw = {(r["metric"].get("pod"), r["metric"].get("container") or None):
               [(float(t), float(v)) for t, v in r.get("values", [])]
               for r in self.client.query(f"{WORKING_SET}{{{sel}}}[{window}]", end)["result"]}
        lives = self.lives(sel, ksm, window, end) if job else {}
        rows, labels = [], []
        for key, (m, value) in sorted(peak.items(), key=lambda kv: str(kv[0])):
            pod_name, container = key
            level = "pod" if N.pod_level({**m, "__name__": WORKING_SET}) else "container"
            # A pod-level series is compared with the pod's containers' limits summed.
            lim = [v for (p, c), (_, v) in limits.items()
                   if p == pod_name and (level == "pod" or c == container)]
            limit = sum(lim) if lim else None
            rows.append({
                "pod": pod_name, "container": container, "level": level,
                "peak_mib": round(value / MiB, 1),
                "median_mib": round(median.get(key, (None, 0))[1] / MiB, 1) if key in median else None,
                "limit_mib": round(limit / MiB, 1) if limit else None,
                "peak_of_limit": round(value / limit, 3) if limit else None,
                "samples": int(samples.get(key, (None, 0))[1]),
                "oom_events": oom[key][1] if key in oom else None,
                "lives": _shape(raw.get(key, []), limit,
                                [x for (p, c), v in lives.items()
                                 if p == pod_name and (level == "pod" or c == container)
                                 for x in v]
                                if level == "container" or len({c for p, c in lives
                                                                if p == pod_name}) == 1
                                else []),
            })
            labels.append({**m, "__name__": WORKING_SET})
            if key in oom:
                labels.append({**oom[key][0], "__name__": OOM_EVENTS})
        notes += self._notes(labels, end - w, end)
        if any(r["peak_of_limit"] and r["peak_of_limit"] >= 0.98 for r in rows):
            notes.append("a peak at 98% or more of the limit means usage reached the limit in a "
                         "sampled moment; between samples it may have gone higher only to be "
                         "killed, which no sample shows")
        if any(r["lives"] for r in rows):
            notes.append("lives: each life from its start to its recorded termination, with "
                         "the working set sampled inside it. A life with fewer than two "
                         "samples shows no shape. One whose first sample is near the limit "
                         "did not climb within the life; rising_reached_limit_within says how "
                         "long the others took from their start")
        return {**self.covered(window, end), "end": N._iso(end), "rows": rows, "notes": notes}

    def restart_report(self, namespace: str, pod: str, container: str | None = None,
                       window: str = "7d", end: float | None = None) -> dict:
        """How often a container restarted across the window, whether it ever
        stopped, when the first restart in the window was, and which reasons its
        terminations had."""
        end = end or _time.time()
        w = N.duration(window)
        job, notes = self.ksm_job(namespace)
        if job is None:
            return {"rows": [], "notes": ["no kube-state-metrics restart series for this "
                                          "namespace: restarts cannot be seen in metrics here"]}
        sel = (f'namespace="{_q(namespace)}",pod=~"{_q(pod)}",job="{_q(job)}"'
               + (f',container="{_q(container)}"' if container else ""))
        r = f"{KSM_RESTARTS}{{{sel}}}"
        iv = self.intervals().get(job, 60.0)
        step = f"{max(int(iv), 1)}s"

        def per(promql: str) -> dict[tuple, float]:
            return {(x["metric"].get("pod"), x["metric"].get("container")): float(x["value"][1])
                    for x in self.client.query(promql, end)["result"]}

        total = per(f"increase({r}[{window}])")
        # Hours are counted only where a whole hour of samples is retained:
        # a partial first hour reads as "no restarts" when it is "no data".
        oldest = self.oldest(end)
        retained = end - max(end - w, oldest or end - w)
        hourly = int(retained - 3600)
        if hourly >= 3600:
            # Hours counted back from `end`, by a range query aligned to its
            # start. A subquery aligns them to the clock instead, so the same
            # samples cut at another time of day give other figures: a replay,
            # shifted by a different amount each run, gave 9-16 one run and
            # 8-14 the next.
            hours: dict[tuple, list[float]] = {}
            # A range query's points run from `start`, so it is whole hours back.
            start = end - (hourly // 3600) * 3600
            for x in self.client.query_range(f"increase({r}[1h])", start, end,
                                             3600)["result"]:
                key = (x["metric"].get("pod"), x["metric"].get("container"))
                hours[key] = [float(v) for _, v in x["values"]]
            hourly_min = {k: min(v) for k, v in hours.items() if v}
            hourly_max = {k: max(v) for k, v in hours.items() if v}
            quiet_hours = {k: sum(1 for x in v if x < 0.5) for k, v in hours.items()}
        else:
            hourly_min = hourly_max = quiet_hours = None
            notes.append(f"less than two hours of samples are retained "
                         f"({N._fmt(round(retained))}): per-hour figures are not given")
        first_seen = per(f"min_over_time(timestamp({r})[{window}:{step}])")
        # Two intervals: an increase over one holds a single sample and is empty.
        pair = f"{2 * max(int(iv), 1)}s"
        first_restart = per(f"min_over_time(timestamp(increase({r}[{pair}]) > 0)[{window}:{step}])")
        reasons: dict[tuple, dict[str, float]] = {}
        for x in self.client.query(
                f"sum by (pod, container, reason) "
                f"(count_over_time({KSM_REASON}{{{sel}}}[{window}]))", end)["result"]:
            key = (x["metric"].get("pod"), x["metric"].get("container"))
            reasons.setdefault(key, {})[x["metric"].get("reason")] = round(float(x["value"][1]) * iv / 60, 1)
        rows = []
        for key in sorted(total):
            fs = first_seen.get(key)
            at_first = None
            if fs is not None:
                v = self.client.query(f"{r}", fs)["result"]
                at_first = next((float(x["value"][1]) for x in v
                                 if (x["metric"].get("pod"), x["metric"].get("container")) == key), None)
            began_before = bool(at_first) and at_first > 0
            rows.append({
                "pod": key[0], "container": key[1],
                "restarts": round(total[key], 1),
                "per_hour_min": round(hourly_min.get(key, 0), 1) if hourly_min is not None else None,
                "per_hour_max": round(hourly_max.get(key, 0), 1) if hourly_max is not None else None,
                "hours_without_restart": (int(quiet_hours.get(key, 0))
                                          if quiet_hours is not None else None),
                "first_sample": N._iso(fs) if fs else None,
                "restart_count_at_first_sample": at_first,
                "first_restart_in_window": N._iso(first_restart[key]) if key in first_restart else None,
                "onset_in_window": (not began_before) and key in first_restart,
                "last_reason_minutes": reasons.get(key, {}),
            })
        labels = [{"__name__": KSM_RESTARTS, "job": job}, {"__name__": KSM_REASON, "job": job}]
        notes += self._notes(labels, end - w, end)
        if any(row["restart_count_at_first_sample"] for row in rows):
            notes.append("restart_count_at_first_sample above zero: the container was already "
                         "restarting when the first retained sample was taken, so the onset is "
                         "before the window and is not observed (onset_in_window false)")
        return {**self.covered(window, end), "end": N._iso(end), "job": job, "rows": rows,
                "notes": notes}


def _shape(samples: list[tuple[float, float]], limit: float | None,
           lives: list[tuple[float, float]]) -> dict | None:
    """The working set within each life: whether it climbs, or starts at the
    limit and stays there. None without both lives and a limit."""
    if not lives or not limit:
        return None
    lives = sorted(set(lives))
    near = NEAR_LIMIT * limit
    timed = [[(t - s, v) for t, v in samples if s <= t <= e] for s, e in lives]
    inside = [[v for _, v in x] for x in timed]
    lifetimes = sorted(e - s for s, e in lives)
    shaped = [x for x in inside if len(x) >= 2]
    firsts = sorted(x[0] / limit for x in shaped)
    # How long a life that starts below the limit takes to reach it: seconds
    # is startup, hours is accumulation.
    rising = [next(t for t, v in x if v >= near) for x in timed
              if len(x) >= 2 and x[0][1] < near <= max(v for _, v in x[1:])]
    return {
        "count": len(lives),
        "lifetime_min": N._fmt(round(lifetimes[0])),
        "lifetime_median": N._fmt(round(lifetimes[len(lifetimes) // 2])),
        "lifetime_max": N._fmt(round(lifetimes[-1])),
        "with_no_sample": sum(1 for x in inside if not x),
        "with_two_or_more_samples": len(shaped),
        "first_sample_of_limit_median": round(firsts[len(firsts) // 2], 3) if firsts else None,
        "starting_near_limit": sum(1 for x in shaped if x[0] >= near),
        "rising_to_limit": len(rising),
        "rising_reached_limit_within": N._fmt(round(max(rising))) if rising else None,
        "never_near_limit": sum(1 for x in shaped if max(x) < near),
    }


def _cut(result: Any) -> tuple[Any, int]:
    if isinstance(result, list) and len(result) > MAX_SERIES:
        return result[:MAX_SERIES], len(result) - MAX_SERIES
    if isinstance(result, list):
        for s in result:
            if isinstance(s, dict) and len(s.get("values") or []) > MAX_POINTS:
                s["values"] = s["values"][-MAX_POINTS:]
    return result, 0


def _range_start(promql: str, time: float | None) -> float | None:
    """The earliest moment an instant query's range selectors reach back to."""
    spans = [N.duration(d) for d in re.findall(r"\[(\d[\dsmhdwy.]*)(?::[^\]]*)?\]", promql)]
    return ((time or _time.time()) - max(spans)) if spans else None


def _q(value: str) -> str:
    """A value safe inside a PromQL double-quoted label matcher."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
