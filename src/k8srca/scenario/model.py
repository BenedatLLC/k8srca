"""Scenario and ground-truth schemas (design 004 §2, §6.3).

A scenario is a directory, not an object: a question, the captured state needed
to answer it, and the truth. This module is only the reading of those two YAML
files and the one rule that binds them together -- see :class:`ScenarioDir`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from kubewiki.eval import CaseSource
from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Where the capture puts a candidate (008 §3.1). "Worth checking later" is not
#: among them, and that omission is the point: an answer that defers a
#: candidate has not placed it (004 §6.2).
Standing = Literal["most_likely", "not_ruled_out", "ruled_out"]

#: How strongly the capture supports a candidate it does not rule out (008 §3.2).
Strength = Literal["weak", "moderate", "strong"]

#: The verdicts truth files gave before 008, and the standing each became
#: (008 §4.3). Weakened and could-not-check differ in the reason, which a
#: standing does not carry; the strength range does.
Disposition = Literal["confirmed", "weakened", "refuted", "could_not_check"]
FROM_DISPOSITION: dict[str, Standing] = {
    "confirmed": "most_likely", "weakened": "not_ruled_out",
    "could_not_check": "not_ruled_out", "refuted": "ruled_out"}

#: The two forms of the cluster-architecture skill a scenario can carry. The
#: legacy snapshot of observed state is what scenarios were recorded with; the
#: wiki is kubewiki's, which replaces it (kubewiki design §10, step 4). Both
#: are kept so one scenario measures the agent with either: the before/after.
Arch = Literal["legacy", "wiki"]
ARCHES: tuple[Arch, ...] = ("legacy", "wiki")


class K8sToolsSource(BaseModel):
    """A k8stools capture replayed by `k8s-mcp-server --state-file`."""

    state: str                                     # capture file, relative to the scenario dir
    clock: Literal["frozen", "advancing"] = "frozen"
    #: Digest of the capture's JSON. The capture lives in the scenario data
    #: repository, fetched by this name to `state`, which git ignores (issue
    #: #7). None for a capture kept beside its scenario, as a draft or a
    #: hold-out scenario (006) keeps it.
    digest: str | None = None


class PrometheusSource(BaseModel):
    """A promtools metrics capture, replayed by a Prometheus behind promtools
    (design 009 §5). Stored as a TSDB block in the scenario data repository and
    fetched by the digest truth.yaml pins (009 §8.1); the manifest beside it is
    committed here."""

    #: Where the block's tarball sits once fetched, relative to the scenario
    #: dir. Gitignored.
    capture: str = "metrics/block.tar.gz"


class Sources(BaseModel):
    """The sources a scenario needs stood up.

    One field per source kind. Adding Prometheus (009 §5) added a field here
    and a container in sources.py; the grader is untouched, which is the
    extensibility claim 004 §2 makes. The runner turns on the metrics server
    the config declares.
    """

    k8stools: K8sToolsSource
    prometheus: PrometheusSource | None = None


class Budget(BaseModel):
    max_tool_calls: int = 25
    max_usd: float = 0.25


class ArchitectureSources(BaseModel):
    """What the scenario's wiki is built from (`k8srca scenario build-wiki`).

    The cluster half is always the scenario's own capture, replayed. The rest
    defaults to k8srca.yaml's: right for a scenario recorded on our install,
    wrong for anyone else's, whose chart and docs ours do not describe
    (004 §12.1). Such a scenario lists its own.
    """

    model_config = ConfigDict(extra="forbid")

    namespaces: list[str] | None = None
    sources: list[CaseSource] | None = None


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
    architecture: ArchitectureSources | None = None


class Cause(BaseModel):
    summary: str
    #: Substrings the answer must contain. Deliberately crude: the rubric grader
    #: judges whether the cause is *right*, while this only catches an answer
    #: that never names the thing at all.
    must_identify: list[str] = Field(default_factory=list)
    #: The candidate the capture ranks first, by its knowledge-base id where it
    #: has one. Informational: `summary` is what the grader checks the cause
    #: against.
    most_likely: str | None = None
    #: The grades of support the capture gives the cause (008 §6.1). A range,
    #: because two sound readings can grade the same evidence a step apart.
    #: Outside it is over- or under-committing; empty means not graded.
    strength: list[Strength] = Field(default_factory=list)


class Candidate(BaseModel):
    """A cause the answer must place: most likely, not ruled out, or ruled out."""

    id: str
    standing: Standing
    #: Other standings that are also correct. For a candidate the capture sits
    #: on the boundary of -- a leak in a container killed during startup is
    #: either not ruled out (no time series) or ruled out (no operating period
    #: to leak over), and demanding one grades the author's taste, not the agent.
    also_accept: list[Standing] = Field(default_factory=list)
    #: The strengths the capture allows when the answer does not rule it out.
    #: Empty means any.
    strength: list[Strength] = Field(default_factory=list)
    note: str = ""

    @model_validator(mode="after")
    def _strength_needs_a_standing_it_applies_to(self) -> "Candidate":
        if self.strength and set(self.accepted()) == {"ruled_out"}:
            raise ValueError(f"{self.id}: a strength range for a candidate only ruled out")
        return self

    def accepted(self) -> list[Standing]:
        return [self.standing, *self.also_accept]


class _OldRival(BaseModel):
    """`rivals:` as truth files wrote them before 008; read, never written."""

    id: str
    disposition: Disposition
    also_accept: list[Disposition] = Field(default_factory=list)
    note: str = ""


class Trap(BaseModel):
    id: str
    expect: Literal["flagged", "avoided"]
    note: str = ""


class Gap(BaseModel):
    id: str
    expect: Literal["reported_unavailable"] = "reported_unavailable"
    note: str = ""


class Restraint(BaseModel):
    """The scenario is about declining (004 §5, scenario 7).

    The right answer finds no cause for what was asked, says what it checked,
    and does not reach for the visible breakage the note describes. Set here
    rather than inferred by the grader, so `restraint` applies to exactly the
    scenarios that say so.
    """

    expect: Literal["declined"] = "declined"
    note: str = ""


class MetricsRef(BaseModel):
    """A metrics capture, pinned by content (009 §5). `end` is the moment it
    was taken to, which is the k8stools capture's `captured_at`."""

    end: str
    digest: str


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
    #: Bundle digest of the legacy cluster-architecture skill this truth was
    #: written against. That skill is a *second observed read of the same
    #: cluster*, not supplementary context, so a scenario is only one moment if
    #: both are pinned together (see ScenarioDir.skill_dir).
    skill_digest: str | None = None
    #: Digests of the other forms of the skill this truth was reviewed against,
    #: by form (`wiki: <digest>`). A truth reviewed against one form says
    #: nothing about another: the wiki states different things, and a trap or
    #: rival written for the snapshot may not hold for it.
    skill_digests: dict[str, str] = Field(default_factory=dict)

    #: The metrics capture this truth was written against, when the scenario
    #: has one: the same moment as `captured_at`, read from Prometheus.
    metrics: "MetricsRef | None" = None

    def pinned(self, arch: "Arch") -> str | None:
        return self.skill_digests.get(arch) or (self.skill_digest if arch == "legacy" else None)


