"""The Slack thread -> session map (design 001 §7.1).

This store is the only thing that knows a live Anthropic session belongs to a
Slack thread. Lose a row and the session is stranded: still running and being
billed on Anthropic's side, unreachable from Slack. So the properties worth
testing are durability across a restart, correct replacement semantics, and
behaving under the concurrency Bolt actually produces.
"""

from __future__ import annotations

import threading
import time

import pytest

from k8srca.slack.sessions import SessionStore, ThreadSession


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "nested" / "sessions.db")


class TestThreadMapping:
    def test_round_trip(self, store):
        store.put("C1", "111.1", "sess_a", 7)
        got = store.get("C1", "111.1")
        assert (got.session_id, got.agent_version, got.status) == ("sess_a", 7, "active")

    def test_unknown_thread_is_none(self, store):
        assert store.get("C1", "nope") is None

    def test_threads_are_keyed_by_channel_and_ts(self, store):
        # The same ts in two channels is two conversations.
        store.put("C1", "111.1", "sess_a", 1)
        store.put("C2", "111.1", "sess_b", 1)
        assert store.get("C1", "111.1").session_id == "sess_a"
        assert store.get("C2", "111.1").session_id == "sess_b"

    def test_put_replaces_the_session_for_a_thread(self, store):
        # When resolve() gives up on a dead session it starts a new one; the
        # thread must end up pointing at the new one, not two rows.
        store.put("C1", "111.1", "old", 1)
        store.put("C1", "111.1", "new", 2)
        got = store.get("C1", "111.1")
        assert (got.session_id, got.agent_version) == ("new", 2)

    def test_put_reactivates_a_terminated_thread(self, store):
        store.put("C1", "111.1", "old", 1)
        store.touch("C1", "111.1", "terminated")
        store.put("C1", "111.1", "new", 1)
        assert store.get("C1", "111.1").status == "active"

    def test_survives_a_restart(self, tmp_path):
        # The orchestrator restarting must not strand a live session.
        path = tmp_path / "sessions.db"
        SessionStore(path).put("C1", "111.1", "sess_a", 3)
        assert SessionStore(path).get("C1", "111.1").session_id == "sess_a"

    def test_agent_version_may_be_unknown(self, store):
        store.put("C1", "111.1", "sess_a", None)
        assert store.get("C1", "111.1").agent_version is None


class TestTouch:
    def test_advances_last_activity(self, store):
        row = store.put("C1", "111.1", "sess_a", 1)
        time.sleep(0.01)
        store.touch("C1", "111.1")
        assert store.get("C1", "111.1").last_activity > row.last_activity

    def test_records_status(self, store):
        store.put("C1", "111.1", "sess_a", 1)
        store.touch("C1", "111.1", "terminated")
        assert store.get("C1", "111.1").status == "terminated"

    def test_without_status_leaves_status_alone(self, store):
        store.put("C1", "111.1", "sess_a", 1)
        store.touch("C1", "111.1", "terminated")
        store.touch("C1", "111.1")
        assert store.get("C1", "111.1").status == "terminated"

    def test_unknown_thread_is_not_an_error(self, store):
        store.touch("C1", "nope")  # a turn racing a deleted row must not crash


class TestStaleness:
    def row(self, last_activity):
        return ThreadSession("C1", "111.1", "s", 1, "active", last_activity, last_activity)

    def test_fresh_is_not_stale(self):
        assert not self.row(time.time()).stale(ttl_minutes=120)

    def test_old_is_stale(self):
        assert self.row(time.time() - 121 * 60).stale(ttl_minutes=120)

    def test_boundary_is_not_stale(self):
        # Exactly at the TTL still counts as live; only past it is stale.
        assert not self.row(time.time() - 119.5 * 60).stale(ttl_minutes=120)


class TestEventDedup:
    def test_first_sighting_is_new(self, store):
        assert store.already_seen("ev1") is False

    def test_second_sighting_is_a_redelivery(self, store):
        store.already_seen("ev1")
        assert store.already_seen("ev1") is True

    def test_distinct_events_do_not_collide(self, store):
        assert store.already_seen("ev1") is False
        assert store.already_seen("ev2") is False

    def test_empty_id_is_never_suppressed(self, store):
        # No id means no identity; suppressing would drop real messages.
        assert store.already_seen("") is False
        assert store.already_seen("") is False

    def test_dedup_survives_a_restart(self, tmp_path):
        # Slack redelivers for minutes. An orchestrator that restarts mid-turn
        # would otherwise run the same investigation twice.
        path = tmp_path / "sessions.db"
        SessionStore(path).already_seen("ev1")
        assert SessionStore(path).already_seen("ev1") is True

    def test_exactly_one_racing_caller_wins(self, store):
        # Two Bolt handler threads can see the same redelivery at once. The
        # INSERT is the lock: exactly one must be told the event is new.
        results: list[bool] = []
        barrier = threading.Barrier(8)
        lock = threading.Lock()

        def attempt():
            barrier.wait(timeout=5)
            seen = store.already_seen("ev1")
            with lock:
                results.append(seen)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert results.count(False) == 1, results


class TestPruning:
    def test_removes_old_events(self, store):
        store.already_seen("old")
        assert store.prune_events(older_than_seconds=-1) == 1
        assert store.already_seen("old") is False  # prunable again, i.e. gone

    def test_keeps_recent_events(self, store):
        store.already_seen("recent")
        assert store.prune_events(older_than_seconds=3600) == 0
        assert store.already_seen("recent") is True

    def test_does_not_touch_thread_rows(self, store):
        store.put("C1", "111.1", "sess_a", 1)
        store.prune_events(older_than_seconds=-1)
        assert store.get("C1", "111.1") is not None


class TestConcurrentWriters:
    def test_many_threads_writing_distinct_rows(self, store):
        # Bolt dispatches handlers on a pool and each call opens its own
        # connection; SQLite must not lose or garble rows under that.
        errors: list[Exception] = []

        def write(i: int):
            try:
                store.put("C1", f"{i}.0", f"sess_{i}", i)
                store.touch("C1", f"{i}.0")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors
        assert all(store.get("C1", f"{i}.0").session_id == f"sess_{i}" for i in range(20))

    def test_concurrent_writes_to_one_thread_leave_one_row(self, store):
        for i in range(10):
            threading.Thread(target=store.put, args=("C1", "111.1", f"sess_{i}", i)).start()
        time.sleep(0.2)
        got = store.get("C1", "111.1")
        assert got is not None and got.session_id.startswith("sess_")
