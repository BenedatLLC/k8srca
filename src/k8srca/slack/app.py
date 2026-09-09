"""The Slack orchestrator (design 001 §7.1).

Plain messaging: `@k8srca` in a channel, or a DM. A Slack thread is a session;
follow-ups in that thread continue it. Slack's Agents/AI-apps feature is
deliberately not used (001 §7.1).

Runs in Socket Mode -- outbound only, no public endpoint, matching the rest of
the deployment. Holds the Anthropic API key and the Slack tokens, and never
the environment key: this process must not be able to claim work items.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from ..config import Config
from ..session import console_url, create as create_session
from ..settings import SlackSettings
from ..state import State
from .relay import SlackTurn, consume
from .sessions import SessionStore

log = logging.getLogger("k8srca.slack")

MENTION = re.compile(r"<@[A-Z0-9]+>")


def event_key(event: dict) -> str:
    """Stable identity for an inbound event, for redelivery suppression.

    `client_msg_id` is per-message and survives retries; `event_ts` is the
    fallback for events that carry none.
    """
    return event.get("client_msg_id") or event.get("event_ts") or event.get("ts", "")


class Orchestrator:
    def __init__(self, cfg: Config, state: State, settings: SlackSettings,
                 store: SessionStore, workspace_id: str | None = None,
                 max_concurrent: int = 4) -> None:
        self.cfg = cfg
        self.state = state
        self.settings = settings
        self.store = store
        self.workspace_id = workspace_id
        self.client = anthropic.Anthropic()
        self.pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="turn")
        # One turn at a time per thread: a session processes turns in order,
        # and two overlapping streams on one thread would interleave output.
        self.locks: dict[tuple[str, str], threading.Lock] = defaultdict(threading.Lock)

    # -- session resolution ---------------------------------------------
    def resolve(self, channel: str, thread_ts: str) -> tuple[str, bool]:
        """Session for this thread, creating or replacing as needed."""
        existing = self.store.get(channel, thread_ts)
        if existing and existing.status == "active" and not existing.stale(
                self.cfg.session.idle_ttl_minutes):
            try:
                live = self.client.beta.sessions.retrieve(existing.session_id)
                if live.status != "terminated":
                    return existing.session_id, False
                log.info("session %s terminated; starting a fresh one", existing.session_id)
            except Exception as exc:  # noqa: BLE001 - deleted or unreachable
                log.warning("session %s unusable (%s); starting a fresh one",
                            existing.session_id, exc)
        return "", True

    def start_session(self, channel: str, thread_ts: str, text: str, user: str) -> str:
        coordinator = self.state.agents[self.cfg.coordinator]
        session = create_session(
            self.client, self.cfg, self.state, text,
            title=text[:80],
            # Set at creation and impossible to backfill (003 §2.2).
            metadata={
                "slack_channel": channel,
                "slack_thread_ts": thread_ts,
                "slack_user": user,
                "trigger": "user_question",
                "config_rev": self.state.config_rev or "",
            },
        )
        self.store.put(channel, thread_ts, session.id, coordinator.version)
        return session.id

    # -- turn execution --------------------------------------------------
    def handle(self, slack, channel: str, thread_ts: str, message_ts: str,
               text: str, user: str) -> None:
        lock = self.locks[(channel, thread_ts)]
        with lock:
            turn = SlackTurn(slack, channel, thread_ts)
            turn.ack(message_ts)
            try:
                self._run(slack, turn, channel, thread_ts, text, user)
            except Exception as exc:  # noqa: BLE001 - never leave a thread hanging
                log.exception("turn failed")
                turn.fail(f"Something went wrong: `{exc}`")

    def _run(self, slack, turn: SlackTurn, channel: str, thread_ts: str,
             text: str, user: str) -> None:
        session_id, is_new = self.resolve(channel, thread_ts)
        turn.start("🔍 investigating…" if not is_new else "🔍 starting…")

        if is_new:
            session_id = self.start_session(channel, thread_ts, text, user)
            # initial_events already started the run; just read it.
            with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
                render = consume(stream, turn)
        else:
            # Stream before send (001 §7.2): the stream only carries events
            # emitted after it opens.
            with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
                self.client.beta.sessions.events.send(
                    session_id=session_id,
                    events=[{"type": "user.message",
                             "content": [{"type": "text", "text": text}]}],
                )
                render = consume(stream, turn)

        self.store.touch(channel, thread_ts,
                         "terminated" if render.terminated else "active")

        if render.answer:
            turn.deliver(render.answer)
        elif render.stop_reason == "budget_reached":
            turn.fail("This investigation hit its cost cap and paused. "
                      "Start a new thread to continue.")
        elif render.errors:
            turn.fail(f"The agent reported an error: `{render.errors[0]}`")
        else:
            turn.fail(f"The agent stopped without answering (stop_reason={render.stop_reason}). "
                      f"{console_url(session_id, self.workspace_id)}")

        log.info("turn done session=%s tools=%d delegations=%d stop=%s",
                 session_id, len(render.tools), render.delegations, render.stop_reason)


def build_app(orch: Orchestrator, settings: SlackSettings) -> App:
    app = App(token=settings.bot_token, signing_secret=settings.signing_secret or None,
              token_verification_enabled=False, request_verification_enabled=False)
    bot_user_id = app.client.auth_test()["user_id"]

    def dispatch(event: dict, say, client) -> None:
        channel = event.get("channel", "")
        if not settings.channel_allowed(channel):
            log.info("ignoring message in %s (not in allowlist)", channel)
            return
        text = MENTION.sub("", event.get("text", "")).strip()
        if not text:
            return
        # A Slack thread is a session; a top-level message starts one.
        thread_ts = event.get("thread_ts") or event["ts"]
        orch.pool.submit(orch.handle, client, channel, thread_ts,
                         event["ts"], text, event.get("user", ""))

    @app.event("app_mention")
    def on_mention(event, say, client):  # noqa: ANN001
        # Dedupe unconditionally. Turns take a minute or more, well past
        # Slack's redelivery window, so a retry is the normal case rather
        # than an exceptional one -- without this, a slow investigation runs
        # twice and posts two answers.
        if orch.store.already_seen(event_key(event)):
            return
        dispatch(event, say, client)

    @app.event("message")
    def on_message(event, say, client):  # noqa: ANN001
        if event.get("bot_id") or event.get("subtype"):
            return
        if event.get("user") == bot_user_id:
            return
        thread_ts = event.get("thread_ts")
        is_dm = event.get("channel_type") == "im"
        # In channels, only follow-ups inside a thread we already own: a bare
        # message in a busy channel is not addressed to us. `app_mention`
        # covers the first turn.
        if not is_dm:
            if not thread_ts or not orch.store.get(event.get("channel", ""), thread_ts):
                return
            if f"<@{bot_user_id}>" in event.get("text", ""):
                return  # app_mention will handle it; do not run it twice
        if orch.store.already_seen(event_key(event)):
            return
        dispatch(event, say, client)

    return app


def run(cfg: Config, state: State, settings: SlackSettings, store: SessionStore,
        workspace_id: str | None = None) -> None:
    orch = Orchestrator(cfg, state, settings, store, workspace_id)
    app = build_app(orch, settings)
    log.info("orchestrator ready; channels=%s",
             sorted(settings.allowed_channels) or "ALL")
    SocketModeHandler(app, settings.socket_mode_token).start()
