"""Which Slack events the bot acts on (design 001 §7.1).

`build_app` decides, for every message Slack delivers, whether this is a
question for us. Getting it wrong is loud in both directions: answer too
eagerly and the bot interrupts a busy channel, answer too reluctantly and an
SRE's follow-up disappears. The decision is pure filtering, so it is tested
here without Bolt's socket, by calling the registered handlers directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from k8srca.settings import SlackSettings
from k8srca.slack import app as app_mod
from k8srca.slack.app import Orchestrator, build_app
from k8srca.slack.sessions import SessionStore

from fakes import config, state

# Real Slack IDs are uppercase alphanumerics with no punctuation, and the
# mention regex only matches that shape -- underscores here would let a test
# pass against text the code would never have stripped.
BOT = "U08BOTID01"
HUMAN = "U08HUMAN02"
CHANNEL = "C08OPS0001"
DM = "D08DM00001"


class FakeApp:
    """Stands in for slack_bolt.App: collects handlers, answers auth_test."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.client = SimpleNamespace(auth_test=lambda: {"user_id": BOT})
        self.handlers: dict[str, callable] = {}

    def event(self, name):
        def register(fn):
            self.handlers[name] = fn
            return fn
        return register


class RecordingPool:
    """Captures what would have been run, instead of running it."""

    def __init__(self):
        self.submissions: list[tuple] = []

    def submit(self, fn, *args):
        self.submissions.append((fn, args))


class Harness:
    """One bot, one store, and a record of what it decided to answer."""

    def __init__(self, app, orch, pool, store):
        self.app = app
        self.orch = orch
        self.pool = pool
        self.store = store
        self.slack = SimpleNamespace(name="workspace-client")

    def mention(self, **event):
        self.app.handlers["app_mention"](event, None, self.slack)

    def message(self, **event):
        self.app.handlers["message"](event, None, self.slack)

    @property
    def turns(self) -> list[dict]:
        out = []
        for fn, args in self.pool.submissions:
            slack, channel, thread_ts, message_ts, text, user = args
            out.append({"fn": fn, "slack": slack, "channel": channel,
                        "thread_ts": thread_ts, "message_ts": message_ts,
                        "text": text, "user": user})
        return out

    @property
    def answered(self) -> bool:
        return bool(self.pool.submissions)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    def build(allowed=()):
        monkeypatch.setattr(app_mod, "App", FakeApp)
        monkeypatch.setattr(app_mod.anthropic, "Anthropic", lambda *a, **k: object())
        settings = SlackSettings(socket_mode_token="xapp-x", bot_token="xoxb-x",
                                 allowed_channels=set(allowed))
        store = SessionStore(tmp_path / "sessions.db")
        orch = Orchestrator(config(), state(), settings, store)
        pool = RecordingPool()
        orch.pool = pool
        return Harness(build_app(orch, settings), orch, pool, store)
    return build


# -- wiring -------------------------------------------------------------
class TestRegistration:
    def test_subscribes_to_mentions_and_messages(self, harness):
        assert set(harness().app.handlers) == {"app_mention", "message"}

    def test_socket_mode_does_its_own_verification(self, harness):
        # There is no HTTP endpoint to verify against; leaving these on makes
        # App construction demand a signing secret that Socket Mode never uses.
        kwargs = harness().app.kwargs
        assert kwargs["token_verification_enabled"] is False
        assert kwargs["request_verification_enabled"] is False


# -- @k8srca ... --------------------------------------------------------
class TestMentions:
    def test_starts_a_turn(self, harness):
        h = harness()
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1",
                  text=f"<@{BOT}> why is ad crashing?")
        assert h.turns == [{"fn": h.orch.handle, "slack": h.slack, "channel": CHANNEL,
                            "thread_ts": "1.1", "message_ts": "1.1",
                            "text": "why is ad crashing?", "user": HUMAN}]

    def test_strips_every_mention_not_just_the_first(self, harness):
        h = harness()
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1",
                  text=f"<@{BOT}> ask <@{HUMAN}> about the ad service")
        assert h.turns[0]["text"] == "ask  about the ad service"

    def test_a_bare_mention_is_not_a_question(self, harness):
        h = harness()
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1", text=f"<@{BOT}>")
        assert not h.answered

    def test_a_top_level_mention_opens_a_thread_on_itself(self, harness):
        h = harness()
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1", text=f"<@{BOT}> hi")
        assert h.turns[0]["thread_ts"] == "1.1"

    def test_a_mention_inside_a_thread_joins_that_thread(self, harness):
        # The reaction goes on the reply; the session belongs to the parent.
        h = harness()
        h.mention(channel=CHANNEL, ts="2.2", thread_ts="1.1", user=HUMAN,
                  client_msg_id="m1", text=f"<@{BOT}> and the logs?")
        assert h.turns[0]["thread_ts"] == "1.1"
        assert h.turns[0]["message_ts"] == "2.2"


