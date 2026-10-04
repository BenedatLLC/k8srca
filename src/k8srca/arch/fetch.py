"""Pinned external sources for the cluster-architecture generator.

The `chart_repo` and `docs` sources should describe the software as its
publishers declared and documented it, not as we transcribed it. So they can
point at the official artifact, pinned to the version that was deployed:

    - type: chart_repo
      helm: {repo: https://open-telemetry.github.io/opentelemetry-helm-charts,
             chart: opentelemetry-demo, version: 0.40.7}
    - type: docs
      git: {repo: https://github.com/open-telemetry/opentelemetry.io,
            ref: <commit sha>, path: content/en/docs/demo/services}

`resolve()` fetches each into a local cache once (`.k8srca/sources/`) and
returns the source re-pointed at the cached directory, so the collectors read
plain files exactly as they do for a local `path`. The cache key includes the
version or commit, so a pinned source never changes underneath a build, and a
second build reads the cache without touching the network.

**`helm template` is the one helm command k8srca runs** (CLAUDE.md). It
renders a chart to manifests offline: no `--validate`, no cluster flags, and
`KUBECONFIG` pointed at `/dev/null`, so there is no credential for it to
reach a cluster with even by mistake. `tests/test_no_direct_cluster_access.py`
allows `helm` here and nowhere else, and only as `helm template`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import yaml

from ..config import ArchSource

CACHE = Path(".k8srca/sources")


class FetchError(RuntimeError):
    pass


def resolve(source: ArchSource, cache: Path = CACHE) -> ArchSource:
    """The source, re-pointed at a local copy of its pinned artifact."""
    if source.helm is not None:
        return source.model_copy(update={"path": fetch_chart(source, cache)})
    if source.git is not None:
        return source.model_copy(update={"path": fetch_git(source, cache)})
    return source


# ---------------------------------------------------------------------------
# Helm charts
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - pinned https sources
        dest.write_bytes(r.read())


def _chart_url(repo: str, chart: str, version: str) -> str:
    with urllib.request.urlopen(f"{repo.rstrip('/')}/index.yaml", timeout=60) as r:  # noqa: S310
        index = yaml.safe_load(r.read())
    for entry in (index.get("entries") or {}).get(chart) or []:
        if entry.get("version") == version:
            url = entry["urls"][0]
            return url if "://" in url else f"{repo.rstrip('/')}/{url}"
    raise FetchError(f"chart {chart} {version} not found in {repo}")


def render_chart(chart_archive: Path, release: str, namespace: str,
                 values: Path | None = None) -> str:
    """`helm template`, offline and without any cluster credential."""
    if shutil.which("helm") is None:
        raise FetchError("the helm binary is not installed; it renders the pinned chart "
                         "(https://helm.sh/docs/intro/install/)")
    argv = ["helm", "template", release, str(chart_archive), "--namespace", namespace]
    if values is not None:
        argv += ["--values", str(values)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("KUBE")}
    env["KUBECONFIG"] = os.devnull
    r = subprocess.run(argv, capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        raise FetchError(f"helm template failed: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def fetch_chart(source: ArchSource, cache: Path = CACHE) -> Path:
    spec = source.helm
    namespace = source.namespaces[0] if source.namespaces else "default"
    dest = cache / "charts" / f"{spec.chart}-{spec.version}-{spec.release}-{namespace}"
    # Named for the chart: chart facts take the file name as their origin, so
    # this is what the agent sees as the source of a declared value.
    rendered = dest / f"{spec.chart}-{spec.version}.yaml"
    if rendered.exists():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{spec.chart}-{spec.version}.tgz"
        _download(_chart_url(spec.repo, spec.chart, spec.version), archive)
        with tarfile.open(archive) as t:          # a valid archive, not a stray page
            t.getmembers()
        text = render_chart(archive, spec.release, namespace,
                            Path(spec.values) if spec.values else None)
    header = (f"# Rendered by k8srca from {spec.repo} {spec.chart} {spec.version}\n"
              f"# release={spec.release} namespace={namespace} "
              f"values={spec.values or 'chart defaults'}\n")
    rendered.write_text(header + text)
    return dest


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> None:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise FetchError(f"git {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[:400]}")


def fetch_git(source: ArchSource, cache: Path = CACHE) -> Path:
    """A sparse, shallow checkout of one path at one pinned commit."""
    spec = source.git
    name = spec.repo.rstrip("/").split("/")[-1].removesuffix(".git")
    root = cache / "git" / f"{name}@{spec.ref}"
    target = root / spec.path
    if target.exists():
        return target
    if root.exists():
        shutil.rmtree(root)                        # a half-finished earlier fetch
    root.mkdir(parents=True)
    _git("init", "--quiet", cwd=root)
    _git("remote", "add", "origin", spec.repo, cwd=root)
    _git("sparse-checkout", "set", "--no-cone", spec.path, cwd=root)
    _git("fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", spec.ref, cwd=root)
    _git("checkout", "--quiet", "FETCH_HEAD", cwd=root)
    if not target.exists():
        raise FetchError(f"{spec.path} does not exist in {spec.repo} at {spec.ref}")
    return target
