"""The generator contract (design 005 §6.2).

A generator is a build-time component: it reads sources and writes a skill
bundle. It is not a plugin -- a plugin delivers a bundle at run time; a
generator produces one, per deployment, on demand or on a schedule (005 §6.1).

    generate(cfg, dest) -> GeneratorReport

Every generator is run through `run()`, which adds what 005 §6.3 asks of every
generated bundle and what no generator should have to remember:

- **a manifest** (`GENERATED.json`) inside the bundle, naming the generator,
  its output format and the k8srca release, plus a digest of the content. A
  regression can then be traced to a generator change or a source change. The
  manifest holds no timestamp: sync re-uploads a bundle whenever its digest
  moves, so a timestamp would re-upload an unchanged skill on every build.
- **a report** saved outside the bundle (`.k8srca/reports/<name>.json`):
  what was built, what was skipped or unresolved, and when. It is for people
  and for the generator's evals, not for the agent, so it is not shipped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..config import Config

#: The bundle manifest's file name, at the bundle root.
MANIFEST = "GENERATED.json"

#: Where reports go. Local state, like `.k8srca/state.json`.
REPORTS = Path(".k8srca/reports")

#: Not bundle content: build debris, and the manifest itself.
_NOT_CONTENT = {"__pycache__", ".DS_Store", MANIFEST}


@dataclass
class GeneratorReport:
    """What one generation did."""

    generator: str
    #: One line per thing built, for a terminal.
    lines: list[str] = field(default_factory=list)
    #: Inputs that were skipped or could not be resolved. Reported, never
    #: silently dropped: a generator that loses input quietly produces a skill
    #: that is wrong in a way nobody sees.
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    built_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def render(self) -> str:
        out = [f"  {line}" for line in self.lines]
        out += [f"  WARNING  {w}" for w in self.warnings]
        return "\n".join(out)


@dataclass(frozen=True)
class SkillBundle:
    """A generated bundle on disk, and what produced it."""

    name: str
    path: Path
    generator: str
    format: int
    content_digest: str


class Generator(Protocol):
    #: The skill this generator writes; also the bundle's directory name.
    name: str
    #: Output format. Bumped when the meaning of the output changes, so a
    #: consumer can tell an old bundle from a new one without diffing it.
    format: int

    async def generate(self, cfg: "Config", dest: Path) -> GeneratorReport: ...


def content_files(bundle: Path) -> list[Path]:
    return sorted(p for p in bundle.rglob("*")
                  if p.is_file() and not any(part in _NOT_CONTENT for part in p.parts))


def content_digest(bundle: Path) -> str:
    """A digest of the bundle's content, excluding the manifest that records it."""
    h = hashlib.sha256()
    for f in content_files(bundle):
        h.update(f.relative_to(bundle).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _k8srca_version() -> str:
    try:
        return package_version("k8srca")
    except PackageNotFoundError:
        return "unknown"


def finalize(bundle: Path, generator: Generator) -> SkillBundle:
    """Write the bundle's manifest, and describe the bundle."""
    digest = content_digest(bundle)
    manifest = {
        "name": generator.name,
        "generator": type(generator).__name__,
        "format": generator.format,
        "k8srca": _k8srca_version(),
        "content_digest": digest,
    }
    (bundle / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return SkillBundle(name=generator.name, path=bundle, generator=manifest["generator"],
                       format=generator.format, content_digest=digest)


def read_manifest(bundle: Path) -> dict | None:
    path = bundle / MANIFEST
    return json.loads(path.read_text()) if path.exists() else None


async def run(generator: Generator, cfg: "Config", dest: Path,
              reports: Path = REPORTS) -> tuple[SkillBundle, GeneratorReport]:
    """Generate, then manifest the bundle and save the report."""
    report = await generator.generate(cfg, dest)
    bundle = finalize(dest, generator)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{generator.name}.json").write_text(json.dumps(
        {**asdict(report), "bundle": {**asdict(bundle), "path": str(bundle.path)}},
        indent=2, sort_keys=True) + "\n")
    return bundle, report
