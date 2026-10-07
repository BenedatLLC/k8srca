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
import logging
import os
import re
import threading
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..state import State

log = logging.getLogger(__name__)
from .checks import CheckResult, run_all
from .grader import Grade, grade
from .model import ScenarioDir
from . import sources


class RunError(RuntimeError):
    pass


class BillingExhausted(RunError):
    """The account cannot pay for the rest of the suite.

    Distinguished from an ordinary failure because the response is different:
    every remaining run will fail the same way, so the suite stops rather than
    burning through its scenarios to collect identical errors. It surfaces two
    ways -- as a raised API error when a session is created, and as an error
    event inside a session that had already started -- and both are the same
    situation.
    """


#: Substrings that identify a billing failure across both shapes.
_BILLING_MARKERS = ("credit balance is too low", "billing_error")


def _is_billing(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _BILLING_MARKERS)


@dataclass
class Run:
    """What one run of one scenario produced."""

    scenario_id: str
    answer: str
    tool_calls: list[str] = field(default_factory=list)
    usd: float = 0.0
    session_id: str = ""
    #: Digest of the cluster-architecture bundle this run actually used, and the
    #: capture it answered against. A baseline is only comparable to a run that
    #: saw the same world, so both travel with the result.
    skill_digest: str = ""
    #: Skills this run took from the working tree instead of production
    #: (`--local-skill`), name -> digest. Their digests are also folded into
    #: `skill_digest`, so such a run is never diffed against a baseline as if
    #: only the agent had changed.
    local_skills: dict[str, str] = field(default_factory=dict)
    capture_captured_at: str = ""
    #: Digest of truth.yaml, so a rewritten rubric cannot be read as a change
    #: in the agent.
    truth_digest: str = ""
    setup_log: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Why this run measured nothing, when it did not: its turn never finished
    #: (#2). Such a run is neither graded nor counted, so a lost stream cannot
    #: read as the agent getting everything wrong.
    invalid: str = ""
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
    * **Every agent with tools is rebuilt per scenario.** An agent's tool
      declarations are snapshotted from the k8stools it was synced against, and
      the sandbox refuses to run when they differ from the server it is given.
      Inherited from production, k8s-investigator carried production's
      k8stools surface into a replay of a newer one, and every session failed
      (ManifestMismatch). It is synced against the replay instead, like the
      coordinator.

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


#: Agents rebuilt per scenario: the coordinator because its skills are, and
#: both because their tools are declared from the scenario's own k8stools.
#: Specialists first, so the coordinator's roster can reference them.
SCENARIO_SCOPED_AGENTS = ("k8s-investigator", "rca-coordinator")
#: Skills pinned per scenario, because they describe the cluster.
SCENARIO_SCOPED_SKILLS = ("cluster-architecture",)


def upload_local_skill(name: str, source: Path, state: State, *, client: Any, path: Path,
                       log) -> str:
    """Publish a working-tree skill as this scenario's own copy, and use it.

    For measuring a change to a skill the scenario otherwise inherits from
    production (k8s-rca) before production has it. It gets its own skill
    object, recorded beside the scenario's state, never a new version of
    production's: agents reference skills at "latest", so a version added to
    production's would repoint the Slack bot at an unmeasured change. The next
    run without `--local-skill` inherits production's again. Returns the digest.
    """
    from ..kb.skills import UploadedSkill, upload
    from ..state import SkillState

    records_path = path.with_name("local-skills.json")
    records = json.loads(records_path.read_text()) if records_path.exists() else {}
    known = records.get(name)
    prior = UploadedSkill(known["skill_id"], known["version"], known["digest"]) if known else None
    uploaded = upload(client, source, prior, log)
    records[name] = {"skill_id": uploaded.skill_id, "version": uploaded.version,
                     "digest": uploaded.digest, "source": str(source)}
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
    state.skills[name] = SkillState(uploaded.skill_id, uploaded.version, uploaded.digest)
    return uploaded.digest


