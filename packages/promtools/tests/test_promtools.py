"""promtools against a fake Prometheus (design 009 §4.1, §5)."""

import gzip
import json

import pytest

from conftest import TARGETS, matrix, ok, vector
from promtools import notes as N
from promtools.capture import NODE_LABELS, capture
from promtools.client import PromClient, PromError
from promtools.server import build, when
from promtools.tools import Tools

END = 1792352556.0
AD = {"namespace": "default", "pod": "ad-5547bd5bd9-v65gj", "container": "ad",
      "job": "kube-state-metrics"}
AD_POD = {"__name__": "container_memory_working_set_bytes", "namespace": "default",
          "pod": "ad-5547bd5bd9-v65gj", "job": "kubernetes-nodes-cadvisor",
          "id": "/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod3c38_8153.slice"}


# -- the client can only read ---------------------------------------------------
class TestClient:
    def test_it_sends_get_and_reads_data(self, prom):
        prom.respond = lambda p, q: vector(({"a": "1"}, 2))
        assert PromClient(prom.url).query("up")["result"][0]["value"][1] == "2"
        assert prom.requests == [("GET", "/api/v1/query", {"query": ["up"]})]

    @pytest.mark.parametrize("path", ["/api/v1/admin/tsdb/snapshot", "/-/reload", "/-/quit",
                                      "/api/v1/write", "/api/v1/otlp/v1/metrics",
                                      "/api/v1/label/../admin/values"])
    def test_anything_else_is_refused_before_it_is_sent(self, prom, path):
        with pytest.raises(PromError, match="refused"):
            PromClient(prom.url).get(path)
        assert prom.requests == []

    def test_label_values_has_its_name_in_the_path(self, prom):
        prom.respond = lambda p, q: ok(["default"])
        assert PromClient(prom.url).label_values("namespace") == ["default"]
        assert prom.requests[0][1] == "/api/v1/label/namespace/values"

    def test_a_bad_query_says_why(self, prom):
        prom.respond = lambda p, q: {"status": "error", "errorType": "bad_data",
                                     "error": "parse error at char 3"}
        with pytest.raises(PromError, match="bad_data: parse error"):
            PromClient(prom.url).query("up{")

    def test_unreachable_is_an_error_not_an_empty_result(self):
        with pytest.raises(PromError):
            PromClient("http://127.0.0.1:9", timeout=2).query("up")


# -- notes ------------------------------------------------------------------------
class TestNotes:
    def test_durations(self):
        assert N.duration("90s") == 90 and N.duration("1h30m") == 5400
        assert N.duration("7d") == 604800
        with pytest.raises(ValueError):
            N.duration("7 days")

    def test_a_pod_cgroup_series_is_pod_level(self):
        assert N.pod_level(AD_POD)
        assert not N.pod_level({**AD_POD, "container": "ad"})
        assert not N.pod_level({"__name__": "kube_pod_info", "pod": "x"})

    def test_the_pod_level_oom_counter_is_not_evidence(self):
        """It read 0 through thousands of kills on benben (009 §2)."""
        notes = N.for_series([{**AD_POD, "__name__": "container_oom_events_total"}])
        assert any("is not evidence against OOM" in n for n in notes)

    def test_two_jobs_scraping_one_series_are_said(self):
        s = {"__name__": "kube_pod_container_status_restarts_total", **AD}
        notes = N.for_series([s, {**s, "job": "kubernetes-service-endpoints"}])
        assert any("double-count" in n for n in notes)

    def test_the_interval_and_the_retention_edge_are_said(self):
        notes = N.for_series([AD_POD], intervals={"kubernetes-nodes-cadvisor": 60},
                             oldest=END - 3600, start=END - 7 * 86400)
        assert any("every 1m" in n for n in notes)
        assert any("before the oldest retained sample" in n for n in notes)


# -- declared intervals ------------------------------------------------------------
class TestDeclaredIntervals:
    """A replay scrapes nothing, so it has no targets: the capture's intervals
    are declared to the server instead (k8srca's scenario replay, 009 §5)."""

    def test_they_fill_in_where_there_are_no_targets(self, prom):
        prom.respond = lambda p, q: ok({"activeTargets": []})
        t = Tools(PromClient(prom.url), {"kube-state-metrics": 15.0})
        assert t.intervals() == {"kube-state-metrics": 15.0}

    def test_a_live_target_wins(self, prom):
        prom.respond = lambda p, q: TARGETS
        t = Tools(PromClient(prom.url), {"kube-state-metrics": 999.0})
        assert t.intervals()["kube-state-metrics"] != 999.0

    def test_the_command_line_takes_them(self, monkeypatch):
        from promtools import cli
        got = {}
        monkeypatch.setattr(cli, "build", lambda c, iv: got.update(iv) or
                            type("S", (), {"run": lambda *a, **k: None})())
        cli.main(["serve", "--url", "http://p:9090", "--scrape-interval",
                  "kube-state-metrics=15s", "--scrape-interval", "kubelet=1m"])
        assert got == {"kube-state-metrics": 15.0, "kubelet": 60.0}
        with pytest.raises(SystemExit):
            cli.main(["serve", "--url", "http://p:9090", "--scrape-interval", "15s"])