class Truth(BaseModel):
    """truth.yaml -- what a good answer looks like."""

    id: str
    capture: CaptureRef
    cause: Cause
    candidates: list[Candidate] = Field(default_factory=list)
    traps: list[Trap] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    restraint: Restraint | None = None

    @model_validator(mode="before")
    @classmethod
    def _rivals_written_before_008(cls, data: Any) -> Any:
        """A truth file written before 008 lists `rivals` with dispositions.
        It reads as candidates by 008 §4.3's table, with no strength ranges, so
        the hold-out suite's files (006) keep working until their author
        migrates them."""
        if not isinstance(data, dict) or "rivals" not in data:
            return data
        if "candidates" in data:
            raise ValueError("truth.yaml has both `rivals` and `candidates`; "
                             "`rivals` is the format before 008")
        data = dict(data)
        out = []
        for r in data.pop("rivals") or []:
            r = _OldRival.model_validate(r)
            standing = FROM_DISPOSITION[r.disposition]
            also = sorted({FROM_DISPOSITION[d] for d in r.also_accept} - {standing})
            out.append({"id": r.id, "standing": standing, "also_accept": also, "note": r.note})
        data["candidates"] = out
        return data

    @model_validator(mode="after")
    def _unique_ids(self) -> "Truth":
        for label, items in (("candidate", self.candidates), ("trap", self.traps),
                             ("gap", self.gaps)):
            seen = [i.id for i in items]
            dupes = sorted({i for i in seen if seen.count(i) > 1})
            if dupes:
                raise ValueError(f"duplicate {label} id(s): {', '.join(dupes)}")
        return self


class StaleTruthError(Exception):
    """The capture changed after the truth was written (004 §6.3)."""


class MissingData(FileNotFoundError):
    """A pinned capture or skill is not here, and fetching is off: in tests,
    which skip on it rather than reach the network (tests/conftest.py)."""


