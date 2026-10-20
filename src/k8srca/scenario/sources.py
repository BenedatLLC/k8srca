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
        # As docker/Dockerfile.k8stools serves production: every tool, with
        # k8srca.yaml choosing per agent. (Needs k8stools >= 3.0.0.)
        "--toolset", "all",
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


# --- metrics (design 009 §5) -----------------------------------------------------
#
# A metrics capture is promtools' OpenMetrics export of an allowlist, taken at
# the k8stools capture's moment. Replayed, it is a real Prometheus with nothing
# to scrape, serving one block built from the capture, behind promtools -- the
# same server production would run -- aliased `promtools` on the scenario
# network, so the sandbox reaches it as k8srca.yaml's `metrics.server.url` says.
#
# The samples are shifted so the capture ends when the replay starts. That is
# the rebasing k8stools applies to every age and log timestamp, so the two
# halves of the world share one clock: an event's age and a restart series'
# last sample say the same "now".

#: Pinned with the rest of the scenario machinery: a new Prometheus can change
#: what a query returns, as a new k8stools can.
PROMETHEUS_IMAGE = "quay.io/prometheus/prometheus:v3.9.0"
PROMETHEUS_CONTAINER = "k8srca-scenario-prometheus"
PROMTOOLS_CONTAINER = "k8srca-scenario-promtools"
#: The alias the sandbox resolves; must match the host in metrics.server.url.
PROMTOOLS_ALIAS = "promtools"
PROMTOOLS_REPOSITORY = "k8srca/promtools"
PROMTOOLS_SRC = Path("packages/promtools")
#: One block for the whole capture. promtool's default of 2h repeats the index
#: in every block: 84 times in a week (009 §7.1).
MAX_BLOCK = "168h"


@dataclass
class MetricsReplay:
    container: str
    #: promtools, reachable from the host for the agent's tool declarations.
    host_url: str
    #: Seconds added to every captured timestamp.
    shift: float


#: Prometheus' own retention edge, which promtools reads as "oldest".
LOWEST = "prometheus_tsdb_lowest_timestamp_seconds"
#: Families whose *value* is a time, in unix seconds. Shifting only sample
#: times would leave "last terminated at" a year before "now".
TIME_VALUED = frozenset({
    "kube_pod_container_status_last_terminated_timestamp",
    "kube_pod_container_state_started",
    "container_start_time_seconds",
    LOWEST,
})


def shift_openmetrics(src: Path, dest: Path, offset: float, *,
                      start: float | None = None) -> int:
    """Copy a (gzipped) OpenMetrics export with `offset` seconds added to every
    sample's timestamp, and to the value of each TIME_VALUED family. Returns
    the number of samples.

    `start` is where the capture begins. The live Prometheus may retain more
    than was captured, and the replay holds only the capture, so its lowest
    retained timestamp is raised to `start`: otherwise promtools reads a week
    with no data as a week with no restarts (009 §6).

    The timestamp is a sample line's last field. Label values may hold spaces,
    but they come before it, so splitting from the right is exact."""
    import gzip

    opener = gzip.open if src.suffix == ".gz" else open
    n = 0
    with opener(src, "rt") as f, dest.open("w") as out:
        for line in f:
            if line.startswith("#") or not line.strip():
                out.write(line)
                continue
            body, value, ts = line.rstrip("\n").rsplit(" ", 2)
            name = body.split("{", 1)[0]
            if name in TIME_VALUED:
                v = float(value)
                if name == LOWEST and start is not None:
                    v = max(v, start)
                value = f"{v + offset:.3f}"
            out.write(f"{body} {value} {float(ts) + offset:.3f}\n")
            n += 1
    return n


def replay_retention(capture: Path) -> str:
    """The captured Prometheus' time retention ("2w or 15GiB" -> "2w"), or ten
    years when the manifest does not say."""
    import re

    said = str(capture_manifest(capture).get("retention") or "").split(" or ")[0].strip()
    return said if re.fullmatch(r"(\d+[smhdwy])+", said) else "3650d"


def capture_manifest(capture: Path) -> dict:
    """What `promtools capture` wrote beside the samples: window, intervals."""
    path = capture.parent / "manifest.json"
    import json

    return json.loads(path.read_text()) if path.exists() else {}


def metrics_digest(path: Path) -> str:
    """What truth.yaml pins for a metrics capture (004 §6.3)."""
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def promtools_image(root: Path = Path(".")) -> str:
    """promtools' image, tagged by its sources, built if it is missing."""
    import hashlib

    h = hashlib.sha256()
    base = root / PROMTOOLS_SRC
    for f in sorted(p for p in base.rglob("*") if p.is_file()):
        if "__pycache__" in f.parts or "tests" in f.relative_to(base).parts:
            continue
        h.update(str(f.relative_to(base)).encode())
        h.update(f.read_bytes())
    ref = f"{PROMTOOLS_REPOSITORY}:{h.hexdigest()[:12]}"
    if _run("docker", "image", "inspect", ref).returncode != 0:
        r = _run("docker", "build", "-f", "docker/Dockerfile.promtools", "-t", ref, str(root),
                 timeout=900)
        if r.returncode != 0:
            raise SourceError(f"could not build {ref}: {(r.stderr or r.stdout).strip()[-400:]}")
    return ref


