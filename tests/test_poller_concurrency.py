"""The host poller's claim loop (design 001 §7.3, 003 §3.2).

A sandbox container lives for its session's whole active period plus `max_idle`
(60s), not for one turn. Calling spawn() inline from the claim loop therefore
parks every new session behind the previous one's idle timeout -- measured at
59s of dead wait for a second Slack thread with nothing running. These tests
pin the fix: spawns go on a pool, and an item already in flight is not spawned
a second time when the control plane redelivers it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from k8srca.worker import poller as P
from k8srca.worker.poller import SpawnConfig


def work(work_id: str, session_id: str = "sess_1", kind: str = "session"):
    return SimpleNamespace(
        id=work_id,
        environment_id="env_1",
        secret="wsec_x",
        data=SimpleNamespace(type=kind, id=session_id),
        model_dump_json=lambda: json.dumps({"id": work_id}),
    )


def spawn_config(tmp_path, max_concurrent=4):
    return SpawnConfig(script=Path("/bin/true"), image="img@sha256:abc", network="k8srca-net",
                       memory="2g", cpus="2", workspaces=tmp_path / "ws",
                       manifests={"k8stools": "deadbeef"}, max_concurrent=max_concurrent)


def client():
    return SimpleNamespace(beta=SimpleNamespace(
        environments=SimpleNamespace(work=SimpleNamespace())))


class Recorder:
    """A fake spawn that tracks how many run at once."""

    def __init__(self, gate=None):
        self.lock = threading.Lock()
        self.calls: list[str] = []
        self.live = 0
        self.peak = 0
        self.gate = gate

    def __call__(self, work, cfg):
        with self.lock:
            self.calls.append(work.id)
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            if self.gate is not None:
                self.gate(work)
        finally:
            with self.lock:
                self.live -= 1
        return 0


def feed(*items, exhausted: threading.Event | None = None):
    def iter_work(_work_api, **kwargs):
        assert kwargs["auto_stop"] is False        # the container stops its own item
        assert kwargs["reclaim_older_than_ms"] == 30_000
        yield from items
        if exhausted is not None:
            exhausted.set()
    return iter_work


class TestParallelSpawning:
    def test_three_sessions_run_at_once(self, monkeypatch, tmp_path):
        # The regression test for the 59s dead wait: if spawn() were called
        # inline the barrier would never fill and peak would be 1.
        barrier = threading.Barrier(3)
        rec = Recorder(gate=lambda w: barrier.wait(timeout=10))
        monkeypatch.setattr(P, "spawn", rec)
        monkeypatch.setattr(P, "iter_work", feed(work("w1", "s1"), work("w2", "s2"),
                                                 work("w3", "s3")))

        P.run(client(), "env_1", spawn_config(tmp_path))
        assert rec.peak == 3
        assert sorted(rec.calls) == ["w1", "w2", "w3"]

    def test_concurrency_is_capped(self, monkeypatch, tmp_path):
        # Each container holds a cluster connection and a few hundred MB;
        # unbounded fan-out is how the host falls over.
        barrier = threading.Barrier(2)
        rec = Recorder(gate=lambda w: barrier.wait(timeout=10))
        monkeypatch.setattr(P, "spawn", rec)
        monkeypatch.setattr(P, "iter_work",
                            feed(*[work(f"w{i}", f"s{i}") for i in range(4)]))

        P.run(client(), "env_1", spawn_config(tmp_path, max_concurrent=2))
        assert rec.peak == 2
        assert len(rec.calls) == 4


class TestRedelivery:
    def test_an_item_already_in_flight_is_not_spawned_twice(self, monkeypatch, tmp_path):
        # Reclaim can hand the poller the same item again while its container
        # is still up. Spawning a second one would put two containers on the
        # same session workspace.
        release = threading.Event()
        exhausted = threading.Event()
        rec = Recorder(gate=lambda w: release.wait(timeout=10))
        monkeypatch.setattr(P, "spawn", rec)
        monkeypatch.setattr(P, "iter_work",
                            feed(work("w1", "s1"), work("w1", "s1"), exhausted=exhausted))

        runner = threading.Thread(target=P.run, args=(client(), "env_1", spawn_config(tmp_path)))
        runner.start()
        assert exhausted.wait(timeout=10), "poller never drained the queue"
        assert rec.calls == ["w1"]
        release.set()
        runner.join(timeout=10)
        assert not runner.is_alive()

    def test_an_item_may_be_redelivered_once_it_finishes(self, monkeypatch, tmp_path):
        # A container that exits early (crash, restart) leaves work to redo;
        # the in-flight guard must not become a permanent blocklist.
        rec = Recorder()
        monkeypatch.setattr(P, "spawn", rec)

        def iter_work(_api, **kw):
            yield work("w1", "s1")
            # The discard is in dispatch's `finally`, microseconds after spawn
            # returns; wait for it rather than racing it.
            deadline = time.time() + 5
            while not rec.calls and time.time() < deadline:
                time.sleep(0.01)
            time.sleep(0.1)
            yield work("w1", "s1")

        monkeypatch.setattr(P, "iter_work", iter_work)
        P.run(client(), "env_1", spawn_config(tmp_path))
        assert rec.calls == ["w1", "w1"]


class TestRobustness:
    def test_a_failing_spawn_does_not_kill_the_poller(self, monkeypatch, tmp_path):
        calls: list[str] = []

        def spawn(work, cfg):
            calls.append(work.id)
            raise RuntimeError("docker daemon is down")

        monkeypatch.setattr(P, "spawn", spawn)
        monkeypatch.setattr(P, "iter_work", feed(work("w1", "s1"), work("w2", "s2")))
        P.run(client(), "env_1", spawn_config(tmp_path))
        assert calls == ["w1", "w2"]

    def test_a_failed_item_is_released_for_retry(self, monkeypatch, tmp_path):
        attempts: list[str] = []

        def spawn(work, cfg):
            attempts.append(work.id)
            raise RuntimeError("boom")

        monkeypatch.setattr(P, "spawn", spawn)

        def iter_work(_api, **kw):
            yield work("w1", "s1")
            deadline = time.time() + 5
            while not attempts and time.time() < deadline:
                time.sleep(0.01)
            time.sleep(0.1)
            yield work("w1", "s1")

        monkeypatch.setattr(P, "iter_work", iter_work)
        P.run(client(), "env_1", spawn_config(tmp_path))
        assert attempts == ["w1", "w1"]

    def test_non_session_work_is_skipped(self, monkeypatch, tmp_path):
        rec = Recorder()
        monkeypatch.setattr(P, "spawn", rec)
        monkeypatch.setattr(P, "iter_work",
                            feed(work("w1", "s1", kind="compaction"), work("w2", "s2")))
        P.run(client(), "env_1", spawn_config(tmp_path))
        assert rec.calls == ["w2"]


class TestSpawnEnvironment:
    """What the container is actually handed."""

    @pytest.fixture
    def captured(self, monkeypatch, tmp_path):
        seen = {}

        def fake_run(cmd, env, input, text):
            seen["cmd"] = cmd
            seen["env"] = env
            seen["input"] = input
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(P.subprocess, "run", fake_run)
        P.spawn(work("w1", "s1"), spawn_config(tmp_path))
        return seen

    def test_identifies_the_session_and_work_item(self, captured):
        env = captured["env"]
        assert env["ANTHROPIC_SESSION_ID"] == "s1"
        assert env["ANTHROPIC_WORK_ID"] == "w1"
        assert env["ANTHROPIC_ENVIRONMENT_ID"] == "env_1"
        assert env["ANTHROPIC_WORK_SECRET"] == "wsec_x"

    def test_passes_the_pinned_image_not_the_moving_tag(self, monkeypatch, tmp_path):
        # A session's image is fixed on its first turn (003 §3.2).
        cfg = spawn_config(tmp_path)
        (cfg.workspaces / "s1").mkdir(parents=True)
        (cfg.workspaces / "s1" / ".image").write_text("img@sha256:older\n")
        seen = {}
        monkeypatch.setattr(P.subprocess, "run",
                            lambda cmd, env, input, text: (seen.update(env=env),
                                                           SimpleNamespace(returncode=0))[1])
        P.spawn(work("w1", "s1"), cfg)
        assert seen["env"]["K8SRCA_SANDBOX_IMAGE"] == "img@sha256:older"

    def test_passes_the_tool_manifests(self, captured):
        assert json.loads(captured["env"]["K8SRCA_MANIFESTS"]) == {"k8stools": "deadbeef"}


class TestSpawnScriptIsolation:
    """The container's environment is an allowlist, not an inheritance.

    `spawn()` hands the script the whole of `os.environ`, so the boundary that
    matters is `docker run`'s explicit `-e` list. An org-scoped API key on the
    poller host must not cross it: agent-authored bash runs inside that
    container (001 §3.1).
    """

    @pytest.fixture
    def run_flags(self):
        text = Path("docker/spawn.sh").read_text()
        body = text[text.index("exec docker run"):]
        return body, set(re.findall(r"-e ([A-Z0-9_]+)", body))

    def test_env_is_an_explicit_allowlist(self, run_flags):
        body, passed = run_flags
        assert "--env-file" not in body and "-e ANTHROPIC_API_KEY" not in body
        assert passed == {
            "ANTHROPIC_SESSION_ID", "ANTHROPIC_WORK_ID", "ANTHROPIC_ENVIRONMENT_ID",
            "ANTHROPIC_ENVIRONMENT_KEY", "ANTHROPIC_WORK_SECRET",
            "ANTHROPIC_BASE_URL", "K8SRCA_MANIFESTS", "K8SRCA_LOG_LEVEL",
        }

    def test_no_credential_of_the_hosts_reaches_the_sandbox(self, run_flags):
        _, passed = run_flags
        assert not {"ANTHROPIC_API_KEY", "KUBECONFIG", "SLACK_BOT_USER_OAUTH_TOKEN",
                    "SLACK_SOCKET_MODE_TOKEN"} & passed

    def test_the_container_stays_unprivileged(self, run_flags):
        body, _ = run_flags
        for flag in ("--cap-drop ALL", "--security-opt no-new-privileges", "--read-only"):
            assert flag in body
