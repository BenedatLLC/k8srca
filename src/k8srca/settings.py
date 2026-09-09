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


# An empty file, so kubectl fails closed instead of falling back to a default.
NEUTRALISED_KUBECONFIG = "/dev/null"

# Credentials that must never be visible to a tool the agent can drive.
# The containerised sandbox has none of these by construction (001 §8.2); the
# in-process worker inherits the whole host environment, so it must scrub.
SCRUB = (
    "KUBECONFIG",               # cluster credential -- the one that matters
    "K8SRCA_KUBECONFIG",
    "ANTHROPIC_API_KEY",        # org-scoped; the worker needs only the environment key
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_ENVIRONMENT_KEY",
    "SLACK_BOT_USER_OAUTH_TOKEN",
    "SLACK_SOCKET_MODE_TOKEN",
    "SLACK_SIGNING_SECRET",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
)


def scrub_environment(keep: tuple[str, ...] = ()) -> list[str]:
    """Remove credentials from this process's environment.

    Bash tools run as subprocesses and inherit `os.environ`, so anything left
    here is readable by the agent. Call once, after the values you need have
    been read into settings objects and are held in memory.

    Returns the names actually removed, for logging.

    `KUBECONFIG` is set to /dev/null rather than merely unset: kubectl falls
    back to ~/.kube/config when the variable is absent, which on a developer
    workstation is usually a cluster-admin credential. Pointing it at an empty
    file makes kubectl fail closed instead.
    """
    removed = []
    for name in SCRUB:
        if name in keep:
            continue
        value = os.environ.pop(name, None)
        # A KUBECONFIG we neutralised on an earlier call is not a finding.
        if value is not None and not (name == "KUBECONFIG" and value == NEUTRALISED_KUBECONFIG):
            removed.append(name)
    if "KUBECONFIG" not in keep:
        os.environ["KUBECONFIG"] = NEUTRALISED_KUBECONFIG
    return removed