def sync_scenario_agent(sd: ScenarioDir, cfg: Config, state: State, *,
                        client: Any, path: Path, log,
                        local_skills: dict[str, Path] | None = None) -> dict[str, str]:
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
    used = {name: upload_local_skill(name, source, state, client=client, path=path, log=log)
            for name, source in sorted((local_skills or {}).items())}

    planned = asyncio.run(plan(cfg, state.skills))
    for key in [k for k in SCENARIO_SCOPED_AGENTS if k in planned]:
        roster = resolve_roster(cfg, key, state)
        state.agents[key] = ensure_agent(client, planned[key], roster, state, git_rev(), log)
    state.save(path)
    return used


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
                 log: Path, sandbox_image: str | None = None) -> Poller:
    """Run `k8srca poller` against the scenario environment and network.

    `sandbox_image` is passed explicitly rather than left for the poller to
    derive. The tag is a hash of the working tree, the poller is a fresh
    subprocess per run, and a suite takes tens of minutes -- so a commit or an
    edit part-way through moves the tag and every later run dies on an image
    that was never built. That is how an n=3 run came back with one result.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = log.open("wb")
    argv = ["uv", "run", "k8srca", "poller", "--state", str(state_path),
            "--network", network, "--env-key-var", env_key_var]
    if sandbox_image:
        argv += ["--image", sandbox_image]
    proc = subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT)
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


#: Retries for control-plane calls (skill and agent sync before each run).
#:
#: The SDK default of 2 gives up after about 1.5s. Twice in one day a 503
#: "Overloaded" on agent sync crashed a whole batch before its first session,
#: and fifteen minutes later the same call succeeded. The SDK's backoff doubles
#: from 0.5s to a cap of 8s, so 25 retries waits about 2-3 minutes in all
#: (jitter shortens each wait by up to a quarter) and honours a `retry-after` of
#: up to 60s. Nothing is spent while it waits -- no session exists yet.
CONTROL_PLANE_MAX_RETRIES = 25


def _control_plane_client() -> Any:
    """An API-key client, for writes the environment key cannot make.

    Creating skills and agents is control-plane work and needs the org key. The
    poller still gets only the environment key -- the split in 001 §3.1 is about
    what sits on the host running agent-authored bash, and this does not.
    """
    import os

    from anthropic import Anthropic

    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                     max_retries=CONTROL_PLANE_MAX_RETRIES)


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


def _against_replay(cfg: Config, host_url: str) -> Config:
    """`cfg` with its k8stools server read, from the host, at the replay.

    Only the host-side address changes: the agent's tools still name the
    server the sandbox reaches (`k8stools` on the scenario network).
    """
    server = cfg.mcp[0].model_copy(update={"sync_url": host_url})
    if server.host_url() != host_url:
        raise RunError(f"the {server.name} MCP server's host URL is overridden "
                       f"({server.host_url()}), so the scenario agent would be planned "
                       f"against it, not the replay; unset the override")
    return cfg.model_copy(update={"mcp": [server, *cfg.mcp[1:]]})


def _consume_turn(client: Any, session_id: str, stream: Any, run: "Run", **where):
    """One turn, marking the run invalid if it never finished (#2)."""
    from ..session import StreamEnded, Turn, consume, turn_events

    from ..session import DROPPED

    try:
        turn = consume(turn_events(client, session_id, stream,
                                   abort=lambda: bool(run.invalid), **where))
    except (StreamEnded, *DROPPED) as exc:
        run.invalid = run.invalid or f"incomplete turn: {exc}"
        return Turn()
    if not turn.complete and not turn.errors:
        run.invalid = f"incomplete turn: stopped at {turn.stop_reason or 'no idle event'}"
    return turn


#: How long one run's session may take, both turns together. The slowest first
#: turn on record took 26 minutes; past this, the run is invalid, not slow.
SESSION_DEADLINE_S = 45 * 60
#: After interrupting, how long the session gets to go idle before the stream
#: is closed under it.
INTERRUPT_GRACE_S = 60


class Watchdog:
    """Ends a run that cannot finish, instead of waiting for it forever.

    A sandbox that fails (the poller logs `sandbox_failed`) leaves the session
    waiting on tool results nothing will send, and the stream waits with it: a
    single run hung for over 25 minutes this way. On a failed sandbox, or past
    the deadline, the run is marked invalid, the session is interrupted, and if
    it still has not gone idle after a grace period the stream is closed.
    """

    def __init__(self, client: Any, session_id: str, run: "Run", poller_log: Path, *,
                 deadline_s: float = SESSION_DEADLINE_S, grace_s: float = INTERRUPT_GRACE_S,
                 poll_s: float = 5.0):
        self.client, self.session_id, self.run = client, session_id, run
        self.poller_log, self.deadline_s, self.grace_s, self.poll_s = \
            poller_log, deadline_s, grace_s, poll_s
        self.stream: Any = None          # the stream currently being read
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def __enter__(self) -> "Watchdog":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=self.poll_s * 2)

    def reason(self, elapsed: float) -> str | None:
        try:
            text = self.poller_log.read_text()
        except OSError:
            text = ""
        m = re.search(rf"sandbox_failed session={re.escape(self.session_id)} ([^\"\n]*)", text)
        if m:
            return f"sandbox failed: {m.group(1)}"
        if elapsed > self.deadline_s:
            return f"no answer within {self.deadline_s / 60:.0f} minutes"
        return None

    def _watch(self) -> None:
        start = time.monotonic()
        while not self._stop.wait(self.poll_s):
            why = self.reason(time.monotonic() - start)
            if why is None:
                continue
            self.run.invalid = why
            log.warning("abandoning session %s: %s", self.session_id, why)
            _interrupt(self.client, self.session_id)
            if not self._stop.wait(self.grace_s) and self.stream is not None:
                try:
                    self.stream.close()
                except Exception:  # noqa: BLE001 - closing is the last resort
                    pass
            return


