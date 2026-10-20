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
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
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


#: Built by `k8srca arch build`; gitignored, and rebuilt from the live cluster.
ARCH_SKILL = Path("skills/cluster-architecture")

#: How far apart the architecture build and the capture may be before the
#: scenario stops describing one moment. Minutes are inevitable -- they are
#: two sequential reads -- but a stale skill is a different cluster.
MAX_SKEW_S = 3600


def snapshot_skill(dest_dir: Path, source: Path = ARCH_SKILL) -> Path | None:
    """Copy the whole cluster-architecture skill into the scenario directory.

    Into the slot for its form: a wiki (generator format 2) beside the legacy
    snapshot rather than over it, so the scenario keeps both (model.ARCHES).

    The *whole* bundle, not just architecture.json: SKILL.md and topology.md are
    rendered from the same build, and the runner has to upload a complete skill
    for the agent to receive one.

    This skill is not supplementary context. 96% of its facts carry
    `source: observed` and come from a k8stools read of the live cluster, so it
    is a second observed snapshot of the same cluster. Pinned beside the capture
    it describes the same moment; left to `k8srca arch build`'s own schedule it
    describes a different one, and the agent cannot tell.
    """
    if not source.exists():
        return None
    from ..core.generator import read_manifest

    wiki = (read_manifest(source) or {}).get("format", 1) >= 2
    dest = dest_dir / "skill" / ("wiki" if wiki else "") / source.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def skew_seconds(skill_dir: Path, captured_at: str) -> float | None:
    """Seconds between the architecture build and the capture.

    Both are observed reads of one cluster, so the gap is how far apart the two
    halves of the scenario's world are. It is also the error in every relative
    age the skill states: `last_changed: 145d ago` is 145 days before the
    *build*, which the agent will read against a replay clock frozen at the
    capture.
    """
    arch = skill_dir / "architecture.json"
    if not arch.exists() or not captured_at:
        return None
    built = (json.loads(arch.read_text()) or {}).get("built_at")
    if not built:
        return None
    to_dt = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    return abs((to_dt(captured_at) - to_dt(built)).total_seconds())


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


#: Not production's, the scenario runner's or the eval's port, network or
#: container, so a scenario's wiki can be built while any of those run.
WIKI_PORT = 8012
WIKI_NETWORK = "k8srca-wiki-net"
WIKI_CONTAINER = "k8srca-wiki-k8stools"


def wiki_settings(sd, cfg, server_name: str):
    """The architecture settings a scenario's wiki is built with.

    The scenario's own sources if it lists them, k8srca.yaml's otherwise; the
    cluster sources always read `server_name`, the replay. Synthesis model,
    review and cost limit are k8srca.yaml's.
    """
    from ..config import ArchSource

    own = sd.scenario.architecture
    if own is not None and own.sources is not None:
        sources = [ArchSource(type=s.type, path=Path(s.path) if s.path else None,
                              helm=s.helm, git=s.git) for s in own.sources]
    else:
        sources = [s.model_copy() for s in cfg.architecture.active()]
    namespaces = own.namespaces if own is not None and own.namespaces else None
    for s in sources:
        if s.type in ("live_cluster", "change_history"):
            s.server = server_name
            if namespaces:
                s.namespaces = list(namespaces)
        elif namespaces and s.type == "chart_repo":
            s.namespaces = list(namespaces)
    return cfg.architecture.model_copy(update={"sources": sources})


def build_wiki(sd, cfg, image: str, *, model: str | None = None, synthesise: bool = True,
               max_usd: float | None = None, reports: Path):
    """Build the scenario's wiki from its own capture, into its wiki slot.

    The wiki holds no state, but its inventory and edges come from the cluster,
    so it is built against the capture -- replayed -- and never the live
    cluster, which by now is a different deployment. Through the same adapter,
    checks and swap as production, so what a scenario measures is what
    `k8srca arch build` ships.
    """
    import asyncio

    from kubewiki.sources import Server

    from ..arch.generator import ArchitectureGenerator
    from ..core.generator import run
    from .sources import replay

    # The wiki slot (ScenarioDir.skill_dir for "wiki"), named here rather than
    # read from a ScenarioDir: that needs truth.yaml, and a hold-out author
    # builds the wiki before writing the truth that pins it.
    target = sd.path / "skill" / "wiki" / "cluster-architecture"
    server = cfg.mcp[0]
    with replay(sd.capture_path, image, host_port=WIKI_PORT, network=WIKI_NETWORK,
                container=WIKI_CONTAINER) as rp:
        scfg = cfg.model_copy(update={"architecture": wiki_settings(sd, cfg, server.name)})
        generator = ArchitectureGenerator(
            model=model, synthesise=synthesise, max_usd=max_usd,
            servers=[Server(name=server.name, url=rp.host_url,
                            timeout_s=float(server.timeout_s))])
        return asyncio.run(run(generator, scfg, target, reports=reports))



#: The live Prometheus a metrics capture reads (009 §4): site topology, so it
#: lives in .env beside the API server's tunnel.
PROMETHEUS_URL_ENV = "K8SRCA_PROMETHEUS_URL"


@dataclass
class RecordedMetrics:
    path: Path
    end: str
    digest: str
    series: int
    samples: int


def record_metrics(dest_dir: Path, url: str, end: str, namespaces: list[str],
                   window: str = "7d") -> RecordedMetrics:
    """Capture the allowlist from Prometheus up to `end`, the k8stools
    capture's `captured_at`, into dest_dir/metrics (009 §5).

    Prometheus is not the Kubernetes API and holds no cluster credential, so
    this reads it from the host, through promtools' GET-only client."""
    from promtools import notes
    from promtools.capture import allowlist, capture
    from promtools.client import PromClient

    from .sources import metrics_digest

    if not namespaces:
        raise RecordError("a metrics capture needs --namespace: the allowlist is "
                          "per namespace, and the whole database is not a scenario")
    when = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
    got = capture(PromClient(url), dest_dir / "metrics", end=when,
                  window=notes.duration(window), selectors=allowlist(namespaces))
    return RecordedMetrics(path=got.path, end=end, digest=metrics_digest(got.path),
                           series=got.series, samples=got.samples)
