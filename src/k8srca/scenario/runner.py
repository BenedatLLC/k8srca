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
    errors: list[str] = field(default_factory=list)
    checks: CheckResult | None = None

    @property
    def passed(self) -> bool:
        return not self.errors and self.checks is not None and self.checks.passed


def scenario_state(state: State, environment_id: str, path: Path) -> Path:
    """A state file identical to production's but pointing at `environment_id`.

    The agents and skills are deliberately the same objects: an agent is not
    scoped to an environment, the session binds the two. Re-syncing a parallel
    set would measure different agents than the ones that answer in Slack.
    """
    clone = State(environment_id=environment_id, agents=dict(state.agents),
                  skills=dict(state.skills), config_rev=state.config_rev)
    clone.save(path)
    return path


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


def _spend_usd(session: Any) -> float:
    """Best-effort cost for one session, in dollars.

    The field has moved between API shapes, so this reads defensively rather
    than failing a run over accounting: a budget check that crashes is worse
    than one that reports zero.
    """
    for attr in ("total_cost", "cost", "usage"):
        node = getattr(session, attr, None)
        if node is None:
            continue
        amount = getattr(node, "amount", None) if not isinstance(node, (int, float)) else node
        if amount is None:
            continue
        try:
            value = float(amount)
        except (TypeError, ValueError):
            continue
        # Minor units when it came back as a string of cents.
        return value / 100.0 if isinstance(amount, str) else value
    return 0.0


def run_once(sd: ScenarioDir, cfg: Config, state: State, *, environment_id: str,
             image: str, env_key_var: str, workdir: Path) -> Run:
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
    state_path = scenario_state(state, environment_id, workdir / "scenario-state.json")
    run = Run(scenario_id=sd.scenario.id, answer="")

    with sources.replay(sd.capture_path, image, clock=src.clock):
        poller = start_poller(state_path, network=sources.NETWORK,
                              env_key_var=env_key_var, log=workdir / "poller.log")
        try:
            client = Anthropic(api_key=api_key)
            scenario_state_obj = State(environment_id=environment_id,
                                       agents=state.agents, skills=state.skills)
            session = create(client, cfg, scenario_state_obj, sd.scenario.question,
                             title=f"scenario:{sd.scenario.id}",
                             metadata={"k8srca_scenario": sd.scenario.id})
            run.session_id = session.id
            turn = consume(client.beta.sessions.events.stream(session_id=session.id))
            run.answer = "\n\n".join(turn.messages)
            run.tool_calls = list(turn.tool_calls)
            run.errors = list(turn.errors)

            if sd.scenario.follow_up and turn.ok:
                client.beta.sessions.events.create(
                    session_id=session.id,
                    events=[{"type": "user.message",
                             "content": [{"type": "text", "text": sd.scenario.follow_up}]}],
                )
                nxt = consume(client.beta.sessions.events.stream(session_id=session.id))
                run.answer += "\n\n" + "\n\n".join(nxt.messages)
                run.tool_calls += list(nxt.tool_calls)
                run.errors += list(nxt.errors)

            run.usd = _spend_usd(client.beta.sessions.retrieve(session_id=session.id))
        finally:
            poller.stop()

    run.checks = run_all(sd, run.answer, run.tool_calls, len(run.tool_calls), run.usd)
    return run
