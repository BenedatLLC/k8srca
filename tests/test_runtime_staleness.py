"""`status` notices a poller or orchestrator running on an older sync.

On 2026-10-08 a `k8srca sync` moved the coordinator v25 -> v26 and both agents'
tool manifests. The poller and orchestrator kept what they had loaded at
startup -- an old sandbox image, old manifests, the old coordinator version --
and `status` reported every line ok. New sessions would have failed the
manifest check until someone thought to restart them.
"""

import os

from k8srca import runtime
from k8srca.state import AgentState, State


def state(version: int = 1, manifest: str = "m1") -> State:
    return State(environment_id="env_1",
                 agents={"rca-coordinator": AgentState(id="agent_1", version=version,
                                                       manifest=manifest, model="m")},
                 config_rev="abc")


def test_fingerprint_ignores_config_rev():
    a, b = state(), state()
    b.config_rev = "def"
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_changes_with_version_and_manifest():
    assert state().fingerprint() != state(version=2).fingerprint()
    assert state().fingerprint() != state(manifest="m2").fingerprint()


def test_a_process_on_the_current_sync_is_ok(tmp_path):
    sp = tmp_path / "state.json"
    runtime.write(sp, "poller", state(), image="k8srca/sandbox:aaa")
    ok, warn, detail = runtime.staleness("poller", runtime.read(sp, "poller"), state(),
                                         image="k8srca/sandbox:aaa")
    assert ok and not warn, detail


def test_a_process_on_an_older_sync_is_down(tmp_path):
    sp = tmp_path / "state.json"
    runtime.write(sp, "slack", state(version=25))
    ok, warn, detail = runtime.staleness("slack", runtime.read(sp, "slack"),
                                         state(version=26))
    assert not ok
    assert "systemctl --user restart k8srca-slack" in detail


def test_an_older_sandbox_image_warns_but_works(tmp_path):
    sp = tmp_path / "state.json"
    runtime.write(sp, "poller", state(), image="k8srca/sandbox:old")
    ok, warn, detail = runtime.staleness("poller", runtime.read(sp, "poller"), state(),
                                         image="k8srca/sandbox:new")
    assert ok and warn
    assert "sandbox build" in detail


def test_no_record_warns(tmp_path):
    """A process started before records existed: unknown, not assumed current."""
    ok, warn, _ = runtime.staleness("poller", runtime.read(tmp_path / "state.json", "poller"),
                                    state())
    assert ok and warn


def test_a_record_from_a_dead_process_counts_as_none(tmp_path):
    sp = tmp_path / "state.json"
    runtime.write(sp, "poller", state())
    rec = runtime.read(sp, "poller")
    rec.pid = _dead_pid()
    ok, warn, detail = runtime.staleness("poller", rec, state())
    assert ok and warn and "startup record" in detail


def _dead_pid() -> int:
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid
