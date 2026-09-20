"""Capturing a live cluster into a scenario directory (design 004 §4.1).

Scenarios are authored by breaking a real cluster and snapshotting it, not by
writing state by hand: real breakage produces evidence that is self-consistent
for free, and produces the things nobody would think to write.

The capture runs *inside* the k8stools container. That is not incidental -- it
is the container that holds the kubeconfig, and k8srca never touches the
Kubernetes API itself (CLAUDE.md). Running `k8s-capture-state` on the host would
be a second process holding cluster credentials, which is the whole thing the
rule exists to prevent.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

CONTAINER = "k8srca-k8stools"
#: Inside the container, /tmp is the only writable path (compose mounts it as a
#: tmpfs because read_only breaks the Kubernetes client otherwise).
REMOTE = "/tmp/k8srca-scenario-capture.json"


class RecordError(RuntimeError):
    pass


@dataclass
class Recorded:
    path: Path
    captured_at: str
    bytes_written: int
    pods: int
    containers: int
    redacted: bool


def _run(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def record(dest: Path, *, namespaces: list[str] | None = None,
           max_log_lines: int = 200, container: str = CONTAINER) -> Recorded:
    """Capture the live cluster to `dest`, via the k8stools container."""
    if _run("docker", "inspect", "-f", "{{.State.Running}}", container).stdout.strip() != "true":
        raise RecordError(
            f"{container} is not running; `uv run k8srca up` first -- the capture "
            f"has to come from the container that holds the kubeconfig"
        )

    cmd = ["docker", "exec", container, "k8s-capture-state",
           "--max-log-lines", str(max_log_lines), "-o", REMOTE]
    if namespaces:
        cmd += ["--namespace", *namespaces]
    r = _run(*cmd)
    if r.returncode != 0:
        raise RecordError(f"capture failed: {(r.stderr or r.stdout).strip()[-400:]}")

    # `docker cp` cannot read the container's /tmp: it is a tmpfs, and tmpfs
    # mounts are not part of the filesystem archive cp reads from. It fails with
    # "Could not find the file", which reads like the capture was never written.
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        stream = subprocess.run(["docker", "exec", container, "cat", REMOTE],
                                stdout=out, stderr=subprocess.PIPE, timeout=600)
    if stream.returncode != 0:
        raise RecordError(f"could not read capture out of {container}: "
                          f"{stream.stderr.decode()[-300:]}")
    _run("docker", "exec", container, "rm", "-f", REMOTE)

    try:
        blob = json.loads(dest.read_text())
    except ValueError as e:
        raise RecordError(f"capture is not valid JSON: {e}") from e

    pods = blob.get("pods") or []
    return Recorded(
        path=dest,
        captured_at=blob.get("captured_at", ""),
        bytes_written=dest.stat().st_size,
        pods=len(pods),
        containers=sum(len(p.get("container_statuses") or []) for p in pods),
        redacted=bool(blob.get("redacted")),
    )


def log_health(capture: dict) -> dict[str, int]:
    """Count log defects a capture should not have (k8stools#6).

    A capture is committed and then trusted for a long time, so it is worth one
    look at whether its logs are usable before it becomes a fixture. `repr_blobs`
    must be zero. `identical` is *not* a defect on its own: while a container
    sits in CrashLoopBackOff there is no running instance, so the kubelet serves
    the same most-recently-terminated one for both current and previous.
    """
    counts = {"containers": 0, "repr_blobs": 0, "identical": 0, "both_empty": 0}
    for pod in capture.get("pods") or []:
        logs = pod.get("logs") or {}
        previous = pod.get("previous_logs") or {}
        for name, current in logs.items():
            counts["containers"] += 1
            prior = previous.get(name, "")
            if current.startswith("b'") or prior.startswith("b'"):
                counts["repr_blobs"] += 1
            if not current and not prior:
                counts["both_empty"] += 1
            elif current == prior:
                counts["identical"] += 1
    return counts
