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


#: (major, minor, patch) the floor must be at least. 1.2.0 gave
#: `get_replicaset_summaries`, which arch/history.py calls; 2.0.3 made the `age`
#: and `last_seen` durations whole-second, so they validate against the
#: `format: "duration"` schema the tools themselves declare -- before it, a
#: schema-checking MCP client rejected those calls outright, on every row.
K8STOOLS_FLOOR = (2, 0, 3)


def test_k8stools_floor_matches_the_tools_actually_used():
    """The floor is a claim about the tool surface, not a preference."""
    deps = config()["project"]["dependencies"]
    k8stools = next((d for d in deps if d.startswith("k8stools")), None)
    assert k8stools is not None
    floor = ".".join(str(n) for n in K8STOOLS_FLOOR)
    assert f">={floor}" in k8stools, (
        f"k8srca needs k8stools >= {floor}; pyproject asks for {k8stools!r}"
    )


def test_k8stools_container_pins_a_version_at_least_as_new():
    """The container serves the tools; a stale pin there is what actually bites."""
    dockerfile = Path("docker/Dockerfile.k8stools").read_text()
    line = next(l for l in dockerfile.splitlines() if "K8STOOLS_VERSION" in l and "ARG" in l)
    version = line.split("=", 1)[1].strip()
    pinned = tuple(int(x) for x in version.split(".")[:3])
    floor = ".".join(str(n) for n in K8STOOLS_FLOOR)
    assert pinned >= K8STOOLS_FLOOR, (
        f"container pins k8stools {version}, needs >= {floor}"
    )


def test_compose_image_tag_tracks_the_pinned_version():
    """`docker compose up -d` builds only when the tag names an image it lacks.

    A fixed tag over a changed Dockerfile is silent: compose finds the old image
    locally, starts it, reports success, and serves the previous k8stools. Tying
    the tag to the pin is what makes a version bump take effect.
    """
    dockerfile = Path("docker/Dockerfile.k8stools").read_text()
    line = next(l for l in dockerfile.splitlines() if "K8STOOLS_VERSION" in l and "ARG" in l)
    version = line.split("=", 1)[1].strip()
    compose = Path("docker/compose.yaml").read_text()
    tags = {l.split("image:", 1)[1].strip()
            for l in compose.splitlines() if l.strip().startswith("image:")}
    assert tags == {f"k8srca/k8stools:{version}"}, (
        f"compose tags {sorted(tags)}, but the Dockerfile installs k8stools {version}"
    )


def test_files_pyproject_references_reach_the_sandbox_build_context():
    """A metadata file pyproject names must be COPYed, or the image cannot build.

    `license = {file = ...}` and `readme` are read by hatchling while generating
    metadata, so a missing one fails `pip install .` -- inside the image build,
    and nowhere else. The editable dev install never builds a wheel, so the whole
    suite stays green while `k8srca sandbox build` is broken, which is how the
    LICENSE added for open-sourcing went unnoticed until the poller refused to
    start.
    """
    project = config()["project"]
    referenced = {project["readme"]}
    license_ = project.get("license")
    if isinstance(license_, dict) and "file" in license_:
        referenced.add(license_["file"])

    dockerfile = Path("docker/Dockerfile.sandbox").read_text()
    copied = {word for line in dockerfile.splitlines()
              if line.startswith("COPY ")
              for word in line.split()[1:-1]}

    missing = sorted(referenced - copied)
    assert not missing, (
        f"pyproject references {missing}, which docker/Dockerfile.sandbox never "
        f"COPYs; `pip install .` fails at metadata generation inside the image"
    )
