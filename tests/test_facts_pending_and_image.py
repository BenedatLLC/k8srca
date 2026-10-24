"""The scheduling, claim and image facts, in code (design 007 §11.4, step 3).

Offline: each scenario's recorded results, through the facts layer and
`kb_query.py open --workload`, with no fact recorded by the agent. No model.
"""

import re
import subprocess
import sys

import pytest

from k8srca.facts.derive import pull_kb, scheduling_kb

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


def test_itbench_33(opened):
    st = standings(opened["itbench-33-invalid-node-selector"])
    assert st["node-selector-or-affinity-matches-no-node"] == "STRONG"
    assert st["unbound-persistent-volume-claim"] == "RULED-OUT"
    # The known limit (step 3 plan): the message cites the control-plane taint
    # on one node of five, and the knowledge reads any cited taint as strong.
    assert st["untolerated-node-taint"] == "STRONG"


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
        assert kb == ["pvc_not_bound[P]"]

    def test_a_message_short_of_the_node_count_settles_only_what_it_cites(self):
        kb = scheduling_kb("0/3 nodes are available: 1 Insufficient cpu.")
        assert kb == ["scheduling_message_cites_insufficient_resource[P, cpu]"]

    def test_a_registry_answer_rules_out_the_other_failures(self):
        kb = pull_kb('Failed to pull image "x:1": Error response from daemon: manifest unknown')
        assert "pull_message_names_manifest_not_found[C]" in kb
        assert "not pull_message_names_unauthorized[C]" in kb
        assert "not pull_message_names_connection_failure[C]" in kb

    def test_an_ambiguous_answer_rules_out_nothing(self):
        kb = pull_kb("Failed to pull image \"x\": pull access denied for x, repository "
                     "does not exist or may require 'docker login'")
        assert not [k for k in kb if k.startswith("not ")]
