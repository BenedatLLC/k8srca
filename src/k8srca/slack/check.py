"""End-to-end verification of a Slack app configuration.

Checks the four things that go wrong, in the order they go wrong:

1. bot token valid            (auth.test)
2. required scopes granted    (from the auth.test response headers)
3. bot invited to the channel (conversations.info + a real post)
4. Socket Mode connects       (app-level token + connections:write)

Then optionally waits for an inbound event, which is the only way to prove
the event subscriptions are right -- scopes and connectivity can all be
correct while no events are delivered because none were subscribed to.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..settings import SlackSettings

# Minimum for the v1 messaging surface (design 001 §7.1).
REQUIRED_SCOPES = {"app_mentions:read", "chat:write", "channels:history", "im:history"}
# Optional, but design 001 §7.5 / 002 §5.3 use them.
OPTIONAL_SCOPES = {"reactions:write", "files:write", "users:read", "groups:history"}


@dataclass
class CheckResult:
    ok: bool
    detail: str


def check_auth(client: WebClient) -> tuple[CheckResult, set[str]]:
    try:
        resp = client.auth_test()
    except SlackApiError as exc:
        return CheckResult(False, f"auth.test failed: {exc.response['error']}"), set()
    # Slack returns the token's granted scopes in this response header.
    granted = {s.strip() for s in (resp.headers.get("x-oauth-scopes") or "").split(",") if s.strip()}
    return (
        CheckResult(True, f"bot @{resp['user']} in workspace {resp['team']} (bot_id={resp.get('bot_id')})"),
        granted,
    )


def check_scopes(granted: set[str]) -> CheckResult:
    if not granted:
        return CheckResult(True, "could not read granted scopes from the response; skipping")
    missing = REQUIRED_SCOPES - granted
    if missing:
        return CheckResult(
            False,
            "missing required scopes: " + ", ".join(sorted(missing))
            + " -- add under OAuth & Permissions, then REINSTALL the app",
        )
    absent_optional = OPTIONAL_SCOPES - granted
    note = f" (optional not granted: {', '.join(sorted(absent_optional))})" if absent_optional else ""
    return CheckResult(True, f"all required scopes present{note}")


def check_channel(client: WebClient, channel_id: str) -> CheckResult:
    """Best-effort membership check.

    conversations.info needs `channels:read`, which is not required for the
    app to work. When it is absent we skip rather than demanding another
    reinstall: chat.postMessage returning `not_in_channel` is an equally good
    membership test, and we are about to call it anyway.
    """
    try:
        info = client.conversations_info(channel=channel_id)
    except SlackApiError as exc:
        err = exc.response["error"]
        if err == "missing_scope":
            return CheckResult(True, "skipped (needs channels:read; the send below tests membership)")
        if err == "channel_not_found":
            return CheckResult(False, f"{channel_id}: not found, or the bot cannot see it")
        return CheckResult(False, f"conversations.info failed: {err}")
    ch = info["channel"]
    if not ch.get("is_member"):
        return CheckResult(False, f"#{ch['name']}: bot is NOT a member -- run `/invite @<app>` in that channel")
    return CheckResult(True, f"#{ch['name']}: bot is a member")


def post_test_message(client: WebClient, channel_id: str, text: str) -> tuple[CheckResult, str | None]:
    try:
        resp = client.chat_postMessage(channel=channel_id, text=text)
    except SlackApiError as exc:
        err = exc.response["error"]
        if err == "not_in_channel":
            return CheckResult(False, f"{channel_id}: bot is not a member -- run `/invite @<app>` there"), None
        if err == "channel_not_found":
            return CheckResult(False, f"{channel_id}: no such channel visible to this bot"), None
        return CheckResult(False, f"chat.postMessage failed: {err}"), None
    return CheckResult(True, f"posted (ts={resp['ts']})"), resp["ts"]


def listen(settings: SlackSettings, channel_id: str | None, seconds: int) -> list[dict]:
    """Open Socket Mode and collect inbound events for `seconds`.

    Proves the app-level token, `connections:write`, and the event
    subscriptions all work -- the last of which nothing else can prove.
    """
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    received: list[dict] = []
    app = App(token=settings.bot_token, signing_secret=settings.signing_secret or None,
              token_verification_enabled=False, request_verification_enabled=False)

    @app.event("app_mention")
    def _mention(event, say):  # noqa: ANN001
        received.append({"type": "app_mention", "user": event.get("user"),
                         "channel": event.get("channel"), "text": event.get("text", "")})
        say(text="Got it - k8srca is receiving events.", thread_ts=event.get("thread_ts") or event["ts"])

    @app.event("message")
    def _message(event, logger):  # noqa: ANN001
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return  # our own echo
        received.append({"type": f"message.{event.get('channel_type', '?')}",
                         "user": event.get("user"), "channel": event.get("channel"),
                         "text": event.get("text", "")})

    handler = SocketModeHandler(app, settings.socket_mode_token)
    thread = threading.Thread(target=handler.start, daemon=True)
    thread.start()
    time.sleep(seconds)
    try:
        handler.close()
    except Exception:  # noqa: BLE001 - best effort on shutdown
        pass
    return received