class ScenarioDir:
    """One scenario on disk, with its two files read and cross-checked."""

    def __init__(self, path: Path, arch: Arch = "wiki"):
        if arch not in ARCHES:
            raise ValueError(f"unknown architecture skill form {arch!r}; one of {ARCHES}")
        self.path = Path(path)
        #: Which form of the cluster-architecture skill runs use.
        self.arch: Arch = arch
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
    def metrics_path(self) -> Path | None:
        src = self.scenario.sources.prometheus
        return self.path / src.capture if src is not None else None

    @property
    def skill_dir(self) -> Path:
        """The pinned skill for this scenario's form of it.

        Both are named `cluster-architecture`, because the upload names the
        skill after its directory and the agent reads it at that path.
        """
        if self.arch == "wiki":
            return self.path / "skill" / "wiki" / "cluster-architecture"
        return self.path / "skill" / "cluster-architecture"

    @property
    def architecture_path(self) -> Path:
        """The file holding everything the skill states: the grader's reference."""
        return self.skill_dir / ("graph.json" if self.arch == "wiki" else "architecture.json")

    def ensure_data(self) -> None:
        """Fetch the capture and skill this scenario pins, if they are not here."""
        from . import sources

        sources.ensure_capture(self)
        sources.ensure_skill(self)

    def architecture(self) -> dict | None:
        """The cluster-architecture skill as it stood when this was recorded.

        The capture is *not* the whole world the agent reasons in. It also
        carries the cluster-architecture skill -- declared facts from charts and
        documented ones from runbooks -- and cites them freely. Grading an
        answer against the capture alone reports every such citation as a
        fabrication, which is how this came to be snapshotted here.
        """
        self.ensure_data()
        if not self.architecture_path.exists():
            return None
        return json.loads(self.architecture_path.read_text())

    def capture(self) -> dict:
        self.ensure_data()
        return json.loads(self.capture_path.read_text())

    def truth_sha(self) -> str:
        """Digest of truth.yaml itself.

        The capture and the skill pin the *world* a run saw; this pins the
        *rubric it was graded against*. Editing a trap or a rival's expected
        standing moves every number the grader produces just as surely as
        changing the grader does, and a baseline compared across such an edit
        reports the rewrite as a change in the agent.
        """
        return hashlib.sha256((self.path / "truth.yaml").read_bytes()).hexdigest()[:16]

    def skill_sha(self) -> str | None:
        """Digest of the pinned skill bundle, by the same rule sync uses."""
        if not self.skill_dir.exists():
            return None
        from ..kb.skills import bundle_digest

        return bundle_digest(self.skill_dir)

    def check_truth_is_current(self) -> None:
        """Refuse to grade against a capture the truth was not written for."""
        self.ensure_data()
        expected = self.truth.capture.pinned(self.arch)
        if self.arch != "legacy" and expected is None:
            # The legacy pin predates pinning and may be absent; a new form has
            # no such excuse. Its truth must have been read against it.
            raise StaleTruthError(
                f"{self.scenario.id}: truth.yaml has not been reviewed against the "
                f"{self.arch} skill ({self.skill_dir}). Re-read its candidates and traps "
                f"against that skill, then pin it: capture.skill_digests.{self.arch}: "
                f"{self.skill_sha() or '<build it first>'}"
            )
        if expected is not None and expected != self.skill_sha():
            raise StaleTruthError(
                f"{self.scenario.id}: the pinned cluster-architecture skill ({self.arch}) "
                f"has changed since truth.yaml was written (pinned {expected}, now "
                f"{self.skill_sha()}). The agent reads it beside the capture, so this is "
                f"a different world -- re-review truth.yaml against it."
            )
        self._check_metrics()
        captured_at = self.capture().get("captured_at")
        if captured_at != self.truth.capture.captured_at:
            raise StaleTruthError(
                f"{self.scenario.id}: truth.yaml was written against a capture taken at "
                f"{self.truth.capture.captured_at!r}, but {self.capture_path.name} was "
                f"taken at {captured_at!r}. Re-review truth.yaml against the new capture "
                f"-- restart counts, ages and traps all move -- then update "
                f"capture.captured_at."
            )


    def _check_metrics(self) -> None:
        """A truth read against the cluster but not its metrics, or the other
        way round, is a truth about a different world (009 §5)."""
        from .sources import metrics_digest

        pin, path = self.truth.capture.metrics, self.metrics_path
        if path is None and pin is None:
            return
        if path is None:
            raise StaleTruthError(f"{self.scenario.id}: truth.yaml pins a metrics capture, "
                                  f"but scenario.yaml has no prometheus source")
        if pin is None:
            raise StaleTruthError(
                f"{self.scenario.id}: the scenario replays metrics, but truth.yaml was not "
                f"written against them. Review it with the metrics in view, then pin "
                f"capture.metrics: {{end: <its end>, digest: <its digest>}}")
        # Absent is fine: it is fetched by this digest, and checked, at replay.
        if path.exists() and pin.digest != metrics_digest(path):
            raise StaleTruthError(
                f"{self.scenario.id}: the metrics capture changed since truth.yaml was "
                f"written (pinned {pin.digest}, now {metrics_digest(path)}). Re-review it.")
        if pin.end != self.truth.capture.captured_at:
            raise StaleTruthError(
                f"{self.scenario.id}: the metrics capture ends at {pin.end}, the cluster "
                f"capture was taken at {self.truth.capture.captured_at}: two moments")

    def metrics_manifest(self) -> dict:
        """What `promtools capture` recorded beside the samples: window,
        retention, scrape intervals. Committed, unlike the block."""
        path = self.metrics_path
        manifest = path.parent / "manifest.json" if path is not None else None
        return json.loads(manifest.read_text()) if manifest and manifest.exists() else {}

    def metrics_reference(self) -> dict | None:
        """promtools' reports on the metrics capture, as `scenario
        build-metrics-reference` wrote them: the grader's view of Prometheus.
        None without one; refused when it was read off another capture."""
        path = self.metrics_path.parent / "reference.json" if self.metrics_path else None
        if path is None or not path.exists():
            return None
        ref = json.loads(path.read_text())
        pin = self.truth.capture.metrics
        if pin is None or ref.get("digest") != pin.digest:
            raise StaleTruthError(
                f"{self.scenario.id}: metrics/reference.json was read off capture "
                f"{ref.get('digest')}, truth.yaml pins {pin.digest if pin else 'none'}. "
                f"Rebuild it: k8srca scenario build-metrics-reference {self.scenario.id}")
        return ref

    @property
    def checklist_path(self) -> Path:
        return self.path / "checklist.yaml"

    def checklist(self):
        """The statements an answer is graded on (010 §7), or None before one
        is written. Kept beside truth.yaml rather than in it, so the rubric
        grader, which reads the whole truth, is not moved by it."""
        if not self.checklist_path.exists():
            return None
        from .checklist import Checklist

        checklist = Checklist.load(self.checklist_path)
        if checklist.id != self.scenario.id:
            raise ValueError(f"{self.checklist_path}: id {checklist.id!r} != "
                             f"scenario id {self.scenario.id!r}")
        return checklist

    def metrics_end(self) -> float | None:
        """The metrics capture's end, as unix seconds."""
        pin = self.truth.capture.metrics
        if pin is None:
            return None
        from datetime import datetime

        return datetime.fromisoformat(pin.end.replace("Z", "+00:00")).timestamp()


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return yaml.safe_load(path.read_text()) or {}


