"""Identifying the sandbox image by what is actually in it.

A moving tag does not pin. Containers are per work item, so a session that
spans a rebuild gets the new code mid-investigation even though the poller
recorded "the image" at session start (003 §3.2) -- the tag was recorded, and
the tag moved underneath it.

The tag is therefore derived from the commit, and from the working tree when it
differs. Two refinements matter:

* **Only the files that go into the image count.** Editing `docs/` or a design
  document does not change the sandbox, so it must not invalidate its tag or
  every documentation commit forces a rebuild.
* **A dirty tree gets a content hash, not just `-dirty`.** Two different
  uncommitted states would otherwise share a tag, which is the same failure as
  a moving tag with extra steps.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

# Everything the Dockerfile COPYs. Keep in step with docker/Dockerfile.sandbox;
# tests/test_sandbox_image.py checks that they agree.
IMAGE_INPUTS = ("pyproject.toml", "README.md", "LICENSE", "src", "k8srca.yaml")


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60, cwd=cwd)


def commit() -> str | None:
    r = _run("git", "rev-parse", "--short=12", "HEAD")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def dirty_inputs(root: Path = Path(".")) -> list[str]:
    """Image inputs that differ from HEAD, including untracked files."""
    r = _run("git", "status", "--porcelain", "--", *IMAGE_INPUTS, cwd=root)
    if r.returncode != 0:
        return []
    return [line[3:].strip() for line in r.stdout.splitlines() if line.strip()]


def content_digest(root: Path = Path(".")) -> str:
    """Hash of the image inputs as they are on disk right now."""
    h = hashlib.sha256()
    for entry in sorted(IMAGE_INPUTS):
        path = root / entry
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        for f in files:
            if "__pycache__" in f.parts or not f.exists():
                continue
            h.update(str(f.relative_to(root)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:8]


def image_tag(root: Path = Path(".")) -> str:
    """The tag identifying an image built from the tree as it stands."""
    sha = commit()
    if sha is None:
        # Not a git checkout: fall back to content alone rather than inventing
        # a commit that does not exist.
        return f"tree.{content_digest(root)}"
    if dirty_inputs(root):
        return f"{sha}-dirty.{content_digest(root)}"
    return sha


def image_ref(repository: str, root: Path = Path(".")) -> str:
    return f"{repository}:{image_tag(root)}"


def image_exists(ref: str) -> bool:
    return _run("docker", "image", "inspect", ref).returncode == 0


def build(ref: str, dockerfile: str = "docker/Dockerfile.sandbox",
          context: str = ".") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "build", "-f", dockerfile, "-t", ref, context],
        capture_output=True, text=True, timeout=900,
    )

