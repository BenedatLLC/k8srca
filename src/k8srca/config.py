"""Schema for k8srca.yaml — the single declarative config (design 001 §6).

Per-agent model and tool routing is the point: cost and context window are
traded off per role without touching code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class McpServer(BaseModel):
    name: str
    # Address as seen from the *sandbox* (on the docker network). This is what
    # the worker connects to.
    url: str
    # Address as seen from the *host*, where `k8srca sync` runs. The same
    # server, reached differently: the sandbox resolves `k8stools` on
    # k8srca-net, the host reaches a published port on loopback. Defaults to
    # `url` for deployments where they coincide. Overridable per run with
    # K8SRCA_MCP_<NAME>_URL (name upper-cased, non-alphanumerics -> _).
    sync_url: str | None = None
    wrap: Literal["worker_custom_tools"] = "worker_custom_tools"
    prefix: str = ""
    timeout_s: int = 60
    # Named tool groups referenced by agents. A group is a routing label, not a
    # capability grant: the worker registers the union regardless (001 §4.2).
    groups: dict[str, list[str] | Literal["*"]] = Field(default_factory=dict)

    def group(self, name: str) -> list[str] | str:
        if name not in self.groups:
            raise KeyError(f"MCP server {self.name!r} has no tool group {name!r}")
        return self.groups[name]

    def host_url(self) -> str:
        """The URL to use from the host (sync, tests, CLI inspection)."""
        import os
        import re

        env_key = "K8SRCA_MCP_" + re.sub(r"[^A-Z0-9]", "_", self.name.upper()) + "_URL"
        return os.environ.get(env_key) or self.sync_url or self.url

    def for_host(self) -> "McpServer":
        """A copy addressed for host-side use."""
        return self.model_copy(update={"url": self.host_url()})


class ModelConfig(BaseModel):
    id: str
    # effort is agent configuration only — an effort inside a per-session model
    # override is silently ignored, so it must live here (001 §6).
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

    def to_api(self) -> dict | str:
        return {"id": self.id, **({"effort": self.effort} if self.effort else {})}


class AgentConfig(BaseModel):
    role: Literal["coordinator", "specialist"]
    # The coordinator chooses whom to delegate to from each roster entry's name
    # and description, so a specialist's description is functional, not
    # decorative: say what it is good at and what to hand it.
    description: str | None = None
    model: ModelConfig
    system_prompt: Path
    skills: list[Path] = Field(default_factory=list)
    builtin_tools: list[str] = Field(default_factory=list)
    mcp_tools: dict[str, str] = Field(default_factory=dict)  # server name -> group name
    roster: list[str] = Field(default_factory=list)          # agent keys, or "self"

    @model_validator(mode="after")
    def _roster_only_on_coordinator(self) -> "AgentConfig":
        if self.roster and self.role != "coordinator":
            raise ValueError("only a coordinator may declare a roster (one level of delegation)")
        return self


class EnvironmentConfig(BaseModel):
    type: Literal["self_hosted", "cloud"] = "self_hosted"


class SandboxConfig(BaseModel):
    image: str
    network: str = "k8srca-net"
    memory: str = "2g"
    cpus: str = "2"
    workspaces: Path = Path("/srv/k8srca/workspaces")


class SessionConfig(BaseModel):
    budget_usd: float = 5.00
    idle_ttl_minutes: int = 120


class Config(BaseModel):
    mcp: list[McpServer]
    agents: dict[str, AgentConfig]
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    sandbox: SandboxConfig
    session: SessionConfig = Field(default_factory=SessionConfig)

    @model_validator(mode="after")
    def _validate_references(self) -> "Config":
        servers = {s.name for s in self.mcp}
        for key, agent in self.agents.items():
            for server, group in agent.mcp_tools.items():
                if server not in servers:
                    raise ValueError(f"agent {key!r} references unknown MCP server {server!r}")
                self.server(server).group(group)  # raises if the group is undefined
            for member in agent.roster:
                if member == "self":
                    continue
                if member not in self.agents:
                    raise ValueError(f"agent {key!r} rosters unknown agent {member!r}")
                # One level of delegation, enforced by the API too — catching it
                # here gives a better message than a 400 at apply time (001 §6).
                if self.agents[member].roster:
                    raise ValueError(
                        f"agent {key!r} rosters {member!r}, which has its own roster; "
                        "only one level of delegation is allowed"
                    )
        coordinators = [k for k, a in self.agents.items() if a.role == "coordinator"]
        if len(coordinators) != 1:
            raise ValueError(f"expected exactly one coordinator agent, found {coordinators}")
        return self

    def server(self, name: str) -> McpServer:
        for s in self.mcp:
            if s.name == name:
                return s
        raise KeyError(f"no MCP server named {name!r}")

    @property
    def coordinator(self) -> str:
        return next(k for k, a in self.agents.items() if a.role == "coordinator")

    def sync_order(self) -> list[str]:
        """Specialists first: the roster references them by ID (001 §6)."""
        return [k for k, a in self.agents.items() if a.role == "specialist"] + [self.coordinator]


def load(path: str | Path = "k8srca.yaml") -> Config:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    cfg = Config.model_validate(data)
    # Resolve prompt/skill paths relative to the config file.
    root = path.parent
    for agent in cfg.agents.values():
        agent.system_prompt = root / agent.system_prompt
        agent.skills = [root / s for s in agent.skills]
    return cfg
