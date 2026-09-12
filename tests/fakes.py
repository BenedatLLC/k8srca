"""Fakes shared by more than one test module."""

from __future__ import annotations

import itertools
import threading


class FakeSlack:
    """Records what landed in each thread, so cross-talk is observable."""

    def __init__(self):
        self.lock = threading.Lock()
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.reactions: list[dict] = []
        self._ts = itertools.count(1)

    def chat_postMessage(self, channel, thread_ts, text):
        with self.lock:
            ts = f"ph.{next(self._ts)}"
            self.posts.append({"channel": channel, "thread_ts": thread_ts, "ts": ts, "text": text})
        return {"ts": ts}

    def chat_update(self, channel, ts, text):
        with self.lock:
            self.updates.append({"channel": channel, "ts": ts, "text": text})
        return {"ts": ts}

    def reactions_add(self, channel, timestamp, name):
        with self.lock:
            self.reactions.append({"channel": channel, "timestamp": timestamp, "name": name})

    # -- queries used by assertions -------------------------------------
    def thread_texts(self, channel, thread_ts) -> list[str]:
        """Everything a human would see in one thread, in order."""
        with self.lock:
            mine = {p["ts"] for p in self.posts
                    if p["channel"] == channel and p["thread_ts"] == thread_ts}
            out = [p["text"] for p in self.posts
                   if p["channel"] == channel and p["thread_ts"] == thread_ts]
            out += [u["text"] for u in self.updates if u["ts"] in mine]
        return out

    def final_text(self, channel, thread_ts) -> str:
        with self.lock:
            mine = [p["ts"] for p in self.posts
                    if p["channel"] == channel and p["thread_ts"] == thread_ts]
            updates = [u["text"] for u in self.updates if u["ts"] in set(mine)]
        return updates[-1] if updates else ""