# -- reports ----------------------------------------------------------------------
def answers(table):
    """A responder: the first key found in the query decides the answer."""
    def respond(path, params):
        if path == "/api/v1/targets":
            return TARGETS
        if path == "/api/v1/series":
            return ok([{"__name__": "kube_pod_container_status_restarts_total", **AD},
                       {"__name__": "kube_pod_container_status_restarts_total", **AD,
                        "job": "kubernetes-service-endpoints"}])
        q = params.get("query", [""])[0]
        for key, value in table:
            if key in q:
                return value
        return vector()
    return respond


class TestMemoryReport:
    def test_peak_against_limit_at_pod_level(self, prom):
        mib = 2 ** 20
        prom.respond = answers([
            ("prometheus_tsdb_lowest", vector(({}, END - 7 * 86400))),
            ("max_over_time(container_memory", vector((AD_POD, 300 * mib))),
            ("quantile_over_time", vector((AD_POD, 1.8 * mib))),
            ("count_over_time(container_memory", vector((AD_POD, 10080))),
            ("container_oom_events_total", vector(({**AD_POD, "__name__": "x"}, 0))),
            ("kube_pod_container_resource_limits", vector((AD, 300 * mib))),
        ])
        out = Tools(PromClient(prom.url)).memory_report("default", "ad-.*", end=END)
        [row] = out["rows"]
        assert row["level"] == "pod" and row["peak_mib"] == 300.0 and row["limit_mib"] == 300.0
        assert row["peak_of_limit"] == 1.0 and row["samples"] == 10080
        joined = " ".join(out["notes"])
        assert "not evidence against OOM" in joined and "read from kube-state-metrics only" in joined
        assert "98% or more of the limit" in joined
        # Limits come from one job only: nothing is counted twice.
        limit_q = [q["query"][0] for m, p, q in prom.requests if "resource_limits" in q.get("query", [""])[0]]
        assert limit_q and all('job="kube-state-metrics"' in q for q in limit_q)


class TestRestartReport:
    def table(self, oldest, at_first):
        return [
            ("prometheus_tsdb_lowest", vector(({}, oldest))),
            # Most specific first: the first key found in a query answers it.
            ("min_over_time((increase", vector((AD, 8.1))),
            ("max_over_time((increase", vector((AD, 14.2))),
            ("count_over_time((increase", vector((AD, 0))),
            ("min_over_time(timestamp(increase", vector((AD, END - 7 * 86400 + 60))),
            ("min_over_time(timestamp(", vector((AD, END - 7 * 86400 + 15))),
            ("increase(kube_pod_container_status_restarts_total{", vector((AD, 1724))),
            ("sum by (pod, container, reason)", vector(({**AD, "reason": "OOMKilled"}, 14888),
                                                      ({**AD, "reason": "Error"}, 25344))),
            ("kube_pod_container_status_restarts_total{", vector((AD, at_first))),
        ]

    def test_a_loop_older_than_retention_has_no_observed_onset(self, prom):
        prom.respond = answers(self.table(END - 7 * 86400, 5148))
        out = Tools(PromClient(prom.url)).restart_report("default", "ad-.*", end=END)
        [row] = out["rows"]
        assert row["restart_count_at_first_sample"] == 5148 and row["onset_in_window"] is False
        assert row["per_hour_min"] == 8.1 and row["hours_without_restart"] == 0
        assert row["last_reason_minutes"] == {"OOMKilled": 3722.0, "Error": 6336.0}
        assert any("onset is before the window" in n for n in out["notes"])

    def test_a_first_restart_inside_the_window_is_an_observed_onset(self, prom):
        prom.respond = answers(self.table(END - 7 * 86400, 0))
        [row] = Tools(PromClient(prom.url)).restart_report("default", "ad-.*", end=END)["rows"]
        assert row["onset_in_window"] is True

    def test_too_little_history_gives_no_per_hour_figures(self, prom):
        """benben, eleven minutes after the overlay emptied its volume."""
        prom.respond = answers(self.table(END - 660, 6889))
        out = Tools(PromClient(prom.url)).restart_report("default", "ad-.*", end=END)
        assert out["rows"][0]["per_hour_min"] is None
        assert any("less than two hours" in n for n in out["notes"])


