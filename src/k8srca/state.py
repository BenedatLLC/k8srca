"""Resolved control-plane IDs, written by `sync` and read by everything else.

Gitignored: these are per-deployment, not per-repo. Design 001 §6.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(".k8srca/state.json")


@dataclass
class AgentState:
    id: str
    version: int
    manifest: str          # tool-manifest hash (001 §4.3)
    model: str


@dataclass
class SkillState:
    skill_id: str
    version: str
    digest: str      # content hash, so unchanged bundles are not re-uploaded


@dataclass
class State:
    environment_id: str | None = None
    agents: dict[str, AgentState] = field(default_factory=dict)
    skills: dict[str, SkillState] = field(default_factory=dict)
    config_rev: str | None = None   # git SHA at sync time (003 §2.2)

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(
            environment_id=raw.get("environment_id"),
            agents={k: AgentState(**v) for k, v in (raw.get("agents") or {}).items()},
            skills={k: SkillState(**v) for k, v in (raw.get("skills") or {}).items()},
            config_rev=raw.get("config_rev"),
        )

    def save(self, path: Path = DEFAULT_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"environment_id": self.environment_id,
             "agents": {k: asdict(v) for k, v in self.agents.items()},
             "skills": {k: asdict(v) for k, v in self.skills.items()},
             "config_rev": self.config_rev},
            indent=2,
        ) + "\n")
