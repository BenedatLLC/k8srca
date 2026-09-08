"""Secrets and environment-derived settings, loaded from .env or the environment.

Deliberately separate from config.py: k8srca.yaml is version-controlled and
describes *what the agents are*; this is credentials and per-host paths, and is
never committed. Credential custody follows design 001 §8:

    ANTHROPIC_API_KEY      orchestrator only  (never on the poller host)
    ANTHROPIC_ENVIRONMENT_KEY  poller + sandbox only
    Slack tokens           orchestrator only  (never in the sandbox)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader. Existing environment variables always win."""
    path = Path(path)
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class MissingCredential(RuntimeError):
    pass


def _require(name: str, hint: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingCredential(f"{name} is not set. {hint}")
    return value


@dataclass
class SlackSettings:
    """Orchestrator-only. These must never reach a sandbox."""

    socket_mode_token: str      # xapp-... , app-level token, scope connections:write
    bot_token: str              # xoxb-... , Bot User OAuth Token
    allowed_channels: set[str] = field(default_factory=set)
    signing_secret: str = ""    # unused in Socket Mode

    @classmethod
    def from_env(cls) -> "SlackSettings":
        raw = os.environ.get("SLACK_ALLOWED_CHANNELS", "")
        return cls(
            socket_mode_token=_require(
                "SLACK_SOCKET_MODE_TOKEN",
                "Basic Information -> App-Level Tokens, scope connections:write (xapp-...).",
            ),
            bot_token=_require(
                "SLACK_BOT_USER_OAUTH_TOKEN",
                "OAuth & Permissions -> Bot User OAuth Token, after installing to the workspace (xoxb-...).",
            ),
            allowed_channels={c.strip() for c in raw.split(",") if c.strip()},
            signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
        )

    def channel_allowed(self, channel_id: str) -> bool:
        """Empty allowlist means every channel the bot has been invited to."""
        return not self.allowed_channels or channel_id in self.allowed_channels

    def __repr__(self) -> str:  # keep tokens out of logs and tracebacks
        return (
            f"SlackSettings(socket_mode_token=***, bot_token=***, "
            f"allowed_channels={sorted(self.allowed_channels) or 'ALL'})"
        )


@dataclass
class AnthropicSettings:
    api_key: str

    @classmethod
    def from_env(cls) -> "AnthropicSettings":
        return cls(api_key=_require(
            "ANTHROPIC_API_KEY",
            "Export it, or run `ant auth login` and let the SDK resolve a profile.",
        ))

    def __repr__(self) -> str:
        return "AnthropicSettings(api_key=***)"