# -- capture ------------------------------------------------------------------------
class TestCapture:
    def test_raw_samples_in_pieces_without_node_labels(self, prom, tmp_path):
        node = {"kubernetes_io_hostname": "minikube", "beta_kubernetes_io_os": "linux"}
        def respond(path, params):
            if path == "/api/v1/targets":
                return TARGETS
            if path == "/api/v1/status/runtimeinfo":
                return ok({"storageRetention": "2w"})
            q, t = params["query"][0], float(params["time"][0])
            if q.startswith("container_memory_working_set_bytes"):
                return matrix(({**AD_POD, **node}, [(t - 15, 1), (t, 2)]))
            return matrix()
        prom.respond = respond
        got = capture(PromClient(prom.url), tmp_path, end=END, window=12 * 3600,
                      selectors=['container_memory_working_set_bytes{namespace=~"default"}',
                                 'container_memory_working_set_bytes{id="/"}', "up"],
                      chunk=6 * 3600)
        queries = [(q["query"][0], float(q["time"][0])) for m, p, q in prom.requests
                   if p == "/api/v1/query"]
        assert ('container_memory_working_set_bytes{namespace=~"default"}[21600s]', END) in queries
        assert ('container_memory_working_set_bytes{namespace=~"default"}[21600s]',
                END - 21600) in queries
        text = gzip.open(got.path, "rt").read()
        assert text.count("# TYPE container_memory_working_set_bytes") == 1   # one family
        assert "kubernetes_io_hostname" not in text and 'pod="ad-5547bd5bd9-v65gj"' in text
        assert text.rstrip().endswith("# EOF") and got.samples == 4
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["end"] == END and manifest["retention"] == "2w"

    def test_one_hot_zeros_are_dropped_and_the_state_that_held_is_kept(self, prom, tmp_path):
        """benben's week: 80% of phase samples were 0, and 108 of 135 phase series
        never read anything else."""
        pod = {"__name__": "kube_pod_status_phase", **AD}
        def respond(path, params):
            if path == "/api/v1/targets":
                return TARGETS
            if path == "/api/v1/status/runtimeinfo":
                return ok({"storageRetention": "2w"})
            q, t = params["query"][0], float(params["time"][0])
            if q.startswith("kube_pod_status_phase"):
                return matrix(({**pod, "phase": "Running"}, [(t - 15, 1), (t, 1)]),
                              ({**pod, "phase": "Failed"}, [(t - 15, 0), (t, 0)]),
                              ({**pod, "phase": "Pending"}, [(t - 15, 1), (t, 0)]))
            if q.startswith("kube_pod_container_status_restarts_total"):
                return matrix(({"__name__": "kube_pod_container_status_restarts_total", **AD},
                               [(t - 15, 0), (t, 0)]))
            return matrix()
        prom.respond = respond
        got = capture(PromClient(prom.url), tmp_path, end=END, window=3600,
                      selectors=["kube_pod_status_phase", "kube_pod_container_status_restarts_total"],
                      chunk=3600)
        text = gzip.open(got.path, "rt").read()
        assert 'phase="Failed"' not in text                 # never held: not written
        assert text.count('phase="Running"') == 2 and text.count('phase="Pending"') == 1
        # A counter at 0 is a reading, not a one-hot state: kept.
        assert text.count("kube_pod_container_status_restarts_total{") == 2
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["one_hot_zeros_dropped"] == ["kube_pod_status_phase"]
        assert manifest["by_selector"]["kube_pod_status_phase"]["zeros_dropped"] == 3

    def test_one_hot_zeros_can_be_kept(self, prom, tmp_path):
        prom.respond = lambda p, q: (matrix(({"__name__": "kube_pod_status_phase", "phase": "Failed"},
                                             [(END, 0)])) if p == "/api/v1/query" else TARGETS)
        got = capture(PromClient(prom.url), tmp_path, end=END, window=3600,
                      selectors=["kube_pod_status_phase"], one_hot=frozenset())
        assert got.samples == 1

    def test_dropping_labels_that_would_merge_series_is_refused(self, prom, tmp_path):
        def respond(path, params):
            return matrix(({"__name__": "up", "kubernetes_io_hostname": "a"}, [(END, 1)]),
                          ({"__name__": "up", "kubernetes_io_hostname": "b"}, [(END, 1)]))
        prom.respond = respond
        with pytest.raises(ValueError, match="merge two series"):
            capture(PromClient(prom.url), tmp_path, end=END, window=3600, selectors=["up"],
                    drop=NODE_LABELS)


# -- the server ------------------------------------------------------------------------
async def test_the_server_offers_the_tools_and_nothing_that_writes(prom):
    tools = {t.name for t in await build(PromClient(prom.url)).list_tools()}
    assert tools == {"query", "query_range", "series", "label_values", "metadata", "targets",
                     "runtime_info", "memory_report", "restart_report"}


def test_times_are_rfc3339_or_unix_seconds():
    assert when("1792352556") == 1792352556.0
    assert when("2026-10-18T19:42:36Z") == pytest.approx(1792352556.0, abs=1)
    assert when(None) is None
