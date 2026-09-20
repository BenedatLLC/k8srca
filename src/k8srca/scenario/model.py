"""Scenario and ground-truth schemas (design 004 §2, §6.3).

A scenario is a directory, not an object: a question, the captured state needed
to answer it, and the truth. This module is only the reading of those two YAML
files and the one rule that binds them together -- see :class:`ScenarioDir`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

#: Every rival in truth.yaml must be given one of these (004 §6.2). "Worth
#: checking later" is not among them, and that omission is the point: an answer
#: that defers a rival has not dispositioned it.
Disposition = Literal["confirmed", "weakened", "refuted", "could_not_check"]


class K8sToolsSource(BaseModel):
    """A k8stools capture replayed by `k8s-mcp-server --state-file`."""

    state: str                                     # capture file, relative to the scenario dir
    clock: Literal["frozen", "advancing"] = "frozen"


class Sources(BaseModel):
    """The sources a scenario needs stood up.

    One field per source kind. Adding Prometheus (004 §3.3) adds a field here
    and a container in sources.py; it does not touch the runner or the grader,
    which is the extensibility claim 004 §2 makes.
    """

    k8stools: K8sToolsSource


class Budget(BaseModel):
    max_tool_calls: int = 25
    max_usd: float = 0.25


class Scenario(BaseModel):
    """scenario.yaml -- the question and how to serve it."""

    id: str
    question: str
    sources: Sources
    budget: Budget = Field(default_factory=Budget)
    follow_up: str | None = None
    #: Tool calls the scenario is *about*. Scenario 5 exists to find out whether
    #: the agent reaches for get_replicaset_summaries, so not calling it is a
    #: failure regardless of how good the prose is (004 §6.2).
    requires_tools: list[str] = Field(default_factory=list)
    #: True for scenarios authored rather than captured from real breakage, so
    #: their results are read with the 004 §4.1 caveat in mind.
    authored: bool = False


class Cause(BaseModel):
    summary: str
    #: Substrings the answer must contain. Deliberately crude: the rubric grader
    #: judges whether the cause is *right*, while this only catches an answer
    #: that never names the thing at all.
    must_identify: list[str] = Field(default_factory=list)


class Rival(BaseModel):
    id: str
    disposition: Disposition
    note: str = ""


class Trap(BaseModel):
    id: str
    expect: Literal["flagged", "avoided"]
    note: str = ""


class Gap(BaseModel):
    id: str
    expect: Literal["reported_unavailable"] = "reported_unavailable"
    note: str = ""


class CaptureRef(BaseModel):
    """Which capture this truth was written against.

    004 §6.3 says the runner must refuse to grade a scenario whose capture is
    newer than its truth. Comparing mtimes would be the obvious reading and is
    the wrong mechanism: git does not preserve mtimes, so a fresh clone sets
    both to checkout time and the guard silently never fires -- exactly when it
    matters, on someone else's machine or in CI.

    Pinning the capture's own `captured_at` instead is content-based, survives
    a clone, and is strictly stronger: *any* re-record invalidates the truth,
    not merely a newer one. Re-recording therefore forces a human back through
    truth.yaml, which is what the rule is for.
    """

    captured_at: str
    #: Bundle digest of the cluster-architecture skill this truth was written
    #: against. That skill is a *second observed read of the same cluster*, not
    #: supplementary context, so a scenario is only one moment if both are
    #: pinned together (see ScenarioDir.skill_dir).
    skill_digest: str | None = None


class Truth(BaseModel):
    """truth.yaml -- what a good answer looks like."""

    id: str
    capture: CaptureRef
    cause: Cause
    rivals: list[Rival] = Field(default_factory=list)
    traps: list[Trap] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> "Truth":
        for label, items in (("rival", self.rivals), ("trap", self.traps), ("gap", self.gaps)):
            seen = [i.id for i in items]
            dupes = sorted({i for i in seen if seen.count(i) > 1})
            if dupes:
                raise ValueError(f"duplicate {label} id(s): {', '.join(dupes)}")
        return self


class StaleTruthError(Exception):
    """The capture changed after the truth was written (004 §6.3)."""


class ScenarioDir:
    """One scenario on disk, with its two files read and cross-checked."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.scenario = Scenario.model_validate(_read_yaml(self.path / "scenario.yaml"))
        self.truth = Truth.model_validate(_read_yaml(self.path / "truth.yaml"))
        if self.scenario.id != self.truth.id:
            raise ValueError(
                f"{self.path}: scenario.yaml id {self.scenario.id!r} != "
                f"truth.yaml id {self.truth.id!r}"
            )

    @property
    def capture_path(self) -> Path:
        return self.path / self.scenario.sources.k8stools.state

    @property
    def skill_dir(self) -> Path:
        return self.path / "skill" / "cluster-architecture"

    @property
    def architecture_path(self) -> Path:
        return self.skill_dir / "architecture.json"

    def architecture(self) -> dict | None:
        """The cluster-architecture skill as it stood when this was recorded.

        The capture is *not* the whole world the agent reasons in. It also
        carries the cluster-architecture skill -- declared facts from charts and
        documented ones from runbooks -- and cites them freely. Grading an
        answer against the capture alone reports every such citation as a
        fabrication, which is how this came to be snapshotted here.
        """
        if not self.architecture_path.exists():
            return None
        return json.loads(self.architecture_path.read_text())

    def capture(self) -> dict:
        return json.loads(self.capture_path.read_text())

    def skill_sha(self) -> str | None:
        """Digest of the pinned skill bundle, by the same rule sync uses."""
        if not self.skill_dir.exists():
            return None
        from ..kb.skills import bundle_digest

        return bundle_digest(self.skill_dir)

    def check_truth_is_current(self) -> None:
        """Refuse to grade against a capture the truth was not written for."""
        expected = self.truth.capture.skill_digest
        if expected is not None and expected != self.skill_sha():
            raise StaleTruthError(
                f"{self.scenario.id}: the pinned cluster-architecture skill has changed "
                f"since truth.yaml was written (pinned {expected}, now {self.skill_sha()}). "
                f"It is a second observed read of the cluster, so this is a different "
                f"world -- re-review truth.yaml against it."
            )
        captured_at = self.capture().get("captured_at")
        if captured_at != self.truth.capture.captured_at:
            raise StaleTruthError(
                f"{self.scenario.id}: truth.yaml was written against a capture taken at "
                f"{self.truth.capture.captured_at!r}, but {self.capture_path.name} was "
                f"taken at {captured_at!r}. Re-review truth.yaml against the new capture "
                f"-- restart counts, ages and traps all move -- then update "
                f"capture.captured_at."
            )


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return yaml.safe_load(path.read_text()) or {}


def discover(root: Path) -> list[ScenarioDir]:
    """Every scenario directory under `root`, by id."""
    if not root.exists():
        return []
    found = [ScenarioDir(p) for p in sorted(root.iterdir())
             if (p / "scenario.yaml").exists()]
    return sorted(found, key=lambda s: s.scenario.id)
