"""Change history from ReplicaSet revisions, read through the k8stools MCP tool.

An upstream chart repository records what the *project* changed. This records
what happened to *this cluster*, which is the question RCA actually asks.

k8srca never touches the Kubernetes API itself (CLAUDE.md), so these tests
exercise the parsing and diffing of what the MCP tool returns.
"""

from datetime import timedelta

import pytest

from k8srca.arch import history


class TestParseAge:
    """k8stools renders durations as ISO-8601."""

    def test_days_and_time(self):
        assert history.parse_age("P145DT8H3M") == timedelta(days=145, hours=8, minutes=3)

    def test_days_only(self):
        assert history.parse_age("P145D").days == 145

    def test_time_only(self):
        assert history.parse_age("PT30M") == timedelta(minutes=30)

    def test_fractional_seconds(self):
        assert history.parse_age("PT1.5S") == timedelta(seconds=1.5)

    def test_numeric_seconds_are_accepted(self):
        assert history.parse_age(86400) == timedelta(days=1)

    def test_unparseable_is_none_not_an_exception(self):
        # A shape we did not anticipate must not fail the whole build.
        assert history.parse_age("not-a-duration") is None
        assert history.parse_age(None) is None


class TestDescribe:
    def test_reports_an_image_change(self):
        before = {"images": ["demo:2.0.2-ad"], "desired_replicas": 1}
        after = {"images": ["demo:2.2.0-ad"], "desired_replicas": 1}
        assert "images" in history.describe(before, after)[0]

    def test_silent_when_nothing_tracked_changed(self):
        same = {"images": ["x"], "desired_replicas": 1}
        assert history.describe(same, dict(same)) == []

    def test_untracked_fields_are_ignored(self):
        # Only images and replica counts matter; other churn buries the signal.
        before = {"images": ["x"], "desired_replicas": 1, "name": "ad-1"}
        after = {"images": ["x"], "desired_replicas": 1, "name": "ad-2"}
        assert history.describe(before, after) == []


class FakeTool:
    def __init__(self, rows):
        self.rows = rows
        self.name = "get_replicaset_summaries"

    async def call(self, args):
        import json
        return [{"type": "text", "text": json.dumps(r)} for r in self.rows]


def rs(name, owner="ad", revision=1, images=("demo:1",), age="P10D", desired=1):
    return {"name": name, "namespace": "default", "owner_deployment": owner,
            "revision": revision, "images": list(images), "age": age,
            "desired_replicas": desired, "current_replicas": desired,
            "ready_replicas": desired}


@pytest.fixture
def collect(monkeypatch):
    """Drive collect_history against a fake MCP session."""
    import contextlib
    from types import SimpleNamespace

    from k8srca.arch.model import Architecture
    from k8srca.config import ArchSource, McpServer

    def run(rows):
        tool = FakeTool(rows)

        @contextlib.asynccontextmanager
        async def fake_connect(spec):
            yield SimpleNamespace(spec=spec, session=None, tools=[tool])

        monkeypatch.setattr(history, "connect", fake_connect)
        monkeypatch.setattr(history, "wrap_mcp_tool", lambda t, s, prefix="": t)

        import asyncio
        arch = Architecture()
        asyncio.run(history.collect_history(
            ArchSource(type="change_history", namespaces=["default"]), arch,
            McpServer(name="k8stools", url="http://k8stools:8000/mcp")))
        return arch

    return run


class TestCollect:
    def test_records_when_a_workload_last_changed(self, collect):
        arch = collect([rs("ad-1", age="P145DT2H")])
        assert arch.services["ad"].best("last_changed").value == "145d ago"

    def test_diffs_consecutive_revisions(self, collect):
        arch = collect([
            rs("ad-1", revision=1, images=("demo:2.0.2-ad",), desired=0),
            rs("ad-2", revision=2, images=("demo:2.2.0-ad",)),
        ])
        change = arch.services["ad"].best("last_change_was").value
        assert "2.0.2" in change and "2.2.0" in change

    def test_orders_revisions_regardless_of_listing_order(self, collect):
        # The diff is meaningless in the wrong order, so do not trust the
        # server's ordering even though k8stools sorts.
        arch = collect([
            rs("ad-2", revision=2, images=("demo:new",)),
            rs("ad-1", revision=1, images=("demo:old",)),
        ])
        assert "demo:old'] -> ['demo:new" in arch.services["ad"].best("last_change_was").value

    def test_single_revision_has_no_diff(self, collect):
        arch = collect([rs("ad-1")])
        s = arch.services["ad"]
        assert s.best("revisions").value == 1 and s.best("last_change_was") is None

    def test_replicasets_without_a_deployment_owner_are_skipped(self, collect):
        arch = collect([rs("solo", owner=None)])
        assert "solo" not in arch.services

    def test_missing_tool_names_the_version_needed(self, monkeypatch):
        import asyncio
        import contextlib
        from types import SimpleNamespace

        from k8srca.arch.model import Architecture
        from k8srca.config import ArchSource, McpServer

        @contextlib.asynccontextmanager
        async def empty(spec):
            yield SimpleNamespace(spec=spec, session=None, tools=[])

        monkeypatch.setattr(history, "connect", empty)
        with pytest.raises(RuntimeError, match="1.2.0"):
            asyncio.run(history.collect_history(
                ArchSource(type="change_history", namespaces=["default"]),
                Architecture(), McpServer(name="k8stools", url="http://x/mcp")))
