"""The committed scenarios themselves (design 004 §5).

Hermetic and free: this loads what is on disk and checks it is internally
consistent. It deliberately does *not* run a scenario -- `k8srca scenario run`
spends money on every invocation, which is why 004 §7 makes it a command rather
than a pytest target.
"""

from pathlib import Path

import pytest

from k8srca.scenario import checks
from k8srca.scenario.model import discover

ROOT = Path("tests/scenarios")
SCENARIOS = discover(ROOT)


def test_there_is_at_least_one_scenario():
    assert SCENARIOS, f"no scenarios under {ROOT}"


@pytest.mark.parametrize("sd", SCENARIOS, ids=lambda s: s.scenario.id)
class TestEveryScenario:
    def test_truth_matches_its_capture(self, sd):
        """Catches a re-record that was never followed by a truth review."""
        sd.check_truth_is_current()

    def test_the_capture_has_usable_logs(self, sd):
        """No repr blobs -- a capture from k8stools < 2.0.4 (k8stools#6)."""
        from k8srca.scenario.record import log_health

        health = log_health(sd.capture())
        assert health["repr_blobs"] == 0, (
            f"{health['repr_blobs']}/{health['containers']} container logs are "
            f"repr blobs; re-record with k8stools >= 2.0.4"
        )

    def test_required_tools_exist_in_the_capture_world(self, sd):
        """A scenario cannot require a tool no capture can answer."""
        capture = sd.capture()
        for tool in sd.scenario.requires_tools:
            if tool == "get_replicaset_summaries":
                assert capture.get("replicasets"), "capture has no replicasets"

    def test_must_identify_terms_are_actually_findable(self, sd):
        """A term no correct answer could contain makes the check unpassable.

        Each term must appear somewhere in the capture, otherwise truth is
        asking the agent to name something that is not in its world.
        """
        import json

        blob = json.dumps(sd.capture())
        for term in sd.truth.cause.must_identify:
            assert term.lower() in blob.lower(), (
                f"must_identify term {term!r} appears nowhere in the capture"
            )

    def test_a_fabricated_pod_would_be_caught(self, sd):
        """The S1 gate, against the real capture rather than a fixture."""
        result = checks.closed_world("The pod zz-9f8e7d6c5b-q1w2e died.", sd.capture())
        assert not result.passed
