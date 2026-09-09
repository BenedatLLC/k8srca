"""Host poller: claim work items, spawn one sandbox container per item.

Design 001 §3.2/§7.3. The poller holds the *environment* key and never the
org-scoped API key -- agent-authored bash runs in the containers this spawns,
and an API key on this host would be within its reach (001 §3.1).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic
from anthropic.lib.environments import iter_work

log = logging.getLogger("k8srca.poller")


@dataclass
class SpawnConfig:
    script: Path
    image: str
    network: str
    memory: str
    cpus: str
    workspaces: Path
    manifests: dict[str, str]


def session_image(cfg: SpawnConfig, workspace: Path) -> str:
    """Pin the image for a session's lifetime (design 003 §3.2).

    Containers are per turn, so a moving tag would swap code mid-conversation.
    The first turn records the tag; later turns of the same session reuse it.
    """
    marker = workspace / ".image"
    if marker.exists():
        return marker.read_text().strip()
    workspace.mkdir(parents=True, exist_ok=True)
    marker.write_text(cfg.image)
    return cfg.image


def spawn(work, cfg: SpawnConfig) -> int:
    session_id = work.data.id
    workspace = cfg.workspaces / session_id
    image = session_image(cfg, workspace)

    env = {
        **os.environ,
        "ANTHROPIC_SESSION_ID": session_id,
        "ANTHROPIC_WORK_ID": work.id,
        "ANTHROPIC_ENVIRONMENT_ID": work.environment_id,
        "ANTHROPIC_WORK_SECRET": getattr(work, "secret", "") or "",
        "K8SRCA_SANDBOX_IMAGE": image,
        "K8SRCA_NETWORK": cfg.network,
        "K8SRCA_SANDBOX_MEMORY": cfg.memory,
        "K8SRCA_SANDBOX_CPUS": cfg.cpus,
        "K8SRCA_WORKSPACES": str(cfg.workspaces),
        "K8SRCA_MANIFESTS": json.dumps(cfg.manifests),
    }
    log.info(json.dumps({"event": "spawn", "session": session_id, "work": work.id, "image": image}))
    proc = subprocess.run(
        [str(cfg.script)],
        env=env,
        input=work.model_dump_json() if hasattr(work, "model_dump_json") else "{}",
        text=True,
    )
    log.info(json.dumps({"event": "container_exit", "session": session_id,
                         "work": work.id, "code": proc.returncode}))
    return proc.returncode


def run(client: Anthropic, environment_id: str, cfg: SpawnConfig) -> None:
    """Claim work items forever, spawning a container for each."""
    log.info(json.dumps({"event": "poller_start", "environment": environment_id,
                         "image": cfg.image, "workspaces": str(cfg.workspaces)}))
    # auto_stop=False: the container's handle_item() force-stops its own item.
    for work in iter_work(client.beta.environments.work, environment_id=environment_id,
                          auto_stop=False, reclaim_older_than_ms=30_000):
        if getattr(work.data, "type", "session") != "session":
            log.info(json.dumps({"event": "skip_non_session", "work": work.id}))
            continue
        try:
            spawn(work, cfg)
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the poller
            log.exception(json.dumps({"event": "spawn_failed", "work": work.id, "error": str(exc)}))
