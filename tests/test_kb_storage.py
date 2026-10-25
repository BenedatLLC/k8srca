"""The storage family, and the claim and volume facts in code (design 007
§11.4, step 4).

Offline: each scenario's recorded results, through the facts layer and
`kb_query.py open --workload`, with no fact recorded by the agent. No model.
"""

import re
import subprocess
import sys

import pytest

from k8srca.facts.derive import claim_kb, volume_kb
from k8srca.kb import knowledge

from test_facts import SKILL, log_of
from test_kb import standings

#: The binding candidates: one per kind of answer a claim's events give.
BINDING = {"requested-storage-class-does-not-exist", "no-class-requested-and-no-default-class",
           "no-matching-static-volume", "storage-class-provisioner-not-running",
           "volume-provisioning-failed", "waiting-for-first-consumer"}

NOT_FOUND = {"reason": "ProvisioningFailed",
             "message": 'storageclass.storage.k8s.io "fast-ssd" not found'}
EXTERNAL = {"reason": "ExternalProvisioning",
            "message": "Waiting for a volume to be created either by the external provisioner "
                       "'ebs.csi.aws.com' or manually by the system administrator. If volume "
                       "creation is delayed, please verify that the provisioner is running and "
                       "correctly registered."}
CSI_FAILED = {"reason": "ProvisioningFailed",
              "message": 'failed to provision volume with StorageClass "gp3": rpc error: code = '
                         "ResourceExhausted desc = insufficient capacity"}


@pytest.fixture(scope="module")
def kb():
    return knowledge.load_layers([knowledge.MINED, knowledge.KNOWLEDGE])


@pytest.fixture(scope="module")
def opened(tmp_path_factory):
    out = {}
    for sid, workload in (("pvc-pending-no-storageclass", "quote"), ("image-pull-typo", "email")):
        log = log_of(sid, tmp_path_factory.mktemp(sid)).path
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "open", "KubePodNotReady",
                            "--workload", workload, "--facts-log", str(log)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        out[sid] = r.stdout
    return out


class TestFamily:
    def test_the_top_levels_stay_small(self, kb):
        assert set(kb.layer.symptoms["pvc-pending"].claims) == BINDING
        assert len(kb.layer.symptoms["volume-mount-failing"].claims) <= 8

    def test_an_unbound_claim_refines_into_the_binding_candidates(self, kb):
        assert BINDING <= set(kb.layer.claims["unbound-persistent-volume-claim"].refined_by)

    def test_pod_not_ready_offers_a_volume_that_is_not_attached_or_mounted(self, kb):
        assert "volume-not-attached-or-mounted" in kb.layer.symptoms["pod-not-ready"].claims


class TestOpen:
    def test_pvc_pending_leads_with_the_missing_class(self, opened):
        out = opened["pvc-pending-no-storageclass"]
        lead = next(ln for ln in out.splitlines() if ln.startswith("most likely:"))
        assert re.search(r"\[pod-not-scheduled\], strong; within it, .*"
                         r"\[unbound-persistent-volume-claim\], strong; within it, .*"
                         r"\[requested-storage-class-does-not-exist\], strong", lead)
        st = standings(out)
        for cid in BINDING - {"requested-storage-class-does-not-exist"}:
            assert st[cid] == "RULED-OUT", cid
        assert st["volume-not-attached-or-mounted"] == "RULED-OUT"

    def test_image_pull_typo_rules_out_the_volume(self, opened):
        assert standings(opened["image-pull-typo"])["volume-not-attached-or-mounted"] == "RULED-OUT"


class TestClaimEvents:
    def test_a_missing_class_settles_the_other_kinds(self):
        kb = claim_kb("fast-ssd", [NOT_FOUND], complete=False)
        assert "claim_event_storage_class_not_found[P]" in kb
        assert "claim_storage_class_requested[P, fast-ssd]" in kb
        assert "not claim_event_provisioner_error[P]" in kb
        assert "not claim_event_waiting_external_provisioner[P]" in kb

    def test_a_waiting_claim_settles_nothing_from_its_newest_event_alone(self):
        kb = claim_kb("gp3", [EXTERNAL], complete=False)
        assert "claim_event_waiting_external_provisioner[P]" in kb
        assert "storage_class_provisioner[P, ebs.csi.aws.com]" in kb
        assert not any(k.startswith("not claim_event") for k in kb)

    def test_a_failing_csi_provisioner_is_not_ruled_out_by_its_alternating_events(self):
        # The controller's ExternalProvisioning and the provisioner's
        # ProvisioningFailed repeat in turn while a CSI provisioner fails.
        kb = claim_kb("gp3", [EXTERNAL, CSI_FAILED], complete=True)
        assert "not claim_event_provisioner_error[P]" not in kb
        kb = claim_kb("gp3", [CSI_FAILED, EXTERNAL], complete=True)
        assert "claim_event_provisioner_error[P]" in kb
        assert "claim_event_names_insufficient_capacity[P]" in kb

    def test_every_event_of_one_kind_settles_the_others(self):
        kb = claim_kb("gp3", [EXTERNAL, EXTERNAL], complete=True)
        assert "not claim_event_provisioner_error[P]" in kb

    def test_no_class_is_not_stated(self):
        # k8stools shows an unset class and an empty one alike.
        assert claim_kb(None, [], complete=True) == []

    def test_a_class_name_that_is_no_object_name(self):
        assert "storage_class_name_invalid_dns_subdomain[P]" in claim_kb("Fast_SSD", [], True)


class TestVolumeEvents:
    def test_multi_attach(self):
        kb = volume_kb("FailedAttachVolume", 'Multi-Attach error for volume "pvc-1" Volume is '
                                             "already used by pod(s) web-0")
        assert kb == ["failed_attach_volume_event[P]", "multi_attach_error_event[P]"]

    def test_a_missing_configmap_rules_out_the_other_mount_kinds(self):
        kb = volume_kb("FailedMount", 'MountVolume.SetUp failed for volume "config" : '
                                      'configmap "app-config" not found')
        assert "mount_message_names_missing_volume[P]" in kb
        assert "not mount_message_names_csi_error[P]" in kb
        assert "not mount_message_names_permission_denied[P]" in kb

    def test_a_csi_error_that_names_permission_denied_settles_neither(self):
        kb = volume_kb("FailedMount", "MountVolume.MountDevice failed: rpc error: code = "
                                      "Internal desc = mount failed: permission denied")
        assert {"mount_message_names_csi_error[P]",
                "mount_message_names_permission_denied[P]"} <= set(kb)
        assert not any(k.startswith("not ") for k in kb)

    def test_other_events_state_nothing(self):
        assert volume_kb("Pulled", "Successfully pulled image") == []