class TestRedelivery:
    def test_a_repeated_mention_runs_once(self, harness):
        # Turns take minutes, well past Slack's retry window, so redelivery is
        # the normal case. Twice means two investigations and two answers.
        h = harness()
        event = dict(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1",
                     text=f"<@{BOT}> why is ad crashing?")
        h.mention(**event)
        h.mention(**event)
        assert len(h.turns) == 1

    def test_identity_is_the_message_not_the_delivery(self, harness):
        # Slack varies event_ts across retries; client_msg_id is stable.
        h = harness()
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1",
                  event_ts="9.1", text=f"<@{BOT}> q")
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1",
                  event_ts="9.2", text=f"<@{BOT}> q")
        assert len(h.turns) == 1

    def test_two_different_questions_both_run(self, harness):
        h = harness()
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1", text=f"<@{BOT}> a")
        h.mention(channel=CHANNEL, ts="2.2", user=HUMAN, client_msg_id="m2", text=f"<@{BOT}> b")
        assert len(h.turns) == 2


class TestChannelAllowlist:
    def test_an_allowed_channel_is_answered(self, harness):
        h = harness(allowed=[CHANNEL])
        h.mention(channel=CHANNEL, ts="1.1", user=HUMAN, client_msg_id="m1", text=f"<@{BOT}> q")
        assert h.answered

    def test_another_channel_is_ignored(self, harness):
        h = harness(allowed=[CHANNEL])
        h.mention(channel="C08RANDOM1", ts="1.1", user=HUMAN, client_msg_id="m1",
                  text=f"<@{BOT}> q")
        assert not h.answered

    def test_an_empty_allowlist_means_anywhere_invited(self, harness):
        h = harness()
        h.mention(channel="C08ANYTH01", ts="1.1", user=HUMAN, client_msg_id="m1",
                  text=f"<@{BOT}> q")
        assert h.answered


# -- follow-ups without a mention ---------------------------------------
class TestThreadFollowUps:
    def owned(self, h, thread_ts="1.1", channel=CHANNEL):
        h.store.put(channel, thread_ts, "sess_a", 1)
        return h

    def test_a_reply_in_our_thread_continues_it(self, harness):
        h = self.owned(harness())
        h.message(channel=CHANNEL, ts="1.2", thread_ts="1.1", user=HUMAN,
                  client_msg_id="m2", text="and the logs?")
        assert h.turns[0]["thread_ts"] == "1.1"
        assert h.turns[0]["text"] == "and the logs?"

    def test_a_reply_in_someone_elses_thread_is_ignored(self, harness):
        h = harness()
        h.message(channel=CHANNEL, ts="9.2", thread_ts="9.1", user=HUMAN,
                  client_msg_id="m2", text="unrelated chatter")
        assert not h.answered

    def test_a_bare_channel_message_is_not_addressed_to_us(self, harness):
        # A busy incident channel is full of messages that are not questions.
        h = self.owned(harness())
        h.message(channel=CHANNEL, ts="5.5", user=HUMAN, client_msg_id="m3",
                  text="anyone else seeing this?")
        assert not h.answered

    def test_a_mention_in_our_thread_is_left_to_app_mention(self, harness):
        h = self.owned(harness())
        h.message(channel=CHANNEL, ts="1.2", thread_ts="1.1", user=HUMAN,
                  client_msg_id="m2", text=f"<@{BOT}> and the logs?")
        assert not h.answered

    def test_and_app_mention_still_picks_it_up(self, harness):
        # The ordering that makes this work: `message` bails out *before*
        # recording the event as seen. Dedup first and the mention handler
        # would find it already claimed, and nobody would answer.
        h = self.owned(harness())
        event = dict(channel=CHANNEL, ts="1.2", thread_ts="1.1", user=HUMAN,
                     client_msg_id="m2", text=f"<@{BOT}> and the logs?")
        h.message(**event)
        h.mention(**event)
        assert len(h.turns) == 1
        assert h.turns[0]["text"] == "and the logs?"

    def test_an_ignored_thread_does_not_consume_its_dedup_slot(self, harness):
        h = harness()
        event = dict(channel=CHANNEL, ts="9.2", thread_ts="9.1", user=HUMAN,
                     client_msg_id="m2", text="chatter")
        h.message(**event)
        h.store.put(CHANNEL, "9.1", "sess_a", 1)   # we adopt the thread later
        h.message(**event)
        assert h.answered

    def test_a_repeated_follow_up_runs_once(self, harness):
        h = self.owned(harness())
        event = dict(channel=CHANNEL, ts="1.2", thread_ts="1.1", user=HUMAN,
                     client_msg_id="m2", text="and the logs?")
        h.message(**event)
        h.message(**event)
        assert len(h.turns) == 1


