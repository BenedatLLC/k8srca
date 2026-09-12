"""Change history from ReplicaSet revisions.

An upstream chart repository records what the *project* changed. This records
what happened to *this cluster*, which is the question RCA actually asks.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from k8srca.arch import history
from k8srca.arch.model import Architecture


def rs(name, revision, created, image, memory=None, replicas=1, owner="api"):
    res = SimpleNamespace(limits={"memory": memory}, requests=None) if memory else None
    container = SimpleNamespace(image=image, resources=res)
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            annotations={"deployment.kubernetes.io/revision": str(revision)},
            creation_timestamp=created,
            owner_references=[SimpleNamespace(kind="Deployment", name=owner)],
        ),
        spec=SimpleNamespace(
            replicas=replicas,
            template=SimpleNamespace(spec=SimpleNamespace(containers=[container])),
        ),
    )


class TestRevisionParsing:
    def test_revision_number_read_from_the_annotation(self):
        assert history._revision(rs("a", 3, None, "i")) == 3

    def test_missing_annotation_sorts_first(self):
        r = rs("a", 1, None, "i")
        r.metadata.annotations = {}
        assert history._revision(r) == 0

    def test_owner_is_the_deployment(self):
        assert history._owner(rs("a", 1, None, "i", owner="checkout")) == "checkout"

    def test_replicaset_without_a_deployment_owner_is_ignored(self):
        r = rs("a", 1, None, "i")
        r.metadata.owner_references = []
        assert history._owner(r) is None


class TestDescribe:
    def test_reports_an_image_change(self):
        before = {"image": "demo:2.0.2-ad", "resources": {}, "replicas": 1}
        after = {"image": "demo:2.2.0-ad", "resources": {}, "replicas": 1}
        assert "image" in history._describe(before, after)[0]

    def test_reports_a_resource_change(self):
        before = {"image": "x", "resources": {"limits": {"memory": "120Mi"}}, "replicas": 1}
        after = {"image": "x", "resources": {"limits": {"memory": "140Mi"}}, "replicas": 1}
        assert any("resources" in c for c in history._describe(before, after))

    def test_silent_when_nothing_tracked_changed(self):
        same = {"image": "x", "resources": {}, "replicas": 1}
        assert history._describe(same, dict(same)) == []

    def test_untracked_fields_are_not_reported(self):
        # Only image, resources and replicas matter for "what changed"; other
        # template churn would bury the signal.
        before = {"image": "x", "resources": {}, "replicas": 1, "annotations": {"a": 1}}
        after = {"image": "x", "resources": {}, "replicas": 1, "annotations": {"a": 2}}
        assert history._describe(before, after) == []


class TestCollect:
    """The API is faked; the grouping and diffing logic is what matters."""

    def _collect(self, monkeypatch, replicasets):
        from k8srca.config import ArchSource

        class FakeApps:
            def list_namespaced_replica_set(self, ns):
                return SimpleNamespace(items=replicasets)

        fake = SimpleNamespace(
            client=SimpleNamespace(AppsV1Api=lambda: FakeApps()),
            config=SimpleNamespace(load_kube_config=lambda **kw: None),
        )
        monkeypatch.setitem(__import__("sys").modules, "kubernetes", fake)
        arch = Architecture()
        history.collect_history(ArchSource(type="change_history", namespaces=["default"]),
                                arch, None)
        return arch

    def test_records_when_a_workload_last_changed(self, monkeypatch):
        old = datetime.now(timezone.utc) - timedelta(days=145)
        arch = self._collect(monkeypatch, [rs("api-1", 1, old, "demo:1")])
        assert "145d ago" in arch.services["api"].best("last_changed").value

    def test_diffs_consecutive_revisions(self, monkeypatch):
        now = datetime.now(timezone.utc)
        arch = self._collect(monkeypatch, [
            rs("api-1", 1, now - timedelta(days=200), "demo:2.0.2", memory="300Mi"),
            rs("api-2", 2, now - timedelta(days=145), "demo:2.2.0", memory="300Mi"),
        ])
        change = arch.services["api"].best("last_change_was").value
        assert "2.0.2" in change and "2.2.0" in change
        # The limit did NOT move across that upgrade -- the point of the diff.
        assert "resources" not in change

    def test_a_single_revision_has_no_diff(self, monkeypatch):
        arch = self._collect(monkeypatch, [rs("api-1", 1, datetime.now(timezone.utc), "demo:1")])
        s = arch.services["api"]
        assert s.best("revisions").value == 1 and s.best("last_change_was") is None

    def test_revisions_are_ordered_regardless_of_listing_order(self, monkeypatch):
        now = datetime.now(timezone.utc)
        arch = self._collect(monkeypatch, [
            rs("api-2", 2, now, "demo:new"),
            rs("api-1", 1, now - timedelta(days=10), "demo:old"),
        ])
        assert "demo:old -> demo:new" in arch.services["api"].best("last_change_was").value
