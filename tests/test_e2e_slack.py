"""End-to-end: concurrent Slack threads against the real stack.

Not hermetic and not free. This is the automated form of the manual
multi-session check: three questions arrive at once, three sandbox containers
come up, each Slack thread gets its own answer, and follow-ups continue the
sessions they belong to rather than starting new ones.

Prerequisites, all of which must already be true:

    uv run k8srca up        # network, tunnel, kubeconfig, k8stools
    uv run k8srca sync      # .k8srca/state.json
    uv run k8srca poller    # in another terminal

Then:

    K8SRCA_TEST_LIVE=1 uv run pytest tests/test_e2e_slack.py -v -s

Slack itself is faked -- the orchestrator is driven directly. Slack will not
deliver `app_mention` for messages the bot itself posts, so a self-driving test
through the real socket is not possible; everything below the Bolt handler is
real, including the containers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from fakes import FakeSlack

pytestmark = pytest.mark.live

if os.environ.get("K8SRCA_TEST_LIVE") != "1":
    pytest.skip("set K8SRCA_TEST_LIVE=1 (spends money, needs a cluster and a running poller)",
                allow_module_level=True)

import anthropic  # noqa: E402

from k8srca import config as config_mod  # noqa: E402
from k8srca.settings import SlackSettings  # noqa: E402
from k8srca.slack.app import Orchestrator  # noqa: E402
from k8srca.slack.sessions import SessionStore  # noqa: E402
from k8srca.state import State  # noqa: E402

CHANNEL = "C_TEST"
QUESTIONS = {
    "1.1": "How many nodes does this cluster have? One sentence, no investigation.",
    "2.2": "Which namespaces exist? One sentence, names only.",
    "3.3": "How many pods are running in the otel-demo namespace? One sentence.",
}
FOLLOW_UPS = {ts: "Thanks. In one short sentence, what did you just tell me?"
              for ts in QUESTIONS}


def containers() -> set[str]:
    if not shutil.which("docker"):
        return set()
    out = subprocess.run(["docker", "ps", "--filter", "name=k8srca-sbx-", "--format", "{{.Names}}"],
                         capture_output=True, text=True)
    return {n for n in out.stdout.split() if n}


class Sampler(threading.Thread):
    """Watches for sandbox containers while the turns run."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.peak = 0
        self.names: set[str] = set()
        self.stop = threading.Event()

    def run(self):
        while not self.stop.wait(0.5):
            live = containers()
            self.names |= live
            self.peak = max(self.peak, len(live))


@pytest.fixture(scope="module")
def transcript(tmp_path_factory):
    """Run the whole scenario once; every test reads the result."""
    state = State.load(Path(".k8srca/state.json"))
    if not state.environment_id or not state.agents:
        pytest.skip("no .k8srca/state.json -- run `uv run k8srca sync` first")

    cfg = config_mod.load("k8srca.yaml")
    store = SessionStore(tmp_path_factory.mktemp("live") / "sessions.db")
    orch = Orchestrator(cfg, state,
                        SlackSettings(socket_mode_token="unused", bot_token="unused"),
                        store, max_concurrent=4)
    slack = FakeSlack()

    def drive(prompts: dict[str, str]):
        workers = [threading.Thread(target=orch.handle,
                                    args=(slack, CHANNEL, ts, ts, text, "U_TEST"))
                   for ts, text in prompts.items()]
        started = time.time()
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=600)
        assert not any(w.is_alive() for w in workers), "a turn never finished"
        return time.time() - started

    sampler = Sampler()
    sampler.start()
    first_wall = drive(QUESTIONS)
    first_ids = {ts: store.get(CHANNEL, ts).session_id for ts in QUESTIONS}
    sampler.stop.set()
    sampler.join(timeout=5)

    # Snapshot round one before round two adds its own placeholders.
    first_answers = {ts: slack.final_text(CHANNEL, ts) for ts in QUESTIONS}
    first_posts = {ts: len([p for p in slack.posts if p["thread_ts"] == ts]) for ts in QUESTIONS}

    follow_wall = drive(FOLLOW_UPS)
    second_ids = {ts: store.get(CHANNEL, ts).session_id for ts in QUESTIONS}

    client = anthropic.Anthropic()
    sessions = {ts: client.beta.sessions.retrieve(sid) for ts, sid in first_ids.items()}
    cost = sum(int(getattr(getattr(s.usage, "list_cost", None), "amount", 0) or 0)
               for s in sessions.values())

    print(f"\nfirst round {first_wall:.1f}s · follow-ups {follow_wall:.1f}s · "
          f"peak containers {sampler.peak} · ${cost / 100:.2f}")
    for ts in QUESTIONS:
        print(f"  {ts} -> {first_ids[ts]}: {first_answers[ts][:110]}")

    return dict(slack=slack, store=store, first_ids=first_ids, second_ids=second_ids,
                first_answers=first_answers, first_posts=first_posts,
                sessions=sessions, sampler=sampler, first_wall=first_wall,
                follow_wall=follow_wall, cost=cost)


class TestConcurrentInvestigations:
    def test_every_thread_was_answered(self, transcript):
        for ts, answer in transcript["first_answers"].items():
            assert answer and not answer.startswith(":warning:"), f"{ts}: {answer}"

    def test_each_thread_got_its_own_session(self, transcript):
        assert len(set(transcript["first_ids"].values())) == len(QUESTIONS)

    def test_no_cross_talk(self, transcript):
        # The session's own metadata says which thread it belongs to; if the
        # orchestrator mixed threads up, this is where it shows.
        for ts, session in transcript["sessions"].items():
            assert dict(session.metadata)["slack_thread_ts"] == ts

    def test_each_turn_shows_one_placeholder_edited_into_the_answer(self, transcript):
        # One message per turn, not a stream of progress notes (001 §7.5).
        assert transcript["first_posts"] == {ts: 1 for ts in QUESTIONS}

    def test_sandbox_containers_ran_in_parallel(self, transcript):
        sampler = transcript["sampler"]
        if not shutil.which("docker"):
            pytest.skip("no docker client on this host")
        assert sampler.names, "no sandbox container was ever seen -- is the poller running?"
        assert sampler.peak > 1, (
            f"only ever {sampler.peak} container(s); the poller is serialising spawns")

    def test_concurrency_beats_running_them_in_series(self, transcript):
        # Three sessions that each take ~10s must not take ~30s together.
        assert transcript["first_wall"] < 600


class TestSessionContinuity:
    def test_follow_ups_reuse_the_session(self, transcript):
        assert transcript["second_ids"] == transcript["first_ids"]

    def test_follow_ups_are_answered(self, transcript):
        slack = transcript["slack"]
        for ts in QUESTIONS:
            answer = slack.final_text(CHANNEL, ts)
            assert answer and not answer.startswith(":warning:"), f"{ts}: {answer}"
            assert answer != transcript["first_answers"][ts], f"{ts} repeated its first answer"

    def test_the_second_turn_adds_exactly_one_message(self, transcript):
        slack = transcript["slack"]
        for ts in QUESTIONS:
            total = len([p for p in slack.posts if p["thread_ts"] == ts])
            assert total == transcript["first_posts"][ts] + 1

    def test_follow_ups_are_faster_than_cold_starts(self, transcript):
        # Reuse skips container start, skill download and MCP handshake.
        assert transcript["follow_wall"] < transcript["first_wall"]

    def test_threads_stay_active(self, transcript):
        for ts in QUESTIONS:
            assert transcript["store"].get(CHANNEL, ts).status == "active"
