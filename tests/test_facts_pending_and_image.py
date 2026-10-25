"""The scheduling, claim and image facts, in code (design 007 §11.4, step 3).

Offline: each scenario's recorded results, through the facts layer and
`kb_query.py open --workload`, with no fact recorded by the agent. No model.
"""

import re
import subprocess
import sys

import pytest

from k8srca.facts.derive import placement_kb, pull_kb, scheduling_kb, unscheduled_kb

from test_facts import SKILL, log_of
from test_kb import standings

#: scenario -> (the workload, the alert it is opened by).
CASES = {
    "image-pull-typo": ("email", "KubePodNotReady"),
    "pvc-pending-no-storageclass": ("quote", "KubePodNotReady"),
    "itbench-33-invalid-node-selector": ("ad", "PendingPodsDetected"),
}


@pytest.fixture(scope="module")
def opened(tmp_path_factory):
    out = {}
    for sid, (workload, alert) in CASES.items():
        log = log_of(sid, tmp_path_factory.mktemp(sid)).path
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "open", alert,
                            "--workload", workload, "--facts-log", str(log)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        out[sid] = r.stdout
    return out


def leader(out: str) -> str:
    return next(ln for ln in out.splitlines() if ln.startswith("most likely:"))


def test_image_pull_typo(opened):
    out = opened["image-pull-typo"]
    st = standings(out)
    assert re.search(r"\[image-pull-failure\], strong; within it, .*"
                     r"\[image-not-found-in-registry\], strong", leader(out))
    assert st["registry-network-or-auth-problem"] == "RULED-OUT"
    assert st["pod-not-scheduled"] == "RULED-OUT"
    # The capitalised-literal defect made this strong and the leader.
    assert st["invalid-image-name"] not in ("STRONG", "MODERATE")


def test_pvc_pending(opened):
    out = opened["pvc-pending-no-storageclass"]
    st = standings(out)
    assert re.search(r"\[pod-not-scheduled\], strong; within it, .*"
                     r"\[unbound-persistent-volume-claim\], strong", leader(out))
    assert st["node-selector-or-affinity-matches-no-node"] == "RULED-OUT"
    # The claim check rejects the pod before any node is weighed (step 5).
    assert st["insufficient-cluster-resources"] == "RULED-OUT"
    assert st["untolerated-node-taint"] == "RULED-OUT"


def test_pvc_pending_from_the_health_call_alone(tmp_path):
    """What the live sessions read (step 5): get_namespace_health and nothing
    else. Its unscheduled line carries the scheduler's message."""
    log = log_of("pvc-pending-no-storageclass", tmp_path,
                 only={"k8s_get_namespace_health"}).path
    r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "open", "KubePodNotReady",
                        "--workload", "quote", "--facts-log", str(log)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    st = standings(r.stdout)
    assert st["insufficient-cluster-resources"] == "RULED-OUT"
    assert st["untolerated-node-taint"] == "RULED-OUT"
    assert re.search(r"\[pod-not-scheduled\], strong; within it, .*"
                     r"\[unbound-persistent-volume-claim\]", leader(r.stdout))


def test_itbench_33(opened):
    st = standings(opened["itbench-33-invalid-node-selector"])
    assert st["node-selector-or-affinity-matches-no-node"] == "STRONG"
    assert st["unbound-persistent-volume-claim"] == "RULED-OUT"
    # The message gives every node's reason, and none is resources (step 5).
    assert st["insufficient-cluster-resources"] == "RULED-OUT"
    # A cited taint is only the first filter to reject its node: the selector
    # admits none of the five (k8stools 4.3.0's placement), so no taint is why.
    assert st["untolerated-node-taint"] == "RULED-OUT"
    assert re.search(r"\[pod-not-scheduled\], strong; within it, .*"
                     r"\[node-selector-or-affinity-matches-no-node\], strong",
                     leader(opened["itbench-33-invalid-node-selector"]))


