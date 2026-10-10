"""What kubewiki reads: the sources of a deployment's architecture skill.

`server` on a live source names an MCP server; `build()` is given a map from
those names to URLs, so the caller decides how a server is reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class Server:
    """An MCP server kubewiki reads a cluster through: k8stools."""

    name: str
    url: str
    timeout_s: float = 60.0


class HelmChartRef(BaseModel):
    """An official chart, pinned: rendered offline by `helm template` (kubewiki.fetch)."""

    repo: str
    chart: str
    version: str
    release: str = "release"
    values: str | None = None             # a values file; chart defaults if omitted


class GitRef(BaseModel):
    """One path of a git repository at a pinned commit (kubewiki.fetch)."""

    repo: str
    #: A full commit SHA, so the content cannot change underneath a build.
    ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    path: str


class ArchSource(BaseModel):
    """One contributor to the deployment's architecture skill.

    Four kinds, answering different questions: `live_cluster` how the system
    works today, `chart_repo` what it is declared to be, `docs` what each part
    is for, and `change_history` what changed and when. They are merged with
    provenance rather than collapsed, so disagreement between them stays
    visible.
    """

    type: Literal["live_cluster", "chart_repo", "docs", "change_history"]
    enabled: bool = True
    # live_cluster
    server: str | None = None
    namespaces: list[str] = Field(default_factory=lambda: ["default"])
    # chart_repo / docs: a local directory, or a pinned external artifact
    path: Path | None = None
    url: str | None = None
    helm: HelmChartRef | None = None      # chart_repo only
    git: GitRef | None = None             # docs (or chart_repo manifests in git)


class ArchitectureConfig(BaseModel):
    sources: list[ArchSource] = Field(default_factory=list)

    def active(self) -> list[ArchSource]:
        return [s for s in self.sources if s.enabled]
