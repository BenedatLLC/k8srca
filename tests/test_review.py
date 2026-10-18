"""The host's check of a final answer (src/k8srca/review.py, design 007 §6.1):
`open` leaves what it decided beside the evidence record, and an answer that
leaves an owed claim unnamed gets one revision turn."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from k8srca import review as R
from k8srca.scenario import runner

SKILL = Path(__file__).parent.parent / "skills" / "k8s-rca"
FACTS = "killed_on_every_life[C]\nnot completes_startup_each_life[C]\n"


def workspace(tmp_path, facts=FACTS, name="ad.facts"):
    """A session workspace as the sandbox lays it out, after one `open`."""
    shutil.copytree(SKILL, tmp_path / "skills" / "k8s-rca")
    (tmp_path / "evidence").mkdir(exist_ok=True)
    f = tmp_path / name
    f.write_text(facts)
    r = subprocess.run([sys.executable, str(tmp_path / "skills/k8s-rca/kb_query.py"), "open",
                        "CrashLoopBackOff", "--facts", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return tmp_path


def test_open_leaves_its_rows_beside_the_evidence_record(tmp_path):
    [rec] = R.open_records(workspace(tmp_path))
    assert rec["symptom"] == "pod-restarting" and rec["facts"] == "ad.facts"
    rows = {r["id"]: r for r in rec["rows"]}
    assert rows["memory-leak"]["status"] == "refuted"
    assert rows["memory-leak"]["parent"] == "oom-kill"
    assert rows["oom-kill"]["status"] == "confirmed" and rows["oom-kill"]["depth"] == 0


def test_open_writes_nothing_without_a_record_beside_it():
    """Run from the repo, as the tests do, there is no evidence directory."""
    assert not (SKILL.parents[1] / "evidence").exists()


def test_owed_is_every_candidate_and_every_decided_sub_cause(tmp_path):
    ids = {c["id"] for c in R.owed(R.open_records(workspace(tmp_path)))}
    assert {"oom-kill", "bad-config", "application-crash", "node-issue"} <= ids
    assert {"memory-limit-too-low", "memory-leak", "workload-spike"} <= ids
    # Open sub-causes are not owed: there are dozens.
    assert "port-conflict" not in ids and "env-vars-wrong" not in ids


def test_the_last_open_on_each_facts_file_counts(tmp_path):
    ws = workspace(tmp_path, facts="")
    assert "memory-leak" not in {c["id"] for c in R.owed(R.open_records(ws))}
    workspace_again = workspace(tmp_path / "x")  # a fresh copy decides it
    recs = R.open_records(ws) + R.open_records(workspace_again)
    assert "memory-leak" in {c["id"] for c in R.owed(recs)}


def test_a_claim_is_named_by_id_or_title():
    leak = {"id": "memory-leak", "title": "memory leak", "status": "refuted"}
    assert R.named("Memory leak: refuted, it never completes startup.", leak)
    assert R.named("`memory-leak` -- refuted", leak)
    assert not R.named("A leak is unlikely.", leak)
    oom = {"id": "oom-kill", "title": "OOM", "status": "confirmed"}
    assert R.named("Most likely: OOM, the limit is too low.", oom)
    assert not R.named("ZOOMING out", oom)


def test_an_answer_that_drops_the_refuted_siblings_is_sent_back(tmp_path):
    """The seventh gate's misses (007 §11.2)."""
    rv = R.completeness("Most likely: OOM -- memory limit too low.", workspace(tmp_path))
    missing = {m["id"] for m in rv.missing}
    assert {"memory-leak", "workload-spike"} <= missing
    assert "memory-limit-too-low" not in missing and "oom-kill" not in missing
    assert "- memory-leak (memory leak): `open` says refuted" in rv.prompt
    assert "Change nothing else." in rv.prompt


def test_a_complete_answer_needs_no_revision(tmp_path):
    ws = workspace(tmp_path)
    answer = "\n".join(f"- {c['id']}: {c['status']}" for c in R.owed(R.open_records(ws)))
    rv = R.completeness(answer, ws)
    assert rv.missing == [] and rv.prompt is None


def test_a_workspace_the_host_cannot_see_is_not_checked(tmp_path):
    assert R.completeness("Most likely: OOM.", tmp_path / "elsewhere") is None


def test_no_open_run_asks_for_one(tmp_path):
    rv = R.completeness("Most likely: OOM.", tmp_path)
    assert rv.opens == 0 and "without running `kb_query.py open`" in rv.prompt


class _Client:
    """Just enough of the sessions API for one revision turn."""

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def __init__(self):
        self.sent = []
        self.beta = self
        self.sessions = self
        self.events = self

    def stream(self, session_id):
        return self._Stream()

    def send(self, session_id, events):
        self.sent.append(events)
        return type("Sent", (), {"id": "sevt_1"})()