class TestMessages:
    def test_a_message_that_accounts_for_every_node_settles_what_it_omits(self):
        kb = scheduling_kb("0/5 nodes are available: 1 node(s) had untolerated taint "
                           "{node-role.kubernetes.io/control-plane: }, 4 node(s) didn't "
                           "match Pod's node affinity/selector. preemption: 0/5 nodes")
        assert "scheduling_message_cites_affinity[P]" in kb
        assert "scheduling_message_cites_taint[P]" in kb
        assert "not untolerated_noschedule_taint_on_all_candidate_nodes[P]" in kb
        assert "not scheduling_message_cites_insufficient_resource[P, cpu]" in kb

    def test_the_claim_check_weighs_no_node(self):
        kb = scheduling_kb("0/1 nodes are available: pod has unbound immediate "
                           "PersistentVolumeClaims. not found")
        assert kb == ["pvc_not_bound[P]", "scheduling_rejected_before_nodes_weighed[P]"]

    def test_a_message_short_of_the_node_count_settles_only_what_it_cites(self):
        kb = scheduling_kb("0/3 nodes are available: 1 Insufficient cpu.")
        assert kb == ["scheduling_message_cites_insufficient_resource[P, cpu]"]

    def test_a_message_that_weighs_every_node_did_not_stop_before_them(self):
        kb = scheduling_kb("0/2 nodes are available: 2 Insufficient memory.")
        assert "not scheduling_rejected_before_nodes_weighed[P]" in kb

    def test_a_registry_answer_rules_out_the_other_failures(self):
        kb = pull_kb('Failed to pull image "x:1": Error response from daemon: manifest unknown')
        assert "pull_message_names_manifest_not_found[C]" in kb
        assert "not pull_message_names_unauthorized[C]" in kb
        assert "not pull_message_names_connection_failure[C]" in kb

    def test_an_ambiguous_answer_rules_out_nothing(self):
        kb = pull_kb("Failed to pull image \"x\": pull access denied for x, repository "
                     "does not exist or may require 'docker login'")
        assert not [k for k in kb if k.startswith("not ")]


class TestUnscheduledLine:
    def test_the_line_carries_the_schedulers_message(self):
        assert unscheduled_kb("q-x: not scheduled for 20m0s: Unschedulable: 0/1 nodes are "
                              "available: pod has unbound immediate PersistentVolumeClaims.") \
            == ["pvc_not_bound[P]", "scheduling_rejected_before_nodes_weighed[P]"]

    def test_a_reason_not_recorded_states_nothing(self):
        assert unscheduled_kb("q-x: not scheduled for 9m41s: reason not recorded "
                              "(the capture predates k8stools 4.1.0)") == []


class TestPlacement:
    def test_a_selector_that_admits_no_node(self):
        assert placement_kb("ad-x: 0 of 5 nodes match its nodeSelector and required node "
                            "affinity") == ["node_selector_or_affinity_set[P]",
                                            "no_node_matches_node_selector_or_affinity[P]"]

    def test_every_admitted_node_repels_the_pod(self):
        kb = placement_kb("ad-x: 2 of 5 nodes match its nodeSelector and required node affinity; "
                          "of those, 2 have a NoSchedule or NoExecute taint it doesn't tolerate")
        assert "not no_node_matches_node_selector_or_affinity[P]" in kb
        assert "untolerated_noschedule_taint_on_all_candidate_nodes[P]" in kb

    def test_no_selector_and_a_node_that_takes_the_pod(self):
        kb = placement_kb("q-x: no nodeSelector or required node affinity, so all nodes match (3); "
                          "of those, 1 has a NoSchedule or NoExecute taint it doesn't tolerate")
        assert kb == ["not node_selector_or_affinity_set[P]",
                      "not no_node_matches_node_selector_or_affinity[P]",
                      "not untolerated_noschedule_taint_on_all_candidate_nodes[P]"]

    def test_a_line_not_evaluated_states_nothing(self):
        assert placement_kb("ad-x: not evaluated, nodes could not be read: forbidden") is None
