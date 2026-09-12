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


class SshTunnelConfig(BaseModel):
    """The only genuinely site-specific fact about cluster access.

    Everything else -- gateway, subnet, TLS server name, uid -- is derived at
    run time. Because these two values *are* site-specific, they belong in the
    environment rather than in the version-controlled config: a bastion address
    and an internal API-server address are infrastructure topology, and
    committing them makes k8srca.yaml non-portable in exactly the way the rest
    of it is portable.
    """

    host: str                     # ssh target
    remote_endpoint: str          # host:port of the API server, as the ssh host sees it
    port: int = 6443              # local port to bind on the docker gateway


class ClusterAccess(BaseModel):
    """How the k8stools *container* reaches the API server.

    `auto` inspects the kubeconfig: a loopback server cannot be reached from a
    container, so a tunnel is required; anything else is used directly.
    """

    mode: Literal["auto", "direct", "ssh_tunnel"] = "auto"
    kubeconfig: Path | None = None
    container_kubeconfig: Path | None = None
    # Prefer the environment (see ssh_settings). Kept here for deployments that
    # have no reason to hide it.
    ssh: SshTunnelConfig | None = None

    def ssh_settings(self) -> SshTunnelConfig | None:
        """Tunnel details from the environment, falling back to YAML.

        Environment wins per field, so a checked-in default can be overridden
        for one machine without editing the file.
        """
        import os

        host = os.environ.get("K8SRCA_SSH_HOST") or (self.ssh.host if self.ssh else None)
        remote = (os.environ.get("K8SRCA_SSH_REMOTE")
                  or (self.ssh.remote_endpoint if self.ssh else None))
        port = os.environ.get("K8SRCA_SSH_PORT") or (self.ssh.port if self.ssh else 6443)
        if not host or not remote:
            return None
        return SshTunnelConfig(host=host, remote_endpoint=remote, port=int(port))


class ArchSource(BaseModel):
    """One contributor to the cluster-architecture skill.

    Three kinds, answering different questions: `live_cluster` how the system
    works today, `chart_repo` what it is declared to be, `docs` why and what to
    do about it. They are merged with provenance rather than collapsed, so
    disagreement between them stays visible.
    """

    type: Literal["live_cluster", "chart_repo", "docs", "change_history"]
    enabled: bool = True
    # live_cluster
    server: str | None = None
    namespaces: list[str] = Field(default_factory=lambda: ["default"])
    # chart_repo / docs
    path: Path | None = None
    url: str | None = None


class ArchitectureConfig(BaseModel):
    sources: list[ArchSource] = Field(default_factory=list)

    def active(self) -> list[ArchSource]:
        return [s for s in self.sources if s.enabled]


class EnvironmentConfig(BaseModel):
    type: Literal["self_hosted", "cloud"] = "self_hosted"


class SandboxConfig(BaseModel):
    # A repository, NOT a full reference. The tag is derived from the commit
    # and the working tree at run time (see k8srca.sandbox), because a fixed
    # tag can be rebuilt underneath a running session and stops pinning
    # anything.
    image: str
    network: str = "k8srca-net"
    memory: str = "2g"
    cpus: str = "2"
    workspaces: Path = Path("/srv/k8srca/workspaces")

    @model_validator(mode="after")
    def _image_is_a_repository(self) -> "SandboxConfig":
        # A tag here would be ignored, which is worse than rejecting it: the
        # operator would believe they had pinned something.
        name = self.image.rsplit("/", 1)[-1]
        if ":" in name:
            raise ValueError(
                f"sandbox.image must be a repository without a tag (got {self.image!r}). "
                "The tag is derived from the commit; build with `k8srca sandbox build`."
            )
        return self


class SessionConfig(BaseModel):
    budget_usd: float = 5.00
    idle_ttl_minutes: int = 120


class Config(BaseModel):
    mcp: list[McpServer]
    cluster_access: ClusterAccess = Field(default_factory=ClusterAccess)
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)
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
