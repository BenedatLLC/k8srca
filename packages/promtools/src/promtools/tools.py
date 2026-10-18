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


class Tools:
    def __init__(self, client: PromClient):
        self.client = client
        self._intervals: dict[str, float] | None = None

    # -- context every result needs -----------------------------------------
    def intervals(self) -> dict[str, float]:
        """Scrape interval per job, from the active targets."""
        if self._intervals is None:
            out: dict[str, float] = {}
            for t in self.client.targets().get("activeTargets", []):
                job, iv = t.get("labels", {}).get("job"), t.get("scrapeInterval")
                if job and iv:
                    out[job] = N.duration(iv)
            self._intervals = out
        return self._intervals

    def oldest(self) -> float | None:
        """The oldest retained sample, from Prometheus' own TSDB metric."""
        r = self.client.query("min(prometheus_tsdb_lowest_timestamp_seconds)")["result"]
        return float(r[0]["value"][1]) if r else None

    def _notes(self, series: list[dict], start: float | None = None) -> list[str]:
        return N.for_series(series, intervals=self.intervals(),
                            oldest=self.oldest() if start is not None else None, start=start)

    # -- passthrough -----------------------------------------------------------
    def query(self, promql: str, time: float | None = None) -> dict:
        data = self.client.query(promql, time)
        result, cut = _cut(data.get("result"))
        start = _range_start(promql, time)
        labels = [r["metric"] for r in result] if isinstance(result, list) else []
        return {"result_type": data.get("resultType"), "result": result,
                **({"cut": cut} if cut else {}), "notes": self._notes(labels, start)}

    def query_range(self, promql: str, start: float, end: float, step: str | float) -> dict:
        step_s = N.duration(step) if isinstance(step, str) else float(step)
        if (end - start) / step_s > MAX_POINTS:
            raise ValueError(f"{(end - start) / step_s:.0f} points per series is more than "
                             f"{MAX_POINTS}: use a larger step")
        data = self.client.query_range(promql, start, end, step_s)
        result, cut = _cut(data.get("result"))
        return {"result_type": data.get("resultType"), "result": result,
                **({"cut": cut} if cut else {}),
                "notes": self._notes([r["metric"] for r in result], start)}

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
        limits = by_subject(f'max by (pod, container) ({KSM_LIMITS}{{{sel},resource="memory"{ksm}}})')
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
            })
            labels.append({**m, "__name__": WORKING_SET})
            if key in oom:
                labels.append({**oom[key][0], "__name__": OOM_EVENTS})
        notes += self._notes(labels, end - w)
        if any(r["peak_of_limit"] and r["peak_of_limit"] >= 0.98 for r in rows):
            notes.append("a peak at 98% or more of the limit means usage reached the limit in a "
                         "sampled moment; between samples it may have gone higher only to be "
                         "killed, which no sample shows")
        return {"window": window, "end": N._iso(end), "rows": rows, "notes": notes}

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
        oldest = self.oldest()
        retained = end - max(end - w, oldest or end - w)
        hourly = int(retained - 3600)
        if hourly >= 3600:
            span = f"{hourly}s"
            hourly_min = per(f"min_over_time((increase({r}[1h]))[{span}:1h])")
            hourly_max = per(f"max_over_time((increase({r}[1h]))[{span}:1h])")
            quiet_hours = per(f"count_over_time((increase({r}[1h]) < 0.5)[{span}:1h])")
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
        notes += self._notes(labels, end - w)
        if any(row["restart_count_at_first_sample"] for row in rows):
            notes.append("restart_count_at_first_sample above zero: the container was already "
                         "restarting when the first retained sample was taken, so the onset is "
                         "before the window and is not observed (onset_in_window false)")
        return {"window": window, "end": N._iso(end), "job": job, "rows": rows, "notes": notes}


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
