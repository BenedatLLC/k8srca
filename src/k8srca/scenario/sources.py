"""Standing up a scenario's data sources (design 004 §3).

The whole substitution is a container argument. k8stools replays a capture with
`--state-file`, and the sandbox reaches it by *name*: `http://k8stools:8000/mcp`
is baked into k8srca.yaml and therefore into the sandbox image. So the replay
runs on its own network under the network alias `k8stools`, and nothing above
the MCP boundary can tell it apart from the live server -- the same single seam
that makes the read-only guarantee checkable in one place (CLAUDE.md).

Nothing here mounts a kubeconfig. A replay answers from a JSON file, so the
credential that the production container exists to hold has no counterpart.
"""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

#: Where the capture is mounted inside the replay container.
STATE_MOUNT = "/state/capture.json"

CONTAINER = "k8srca-scenario-k8stools"
NETWORK = "k8srca-scenario-net"
#: The alias the sandbox resolves. Must match the host in k8srca.yaml's mcp url.
ALIAS = "k8stools"


class SourceError(RuntimeError):
    pass


def _run(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


@dataclass
class Replay:
    """A running replay server."""

    network: str
    container: str
    #: Reachable from the *host*, for the runner's own checks.
    host_url: str


def ensure_network(name: str = NETWORK) -> None:
    if _run("docker", "network", "inspect", name).returncode != 0:
        r = _run("docker", "network", "create", name)
        if r.returncode != 0:
            raise SourceError(f"could not create network {name}: {r.stderr.strip()}")


def remove(container: str = CONTAINER) -> None:
    _run("docker", "rm", "-f", container)


@contextmanager
def replay(capture: Path, image: str, *, clock: str = "frozen",
           host_port: int = 8010, network: str = NETWORK, container: str = CONTAINER):
    """Serve `capture` as the cluster, on `network`, aliased as `k8stools`.

    `host_port` is published on loopback only, so the runner can verify what the
    agent will see without joining the network itself. It deliberately is not
    8000: that is the live container, and a scenario that silently talked to the
    real cluster would produce results that look fine and mean nothing.
    """
    capture = Path(capture).resolve()
    if not capture.exists():
        raise SourceError(f"capture not found: {capture}")
    if clock not in ("frozen", "advancing"):
        raise SourceError(f"unknown clock {clock!r}")

    ensure_network(network)
    remove(container)                      # a leftover from a crashed run
    r = _run(
        "docker", "run", "-d", "--name", container,
        "--network", network, "--network-alias", ALIAS,
        "-p", f"127.0.0.1:{host_port}:8000",
        "-v", f"{capture}:{STATE_MOUNT}:ro",
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--entrypoint", "k8s-mcp-server", image,
        "--transport=streamable-http", "--host", "0.0.0.0", "--port", "8000",
        "--state-file", STATE_MOUNT, "--state-time", clock,
    )
    if r.returncode != 0:
        raise SourceError(f"could not start replay: {(r.stderr or r.stdout).strip()[:300]}")

    try:
        _await_ready(container)
        yield Replay(network=network, container=container,
                     host_url=f"http://127.0.0.1:{host_port}/mcp")
    finally:
        remove(container)


def _await_ready(container: str, timeout_s: float = 30.0) -> None:
    """Wait for the server to log that it is serving the capture.

    Polling the port is not enough: the process binds before it has parsed the
    state file, and a malformed capture exits non-zero *after* the port is up.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _run("docker", "inspect", "-f", "{{.State.Running}}", container)
        logs = _run("docker", "logs", container)
        blob = (logs.stdout or "") + (logs.stderr or "")
        if "Application startup complete" in blob or "Uvicorn running" in blob:
            return
        if state.stdout.strip() != "true":
            raise SourceError(f"replay exited during startup:\n{blob.strip()[-500:]}")
        time.sleep(0.3)
    raise SourceError(f"replay did not become ready within {timeout_s:.0f}s")