def test_the_revised_answer_replaces_the_first(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    run = runner.Run(scenario_id="jvm", answer="Most likely: OOM.")
    from k8srca.session import Turn
    monkeypatch.setattr(runner, "_consume_turn", lambda *a, **k: Turn(
        messages=["Most likely: OOM. memory leak: refuted. workload spike: refuted."],
        tool_calls=["bash"]))
    client = _Client()
    runner._revise(client, "sesn_1", run, type("W", (), {"stream": None})(),
                   R.completeness(run.answer, ws))
    [[msg]] = client.sent
    assert "memory-leak" in msg["content"][0]["text"]
    assert run.first_answer == "Most likely: OOM."
    assert "workload spike: refuted" in run.answer
    assert run.tool_calls == ["bash"] and run.review["missing"]


def test_nothing_found_sends_nothing(tmp_path):
    ws = workspace(tmp_path)
    answer = "\n".join(c["id"] for c in R.owed(R.open_records(ws)))
    run = runner.Run(scenario_id="jvm", answer=answer)
    client = _Client()
    runner._revise(client, "sesn_1", run, None, R.completeness(answer, ws))
    assert client.sent == [] and run.first_answer == "" and run.review["missing"] == []


# -- the reviewer -------------------------------------------------------
class _Messages:
    """A Messages client whose reply is a `report` call with these flags."""

    def __init__(self, flags=None, raises=None):
        self.flags, self.raises, self.calls = flags or [], raises, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.raises:
            raise self.raises
        block = type("B", (), {"type": "tool_use", "input": {"flags": self.flags}})()
        return type("M", (), {"content": [block], "stop_reason": "tool_use"})()


ANSWER = ("Most likely: OOM, confirmed. It has been crash-looping since the rollout "
          "163 days ago.")
LOOPING = {"quote": "crash-looping since the rollout 163 days ago", "kind": "unsupported",
           "why": "A pod's age is not an onset."}
CERTAIN = {"quote": "OOM, confirmed", "kind": "overstated",
           "why": "The reason is Error, not OOMKilled."}


def record(tmp_path, session="sesn_1"):
    d = tmp_path / "evidence" / session
    d.mkdir(parents=True)
    (d / "0001-k8s_get_workload_report.json").write_text(json.dumps(
        {"n": 1, "tool": "k8s_get_workload_report", "arguments": {"name": "ad"},
         "is_error": False, "text": ['{"restarts": 3200, "pod_age": "P163D"}']}))
    return tmp_path


def test_the_reviewer_reads_the_answer_against_the_record(tmp_path):
    client = _Messages([LOOPING])
    flags = R.reviewer(client, ANSWER, record(tmp_path) / "evidence" / "sesn_1", "m")
    assert flags == [LOOPING]
    [call] = client.calls
    text = call["messages"][0]["content"]
    assert "[1] k8s_get_workload_report" in text and "3200" in text and ANSWER in text
    assert "not an onset" in call["system"]


def test_a_flag_quoting_nothing_in_the_answer_is_dropped(tmp_path):
    made_up = dict(LOOPING, quote="the node ran out of memory")
    assert R.reviewer(_Messages([made_up]), ANSWER, tmp_path, "m") == []


def test_a_failed_review_holds_nothing_back(tmp_path):
    assert R.reviewer(_Messages(raises=RuntimeError("529 overloaded")), ANSWER,
                      tmp_path, "m") is None


def test_overstated_asks_for_certainty_not_removal():
    """The offline eval's reviewer disputed the right cause; an answer that
    dropped it would be worse off (007 §6.1)."""
    prompt = R.Review(opens=None, flags=[LOOPING, CERTAIN]).prompt
    unsupported, certainty = prompt.split("more certainty")
    assert LOOPING["quote"] in unsupported and "remove it" in unsupported
    assert CERTAIN["quote"] in certainty and "Keep each claim" in certainty


def test_check_runs_what_the_config_turns_on(tmp_path):
    from k8srca.config import ReviewConfig
    ws = record(tmp_path)
    off = R.check(ANSWER, ws, "sesn_1", ReviewConfig(completeness=False), _Messages([LOOPING]))
    assert off is None
    only = R.check(ANSWER, ws, "sesn_1", ReviewConfig(completeness=False, reviewer=True),
                   _Messages([LOOPING]))
    assert only.opens is None and only.flags == [LOOPING]
    assert "without running" not in only.prompt
    both = R.check(ANSWER, ws, "sesn_1", ReviewConfig(reviewer=True), _Messages([]))
    assert both.opens == 0 and both.flags == [] and "without running" in both.prompt