# -- things that are not a person talking -------------------------------
class TestNonHumanMessages:
    def test_our_own_answers_do_not_start_a_turn(self, harness):
        # The bot posts into the thread it is watching; without this it would
        # answer itself forever.
        h = harness()
        h.store.put(CHANNEL, "1.1", "sess_a", 1)
        h.message(channel=CHANNEL, ts="1.2", thread_ts="1.1", user=BOT,
                  client_msg_id="m2", text="*Finding* — OOMKilled.")
        assert not h.answered

    def test_other_bots_are_ignored(self, harness):
        h = harness()
        h.store.put(CHANNEL, "1.1", "sess_a", 1)
        h.message(channel=CHANNEL, ts="1.2", thread_ts="1.1", bot_id="B08ALERT01",
                  client_msg_id="m2", text="PagerDuty: ad is crashlooping")
        assert not h.answered

    @pytest.mark.parametrize("subtype", ["message_changed", "message_deleted",
                                         "channel_join", "thread_broadcast"])
    def test_edits_deletions_and_joins_are_ignored(self, harness, subtype):
        h = harness()
        h.store.put(CHANNEL, "1.1", "sess_a", 1)
        h.message(channel=CHANNEL, ts="1.2", thread_ts="1.1", user=HUMAN,
                  subtype=subtype, client_msg_id="m2", text="whatever")
        assert not h.answered


# -- direct messages ----------------------------------------------------
class TestDirectMessages:
    def test_a_dm_needs_no_mention(self, harness):
        h = harness()
        h.message(channel=DM, channel_type="im", ts="1.1", user=HUMAN,
                  client_msg_id="m1", text="why is ad crashing?")
        assert h.turns[0]["text"] == "why is ad crashing?"

    def test_a_dm_opens_a_thread_on_itself(self, harness):
        h = harness()
        h.message(channel=DM, channel_type="im", ts="1.1", user=HUMAN,
                  client_msg_id="m1", text="q")
        assert h.turns[0]["thread_ts"] == "1.1"

    def test_a_dm_reply_continues_the_thread(self, harness):
        h = harness()
        h.message(channel=DM, channel_type="im", ts="1.2", thread_ts="1.1", user=HUMAN,
                  client_msg_id="m2", text="and the logs?")
        assert h.turns[0]["thread_ts"] == "1.1"

    def test_a_dm_still_obeys_the_allowlist(self, harness):
        # An allowlist is a deployment boundary, not a channel preference.
        h = harness(allowed=[CHANNEL])
        h.message(channel=DM, channel_type="im", ts="1.1", user=HUMAN,
                  client_msg_id="m1", text="q")
        assert not h.answered

    def test_our_own_dm_replies_are_ignored(self, harness):
        h = harness()
        h.message(channel=DM, channel_type="im", ts="1.2", user=BOT,
                  client_msg_id="m2", text="*Finding* — OOMKilled.")
        assert not h.answered
