"""skills/k8s-rca/answer_check.py: a draft answer against its evidence record
(design 007 §6.1). The cases are graded unsupported claims from jvm runs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent / "skills" / "k8s-rca"
sys.path.insert(0, str(SKILL))
import answer_check as A  # noqa: E402

HEALTH = {"namespace": "default",
          "workloads": [{"workload": "Deployment/ad", "restarts": 3200,
                         "last_termination": {"instance_lifetime": "PT8S", "finished": "PT31S"},
                         "memory": "limit 300Mi = request", "template_changed": "P163DT16H26M5S"}],
          "healthy": ["Deployment/accounting 1/1, 578 restarts", "Deployment/flagd 1/1, 26 restarts",
                      "Deployment/kafka 1/1, 14 restarts", "Deployment/cart 1/1, 13 restarts"]}
EVENTS = {"events": [{"reason": "BackOff", "count": 4791, "first_seen": "P3DT20H22M37S"}]}
NODE = {"allocatable": {"memory": "68000000Ki"}}


def evidence():
    return [{"n": 1, "tool": "k8s_get_namespace_health", "text": [json.dumps(HEALTH)]},
            {"n": 2, "tool": "k8s_get_events", "text": [json.dumps(EVENTS)]},
            {"n": 3, "tool": "k8s_get_node_summaries", "text": [json.dumps(NODE)]}]


def missing(answer):
    return [m for f in A.check_numbers(answer, A.Haystack(evidence())) for m in f["missing"]]


class TestNumbers:
    @pytest.mark.parametrize("answer", [
        "`ad` has 3,200 restarts.",
        "It is killed ~8s into every start.",
        "The template changed 163 days ago.",
        "Failure events go back 3d20h.",
        "Failure events go back 3 days 20 hours.",
        "`fraud-detection` has ~4,800 BackOff events.",   # a rounding of 4791
        "Its wait is PT31S.",                              # quoting the result
        "The node has about 65Gi allocatable.",            # 68000000Ki
        "The limit is 300Mi.",
    ])
    def test_what_a_result_states_is_not_flagged(self, answer):
        assert missing(answer) == []

    @pytest.mark.parametrize("answer, flagged", [
        ("Events expire after about an hour.", "1 hour"),
        ("All 23 other workloads are healthy.", "23"),
        ("It has been looping for 11–13 days.", "11–13 days"),
        ("Instance lifetime is 2–8s.", "2–8s"),
    ])
    def test_a_number_in_no_result_is_flagged(self, answer, flagged):
        assert flagged in missing(answer)

    def test_advice_and_counterfactuals_need_no_result(self):
        assert missing("Try raising the limit to 512Mi.") == []
        assert missing("Exit code 137, not 143.") == []


class TestScope:
    def test_every_other_workload_healthy_meets_the_restart_counts(self):
        """The largest single kind of unsupported claim (007 §11.2)."""
        [flag] = A.check_scope("All other workloads in the namespace are healthy.", evidence())
        assert flag["against"][0] == "Deployment/accounting 578 restarts"

    def test_the_recovering_group_comes_first(self):
        """k8stools 4.0 moves a ready workload with a restart in the last 24h out
        of `healthy` into `recovering`, with its last termination (k8stools #24)."""
        health = dict(HEALTH, healthy=["Deployment/cart 1/1, 13 restarts"], recovering=[
            {"workload": "Deployment/accounting", "ready": 1, "desired": 1, "restarts": 578,
             "last_termination": {"exit_code": 137, "reason": "OOMKilled"}}])
        ev = [{"n": 1, "tool": "k8s_get_namespace_health", "text": [json.dumps(health)]}]
        [flag] = A.check_scope("Every other workload is healthy.", ev)
        assert flag["against"] == [
            "Deployment/accounting recovering: 578 restarts, last exit 137, OOMKilled",
            "Deployment/cart 13 restarts"]

    def test_workloads_named_as_healthy_meet_their_own_counts(self):
        [flag] = A.check_scope("Both dependencies, flagd and kafka, are healthy.", evidence())
        assert flag["against"] == ["Deployment/flagd 26 restarts", "Deployment/kafka 14 restarts"]

    @pytest.mark.parametrize("answer", [
        "It dies at the same point on every restart.",
        "Every candidate has a disposition.",
        "No OOMKilling events were recorded.",
    ])
    def test_a_universal_that_is_not_about_workload_health_is_not_flagged(self, answer):
        assert A.check_scope(answer, evidence()) == []


def test_the_command_reads_a_record_from_disk(tmp_path):
    record = tmp_path / "evidence" / "sesn_1"
    record.mkdir(parents=True)
    for e in evidence():
        (record / f"{e['n']:04d}-{e['tool']}.json").write_text(json.dumps(e))
    answer = tmp_path / "answer.md"
    answer.write_text("All 23 other workloads are healthy.\n")
    r = subprocess.run([sys.executable, str(SKILL / "answer_check.py"), str(answer)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "23" in r.stdout and "accounting 578 restarts" in r.stdout
