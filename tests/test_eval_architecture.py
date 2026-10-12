"""k8srca's architecture-eval cases: every committed case must load.

The scoring is kubewiki's and is tested there (packages/kubewiki/tests/test_eval.py).
These are k8srca's installs, so the check that each is complete stays here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from k8srca.evals.architecture import discover, load_case


@pytest.mark.parametrize("case_dir", discover(), ids=lambda p: p.name)
def test_every_committed_case_loads(case_dir: Path):
    case, truth = load_case(case_dir)
    assert (case_dir / case.capture).exists(), "the capture a case names must be committed"
    for src in case.sources:
        assert src.path is None or Path(src.path).exists(), src.path


def test_a_case_is_read_from_its_replay_whatever_the_environment(monkeypatch, tmp_path):
    # K8SRCA_MCP_<NAME>_URL overrides host_url(). Honoured here, it would
    # point the eval at the live cluster and score today's deployment
    # against the case's capture -- quietly, since both look like a cluster.
    import contextlib
    from types import SimpleNamespace

    import kubewiki.build

    import k8srca.scenario.sources
    from k8srca.config import load as load_config
    from k8srca.evals.architecture import run_case

    monkeypatch.setenv("K8SRCA_MCP_K8STOOLS_URL", "http://live-cluster:8000/mcp")

    @contextlib.contextmanager
    def fake_replay(*args, **kwargs):
        yield SimpleNamespace(host_url="http://127.0.0.1:8011/mcp")

    seen = []

    async def fake_build(sources, servers, cache=None):
        seen.extend(servers)
        raise RuntimeError("stop here")

    monkeypatch.setattr(k8srca.scenario.sources, "replay", fake_replay)
    monkeypatch.setattr(kubewiki.build, "build", fake_build)
    with pytest.raises(RuntimeError, match="stop here"):
        run_case(Path("tests/evals/architecture/itbench-33-pre-fault"),
                 load_config("k8srca.yaml"), "image", tmp_path)
    assert [s.url for s in seen] == ["http://127.0.0.1:8011/mcp"]
