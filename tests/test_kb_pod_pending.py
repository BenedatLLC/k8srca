"""The pod-pending family against the scenarios it reaches (design 007 §11.4, step 1).

Offline: for each scenario's alert, `lookup`'s candidates include the truth's
cause and every candidate the truth rules out, each with a refute. No model.
"""

from pathlib import Path

import pytest
import yaml

from k8srca.kb import knowledge

ROOT = Path(__file__).parent / "scenarios"

#: scenario -> (the alert it is looked up by, truth candidate -> the claims it is).
#: pvc-pending's own alert, KubePodNotReady, is not mapped yet (§11.4); its new
#: pod's FailedScheduling event is what reaches the family.
CASES = {
    "itbench-33-invalid-node-selector": ("PendingPodsDetected", {
        "cause": ["node-selector-or-affinity-matches-no-node"],
        "insufficient-node-resources": ["insufficient-cluster-resources"],
        "control-plane-taint": ["untolerated-node-taint"],
        "admission-or-quota": ["controller-cannot-create-pods"],
    }),
    "pvc-pending-no-storageclass": ("FailedScheduling", {
        "cause": ["unbound-persistent-volume-claim"],
        "insufficient-node-resources": ["insufficient-cluster-resources"],
        "node-selector-or-taint": ["node-selector-or-affinity-matches-no-node",
                                   "untolerated-node-taint"],
        "admission-or-quota": ["controller-cannot-create-pods"],
    }),
}


@pytest.fixture(scope="module")
def kb():
    return knowledge.load_layers([knowledge.MINED, knowledge.KNOWLEDGE])


@pytest.mark.parametrize("sid", sorted(CASES))
def test_lookup_offers_the_cause_and_what_the_truth_rules_out(kb, sid):
    alert, claims = CASES[sid]
    truth = yaml.safe_load((ROOT / sid / "truth.yaml").read_text())
    ruled_out = {c["id"] for c in truth["candidates"] if c["standing"] == "ruled_out"}
    # Every ruled-out candidate the family covers is listed; application-fault
    # (itbench-33) is no cause of a Pending pod, so the family has no claim for it.
    assert ruled_out - set(claims) <= {"application-fault"}
    symptom = kb.layer.alerts[alert].symptom
    offered = set(kb.layer.symptoms[symptom].claims)
    for candidate, ids in claims.items():
        for cid in ids:
            assert cid in offered, f"{sid}: {candidate} -> {cid} is not a top-level candidate"
            if candidate != "cause":
                assert kb.layer.claims[cid].rules_out, f"{cid} has no refute"


def test_the_top_level_stays_small(kb):
    """§8.4: lookup returns the top-level claims; the rest wait on refinement."""
    assert len(kb.layer.symptoms["pod-pending"].claims) <= 8
