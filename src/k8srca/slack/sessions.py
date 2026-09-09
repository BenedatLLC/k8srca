"""Slack thread <-> Managed Agents session mapping (design 001 §7.1).

One Slack thread is one session. The map is SQLite because it must survive an
orchestrator restart: a session is live on Anthropic's side regardless of
whether this process is, and losing the mapping would strand it.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(".k8srca/sessions.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    channel_id      TEXT NOT NULL,
    thread_ts       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    agent_version   INTEGER,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      REAL NOT NULL,
    last_activity   REAL NOT NULL,
    PRIMARY KEY (channel_id, thread_ts)
);
CREATE INDEX IF NOT EXISTS threads_session ON threads(session_id);
CREATE TABLE IF NOT EXISTS seen_events (
    event_id   TEXT PRIMARY KEY,
    seen_at    REAL NOT NULL
);
"""


@dataclass
class ThreadSession:
    channel_id: str
    thread_ts: str
    session_id: str
    agent_version: int | None
    status: str
    created_at: float
    last_activity: float

    def stale(self, ttl_minutes: int) -> bool:
        return (time.time() - self.last_activity) > ttl_minutes * 60


class SessionStore:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self._conn()) as c:
            c.executescript(SCHEMA)
            c.commit()

    def _conn(self) -> sqlite3.Connection:
        # check_same_thread=False: Bolt dispatches handlers on a pool, and each
        # call opens and closes its own connection.
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, channel_id: str, thread_ts: str) -> ThreadSession | None:
        with closing(self._conn()) as c:
            row = c.execute(
                "SELECT * FROM threads WHERE channel_id=? AND thread_ts=?",
                (channel_id, thread_ts),
            ).fetchone()
        return ThreadSession(**dict(row)) if row else None

    def put(self, channel_id: str, thread_ts: str, session_id: str,
            agent_version: int | None) -> ThreadSession:
        now = time.time()
        with closing(self._conn()) as c:
            c.execute(
                "INSERT INTO threads (channel_id, thread_ts, session_id, agent_version,"
                " status, created_at, last_activity) VALUES (?,?,?,?,'active',?,?)"
                " ON CONFLICT(channel_id, thread_ts) DO UPDATE SET"
                " session_id=excluded.session_id, agent_version=excluded.agent_version,"
                " status='active', created_at=excluded.created_at,"
                " last_activity=excluded.last_activity",
                (channel_id, thread_ts, session_id, agent_version, now, now),
            )
            c.commit()
        return ThreadSession(channel_id, thread_ts, session_id, agent_version, "active", now, now)

    def touch(self, channel_id: str, thread_ts: str, status: str | None = None) -> None:
        with closing(self._conn()) as c:
            if status:
                c.execute("UPDATE threads SET last_activity=?, status=? WHERE channel_id=? AND thread_ts=?",
                          (time.time(), status, channel_id, thread_ts))
            else:
                c.execute("UPDATE threads SET last_activity=? WHERE channel_id=? AND thread_ts=?",
                          (time.time(), channel_id, thread_ts))
            c.commit()

    def already_seen(self, event_id: str) -> bool:
        """Slack redelivers events that are not acked promptly.

        Turns take a minute or more, so redelivery is expected rather than
        exceptional; without this every slow investigation would run twice.
        """
        if not event_id:
            return False
        with closing(self._conn()) as c:
            try:
                c.execute("INSERT INTO seen_events VALUES (?,?)", (event_id, time.time()))
                c.commit()
                return False
            except sqlite3.IntegrityError:
                return True

    def prune_events(self, older_than_seconds: float = 86400) -> int:
        with closing(self._conn()) as c:
            cur = c.execute("DELETE FROM seen_events WHERE seen_at < ?",
                            (time.time() - older_than_seconds,))
            c.commit()
            return cur.rowcount
