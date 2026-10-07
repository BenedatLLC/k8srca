"""Workload histories added to an older capture (scenario backfill-histories)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from k8srca.scenario.backfill import BackfillError, backfill, duration_seconds, to_capture

CAPTURED = datetime(2026, 9, 30, 14, 35, 6, tzinfo=timezone.utc)
READ = CAPTURED + timedelta(days=6)


def history(rev_age="P170DT0H0M0S", written="P10D", complete=True):
    return {"kind": "Deployment", "name": "ad", "namespace": "default", "complete": complete,
            "limits": ["..."],
            "revisions": [{"revision": 2, "source": "ReplicaSet/ad-2", "age": rev_age,
                           "current": True, "changes": []}],
            "config": [{"kind": "ConfigMap", "name": "cm", "used_as": ["env"],
                        "exists": True, "age": "P200D", "last_written": written},
                       {"kind": "Secret", "name": "s", "used_as": ["env"],
                        "exists": None, "age": None, "last_written": None}]}


def test_iso_durations():
    assert duration_seconds("P170DT6H7M26S") == 170 * 86400 + 6 * 3600 + 7 * 60 + 26
    assert duration_seconds("PT0.5S") == 0.5
    with pytest.raises(BackfillError):
        duration_seconds("170 days")


def test_ages_are_shifted_back_to_the_capture():
    r = to_capture(history(), READ, CAPTURED)
    assert r["revisions"][0]["age_seconds"] == 164 * 86400
    assert r["config"][0]["last_written_seconds"] == 4 * 86400
    assert r["config"][1]["age_seconds"] is None and "age" not in r["config"][1]


def test_anything_newer_than_the_capture_refuses_the_merge():
    with pytest.raises(BackfillError, match="after the capture"):
        to_capture(history(written="P2D"), READ, CAPTURED)      # written 4 days after it
    with pytest.raises(BackfillError, match="after the capture"):
        to_capture(history(rev_age="P1D"), READ, CAPTURED)      # a revision since


def test_an_incomplete_history_is_refused():
    with pytest.raises(BackfillError, match="not complete"):
        to_capture(history(complete=False), READ, CAPTURED)


def test_every_captured_workload_is_read_and_the_rest_is_untouched():
    capture = {"captured_at": CAPTURED.isoformat(), "pods": [{"x": 1}],
               "deployments": [{"name": "ad", "namespace": "default"}],
               "statefulsets": [], "daemonsets": []}
    asked = []

    async def call(tool, /, **kw):
        asked.append((tool, kw))
        return [history()]

    out = asyncio.run(backfill(capture, call, now=lambda: READ))
    assert asked == [("get_workload_history",
                      {"name": "ad", "namespace": "default", "kind": "Deployment"})]
    assert out["pods"] == capture["pods"] and len(out["workload_histories"]) == 1
    assert "workload_histories" not in capture          # the input is not mutated


def test_a_capture_that_has_histories_is_left_alone():
    with pytest.raises(BackfillError, match="already"):
        asyncio.run(backfill({"captured_at": CAPTURED.isoformat(),
                              "workload_histories": [{}]}, None))
