"""`k8srca poller --env-key-var X` gives its sandboxes X's value as the environment key.

The scenario suite polls its own environment with `ANTHROPIC_TEST_ENVIRONMENT_KEY`.
`docker/spawn.sh` reads `ANTHROPIC_ENVIRONMENT_KEY`, so unless the poller sets it
the container exits at once (empty) or authenticates with production's key. The
session then waits for a sandbox that never starts.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from typer.testing import CliRunner

from k8srca import cli
from k8srca.state import State


def run_poller(tmp_path, monkeypatch, *args, env):
    state = tmp_path / "state.json"
    State(environment_id="env_x").save(state)
    script = tmp_path / "spawn.sh"
    script.write_text("#!/bin/bash\n")

    for name in ("ANTHROPIC_ENVIRONMENT_KEY", "ANTHROPIC_TEST_ENVIRONMENT_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr("k8srca.sandbox.image_exists", lambda ref: True)
    monkeypatch.setattr("k8srca.runtime.write", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr("k8srca.worker.poller.run",
                        lambda client, env_id, cfg: seen.update(
                            key=os.environ.get("ANTHROPIC_ENVIRONMENT_KEY"), env_id=env_id,
                            auth=client.auth_token))
    result = CliRunner().invoke(cli.app, ["poller", "--state", str(state),
                                          "--spawn", str(script),
                                          "--image", "k8srca/sandbox:test", *args])
    return result, seen


def test_the_named_key_becomes_the_sandbox_environment_key(tmp_path, monkeypatch):
    result, seen = run_poller(tmp_path, monkeypatch,
                              "--env-key-var", "ANTHROPIC_TEST_ENVIRONMENT_KEY",
                              env={"ANTHROPIC_TEST_ENVIRONMENT_KEY": "test-key"})
    assert result.exit_code == 0, result.output
    assert seen["key"] == "test-key" and seen["auth"] == "test-key"


def test_it_replaces_production_s_key_rather_than_passing_it_on(tmp_path, monkeypatch):
    result, seen = run_poller(tmp_path, monkeypatch,
                              "--env-key-var", "ANTHROPIC_TEST_ENVIRONMENT_KEY",
                              env={"ANTHROPIC_TEST_ENVIRONMENT_KEY": "test-key",
                                   "ANTHROPIC_ENVIRONMENT_KEY": "production-key"})
    assert result.exit_code == 0, result.output
    assert seen["key"] == "test-key"


def test_the_default_is_unchanged(tmp_path, monkeypatch):
    result, seen = run_poller(tmp_path, monkeypatch,
                              env={"ANTHROPIC_ENVIRONMENT_KEY": "production-key"})
    assert result.exit_code == 0, result.output
    assert seen["key"] == "production-key"