def _interrupt(client: Any, session_id: str) -> None:
    """Stop an abandoned session, so the next run's poller -- same environment --
    does not go on serving its tool calls and then tear its tools away."""
    try:
        client.beta.sessions.events.send(session_id=session_id,
                                         events=[{"type": "user.interrupt"}])
    except Exception:  # noqa: BLE001 - best effort; the run is already invalid
        log.warning("could not interrupt abandoned session %s", session_id)


def run_once(sd: ScenarioDir, cfg: Config, state: State, *, environment_id: str,
             image: str, env_key_var: str, workdir: Path,
             sandbox_image: str | None = None,
             grader_model: str | None = None,
             local_skills: dict[str, Path] | None = None) -> Run:
    """Stand up the sources, run the agent once, grade deterministically."""
    from anthropic import Anthropic

    from ..session import consume, create, sent_event_id, turn_events

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

    lines: list[str] = []
    with sources.replay(sd.capture_path, image, clock=src.clock) as rp:
        # The scenario's own skill and agent, published before anything runs:
        # the session snapshots the agent at creation, so a late upload is a
        # session answering from the previous scenario's cluster. Its tools are
        # declared from the replayed k8stools -- the version under test -- not
        # production's container: planned against production, a tool newer
        # than production's k8stools could never be measured before it shipped.
        scoped = scenario_state(state, environment_id, state_path)
        used = sync_scenario_agent(sd, _against_replay(cfg, rp.host_url), scoped,
                                   client=_control_plane_client(), path=state_path,
                                   log=lines.append, local_skills=local_skills)
        run = Run(scenario_id=sd.scenario.id, answer="", local_skills=used,
                  skill_digest=(sd.skill_sha() or "")
                  + "".join(f"+{name}:{digest}" for name, digest in sorted(used.items())),
                  capture_captured_at=sd.truth.capture.captured_at,
                  truth_digest=sd.truth_sha())
        run.setup_log = lines

        poller = start_poller(state_path, network=sources.NETWORK,
                              env_key_var=env_key_var,
                              log=scenario_dir / "poller.log",
                              sandbox_image=sandbox_image)
        watchdog = None
        try:
            client = Anthropic(api_key=api_key)
            try:
                session = create(client, cfg, scoped, sd.scenario.question,
                                 title=f"scenario:{sd.scenario.id}",
                                 metadata={"k8srca_scenario": sd.scenario.id})
            except Exception as exc:  # noqa: BLE001 - re-raised unless billing
                if _is_billing(str(exc)):
                    raise BillingExhausted(
                        "could not start a session: the account is out of credits"
                    ) from exc
                raise
            run.session_id = session.id
            watchdog = Watchdog(client, session.id, run, scenario_dir / "poller.log")
            watchdog.__enter__()
            # initial_events already started the run; just read it.
            with client.beta.sessions.events.stream(session_id=session.id) as stream:
                watchdog.stream = stream
                turn = _consume_turn(client, session.id, stream, run, from_start=True)
            run.answer = "\n\n".join(turn.messages)
            run.tool_calls = list(turn.tool_calls)
            run.errors = list(turn.errors)
            for err in run.errors:
                if _is_billing(str(err)):
                    raise BillingExhausted(
                        "the session stopped because the account is out of credits; "
                        "the remaining runs would fail the same way"
                    )

            if sd.scenario.follow_up and turn.ok and not run.invalid:
                # Stream before send (001 §7.2): the stream only carries events
                # emitted after it opens, so sending first loses the whole turn
                # and the follow-up silently reads as an empty answer.
                with client.beta.sessions.events.stream(session_id=session.id) as stream:
                    watchdog.stream = stream
                    sent = client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[{"type": "user.message",
                                 "content": [{"type": "text",
                                              "text": sd.scenario.follow_up}]}],
                    )
                    nxt = _consume_turn(client, session.id, stream, run,
                                        after=sent_event_id(sent))
                run.answer += "\n\n" + "\n\n".join(nxt.messages)
                run.tool_calls += list(nxt.tool_calls)
                run.errors += list(nxt.errors)

            run.usd = _spend_usd(client.beta.sessions.retrieve(session_id=session.id))
            if run.invalid:
                _interrupt(client, session.id)
        finally:
            if watchdog is not None:
                watchdog.__exit__(None, None, None)
            poller.stop()

    if run.invalid:
        return run
    run.checks = run_all(sd, run.answer, run.tool_calls, len(run.tool_calls), run.usd)
    if run.answer.strip():
        kwargs = {"model": grader_model} if grader_model else {}
        run.grade = grade(sd, run.answer, run.tool_calls, client=client, **kwargs)
    return run