def pin_capture_digest(scenario_yaml: Path, digest: str) -> None:
    """Write `sources.k8stools.digest` into scenario.yaml, keeping its comments:
    after the `state:` line, or over the digest already there."""
    import re

    text = scenario_yaml.read_text()
    block = re.search(r"^(\s*)k8stools:\s*\n((?:\1\s+.*\n|\s*#.*\n)*)", text, re.M)
    if block is None:
        raise ValueError(f"{scenario_yaml}: no sources.k8stools block")
    body = block.group(2)
    if re.search(r"^\s+digest:.*$", body, re.M):
        body = re.sub(r"^(\s+)digest:.*$", lambda m: f"{m.group(1)}digest: {digest}", body,
                      count=1, flags=re.M)
    else:
        state = re.search(r"^(\s+)state:.*\n", body, re.M)
        if state is None:
            raise ValueError(f"{scenario_yaml}: sources.k8stools has no state")
        body = body[:state.end()] + f"{state.group(1)}digest: {digest}\n" + body[state.end():]
    scenario_yaml.write_text(text[:block.start(2)] + body + text[block.end(2):])


def discover(root: Path, arch: Arch = "wiki") -> list[ScenarioDir]:
    """Every scenario directory under `root`, by id, using that form of the skill."""
    if not root.exists():
        return []
    found = [ScenarioDir(p, arch) for p in sorted(root.iterdir())
             if (p / "scenario.yaml").exists()]
    return sorted(found, key=lambda s: s.scenario.id)


CaptureRef.model_rebuild()
