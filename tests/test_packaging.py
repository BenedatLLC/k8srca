"""Packaging checks the editable dev install cannot make.

`uv sync` installs k8srca editable, which never builds a wheel -- so a
pyproject change can break `pip install .` and every local check still passes.
It surfaces only when something builds the image, which may be days later.
"""

import tomllib
from pathlib import Path

PYPROJECT = Path("pyproject.toml")


def config() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def wheel_config() -> dict:
    return (config().get("tool", {}).get("hatch", {})
            .get("build", {}).get("targets", {}).get("wheel", {}))


def test_force_include_does_not_overlap_packages():
    """A force-include under a packaged path adds every file twice.

    hatchling then fails with "A second file is being added to the wheel
    archive at the same path", which breaks `pip install .` and so the sandbox
    image build.
    """
    wheel = wheel_config()
    packages = [Path(p) for p in wheel.get("packages", [])]
    for source in wheel.get("force-include", {}):
        src = Path(source)
        for package in packages:
            assert not src.is_relative_to(package), (
                f"force-include {source!r} is inside packaged path {package}; "
                f"`packages` already ships it and hatchling will add it twice."
            )


def test_templates_are_inside_the_package():
    """arch_query.py ships to users; it must live where `packages` picks it up."""
    template = Path("src/k8srca/arch/templates/arch_query.py")
    assert template.exists()
    packages = [Path(p) for p in wheel_config().get("packages", [])]
    assert any(template.is_relative_to(p) for p in packages), (
        "the query script is outside every packaged path and would not ship"
    )


def test_k8stools_floor_matches_the_tools_actually_used():
    """change_history needs get_replicaset_summaries, added in k8stools 1.2.0."""
    deps = config()["project"]["dependencies"]
    k8stools = next((d for d in deps if d.startswith("k8stools")), None)
    assert k8stools is not None
    assert ">=1.2.0" in k8stools, (
        "arch/history.py calls get_replicaset_summaries, which k8stools gained in 1.2.0"
    )


def test_k8stools_container_pins_a_version_at_least_as_new():
    """The container serves the tools; a stale pin there is what actually bites."""
    dockerfile = Path("docker/Dockerfile.k8stools").read_text()
    line = next(l for l in dockerfile.splitlines() if "K8STOOLS_VERSION" in l and "ARG" in l)
    version = line.split("=", 1)[1].strip()
    major, minor = (int(x) for x in version.split(".")[:2])
    assert (major, minor) >= (1, 2), f"container pins k8stools {version}, needs >= 1.2.0"
