"""Metrics in scenarios (design 009 §5): the config switch, the replay, the pin
and the capture at record time.

Hermetic: docker is never run here. The replay's commands are checked as
built, and a capture is generated in the test. `K8SRCA_TEST_DOCKER=1` adds one
test that builds a real block from a tiny capture and queries it.
"""

import gzip
import json
import os
import shutil
import urllib.request
from pathlib import Path

import pytest
import yaml

from k8srca import config as config_mod
from k8srca.scenario import record as record_mod
from k8srca.scenario import sources
from k8srca.scenario.model import ScenarioDir, StaleTruthError

from test_scenario_model import CAPTURE, write_scenario

END = 1_759_243_000.0
OM = f"""\
# TYPE kube_pod_container_status_restarts_total gauge
kube_pod_container_status_restarts_total{{container="ad",namespace="default",pod="ad-1"}} 6889 {END - 60:.3f}
kube_pod_container_status_restarts_total{{container="ad",namespace="default",pod="ad-1"}} 6890 {END:.3f}
# TYPE kube_pod_info gauge
kube_pod_info{{namespace="default",pod="ad-1",created_by_name="ad 7f9c"}} 1 {END:.3f}
# EOF
"""


def capture_file(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "metrics.om.gz"
    with gzip.open(path, "wt") as f:
        f.write(OM)
    return path


# --- the config switch ---------------------------------------------------------------


class TestConfig:
    def test_off_unless_switched_on(self, monkeypatch):
        """A deployment without Prometheus declares no prometheus capability:
        the knowledge base would send the agent to checks it cannot make."""
        monkeypatch.delenv(config_mod.METRICS_ENV, raising=False)
        cfg = config_mod.load()
        assert [s.name for s in cfg.mcp] == ["k8stools"]
        assert "prometheus" not in cfg.capabilities()

    def test_the_switch_wires_the_server_and_its_agents(self, monkeypatch):
        monkeypatch.setenv(config_mod.METRICS_ENV, "1")
        cfg = config_mod.load()
        assert [s.name for s in cfg.mcp] == ["k8stools", "promtools"]
        assert "prometheus" in cfg.capabilities()
        assert cfg.agents["rca-coordinator"].mcp_tools["promtools"] == "triage"
        assert cfg.agents["k8s-investigator"].mcp_tools["promtools"] == "full"
        # k8stools stays first: the runner and the wiki build read mcp[0].
        assert cfg.mcp[0].name == "k8stools"

    def test_a_scenario_points_it_at_its_replay(self, monkeypatch):
        monkeypatch.delenv(config_mod.METRICS_ENV, raising=False)
        cfg = config_mod.load().with_metrics(sync_url="http://127.0.0.1:9999/mcp")
        assert cfg.server("promtools").host_url() == "http://127.0.0.1:9999/mcp"
        # The sandbox's address is the image's, by alias on the scenario network.
        assert cfg.server("promtools").url == f"http://{sources.PROMTOOLS_ALIAS}:8000/mcp"

    def test_an_unknown_agent_or_group_is_refused(self):
        data = yaml.safe_load(Path("k8srca.yaml").read_text())
        data["metrics"]["agents"]["no-such-agent"] = "full"
        with pytest.raises(ValueError, match="unknown agent"):
            config_mod.Config.model_validate(data)
        data["metrics"]["agents"] = {"rca-coordinator": "no-such-group"}
        # A KeyError, as for an agent's own mcp_tools: pydantic passes it through.
        with pytest.raises(KeyError, match="no tool group"):
            config_mod.Config.model_validate(data)


# --- the replay ---------------------------------------------------------------------


class TestShift:
    def test_every_sample_moves_and_nothing_else_does(self, tmp_path):
        src = capture_file(tmp_path)
        out = tmp_path / "shifted.om"
        assert sources.shift_openmetrics(src, out, 1000.0) == 3
        lines = out.read_text().splitlines()
        assert lines[0] == "# TYPE kube_pod_container_status_restarts_total gauge"
        assert lines[-1] == "# EOF"
        assert lines[2].endswith(f" 6890 {END + 1000:.3f}")

    def test_a_label_value_with_a_space_survives(self, tmp_path):
        out = tmp_path / "shifted.om"
        sources.shift_openmetrics(capture_file(tmp_path), out, 5.0)
        assert 'created_by_name="ad 7f9c"} 1 ' in out.read_text()

    def test_a_time_valued_sample_moves_its_value_too(self, tmp_path):
        """A last-terminated time left unshifted would read as a year ago."""
        src = tmp_path / "t.om"
        src.write_text(f"# TYPE container_start_time_seconds gauge\n"
                       f'container_start_time_seconds{{pod="ad-1"}} {END - 8:.3f} {END:.3f}\n'
                       "# EOF\n")
        out = tmp_path / "shifted.om"
        sources.shift_openmetrics(src, out, 100.0)
        assert f" {END - 8 + 100:.3f} {END + 100:.3f}" in out.read_text()

    def test_the_retention_edge_is_where_the_capture_begins(self, tmp_path):
        """The live Prometheus kept 14 days and the capture took 7: the replay
        holds 7, and must not say it holds 14 -- promtools would read the empty
        week as a week without restarts, and an onset as observed."""
        src = tmp_path / "t.om"
        src.write_text(f"# TYPE {sources.LOWEST} gauge\n"
                       f"{sources.LOWEST} {END - 14 * 86400:.3f} {END:.3f}\n# EOF\n")
        out = tmp_path / "shifted.om"
        sources.shift_openmetrics(src, out, 10.0, start=END - 7 * 86400)
        assert f"{sources.LOWEST} {END - 7 * 86400 + 10:.3f} {END + 10:.3f}" in out.read_text()

    def test_the_capture_ends_when_the_replay_starts(self, tmp_path, monkeypatch):
        """The rebasing k8stools applies to ages (009 §5): one clock for both."""
        calls = []
        monkeypatch.setattr(sources, "_run", lambda *a, **k: calls.append(a) or _ok())
        shift = sources.build_block(capture_file(tmp_path / "in"), tmp_path / "w", END,
                                    now=END + 86400)
        assert shift == 86400
        [argv] = calls
        assert "promtool" in argv and "openmetrics" in argv
        assert f"--max-block-duration={sources.MAX_BLOCK}" in argv
        assert argv[argv.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def _ok(stdout=""):
    return type("R", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()


class TestReplay:
    def run_replay(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(sources, "_run", lambda *a, **k: calls.append(a) or _ok(
            "Server is ready to receive web requests Application startup complete true"))
        with sources.metrics_replay(capture_file(tmp_path), END, promtools="img:1") as mr:
            started = [a for a in calls if a[:3] == ("docker", "run", "-d")]
            work = [Path(a[a.index("-v") + 1].split(":")[0]).parent for a in started
                    if sources.PROMETHEUS_CONTAINER in a][0]
            prom_yml = (work / "prometheus.yml").read_text()
        return mr, started, prom_yml, calls

    def test_prometheus_scrapes_nothing(self, tmp_path, monkeypatch):
        """The database holds the capture and only the capture (009 §5)."""
        _, _, prom_yml, _ = self.run_replay(tmp_path, monkeypatch)
        assert "scrape" not in prom_yml

    def test_promtools_is_reached_by_its_alias_and_runs_unprivileged(self, tmp_path,
                                                                     monkeypatch):
        mr, started, _, _ = self.run_replay(tmp_path, monkeypatch)
        [prom, tools] = started
        assert sources.PROMETHEUS_CONTAINER in prom and "--cap-drop" in prom
        assert tools[tools.index("--network-alias") + 1] == sources.PROMTOOLS_ALIAS
        assert "--read-only" in tools and "--cap-drop" in tools
        assert tools[-2:] == ("--url", f"http://{sources.PROMETHEUS_CONTAINER}:9090")
        assert mr.host_url == "http://127.0.0.1:8011/mcp"

    def test_scrape_intervals_come_from_the_capture(self, tmp_path, monkeypatch):
        """A replay scrapes nothing, so it has no targets to read them from."""
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"window_seconds": 3600, "scrape_intervals": [["kube-state-metrics", "15s"],
                                                          ["kubelet", "15s"]]}))
        _, started, _, _ = self.run_replay(tmp_path, monkeypatch)
        tools = started[1]
        assert "kube-state-metrics=15s" in tools and "kubelet=15s" in tools

    def test_the_live_retention_is_what_the_replay_reports(self, tmp_path, monkeypatch):
        (tmp_path / "manifest.json").write_text(json.dumps({"retention": "2w or 15GiB"}))
        _, started, _, _ = self.run_replay(tmp_path, monkeypatch)
        assert "--storage.tsdb.retention.time=2w" in started[0]
        (tmp_path / "manifest.json").write_text(json.dumps({"retention": "15GiB"}))
        _, started, _, _ = self.run_replay(tmp_path, monkeypatch)
        assert "--storage.tsdb.retention.time=3650d" in started[0]

    def test_both_containers_are_removed_after(self, tmp_path, monkeypatch):
        _, _, _, calls = self.run_replay(tmp_path, monkeypatch)
        removed = [a[-1] for a in calls if a[:3] == ("docker", "rm", "-f")]
        assert removed[-2:] == [sources.PROMTOOLS_CONTAINER, sources.PROMETHEUS_CONTAINER]

    def test_a_missing_capture_is_an_error(self, tmp_path):
        with pytest.raises(sources.SourceError, match="not found"):
            with sources.metrics_replay(tmp_path / "nope.om.gz", END, promtools="img:1"):
                pass


# --- the pin ------------------------------------------------------------------------


def with_metrics(tmp_path, *, pin=True, end=None, digest=None):
    d = write_scenario(tmp_path)
    path = capture_file(d / "metrics")
    scenario = yaml.safe_load((d / "scenario.yaml").read_text())
    scenario["sources"]["prometheus"] = {"capture": "metrics/metrics.om.gz"}
    (d / "scenario.yaml").write_text(yaml.safe_dump(scenario))
    if pin:
        truth = yaml.safe_load((d / "truth.yaml").read_text())
        truth["capture"]["metrics"] = {
            "end": end or truth["capture"]["captured_at"],
            "digest": digest or sources.metrics_digest(path)}
        (d / "truth.yaml").write_text(yaml.safe_dump(truth))
    return d


class TestPin:
    def test_a_pinned_capture_is_current(self, tmp_path):
        sd = ScenarioDir(with_metrics(tmp_path), "legacy")
        sd.check_truth_is_current()
        assert sd.metrics_path.name == "metrics.om.gz"
        assert sd.metrics_end() is not None

    def test_a_truth_not_read_against_the_metrics_is_refused(self, tmp_path):
        sd = ScenarioDir(with_metrics(tmp_path, pin=False), "legacy")
        with pytest.raises(StaleTruthError, match="not written against them"):
            sd.check_truth_is_current()

    def test_a_changed_capture_is_refused(self, tmp_path):
        sd = ScenarioDir(with_metrics(tmp_path, digest="0" * 16), "legacy")
        with pytest.raises(StaleTruthError, match="changed since"):
            sd.check_truth_is_current()

    def test_two_moments_are_refused(self, tmp_path):
        sd = ScenarioDir(with_metrics(tmp_path, end="2020-01-01T00:00:00+00:00"), "legacy")
        with pytest.raises(StaleTruthError, match="two moments"):
            sd.check_truth_is_current()

    def test_a_pin_without_a_source_is_refused(self, tmp_path):
        d = with_metrics(tmp_path)
        scenario = yaml.safe_load((d / "scenario.yaml").read_text())
        del scenario["sources"]["prometheus"]
        (d / "scenario.yaml").write_text(yaml.safe_dump(scenario))
        with pytest.raises(StaleTruthError, match="no prometheus source"):
            ScenarioDir(d, "legacy").check_truth_is_current()

    def test_a_scenario_without_metrics_is_unaffected(self, tmp_path):
        sd = ScenarioDir(write_scenario(tmp_path), "legacy")
        sd.check_truth_is_current()
        assert sd.metrics_path is None and sd.metrics_end() is None


# --- recording ----------------------------------------------------------------------


class FakeProm:
    """promtools' client, answering a range selector with one series."""

    def __init__(self, url):
        self.url, self.asked = url, []

    def query(self, promql, time):
        self.asked.append((promql, time))
        if not promql.startswith("kube_pod_container_status_restarts_total"):
            return {"resultType": "matrix", "result": []}
        return {"resultType": "matrix", "result": [{
            "metric": {"__name__": "kube_pod_container_status_restarts_total",
                       "namespace": "default", "pod": "ad-1", "container": "ad"},
            "values": [[time - 15, "6889"], [time, "6890"]]}]}

    def runtime_info(self):
        return {"storageRetention": "14d"}

    def targets(self):
        return {"activeTargets": []}


class TestRecord:
    def test_metrics_are_taken_to_the_capture_moment(self, tmp_path, monkeypatch):
        import promtools.client

        made, built = [], []
        monkeypatch.setattr(promtools.client, "PromClient",
                            lambda url: made.append(FakeProm(url)) or made[-1])
        monkeypatch.setattr(sources, "CACHE", tmp_path / "cache")
        # The block build runs promtool in docker; here it is a directory.
        def fake_build(capture, work, end, now=None, manifest=None, **kw):
            built.append((end, now, manifest))
            (work / "data" / "01BLOCK").mkdir(parents=True)
            (work / "data" / "01BLOCK" / "meta.json").write_text("{}")
            return 0.0
        monkeypatch.setattr(sources, "build_block", fake_build)
        end = json.loads(CAPTURE.read_text())["captured_at"]
        got = record_mod.record_metrics(tmp_path, "http://prom:9090", end, ["default"],
                                        window="6h")
        assert got.end == end and got.series == 1 and got.samples == 2
        # Stored as a block, unshifted, pinned by the tarball's digest.
        assert got.path == tmp_path / "metrics" / "block.tar.gz"
        assert got.digest == sources.metrics_digest(got.path)
        [(at, now, manifest)] = built
        assert at == now and manifest["window_seconds"] == 6 * 3600
        # The text it was built from is the dump cache's, so no replay dumps it.
        assert (tmp_path / "cache" / f"{got.digest}.om.gz").exists()
        assert not (tmp_path / "metrics" / "metrics.om.gz").exists()
        when = record_mod.datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
        # Every query is anchored at the capture's moment, not at now.
        assert {t for _, t in made[0].asked} == {when}
        assert all('namespace=~"default"' in q for q, _ in made[0].asked
                   if q.startswith("kube_pod"))

    def test_no_namespace_is_refused(self, tmp_path):
        with pytest.raises(record_mod.RecordError, match="--namespace"):
            record_mod.record_metrics(tmp_path, "http://prom:9090",
                                      "2026-09-30T14:35:06+00:00", [])


# --- stored as a block, fetched by digest ------------------------------------------


def block_dir(root: Path) -> Path:
    d = root / "data" / "01K0BLOCK"
    (d / "chunks").mkdir(parents=True)
    (d / "chunks" / "000001").write_bytes(b"chunk")
    (d / "index").write_bytes(b"index")
    (d / "meta.json").write_text('{"ulid": "01K0BLOCK"}')
    (root / "data" / "wal").mkdir()
    (root / "data" / "lock").write_text("")
    return root / "data"


class TestBlocks:
    def test_packing_depends_only_on_the_files(self, tmp_path):
        """The digest is what truth.yaml pins: the same block packed again, or
        under another name, must give it again."""
        data = block_dir(tmp_path)
        first = sources.pack_block(data, tmp_path / "a" / "block.tar.gz")
        os.utime(data / "01K0BLOCK" / "index", (0, 12345))
        assert sources.pack_block(data, tmp_path / "b" / "other.tar.gz") == first

    def test_the_wal_and_the_lock_are_left_out(self, tmp_path):
        import tarfile

        dest = tmp_path / "block.tar.gz"
        sources.pack_block(block_dir(tmp_path), dest)
        names = tarfile.open(dest).getnames()
        assert "01K0BLOCK/index" in names and not any("wal" in n or "lock" in n for n in names)

    def test_a_local_checkout_is_used_before_the_network(self, tmp_path, monkeypatch):
        packed = tmp_path / "packed.tar.gz"
        digest = sources.pack_block(block_dir(tmp_path), packed)
        repo = tmp_path / "data-repo"
        (repo / "metrics").mkdir(parents=True)
        shutil.copyfile(packed, repo / sources.data_path(digest))
        monkeypatch.setenv(sources.DATA_DIR_ENV, str(repo))
        monkeypatch.setenv(sources.DATA_URL_ENV, "http://127.0.0.1:9/nowhere")
        got = sources.fetch_block(digest, tmp_path / "scenario" / "block.tar.gz")
        assert sources.metrics_digest(got) == digest

    def test_a_fetch_is_checked_against_its_digest(self, tmp_path, monkeypatch):
        repo = tmp_path / "data-repo"
        (repo / "metrics").mkdir(parents=True)
        (repo / sources.data_path("0123456789abcdef")).write_bytes(b"not it")
        monkeypatch.setenv(sources.DATA_DIR_ENV, str(repo))
        dest = tmp_path / "block.tar.gz"
        with pytest.raises(sources.SourceError, match="has digest"):
            sources.fetch_block("0123456789abcdef", dest)
        assert not dest.exists()

    def test_a_capture_nobody_published_says_so(self, tmp_path, monkeypatch):
        monkeypatch.delenv(sources.DATA_DIR_ENV, raising=False)
        monkeypatch.setenv(sources.DATA_URL_ENV, "http://127.0.0.1:9")
        with pytest.raises(sources.SourceError, match="scenario publish"):
            sources.fetch_block("0123456789abcdef", tmp_path / "block.tar.gz")

    def test_a_cached_dump_is_used_without_the_block(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "0123456789abcdef.om.gz").write_bytes(b"x")
        monkeypatch.setattr(sources, "fetch_block", lambda *a: pytest.fail("fetched"))
        got = sources.ensure_metrics(tmp_path / "absent.tar.gz", "0123456789abcdef",
                                     cache=cache)
        assert got == cache / "0123456789abcdef.om.gz"

    def test_a_block_that_is_not_the_pinned_one_is_fetched_again(self, tmp_path, monkeypatch):
        block = tmp_path / "block.tar.gz"
        block.write_bytes(b"stale")
        asked = []
        monkeypatch.setattr(sources, "fetch_block", lambda d, dest: asked.append(d))
        monkeypatch.setattr(sources, "dumped", lambda b, d, cache: cache / f"{d}.om.gz")
        sources.ensure_metrics(block, "0123456789abcdef", cache=tmp_path / "cache")
        assert asked == ["0123456789abcdef"]


class TestPinnedBlock:
    def test_an_unfetched_block_is_not_stale(self, tmp_path):
        """It is fetched, by the pinned digest, and checked, at replay."""
        d = with_metrics(tmp_path)
        (d / "metrics" / "metrics.om.gz").unlink()
        ScenarioDir(d, "legacy").check_truth_is_current()

    def test_the_manifest_comes_from_beside_the_block(self, tmp_path):
        d = with_metrics(tmp_path)
        (d / "metrics" / "manifest.json").write_text('{"window_seconds": 60}')
        assert ScenarioDir(d, "legacy").metrics_manifest() == {"window_seconds": 60}


class TestPublish:
    def test_it_commits_the_block_under_its_digest(self, tmp_path, monkeypatch):
        import subprocess

        from typer.testing import CliRunner

        from k8srca.cli import app

        repo = tmp_path / "data-repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"],
                       check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        d = with_metrics(tmp_path / "suite")
        block = d / "metrics" / "block.tar.gz"
        digest = sources.pack_block(block_dir(tmp_path / "b"), block)
        scenario = yaml.safe_load((d / "scenario.yaml").read_text())
        scenario["sources"]["prometheus"] = {}
        (d / "scenario.yaml").write_text(yaml.safe_dump(scenario))
        truth = yaml.safe_load((d / "truth.yaml").read_text())
        truth["capture"]["metrics"]["digest"] = digest
        (d / "truth.yaml").write_text(yaml.safe_dump(truth))
        r = CliRunner().invoke(app, ["scenario", "publish", "demo", "--root",
                                     str(tmp_path / "suite"), "--data-repo", str(repo)])
        assert r.exit_code == 0, r.output
        assert sources.metrics_digest(repo / sources.data_path(digest)) == digest
        log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                             capture_output=True, text=True).stdout
        assert f"metrics {digest}: demo" in log


