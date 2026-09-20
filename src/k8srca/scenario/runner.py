"""Running one scenario end to end (design 004 §7).

The agent runs exactly as it does in production -- same sandbox image, same
spawn script, same poller -- with one thing changed: the docker network. The
replay server is aliased `k8stools` on that network, so the sandbox resolves the
same hostname baked into its own k8srca.yaml and cannot tell the difference.

That is the reason the runner drives a real poller rather than the in-process
worker. The suite exists to measure reasoning, and measuring it on a code path
production never executes would answer a question nobody asked.

Scenario sessions run in their own *environment*. Sharing production's would let
whichever poller claimed a work item first serve it -- and the production poller
is wired to the live cluster, so a scenario could be answered from real state
and still look like it passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..state import State
from .checks import CheckResult, run_all
from .grader import Grade, grade
from .model import ScenarioDir
from . import sources


class RunError(RuntimeError):
    pass


@dataclass
class Run:
    """What one run of one scenario produced."""

    scenario_id: str
    answer: str
    tool_calls: list[str] = field(default_factory=list)
    usd: float = 0.0
    session_id: str = ""
    #: Digest of the cluster-architecture bundle this run actually used, so a
    #: result can be attributed when the pinned world changes.
    skill_digest: str = ""
    setup_log: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    checks: CheckResult | None = None
    grade: Grade | None = None

    @property
    def passed(self) -> bool:
        """Deterministic checks only.

        The rubric dimensions are reported per-dimension rather than folded in
        here: 004 §6.4 wants pass rates against a baseline, not a pass/fail
        gate, because a scenario that gets the cause right and one rival's
        disposition wrong is a different signal from one that fabricated a pod.
        """
        return not self.errors and self.checks is not None and self.checks.passed


def scenario_state(state: State, environment_id: str, path: Path) -> State:
    """Per-scenario control-plane state, loaded from `path` if it exists.

    Production's agents and skills are inherited as defaults, then the pieces
    that describe *the cluster* are replaced per scenario. What is inherited and
    what is pinned is the whole design decision:

    * **cluster-architecture is pinned.** It describes the world the scenario
      replays. Left at production's it is a second observed read of the cluster
      from a different moment, and the agent silently reasons over two worlds.
    * **k8s-rca is inherited.** The knowledge base is the agent's *method*, and
      it is what the suite exists to measure -- S3's whole question is whether
      it beats the L0 floor. Pinning it would freeze the variable under test.
    * **k8s-investigator is inherited.** It carries no skills, so it has no view
      of the cluster to be stale about.

    Pin what describes the world; leave free what constitutes the method.
    """
    existing = State.load(path) if path.exists() else State()

    # Scenario-scoped entries are *dropped* from the production base, not
    # overlaid on it. Overlaying leaves production's ids in place on the first
    # run, when the scenario file is empty -- and then the scenario publishes
    # its frozen cluster as a new version of the production skill, which every
    # agent references at "latest". The Slack bot starts answering from a
    # scenario's snapshot, and nothing anywhere reports an error.
    agents = {k: v for k, v in state.agents.items() if k not in SCENARIO_SCOPED_AGENTS}
    agents.update({k: v for k, v in existing.agents.items() if k in SCENARIO_SCOPED_AGENTS})
    skills = {k: v for k, v in state.skills.items() if k not in SCENARIO_SCOPED_SKILLS}
    skills.update({k: v for k, v in existing.skills.items() if k in SCENARIO_SCOPED_SKILLS})
    return State(environment_id=environment_id, agents=agents, skills=skills,
                 config_rev=state.config_rev)


#: Agents rebuilt per scenario, because their skills are.
SCENARIO_SCOPED_AGENTS = ("rca-coordinator",)
#: Skills pinned per scenario, because they describe the cluster.
SCENARIO_SCOPED_SKILLS = ("cluster-architecture",)


def sync_scenario_agent(sd: ScenarioDir, cfg: Config, state: State, *,
                        client: Any, path: Path, log) -> State:
    """Upload the scenario's pinned skill and point its own agent at it.

    The scenario gets its own skill object rather than a new version of
    production's: agents reference skills at "latest" (sync.py resolve_skills),
    so publishing the scenario's frozen cluster as a new version of the
    production skill would repoint the Slack bot at it.
    """
    import asyncio

    from ..kb.skills import UploadedSkill, upload
    from ..sync import ensure_agent, git_rev, plan, resolve_roster

    if not sd.skill_dir.exists():
        raise RunError(
            f"{sd.scenario.id} pins no cluster-architecture skill "
            f"({sd.skill_dir} missing). Re-record it: the agent reads that skill "
            f"as a second view of the cluster, and an unpinned one describes a "
            f"different moment than the capture."
        )
    sd.check_truth_is_current()

    # None on the first run, so a *new* skill object is created rather than a
    # version being added to production's.
    known = state.skills.get("cluster-architecture")
    prior = UploadedSkill(known.skill_id, known.version, known.digest) if known else None
    uploaded = upload(client, sd.skill_dir, prior, log)

    from ..state import SkillState

    state.skills["cluster-architecture"] = SkillState(
        uploaded.skill_id, uploaded.version, uploaded.digest)

    planned = asyncio.run(plan(cfg, state.skills))
    key = cfg.coordinator
    roster = resolve_roster(cfg, key, state)
    state.agents[key] = ensure_agent(client, planned[key], roster, state, git_rev(), log)
    state.save(path)
    return state


@dataclass
class Poller:
    process: subprocess.Popen
    log: Path

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()


def start_poller(state_path: Path, *, network: str, env_key_var: str,
                 log: Path) -> Poller:
    """Run `k8srca poller` against the scenario environment and network."""
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("wb")
    proc = subprocess.Popen(
        ["uv", "run", "k8srca", "poller", "--state", str(state_path),
         "--network", network, "--env-key-var", env_key_var],
        stdout=handle, stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 45
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RunError(f"scenario poller exited immediately:\n"
                           f"{log.read_text()[-600:]}")
        if "poller_start" in _read(log):
            return Poller(proc, log)
        time.sleep(0.4)
    proc.kill()
    raise RunError(f"scenario poller did not start:\n{_read(log)[-600:]}")


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _control_plane_client() -> Any:
    """An API-key client, for writes the environment key cannot make.

    Creating skills and agents is control-plane work and needs the org key. The
    poller still gets only the environment key -- the split in 001 §3.1 is about
    what sits on the host running agent-authored bash, and this does not.
    """
    import os

    from anthropic import Anthropic

    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _spend_usd(session: Any) -> float:
    """What one session cost, in dollars.

    The API reports `usage.list_cost` as {amount, currency} with `amount` in
    *minor units* as a string -- "25" is $0.25, not $25. Reading it as dollars
    would understate every run by 100x and make the budget check decorative.
    """
    usage = getattr(session, "usage", None)
    cost = getattr(usage, "list_cost", None) if usage is not None else None
    amount = getattr(cost, "amount", None) if cost is not None else None
    if amount is None:
        return 0.0
    try:
        return float(amount) / 100.0
    except (TypeError, ValueError):
        return 0.0


def run_once(sd: ScenarioDir, cfg: Config, state: State, *, environment_id: str,
             image: str, env_key_var: str, workdir: Path,
             grader_model: str | None = None) -> Run:
    """Stand up the sources, run the agent once, grade deterministically."""
    from anthropic import Anthropic

    from ..session import consume, create

    sd.check_truth_is_current()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RunError("ANTHROPIC_API_KEY is not set; the runner creates sessions")
    if not os.environ.get(env_key_var, "").strip():
        raise RunError(f"{env_key_var} is not set; the scenario poller needs it")
    if environment_id == state.environment_id:
        raise RunError(
            "the scenario environment is the production environment; the "
            "production poller would claim these sessions and answer them from "
            "the live cluster"
        )

    src = sd.scenario.sources.k8stools
    scenario_dir = workdir / sd.scenario.id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    state_path = scenario_dir / "state.json"

    # The scenario's own skill and agent, published before anything runs: the
    # session snapshots the agent at creation, so a late upload is a session
    # answering from the previous scenario's cluster.
    lines: list[str] = []
    scoped = scenario_state(state, environment_id, state_path)
    scoped = sync_scenario_agent(sd, cfg, scoped, client=_control_plane_client(),
                                 path=state_path, log=lines.append)
    run = Run(scenario_id=sd.scenario.id, answer="", skill_digest=sd.skill_sha() or "")
    run.setup_log = lines

    with sources.replay(sd.capture_path, image, clock=src.clock):
        poller = start_poller(state_path, network=sources.NETWORK,
                              env_key_var=env_key_var, log=workdir / "poller.log")
        try:
            client = Anthropic(api_key=api_key)
            session = create(client, cfg, scoped, sd.scenario.question,
                             title=f"scenario:{sd.scenario.id}",
                             metadata={"k8srca_scenario": sd.scenario.id})
            run.session_id = session.id
            # initial_events already started the run; just read it.
            with client.beta.sessions.events.stream(session_id=session.id) as stream:
                turn = consume(stream)
            run.answer = "\n\n".join(turn.messages)
            run.tool_calls = list(turn.tool_calls)
            run.errors = list(turn.errors)

            if sd.scenario.follow_up and turn.ok:
                # Stream before send (001 §7.2): the stream only carries events
                # emitted after it opens, so sending first loses the whole turn
                # and the follow-up silently reads as an empty answer.
                with client.beta.sessions.events.stream(session_id=session.id) as stream:
                    client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[{"type": "user.message",
                                 "content": [{"type": "text",
                                              "text": sd.scenario.follow_up}]}],
                    )
                    nxt = consume(stream)
                run.answer += "\n\n" + "\n\n".join(nxt.messages)
                run.tool_calls += list(nxt.tool_calls)
                run.errors += list(nxt.errors)

            run.usd = _spend_usd(client.beta.sessions.retrieve(session_id=session.id))
        finally:
            poller.stop()

    run.checks = run_all(sd, run.answer, run.tool_calls, len(run.tool_calls), run.usd)
    if run.answer.strip():
        kwargs = {"model": grader_model} if grader_model else {}
        run.grade = grade(sd, run.answer, run.tool_calls, client=client, **kwargs)
    return run