def build_block(capture: Path, workdir: Path, end: float, now: float | None = None,
                image: str = PROMETHEUS_IMAGE) -> float:
    """Shift `capture` to end at `now` and build it into a TSDB under
    workdir/data. Returns the shift. promtool runs in the Prometheus image, as
    the invoking user, so the block is the host's to delete."""
    import os

    offset = (time.time() if now is None else now) - end
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "data").mkdir(exist_ok=True)
    window = capture_manifest(capture).get("window_seconds")
    shift_openmetrics(capture, workdir / "capture.om", offset,
                      start=end - window if window else None)
    r = _run("docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
             "-v", f"{workdir.resolve()}:/work", "--entrypoint", "promtool", image,
             "tsdb", "create-blocks-from", "openmetrics",
             f"--max-block-duration={MAX_BLOCK}", "/work/capture.om", "/work/data",
             timeout=600)
    if r.returncode != 0:
        raise SourceError(f"promtool could not build the block: "
                          f"{(r.stderr or r.stdout).strip()[-400:]}")
    (workdir / "capture.om").unlink()
    return offset


@contextmanager
def metrics_replay(capture: Path, end: float, *, host_port: int = 8011,
                   network: str = NETWORK, image: str = PROMETHEUS_IMAGE,
                   promtools: str | None = None):
    """Serve a metrics capture as Prometheus, behind promtools, on `network`.

    Nothing is scraped: the configuration has no scrape jobs, so the database
    holds the capture and only the capture. A query for a series off the
    allowlist returns nothing, as it would have at capture time (009 §5).
    """
    import os
    import shutil
    import tempfile

    capture = Path(capture).resolve()
    if not capture.exists():
        raise SourceError(f"metrics capture not found: {capture}")
    promtools = promtools or promtools_image()
    ensure_network(network)
    remove(PROMETHEUS_CONTAINER)
    remove(PROMTOOLS_CONTAINER)
    work = Path(tempfile.mkdtemp(prefix="k8srca-metrics-"))
    try:
        offset = build_block(capture, work, end, image=image)
        (work / "prometheus.yml").write_text("global: {}\n")
        r = _run("docker", "run", "-d", "--name", PROMETHEUS_CONTAINER, "--network", network,
                 "--user", f"{os.getuid()}:{os.getgid()}",
                 "-v", f"{work / 'data'}:/prometheus",
                 "-v", f"{work / 'prometheus.yml'}:/etc/prometheus/prometheus.yml:ro",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", image,
                 "--config.file=/etc/prometheus/prometheus.yml",
                 "--storage.tsdb.path=/prometheus",
                 # The live retention, so `runtime_info` says what the site
                 # keeps rather than the replay's own setting. It is measured
                 # back from the newest block, which ends now, so the capture
                 # stays inside it.
                 f"--storage.tsdb.retention.time={replay_retention(capture)}")
        if r.returncode != 0:
            raise SourceError(f"could not start Prometheus: {(r.stderr or r.stdout).strip()[:300]}")
        _await_log(PROMETHEUS_CONTAINER, ("Server is ready to receive web requests",))
        r = _run("docker", "run", "-d", "--name", PROMTOOLS_CONTAINER,
                 "--network", network, "--network-alias", PROMTOOLS_ALIAS,
                 "-p", f"127.0.0.1:{host_port}:8000",
                 "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                 promtools, "--url", f"http://{PROMETHEUS_CONTAINER}:9090",
                 # The replay scrapes nothing, so it has no targets to read
                 # intervals from: they come from the capture's manifest.
                 *[a for job, iv in capture_manifest(capture).get("scrape_intervals") or []
                   if job and iv for a in ("--scrape-interval", f"{job}={iv}")])
        if r.returncode != 0:
            raise SourceError(f"could not start promtools: {(r.stderr or r.stdout).strip()[:300]}")
        _await_log(PROMTOOLS_CONTAINER, ("Application startup complete", "Uvicorn running"))
        yield MetricsReplay(container=PROMTOOLS_CONTAINER,
                            host_url=f"http://127.0.0.1:{host_port}/mcp", shift=offset)
    finally:
        remove(PROMTOOLS_CONTAINER)
        remove(PROMETHEUS_CONTAINER)
        shutil.rmtree(work, ignore_errors=True)


def _await_log(container: str, ready: tuple[str, ...], timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _run("docker", "inspect", "-f", "{{.State.Running}}", container)
        logs = _run("docker", "logs", container)
        blob = (logs.stdout or "") + (logs.stderr or "")
        if any(s in blob for s in ready):
            return
        if state.stdout.strip() != "true":
            raise SourceError(f"{container} exited during startup:\n{blob.strip()[-500:]}")
        time.sleep(0.3)
    raise SourceError(f"{container} did not become ready within {timeout_s:.0f}s")
