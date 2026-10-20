"""Capture by query, for replay (design 009 §5).

A TSDB snapshot needs the admin API, which stays off, and is the whole
database. This reads raw samples instead, through the read-only API:

- **Raw samples.** An instant query whose expression is a range selector,
  `name{...}[6h]`, returns every sample in the range as scraped, not
  interpolated. Consecutive ranges `(t-6h, t]` do not overlap.
- **Small pieces.** The first export, in one-day ranges, timed out a 400Mi
  Prometheus' probes and cost it a restart (009 §2). Six hours is the default.
- **An allowlist,** not the database: the series an RCA uses (ALLOWLIST).
- **Node labels dropped.** cAdvisor's `labelmap` copies every node label onto
  every series. That is most of the bytes and none of the meaning. Dropping a
  label that would merge two series is refused.
- **Zeros of one-hot families dropped.** kube-state-metrics gives each pod one
  series per phase, and each condition one per status, and all but one read 0
  at any moment. On benben's week, 80% of phase samples were 0, 108 of its 135
  series never read anything else, and phase, ready and node condition were
  more than half of the allowlist's bytes. Exactly one state holds at a time,
  so a dropped 0 can be reconstructed: a state absent at a moment when another
  state of the same subject reads 1 was 0. The manifest lists the families
  trimmed. The reason families need no trim: kube-state-metrics emits only the
  current reason.

- **Known labels only.** A capture is committed and published, and it has no
  redaction pass, as the k8stools capture does. Every label is checked against
  LABELS, the names kube-state-metrics, cAdvisor and the scrape relabelling
  give the allowlist's series, none of which holds free text. An unknown name
  stops the capture: it may carry an annotation or a value nobody reviewed.
  `allow_labels` admits one deliberately.

Output is OpenMetrics text, gzipped, which `promtool tsdb
create-blocks-from openmetrics` builds into blocks, plus a manifest saying
what was asked, of whom, and what came back.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .client import PromClient

KSM = [
    "kube_pod_container_status_restarts_total",
    "kube_pod_container_status_last_terminated_reason",
    "kube_pod_container_status_last_terminated_exitcode",
    "kube_pod_container_status_last_terminated_timestamp",
    "kube_pod_container_status_waiting_reason",
    "kube_pod_container_status_terminated_reason",
    "kube_pod_container_status_ready",
    "kube_pod_container_status_running",
    "kube_pod_container_state_started",
    "kube_pod_container_resource_limits",
    "kube_pod_container_resource_requests",
    "kube_pod_status_phase",
    "kube_pod_status_ready",
    "kube_pod_info",
    "kube_pod_owner",
    "kube_deployment_spec_replicas",
    "kube_deployment_status_replicas_available",
]
CADVISOR = [
    "container_memory_working_set_bytes",
    "container_memory_rss",
    "container_memory_failcnt",
    "container_spec_memory_limit_bytes",
    "container_oom_events_total",
    "container_cpu_usage_seconds_total",
    "container_cpu_cfs_periods_total",
    "container_cpu_cfs_throttled_periods_total",
    "container_start_time_seconds",
    "container_pressure_memory_stalled_seconds_total",
]


def allowlist(namespaces: list[str]) -> list[str]:
    """The selectors a scenario capture takes for these namespaces."""
    ns = "|".join(re.escape(n) for n in namespaces)
    out = [f'{m}{{namespace=~"{ns}"}}' for m in KSM + CADVISOR]
    out += ['kube_node_status_condition', 'container_memory_working_set_bytes{id="/"}',
            'container_cpu_usage_seconds_total{id="/"}', "up",
            "prometheus_tsdb_lowest_timestamp_seconds"]
    return out


#: Families with one series per state, exactly one of which reads 1 at a time.
#: Their 0 samples are dropped (see the module docstring).
ONE_HOT = frozenset({
    "kube_pod_status_phase",
    "kube_pod_status_ready",
    "kube_node_status_condition",
})

#: Node labels cAdvisor's labelmap copies onto every series.
NODE_LABELS = re.compile(r"^(beta_kubernetes_io_|kubernetes_io_|node_kubernetes_io_|"
                         r"node_role_kubernetes_io_|topology_kubernetes_io_|minikube_k8s_io_)")


#: Label names a capture may write. Values are names, ids, addresses and
#: enumerations; none is free text.
LABELS = frozenset({
    "namespace", "pod", "container", "uid", "node", "instance", "job", "service",
    "id", "cpu", "resource", "unit", "reason", "phase", "condition", "status",
    "deployment", "created_by_kind", "created_by_name", "owner_kind", "owner_name",
    "owner_is_controller", "host_ip", "pod_ip", "host_network", "pod_template_hash",
    "s_app", "image", "image_id", "image_spec", "container_id", "name", "interface",
    "device", "priority_class", "qos_class",
})
#: Recommended-label prefixes, copied from pod labels by relabelling: app names.
LABEL_PREFIXES = ("app_kubernetes_io_",)


class UnknownLabel(ValueError):
    pass


def _check_labels(name: str, labels: dict, allow: frozenset[str]) -> None:
    for k in labels:
        if k in LABELS or k in allow or k.startswith(LABEL_PREFIXES):
            continue
        raise UnknownLabel(
            f"{name}: label {k!r} is not one a capture is known to hold safely. A capture "
            f"is published unredacted, so check what its values are, then pass it as "
            f"an allowed label (--allow-label {k})")


@dataclass
class Captured:
    path: Path
    series: int = 0
    samples: int = 0
    by_selector: dict[str, dict] = field(default_factory=dict)


def capture(client: PromClient, out: Path, *, end: float, window: float,
            selectors: list[str], chunk: float = 6 * 3600,
            drop: re.Pattern | None = NODE_LABELS,
            one_hot: frozenset[str] = ONE_HOT,
            allow_labels: frozenset[str] = frozenset()) -> Captured:
    """Export `selectors` over (end - window, end] to out/metrics.om.gz and
    out/manifest.json. `end` is the moment the k8stools capture was taken."""
    out.mkdir(parents=True, exist_ok=True)
    got = Captured(path=out / "metrics.om.gz")
    pieces = math.ceil(window / chunk)
    # OpenMetrics wants each family contiguous, so selectors are taken a
    # metric name at a time, and each family is written, then let go.
    groups: dict[str, list[str]] = defaultdict(list)
    for sel in selectors:
        groups[_name(sel)].append(sel)
    with gzip.open(got.path, "wt") as f:
        for name in sorted(groups):
            series: dict[tuple, dict] = {}
            for sel in groups[name]:
                seen, samples, zeros = set(), 0, 0
                for k in range(pieces):
                    span = min(chunk, window - k * chunk)
                    data = client.query(f"{sel}[{int(span)}s]", end - k * chunk)
                    if data.get("resultType") != "matrix":
                        raise ValueError(f"{sel}: expected a matrix, got {data.get('resultType')}")
                    for r in data["result"]:
                        labels = dict(r["metric"])
                        labels.pop("__name__", None)
                        original = tuple(sorted(labels.items()))
                        kept = tuple((k2, v) for k2, v in original if not (drop and drop.match(k2)))
                        _check_labels(name, dict(kept), allow_labels)
                        owner = series.setdefault(kept, {"from": original, "points": {}})
                        if owner["from"] != original:
                            raise ValueError(
                                f"{name}: dropping node labels would merge two series "
                                f"({dict(original)} and {dict(owner['from'])}); capture "
                                f"without dropping them")
                        for ts, v in r["values"]:
                            if name in one_hot and float(v) == 0:
                                zeros += 1
                                continue
                            owner["points"][float(ts)] = v
                            samples += 1
                        seen.add(kept)
                got.by_selector[sel] = {"series": len(seen), "samples": samples,
                                        **({"zeros_dropped": zeros} if zeros else {})}
            if not series:
                continue
            f.write(f"# TYPE {name} gauge\n")
            for kept, owner in series.items():
                if not owner["points"]:
                    continue                    # a one-hot state that never held
                ls = ",".join(f'{k}="{_esc(v)}"' for k, v in kept)
                for ts in sorted(owner["points"]):
                    f.write(f"{name}{{{ls}}} {owner['points'][ts]} {ts:.3f}\n")
                    got.samples += 1
                got.series += 1
        f.write("# EOF\n")
    info = client.runtime_info()
    manifest = {
        "prometheus": client.url, "end": end, "window_seconds": window,
        "chunk_seconds": chunk, "selectors": selectors,
        "dropped_labels": drop.pattern if drop else None,
        "one_hot_zeros_dropped": sorted(one_hot & set(groups)),
        "series": got.series, "samples": got.samples, "by_selector": got.by_selector,
        "retention": info.get("storageRetention"),
        "scrape_intervals": sorted({(t["labels"].get("job"), t.get("scrapeInterval"))
                                    for t in client.targets().get("activeTargets", [])}),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=list))
    return got


def _name(selector: str) -> str:
    m = re.match(r"[a-zA-Z_:][a-zA-Z0-9_:]*", selector)
    if not m:
        raise ValueError(f"a capture selector starts with its metric name: {selector!r}")
    return m.group()


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
