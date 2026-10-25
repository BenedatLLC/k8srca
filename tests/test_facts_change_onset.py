"""When a change came, and when the failures began (design 007 §11.4, step 6).

Offline: each scenario's recorded results, through the facts layer and
`kb_query.py open --workload`, with no fact recorded by the agent. No model.
"""

import json
import subprocess
import sys

import pytest

from k8srca.facts.derive import change_kinds, change_onset_kb, restart_report

from test_facts import SKILL, log_of
from test_kb import standings

#: scenario -> the workload whose pods crash-loop since its last revision.
CASES = {
    "deploy-regression": "payment",
    "liveness-probe-wrong-port": "shipping",
}


@pytest.fixture(scope="module")
def opened(tmp_path_factory):
    out = {}
    for sid, workload in CASES.items():
        log = log_of(sid, tmp_path_factory.mktemp(sid)).path
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "open",
                            "CrashLoopBackOff", "--workload", workload, "--facts-log", str(log)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        mine = [json.loads(ln) for ln in log.read_text().splitlines()]
        kb = {k for f in mine if f["subject"].startswith(f"default/Deployment/{workload}")
              for k in f.get("kb") or []}
        out[sid] = (r.stdout, kb)
    return out


@pytest.mark.parametrize("sid", sorted(CASES))
def test_a_revision_before_the_failures_leads(opened, sid):
    out, kb = opened[sid]
    assert standings(out)["application-release"] == "STRONG"
    assert out.splitlines()[1].startswith("most likely: application release")
    # The old pod's restarts, long before the metrics begin, no longer date the onset.
    assert "onset_observed[W]" in kb and "not onset_observed[W]" not in kb


def test_bad_config_waits_on_the_logs(opened):
    """The revision is the when; the logs name the what, and the agent reads them."""
    assert standings(opened["deploy-regression"][0])["bad-config"] == "OPEN"


CHANGE = {"revision": 3, "age": "PT22M35S", "reused": False, "rollout_restart": False,
          "changes": ["containers[payment].env[PAYMENT_PORT]: changed"]}
NEW_POD = {"pod": "payment-x", "pod_age": "PT22M36S", "restarts": 9}
HORIZON = 22 * 60 + 36


class TestChangeAndOnset:
    def test_failing_pods_created_with_the_change(self):
        assert change_onset_kb(CHANGE, [NEW_POD], HORIZON) == [
            "onset_observed[W]", "not changed_before_onset[W, image]",
            "not changed_before_onset[W, resources]", "changed_before_onset[W, spec]"]

    def test_a_change_hours_older_than_the_pods(self):
        assert change_onset_kb({**CHANGE, "age": "PT5H"}, [NEW_POD], HORIZON) == []

    def test_an_older_pod_still_failing(self):
        old = {"pod": "payment-y", "pod_age": "P185D", "restarts": 16}
        assert change_onset_kb(CHANGE, [NEW_POD, old], HORIZON) == []

    def test_a_record_that_does_not_reach_the_pods_creation(self):
        assert change_onset_kb(CHANGE, [NEW_POD], 600) == []

    def test_a_rollback_or_a_restart(self):
        assert change_onset_kb({**CHANGE, "reused": True}, [NEW_POD], HORIZON) == []
        assert change_onset_kb({**CHANGE, "rollout_restart": True}, [NEW_POD], HORIZON) == []

    def test_no_failing_pod(self):
        assert change_onset_kb(CHANGE, [{**NEW_POD, "restarts": 0}], HORIZON) == []

    def test_kinds(self):
        assert change_kinds(["containers[a].image: x -> y"]) == {"image"}
        assert change_kinds(["containers[a].resources.limits.cpu: added"]) == {"resources"}
        assert change_kinds(['containers[a].livenessProbe: - -> {"tcpSocket":{}}',
                             "volumes[cache]: - -> pvc"]) == {"spec"}
        assert change_kinds(["metadata.annotations[kubectl.kubernetes.io/restartedAt]: added"]) \
            is None


def _row(**kw):
    return {"pod": "payment-5c5df75598-jjk7z", "container": "payment", "restarts": 5.0,
            "per_hour_min": 0.0, "per_hour_max": 1.0, "hours_without_restart": 0,
            "first_sample": "2026-10-21T11:08:28+00:00", "restart_count_at_first_sample": 16.0,
            "onset_in_window": False, **kw}


def _onset_lines(row):
    r = {"window": "85.9h", "retained_from": "2026-10-21T11:08:09+00:00", "rows": [row]}
    return [k for f in restart_report({"namespace": "default"}, r) for k in f.kb
            if "onset" in k]


class TestMetricsOnset:
    def test_a_series_restarting_since_before_the_window(self):
        assert _onset_lines(_row()) == ["not onset_observed[W]"]

    def test_a_pod_with_no_restarts_in_the_window(self):
        assert _onset_lines(_row(restarts=0.0, hours_without_restart=85)) == []

    def test_a_pod_born_inside_the_window(self):
        assert _onset_lines(_row(first_sample="2026-10-25T00:39:13+00:00",
                                 restart_count_at_first_sample=1.0)) == []