# --- one real block, when docker is there -------------------------------------------


@pytest.mark.skipif(os.environ.get("K8SRCA_TEST_DOCKER") != "1" or not shutil.which("docker"),
                    reason="builds and serves a real block; set K8SRCA_TEST_DOCKER=1")
def test_a_tiny_capture_replays_through_a_real_prometheus(tmp_path):
    """The block promtool builds from a shifted capture answers the query the
    capture was taken with, at the shifted time (009 §7 step 3's gate)."""
    import subprocess
    import time

    work = tmp_path / "w"
    shift = sources.build_block(capture_file(tmp_path / "in"), work, END)
    (work / "prometheus.yml").write_text("global: {}\n")
    name = "k8srca-test-prometheus"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--user",
                    f"{os.getuid()}:{os.getgid()}", "-p", "127.0.0.1:19090:9090",
                    "-v", f"{work / 'data'}:/prometheus",
                    "-v", f"{work / 'prometheus.yml'}:/etc/prometheus/prometheus.yml:ro",
                    sources.PROMETHEUS_IMAGE, "--config.file=/etc/prometheus/prometheus.yml",
                    "--storage.tsdb.path=/prometheus", "--storage.tsdb.retention.time=3650d"],
                   check=True, capture_output=True)
    try:
        sources._await_log(name, ("Server is ready to receive web requests",))
        q = "kube_pod_container_status_restarts_total[5m]"
        url = (f"http://127.0.0.1:19090/api/v1/query?query={urllib.request.quote(q)}"
               f"&time={END + shift:.3f}")
        for _ in range(20):
            data = json.loads(urllib.request.urlopen(url).read())["data"]["result"]
            if data:
                break
            time.sleep(0.5)
        [series] = data
        assert [v for _, v in series["values"]] == ["6889", "6890"]
        assert series["values"][-1][0] == pytest.approx(END + shift, abs=0.01)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
