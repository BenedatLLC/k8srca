"""The image-and-sandbox family and KubePodNotReady against the scenarios they
reach (design 007 §11.4, step 2).

Offline: from each scenario's own alert, through refinement, the knowledge
reaches the truth's cause and every candidate the truth rules out, each with a
refute. No model.
"""

from pathlib import Path

import pytest
import yaml

from k8srca.kb import knowledge

ROOT = Path(__file__).parent / "scenarios"

#: scenario -> truth candidate -> the claims it is. Both scenarios' alert is
#: KubePodNotReady. application-fault is no cause of a pod that never starts.
CASES = {
    "image-pull-typo": {
        "cause": ["image-tag-not-found-in-registry"],
        "registry-auth-or-unreachable": ["registry-network-or-auth-problem"],
    },
    "pvc-pending-no-storageclass": {
        "cause": ["unbound-persistent-volume-claim"],
        "insufficient-node-resources": ["insufficient-cluster-resources"],
        "node-selector-or-taint": ["node-selector-or-affinity-matches-no-node",
                                   "untolerated-node-taint"],
        "admission-or-quota": ["controller-cannot-create-pods"],
    },
}


@pytest.fixture(scope="module")
def kb():
    return knowledge.load_layers([knowledge.MINED, knowledge.KNOWLEDGE])


def test_kubepodnotready_offers_the_three_states_of_its_runbook(kb):
    symptom = kb.layer.alerts["KubePodNotReady"].symptom
    top = kb.layer.symptoms[symptom].claims
    assert {"pod-not-scheduled", "image-pull-failure", "readiness-check-failing"} <= set(top)
    assert len(top) <= 8


@pytest.mark.parametrize("sid", sorted(CASES))
def test_kubepodnotready_reaches_the_cause_and_what_the_truth_rules_out(kb, sid):
    claims = CASES[sid]
    truth = yaml.safe_load((ROOT / sid / "truth.yaml").read_text())
    ruled_out = {c["id"] for c in truth["candidates"] if c["standing"] == "ruled_out"}
    assert ruled_out - set(claims) <= {"application-fault"}
    reached = knowledge.reachable(kb.layer, kb.layer.alerts["KubePodNotReady"].symptom)
    for candidate, ids in claims.items():
        for cid in ids:
            assert cid in reached, f"{sid}: {candidate} -> {cid} is not reached"
            if candidate != "cause":
                assert kb.layer.claims[cid].rules_out, f"{cid} has no refute"


def test_the_image_top_level_stays_small(kb):
    assert len(kb.layer.symptoms["image-pull-failing"].claims) <= 8
