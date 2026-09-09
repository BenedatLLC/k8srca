"""`k8srca sync` — apply k8srca.yaml to the Anthropic control plane.

Design 001 §6. Agents are created once and thereafter *updated*: every update
produces a new immutable version, and sessions pin their version at creation,
so in-flight investigations keep the configuration they started with while new
sessions get the change. Recreating agents would forfeit that and accumulate
orphans.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic

from . import tools as tools_mod
from .config import AgentConfig, Config
from .mcp_client import connect_all
from .state import AgentState, State


def git_rev() -> str | None:
    """Git SHA at sync time, recorded on each agent for provenance (003 §2.2)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except Exception:  # noqa: BLE001 - provenance is best effort
        return None


@dataclass
class PlannedAgent:
    key: str
    cfg: AgentConfig
    tools: list[dict]
    manifest: str
    skills: list[dict]


def builtin_toolset(enabled: list[str]) -> dict:
    """Opt-in toolset: default off, then enable exactly what the agent needs.

    web_search / web_fetch are never enabled — cluster state is the source of
    truth, and those run on Anthropic's servers regardless of environment type,
    so disabling them per-tool is the only control that works (001 §8.3).
    """
    return {
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": False},
        "configs": [{"name": name, "enabled": True} for name in enabled],
    }


async def plan(cfg: Config) -> dict[str, PlannedAgent]:
    """Resolve every agent's tool surface against the live MCP servers.

    Runs before any control-plane write, so a bad group name or an illegal
    schema fails before half the agents have been updated.
    """
    async with connect_all([s.for_host() for s in cfg.mcp]) as servers:
        per_server = {s.spec.name: tools_mod.declarations(s.tools, s.spec.prefix) for s in servers}
    tools_mod.check_collisions(per_server)

    planned: dict[str, PlannedAgent] = {}
    for key in cfg.sync_order():
        agent = cfg.agents[key]
        mcp_decls: list[dict] = []
        for server_name, group_name in agent.mcp_tools.items():
            spec = cfg.server(server_name)
            mcp_decls += tools_mod.select(
                per_server[server_name], spec.group(group_name), spec.prefix
            )
        planned[key] = PlannedAgent(
            key=key,
            cfg=agent,
            tools=[builtin_toolset(agent.builtin_tools), *mcp_decls],
            manifest=tools_mod.manifest_hash(mcp_decls),
            skills=resolve_skills(agent),
        )
    return planned


def resolve_skills(agent: AgentConfig) -> list[dict]:
    """Skill references for the agent.

    Uploading skill bundles is Phase 2 (001 §13); until the directories exist
    there is nothing to attach. Missing skills are a warning, not an error, so
    the pipeline can be proven before the knowledge base is built.
    """
    return []  # populated in Phase 2


def missing_skills(cfg: Config) -> list[Path]:
    return [p for a in cfg.agents.values() for p in a.skills if not p.exists()]


def read_system_prompt(agent: AgentConfig) -> str | None:
    """The agent's system prompt, or None if it is still a placeholder.

    Placeholder files hold only an HTML comment. Sending that as a system
    prompt would be worse than sending nothing, so strip comments and treat a
    file with no remaining content as absent.
    """
    import re

    if not agent.system_prompt.exists():
        return None
    text = re.sub(r"<!--.*?-->", "", agent.system_prompt.read_text(), flags=re.DOTALL).strip()
    return text or None


def ensure_environment(client: Anthropic, cfg: Config, state: State, log) -> str:
    if state.environment_id:
        try:
            env = client.beta.environments.retrieve(state.environment_id)
            log(f"environment  {env.id} (existing)")
            return env.id
        except Exception as exc:  # noqa: BLE001
            log(f"environment  {state.environment_id} unusable ({exc}); creating a new one")
    env = client.beta.environments.create(
        name="k8srca", config={"type": cfg.environment.type}
    )
    log(f"environment  {env.id} (created, {cfg.environment.type})")
    return env.id


def agent_payload(p: PlannedAgent, roster: list, rev: str | None) -> dict:
    body: dict = {
        "name": p.key,
        "model": p.cfg.model.to_api(),
        "tools": p.tools,
        "metadata": {"manifest": p.manifest, **({"config_rev": rev} if rev else {})},
    }
    body["description"] = p.cfg.description  # None clears it
    system = read_system_prompt(p.cfg)
    if system:
        body["system"] = system
    if p.skills:
        body["skills"] = p.skills
    if roster:
        body["multiagent"] = {"type": "coordinator", "agents": roster}
    return body


def ensure_agent(client: Anthropic, p: PlannedAgent, roster: list, state: State,
                 rev: str | None, log) -> AgentState:
    body = agent_payload(p, roster, rev)
    known = state.agents.get(p.key)

    if known:
        try:
            current = client.beta.agents.retrieve(known.id)
        except Exception:  # noqa: BLE001 - recorded id no longer resolves
            current = None
        if current is not None:
            # Optimistic concurrency: pass the version we believe is current so a
            # concurrent update is a 409 rather than a silent overwrite (001 §6).
            agent = client.beta.agents.update(known.id, version=current.version, **body)
            changed = "unchanged" if agent.version == current.version else f"v{current.version} -> v{agent.version}"
            log(f"agent        {p.key:20} {agent.id}  {changed}")
            return AgentState(id=agent.id, version=agent.version, manifest=p.manifest,
                              model=p.cfg.model.id)

    agent = client.beta.agents.create(**body)
    log(f"agent        {p.key:20} {agent.id}  created v{agent.version}")
    return AgentState(id=agent.id, version=agent.version, manifest=p.manifest,
                      model=p.cfg.model.id)


def resolve_roster(cfg: Config, key: str, state: State) -> list:
    """Map config roster entries to API roster entries.

    Specialists are synced first so their IDs exist by the time the coordinator
    references them. Entries pin the specialist's version at coordinator-save
    time, which is why updating a specialist re-saves the coordinator.
    """
    roster: list = []
    for member in cfg.agents[key].roster:
        if member == "self":
            roster.append({"type": "self"})
            continue
        resolved = state.agents.get(member)
        if resolved is None:
            raise RuntimeError(
                f"agent {key!r} rosters {member!r}, which has not been synced yet; "
                "this indicates a sync-order bug"
            )
        roster.append({"type": "agent", "id": resolved.id, "version": resolved.version})
    return roster
