"""Uploading skill bundles to the Skills API (design 001 §5, §6 step 2).

Skills are how knowledge reaches a self-hosted sandbox at all: those
environments reject `file` and `github_repository` resources, so a skill bundle
is the only supported channel (001 F2). The worker downloads them into
`{workdir}/skills/<name>/`.

Agents reference each skill at version "latest", not at a pinned id. Pinning
a `skver_...` id looks right and breaks delivery: when the platform snapshots
the agent onto a session it rewrites the id to a numeric form, which the skills
endpoint then rejects (CLAUDE.md, "Things that bite"; `sync.resolve_skills`).
The cost is that a session's skill content is resolved when the session starts,
not fixed at sync.

A skill version is identified by an opaque `skver_...` id, not an integer --
`skill.latest_version_id` on create, `version.id` on a subsequent version. The
id is recorded in state.json; agents do not reference it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from anthropic import Anthropic

# Files that are inputs to the build, not part of the bundle.
EXCLUDE = {"__pycache__", ".DS_Store"}


def bundle_files(skill_dir: Path) -> list[Path]:
    """Every file in the skill directory, SKILL.md first."""
    files = sorted(
        p for p in skill_dir.rglob("*")
        if p.is_file() and not any(part in EXCLUDE for part in p.parts)
    )
    skill_md = skill_dir / "SKILL.md"
    if skill_md not in files:
        raise FileNotFoundError(f"{skill_dir} has no SKILL.md at its root")
    return [skill_md] + [f for f in files if f != skill_md]


def bundle_digest(skill_dir: Path) -> str:
    """Content hash, so an unchanged skill is not re-uploaded every sync."""
    h = hashlib.sha256()
    for f in bundle_files(skill_dir):
        h.update(f.relative_to(skill_dir).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


@dataclass
class UploadedSkill:
    skill_id: str
    version: str
    digest: str

    def as_reference(self) -> dict:
        return {"type": "custom", "skill_id": self.skill_id, "version": self.version}


def upload(client: Anthropic, skill_dir: Path, known: UploadedSkill | None, log) -> UploadedSkill:
    """Create the skill, or add a version when its contents changed."""
    digest = bundle_digest(skill_dir)
    if known and known.digest == digest:
        log(f"skill        {skill_dir.name:20} {known.skill_id}  v{known.version} (unchanged)")
        return known

    # The API takes file handles; they must share one top-level directory.
    handles = [
        (f"{skill_dir.name}/{f.relative_to(skill_dir).as_posix()}", f.read_bytes())
        for f in bundle_files(skill_dir)
    ]
    if known:
        version = client.beta.skills.versions.create(known.skill_id, files=handles)
        log(f"skill        {skill_dir.name:20} {known.skill_id}  {version.id} (updated)")
        return UploadedSkill(known.skill_id, version.id, digest)

    skill = client.beta.skills.create(files=handles, display_name=skill_dir.name)
    log(f"skill        {skill_dir.name:20} {skill.id}  {skill.latest_version_id} (created)")
    return UploadedSkill(skill.id, skill.latest_version_id, digest)
