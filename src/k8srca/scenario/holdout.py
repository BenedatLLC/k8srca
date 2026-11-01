"""The hold-out kit (design 006): a suite we never see, scored on someone else's machine.

The 004 pipeline already records, builds, runs and grades. What it does not do
is keep a suite *blind*: it inherits our install's architecture sources, writes
answers and grader notes beside the scores, and names everything by its
descriptive scenario id. This module is the part that differs -- the checks an
author's suite must pass, the seal that freezes its truth, and the one artifact
that crosses back to us, a report holding scores and nothing else.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import yaml

from . import checks
from .model import Scenario, ScenarioDir, StaleTruthError, _read_yaml

#: The suite's manifest: opaque id -> scenario, and each scenario's seals.
MANIFEST = "holdout.yaml"

#: Where the kit writes inside the author's suite. Run detail holds answers and
#: grader notes, so it lives with the suite, never under this repo's .k8srca/.
RUNS_DIR = ".runs"
REPORTS_DIR = "reports"


class HoldoutError(Exception):
    """A suite or scenario the kit refuses to work with, and why."""


# --- where the suite lives --------------------------------------------------

def check_suite_location(root: Path, repo: Path) -> None:
    """The suite must not live inside this repo.

    Inside it, one `git add .` puts it in our history, and the suite is not
    blind any more.
    """
    suite, ours = root.resolve(), repo.resolve()
    if suite == ours or ours in suite.parents:
        raise HoldoutError(
            f"{root} is inside this repository ({repo}). A hold-out suite lives in its "
            f"own repo, outside this one, so it can never be committed here (design 006 §2).")


def require_own_architecture(scenario: Scenario) -> None:
    """A hold-out names its own wiki sources and namespaces.

    Left out, both are inherited from k8srca.yaml -- our OTel demo's chart and
    docs, and `namespaces: [default]` -- and the agent is handed a wiki of the
    wrong application. Nothing fails; the scenario just measures something else.
    """
    own = scenario.architecture
    missing = [what for what, absent in (
        ("architecture.sources", own is None or own.sources is None),
        ("architecture.namespaces", own is None or not own.namespaces)) if absent]
    if missing:
        raise HoldoutError(
            f"{scenario.id}: scenario.yaml must set {' and '.join(missing)}. "
            f"Otherwise they are inherited from k8srca.yaml, which describes our "
            f"OpenTelemetry demo, not your application. Use your application's chart "
            f"and docs, or `[{{type: live_cluster}}, {{type: change_history}}]` alone.")


@dataclass
class Draft:
    """A scenario before its truth exists: what `record` and `build-wiki` work on.

    `ScenarioDir` insists on truth.yaml, and rightly -- nothing should be graded
    without one. But an author records the capture and builds the wiki first,
    then writes the truth against both, so those two steps see only this.
    """

    path: Path
    scenario: Scenario
    arch: str = "wiki"

    @classmethod
    def load(cls, path: Path) -> "Draft":
        return cls(Path(path), Scenario.model_validate(_read_yaml(Path(path) / "scenario.yaml")))

    @property
    def capture_path(self) -> Path:
        return self.path / self.scenario.sources.k8stools.state

    @property
    def skill_dir(self) -> Path:
        return self.path / "skill" / "wiki" / "cluster-architecture"


# --- lint --------------------------------------------------------------------

@dataclass
class Lint:
    problems: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def truth_prose(sd: ScenarioDir, checklist=None) -> str:
    """Every sentence the truth and the checklist state about the world, as one text.

    From the checklist, only what a right answer says: the statements and
    phrasings of present items, and what absent items allow. An absent item's
    statement and its `tempting` phrasings are the mistake, written to be
    wrong, so they are no claim about the capture.
    """
    t = sd.truth
    parts = [t.cause.summary]
    parts += [item.note for item in (*t.candidates, *t.traps, *t.gaps)]
    for item in (checklist.items if checklist is not None else []):
        if item.expect == "present":
            parts += [item.statement, *item.accept]
        parts += item.allowed
    return "\n".join(p for p in parts if p)


def lint(sd: ScenarioDir) -> Lint:
    """Mechanical checks on a scenario's truth and checklist, before calibrating.

    The answer checks run over their own prose: a pod, image or restart count
    the truth or the checklist names must be in the capture, for the same
    reason an answer's must. That catches typos and invented names. It cannot
    catch a wrong *reading* of a right fact -- how every one of our own truth
    errors happened (004 §5) -- which is what calibration is for.

    The checklist is what a run is scored on (010 §7), so a scenario without
    one, or with no diagnosis item to gate on, is refused.
    """
    out = Lint()
    try:
        require_own_architecture(sd.scenario)
    except HoldoutError as exc:
        out.problems.append(str(exc))
    try:
        sd.check_truth_is_current()
    except (StaleTruthError, FileNotFoundError) as exc:
        out.problems.append(str(exc))
        return out                       # nothing below means anything without a capture

    checklist, unreadable = None, False
    try:
        checklist = sd.checklist()
    except (ValueError, yaml.YAMLError) as exc:   # a malformed item, duplicate or wrong id
        out.problems.append(f"checklist.yaml: {exc}")
        unreadable = True
    if checklist is None and not unreadable:
        out.problems.append(f"no checklist.yaml: runs are scored on it ({sd.checklist_path})")
    elif checklist is not None:
        if not any(i.diagnosis for i in checklist.items):
            out.problems.append("checklist.yaml marks no item `diagnosis: true`, so no run "
                                "can pass or fail the scenario")
        groups = {i.group for i in checklist.items}
        for group, why in (("ruled_out", "nothing an answer should rule out"),
                           ("traps", "no tempting mistake"),
                           ("next", "no next step")):
            if group not in groups:
                out.advisories.append(f"checklist.yaml has no {group} items: {why} is scored")

    capture = sd.capture()
    world = (sd.capture_path.read_text()
             + (sd.architecture_path.read_text() if sd.architecture_path.exists() else ""))
    for term in sd.truth.cause.must_identify:
        if term.lower() not in world.lower():
            # Advice, not a failure: a term is matched against the answer, and a
            # phrase ("memory limit") can be right without appearing verbatim in
            # any object. A name or value that appears nowhere is usually a typo.
            out.advisories.append(
                f"cause.must_identify {term!r} appears nowhere in the capture or the wiki; "
                f"if it is a name or a value, check its spelling")
    prose = truth_prose(sd, checklist)
    for result in (checks.closed_world(prose, capture), checks.numeric_claims(prose, capture)):
        out.problems += [f"truth {f.check}: {f.detail.replace('answer ', 'truth ')}"
                         for f in result.findings]

    if not sd.truth.cause.must_identify:
        out.advisories.append("cause.must_identify is empty: an answer that never names "
                              "the cause can still pass `cause`")
    if sd.scenario.budget.model_dump() == type(sd.scenario.budget)().model_dump():
        out.advisories.append("budget is the default; set max_tool_calls from the "
                              "calibration runs (004: a gate set from measurement)")
    return out


# --- calibration: does each item read one way? --------------------------------

def checklist_agreement(runs: list, scenarios: dict[str, ScenarioDir], grade_once,
                        *, grades: int = 3, map_fn=map) -> dict[str, tuple[dict, list[str]]]:
    """Per scenario, each item's agreement over `grades` gradings of every
    answer, and what misses the bar (010 §7: 97%, no item split on more than
    one answer).

    A run's own grading counts as the first; `grade_once(sd, checklist,
    answer)` makes the rest, through `map_fn` (a pool's map, to grade in
    parallel). An item graded two ways on one answer is usually an item worded
    two ways, which is the author's to fix before sealing.
    """
    from . import checklist as cl

    lists = {sid: sd.checklist() for sid, sd in scenarios.items()}
    answers, jobs = [], []
    for run in runs:
        record = getattr(run, "checklist", None)
        if record is None or getattr(run, "invalid", "") or not run.answer.strip():
            continue
        sd = scenarios[run.scenario_id]
        answers.append((run.scenario_id,
                        cl.ChecklistGrade.model_validate({"items": record["verdicts"]})))
        jobs += [(sd, lists[run.scenario_id], run.answer)] * (grades - 1)
    more = iter(list(map_fn(lambda job: grade_once(*job), jobs)))
    by_scenario: dict[str, list[list]] = {}
    for sid, first in answers:
        by_scenario.setdefault(sid, []).append([first, *(next(more) for _ in range(grades - 1))])
    out = {}
    for sid, gradings in by_scenario.items():
        agree = cl.agreement(gradings, lists[sid])
        out[sid] = (agree, cl.below_bar(agree))
    return out


# --- the manifest and its seals ------------------------------------------------

@dataclass
class Seal:
    truth: str                           # ScenarioDir.truth_sha() when sealed
    at: str
    reason: str = ""
    #: checklist_sha() when sealed. Empty in a seal made before the kit scored
    #: on checklists, which a checklist-scored run then refuses until re-sealed.
    checklist: str = ""


def checklist_sha(sd: ScenarioDir) -> str:
    """The checklist's digest, or "" before one exists."""
    from .checklist import checklist_sha as sha

    return sha(sd.checklist_path) if sd.checklist_path.exists() else ""


@dataclass
class Entry:
    scenario: str                        # the scenario id, which never leaves the suite
    seals: list[Seal] = field(default_factory=list)


@dataclass
class Manifest:
    entries: dict[str, Entry] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> "Manifest":
        path = root / MANIFEST
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        return cls({hid: Entry(e["scenario"], [Seal(**s) for s in e.get("seals") or []])
                    for hid, e in (raw.get("scenarios") or {}).items()})

    def save(self, root: Path) -> None:
        body = {"scenarios": {
            hid: {"scenario": e.scenario,
                  "seals": [{"truth": s.truth, "checklist": s.checklist, "at": s.at,
                             "reason": s.reason} for s in e.seals]}
            for hid, e in sorted(self.entries.items(), key=lambda kv: _hid_number(kv[0]))}}
        header = ("# Hold-out manifest (k8srca design 006). The H ids are what the score\n"
                  "# report shows; the scenario ids never leave this suite.\n")
        (root / MANIFEST).write_text(header + yaml.safe_dump(body, sort_keys=False))

    def hid_of(self, scenario_id: str) -> str | None:
        return next((h for h, e in self.entries.items() if e.scenario == scenario_id), None)

    def seal(self, sd: ScenarioDir, *, reason: str = "", now: str | None = None) -> str:
        """Freeze a scenario's truth and checklist, giving it an opaque id on its
        first seal.

        A re-seal is a change to either after calibration, which moves every
        score the scenario produces, so it needs a reason, and the report counts
        it (design 006 §7).
        """
        at = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        seal = Seal(sd.truth_sha(), at, reason, checklist_sha(sd))
        hid = self.hid_of(sd.scenario.id)
        if hid is None:
            hid = f"H{max((_hid_number(h) for h in self.entries), default=0) + 1}"
            self.entries[hid] = Entry(sd.scenario.id, [seal])
            return hid
        entry = self.entries[hid]
        if entry.seals and not _moved(entry.seals[-1], sd):
            raise HoldoutError(f"{sd.scenario.id} is already sealed as {hid} with this "
                               f"truth and checklist")
        if not reason:
            moved = " and ".join(_moved(entry.seals[-1], sd))
            raise HoldoutError(
                f"{sd.scenario.id} ({hid}) is sealed, and its {moved} changed since. A "
                f"re-seal moves its scores, so it needs --reason, which the report records.")
        entry.seals.append(seal)
        return hid

    def sealed(self, found: list[ScenarioDir]) -> list[tuple[str, ScenarioDir]]:
        """Each sealed scenario with its id, refusing one whose truth moved since."""
        by_id = {sd.scenario.id: sd for sd in found}
        out = []
        for hid, entry in sorted(self.entries.items(), key=lambda kv: _hid_number(kv[0])):
            sd = by_id.get(entry.scenario)
            if sd is None:
                raise HoldoutError(f"{hid} names scenario {entry.scenario!r}, which is not "
                                   f"in the suite")
            if moved := _moved(entry.seals[-1], sd):
                raise HoldoutError(
                    f"{entry.scenario} ({hid}): {' and '.join(moved)} changed after it was "
                    f"sealed. Re-seal it with a reason, or restore the sealed files.")
            out.append((hid, sd))
        return out


def _moved(seal: Seal, sd: ScenarioDir) -> list[str]:
    """Which of the sealed files differ from the seal: truth.yaml, checklist.yaml."""
    return [name for name, now, then in (
        ("truth.yaml", sd.truth_sha(), seal.truth),
        ("checklist.yaml", checklist_sha(sd), seal.checklist)) if now != then]


def _hid_number(hid: str) -> int:
    return int(hid[1:]) if hid[1:].isdigit() else 0


# --- the score report: the only thing that crosses ------------------------------

def release_of(repo: Path) -> dict:
    """Which build of the agent was scored: the commit, its tag, and whether it was clean.

    A report is a statement about one release. An uncommitted change in the
    tree is a different agent from the tag, and the report would be about
    neither.
    """
    def git(*args: str) -> str:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    return {"commit": git("rev-parse", "HEAD"),
            "tag": git("describe", "--tags", "--exact-match") or None,
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def score_report(aggregates: dict, manifest: Manifest, *, release: dict,
                 grader: str, taken_at: str | None = None) -> dict:
    """Scores under opaque ids, and nothing else (design 006 §6).

    Built field by field from counts, never by copying an aggregate: a field
    added to the aggregate later must not cross by default. No answers, no
    grader notes, no claims, no check findings, no scenario ids -- each of those
    says what a scenario is about. Checklist items cross only as counts per
    group: an item's id ("rules-out-network-policy") names the scenario's world.
    """
    from .checklist import GROUPS

    rows = {}
    for scenario_id, agg in aggregates.items():
        hid = manifest.hid_of(scenario_id)
        if hid is None:
            raise HoldoutError("an unsealed scenario cannot be reported")
        graded = agg.runs - agg.invalid
        diag, scored = agg.diagnosis_runs
        rows[hid] = {
            "runs": agg.runs,
            "invalid": agg.invalid,
            "diagnosis": f"{diag}/{scored}" if scored else "-",
            "checklist_mean": round(mean(agg.checklist), 1) if agg.checklist else None,
            "checklist_items": agg.checklist_listed or None,
            "groups": {g: f"{agg.groups[g][0]}/{agg.groups[g][1]}"
                       for g in GROUPS if agg.groups.get(g, [0, 0])[1]},
            "checks_passed": f"{agg.checks_passed}/{graded}",
            "tool_calls_mean": round(mean(agg.tool_calls), 1) if agg.tool_calls else None,
            "usd_mean": round(mean(agg.usd), 3) if agg.usd else None,
            "truth_revision": len(manifest.entries[hid].seals),
        }
    return {
        "release": release,
        "grader": grader,
        "taken_at": taken_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenarios": dict(sorted(rows.items(), key=lambda kv: _hid_number(kv[0]))),
    }
