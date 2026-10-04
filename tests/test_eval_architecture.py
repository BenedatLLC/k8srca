"""k8srca's architecture-eval cases: every committed case must load.

The scoring is dkgg's and is tested there (packages/dkgg/tests/test_eval.py).
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
