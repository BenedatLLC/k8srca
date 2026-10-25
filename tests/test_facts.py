"""The facts layer (design 010 §3): observations stated by code from tool results.

The fixtures are real results: `k8srca scenario record-results` replays each
scenario's capture through k8stools (and promtools, where it has metrics) and
saves what a triage reads first into `tests/scenarios/<id>/results/`.
"""

import asyncio
import functools
import json
import re
import subprocess
import sys
from pathlib import Path

import mcp.types as mt
import pytest

from k8srca.evidence import EvidenceRecord
from k8srca.facts import DERIVATIONS, Fact, FactLog, block, derive
from k8srca.facts.model import human, seconds
from k8srca.tools import wrap_mcp_tool

SCENARIOS = Path("tests/scenarios")
SKILL = Path("skills/k8s-rca")


def results(scenario: str) -> list[dict]:
    """The recorded results, less errors (a pod an event names may be gone),
    which state nothing."""
    return [e for p in sorted((SCENARIOS / scenario / "results").glob("*.json"))
            if not (e := json.loads(p.read_text())).get("is_error")]


@functools.cache
def facts_of(scenario: str) -> list[Fact]:
    out = []
    for e in results(scenario):
        r = e.get("structured") or json.loads(e["text"][0])
        out += derive(e["tool"].split("_", 1)[1], e["arguments"], r)
    return out


def about(facts, subject, key):
    return [f for f in facts if f.subject == subject and f.key == key]


def log_of(scenario: str, workdir: Path, only: set[str] | None = None) -> FactLog:
    log = FactLog(workdir)
    for e in results(scenario):
        if only is not None and e["tool"] not in only:
            continue
        r = e.get("structured") or json.loads(e["text"][0])
        log.add(derive(e["tool"].split("_", 1)[1], e["arguments"], r),
                {"tool": e["tool"], "n": e["n"]})
    return log


ALL = sorted(p.parent.name for p in SCENARIOS.glob("*/results"))


class TestFixtures:
    def test_every_derivation_has_a_recorded_result(self):
        tools = {e["tool"].split("_", 1)[1] for s in ALL for e in results(s)}
        assert set(DERIVATIONS) <= tools

    @pytest.mark.parametrize("scenario", ALL)
    def test_every_fact_is_a_sentence_and_its_kb_lines_are_the_vocabulary(self, scenario):
        sys.path.insert(0, str(SKILL))
        import kb_query as Q

        K = Q.load()["knowledge"]
        for f in facts_of(scenario):
            assert f.statement and f.statement[0].isalnum(), f
            _, problems = Q.parse_facts(K, "\n".join(f.kb), derived=True)
            assert not problems, (f.statement, problems)


class TestJvm:
    """jvm-oom-on-startup: ad, exit 137 with reason Error, no metrics."""

    @pytest.fixture
    def facts(self):
        return facts_of("jvm-oom-on-startup")

    def test_the_last_termination_is_stated_with_its_scope(self, facts):
        [f] = about(facts, "default/Deployment/ad/ad", "last_termination")
        assert f.kb == ["last_exit_code[C, 137]", "last_termination_reason[C, Error]"]
        assert "exit 137" in f.statement and "8s" in f.statement
        assert "keeps no earlier ones" in f.scope

    def test_every_retained_termination_was_a_kill(self, facts):
        """The fact the agent wrote by hand from the event span (010 §3.3)."""
        [f] = about(facts, "default/Deployment/ad/ad", "killed_in_every_retained_life")
        assert f.kb == ["killed_in_every_retained_life[C]"]
        assert "3d20h" in f.statement

    def test_a_series_that_starts_inside_the_record_is_not_every_life(self, facts):
        """accounting's back-off starts 3d8h ago in a 3d20h record."""
        assert not about(facts, "default/Deployment/accounting/accounting",
                         "killed_in_every_retained_life")

    def test_onset_is_not_visible(self, facts):
        [f] = about(facts, "default/Deployment/ad", "onset")
        assert f.kb == ["not onset_observed[W]"]

    def test_the_spec_facts(self, facts):
        C = "default/Deployment/ad/ad"
        assert about(facts, C, "memory_limit")[0].kb == ["limit_equals_request[C, memory]"]
        assert about(facts, C, "probes")[0].kb == ["not liveness_probe_configured[C]"]
        [sizing] = about(facts, C, "runtime_sizing")
        assert sizing.kb == ["not runtime_sizing_flags_set[C]"]
        # JAVA_TOOL_OPTIONS comes from the image, which the spec does not show:
        # the agent saw it in the log ("Picked up JAVA_TOOL_OPTIONS").
        assert "image" in sizing.scope

    def test_a_restart_count_is_not_a_rate(self, facts):
        [f] = about(facts, "default/Deployment/ad/ad", "restarts")
        assert "3200" in f.statement and "not a rate" in f.scope

    def test_horizons_are_facts(self, facts):
        """Every source's horizon is stated once, by code (010 §3.2)."""
        [ev] = about(facts, "default/events/horizon", "events_horizon")
        assert ev.statement == "Warning events in default go back 3d20h"
        assert "not when any problem began" in ev.scope
        [logs] = about(facts, "default/Deployment/ad/ad", "log_horizon")
        assert "previous one only" in logs.statement


class TestStuckRollout:
    """itbench-33: two pods pending behind ready replicas."""

    @pytest.fixture
    def facts(self):
        return facts_of("itbench-33-invalid-node-selector")

    def test_the_stuck_workloads_are_not_counted_ready(self, facts):
        """Before k8stools 4.1.0 namespace health listed ad and cart ready while
        their new pods could not schedule (#27): "every other workload is
        healthy" was the trap. Now they are unhealthy, with their pods."""
        [ready] = about(facts, "otel-demo/namespace", "workloads_ready")
        assert ready.value == 19 and "no pod waiting for a node" in ready.statement
        for name in ("ad", "cart"):
            # From namespace health and from the workload report: the log keeps one.
            found = about(facts, f"otel-demo/Deployment/{name}", "pods_unscheduled")
            assert found and all(f.same(found[0]) for f in found)
            assert "not scheduled for" in found[0].statement
            assert "1/1" in found[0].scope

    def test_the_pending_pods_and_their_selector(self, facts):
        assert about(facts, "otel-demo/Deployment/ad", "pod_unschedulable")
        [sel] = about(facts, "otel-demo/Deployment/ad", "node_selector")
        assert "kubernetes.io/hostname=invalid-node" in sel.statement


class TestUsage:
    def test_cpu_from_the_same_sample_as_memory(self):
        """checkout-gc-pressure: the sample carries CPU, with no request or limit."""
        facts = facts_of("checkout-gc-pressure")
        [cpu] = about(facts, "default/Deployment/checkout/checkout", "cpu_usage")
        assert cpu.statement.startswith("checkout used 118m CPU in a sample")
        assert "no CPU request or limit" in cpu.statement


class TestMetrics:
    """jvm-oom-with-metrics: promtools' reports."""

    @pytest.fixture
    def facts(self):
        return facts_of("jvm-oom-with-metrics")

    def test_the_metrics_horizon_is_what_is_held(self, facts):
        """Not the 137h missing from the window, which answers read as the
        retention (009 §7.5)."""
        horizons = {f.statement for f in facts if f.key == "metrics_horizon"}
        assert horizons == {"metrics cover the last 30.9h of the 7d asked for"}

    def test_memory_at_the_limit_from_the_start_of_each_life(self, facts):
        [f] = about(facts, "default/Deployment/ad", "lives")
        assert f.kb == ["not memory_grows_within_life[C]"]
        assert "182" in f.statement and "214" in f.statement

    def test_lives_too_short_to_sample_show_no_shape(self, facts):
        [f] = about(facts, "default/Deployment/fraud-detection", "lives")
        assert f.kb == [] and "none shows a shape" in f.statement

    def test_killed_in_every_life_the_metrics_hold(self, facts):
        [f] = about(facts, "default/Deployment/ad/ad", "last_reason_share")
        assert f.kb == ["killed_in_every_retained_life[C]"]
        # accounting had six hours without a restart
        [acc] = about(facts, "default/Deployment/accounting/accounting", "last_reason_share")
        assert acc.kb == []


class TestLog:
    def test_ids_run_on_and_a_known_fact_keeps_its_id(self, tmp_path):
        log = FactLog(tmp_path)
        a = Fact("ns/Deployment/x", "k", 1, "x is one")
        b = Fact("ns/Deployment/x", "k", 2, "x is two")
        assert [f.id for f in log.add([a, b], {"tool": "t", "n": 1})] == ["F1", "F2"]
        again = log.add([Fact("ns/Deployment/x", "k", 1, "x is one")])
        assert again[0].id == "F1" and len(log.facts) == 2
        # another turn of the thread reads the same file
        assert [f.id for f in FactLog(tmp_path).add([Fact("ns/Deployment/x", "k", 3, "three")])] \
            == ["F3"]
        saved = [json.loads(line) for line in log.path.read_text().splitlines()]
        assert [s["id"] for s in saved] == ["F1", "F2", "F3"]
        assert saved[0]["source"] == {"tool": "t", "n": 1}

    def test_the_block_the_agent_reads(self):
        f = Fact("s", "k", 1, "ad restarted 3 times", scope="a count, not a rate", id="F4")
        assert block([f]) == "facts (cite as [F<n>]):\n[F4] ad restarted 3 times (a count, not a rate)"
        assert block([]) == ""

    def test_a_derivation_that_fails_leaves_the_call_alone(self, tmp_path, monkeypatch):
        import k8srca.facts.log as L

        monkeypatch.setattr(L, "derive", lambda *a: 1 / 0)
        assert FactLog(tmp_path).observe("k8s_get_events", "get_events", {}, {"result": []}) == []


class TestOpen:
    """`kb_query.py open --workload`: the standing from derived facts (010 §9,
    step 1's done criterion)."""

    def run(self, *args):
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), *args],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    def leads(self, out):
        line = next(ln for ln in out.splitlines() if ln.startswith("most likely:"))
        return {c: g.upper() for c, g in re.findall(r"\[([\w-]+)\], ([\w-]+)", line)}

    HAND = """\
last_exit_code[C, 137]
last_termination_reason[C, Error]
not process_logged_fatal_error[C]
killed_in_every_retained_life[C]
not completes_startup_in_retained_lives[C]
limit_equals_request[C, memory]
not runtime_sizing_flags_set[C]
"""

    @pytest.mark.parametrize("scenario", ["jvm-oom-on-startup", "jvm-oom-with-metrics"])
    def test_derived_facts_reach_the_hand_written_leader(self, tmp_path, scenario):
        hand = tmp_path / "ad.facts"
        hand.write_text(self.HAND)
        want = self.leads(self.run("open", "CrashLoopBackOff", "--facts", str(hand)))
        assert want["oom-kill"] == want["memory-limit-too-low"] == "STRONG"

        log = log_of(scenario, tmp_path / scenario).path
        # What the agent reads from the logs, which no result field states (010 §3.4).
        for kb, says in (("not process_logged_fatal_error[C]", "no exception before it stops"),
                         ("not completes_startup_in_retained_lives[C]", "stops before listening")):
            self.run("fact", "default/ad", kb, "--says", says, "--facts-log", str(log))
        got = self.leads(self.run("open", "CrashLoopBackOff", "--workload", "ad",
                                  "--facts-log", str(log)))
        assert got["oom-kill"] == got["memory-limit-too-low"] == "STRONG"
        # What rests on a recorded fact cites its id, for the answer to use.
        out = self.run("open", "CrashLoopBackOff", "--workload", "ad", "--facts-log", str(log))
        assert re.search(r"memory-limit-too-low rests on: \[F\d+\]", out)

    def test_an_agent_fact_must_be_in_the_vocabulary(self, tmp_path):
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "fact", "default/ad",
                            "ad_is_sad[C]", "--says", "x", "--facts-log",
                            str(tmp_path / "f.jsonl")], capture_output=True, text=True)
        assert r.returncode == 1 and "no fact 'ad_is_sad'" in r.stdout

    def test_the_agent_cannot_record_onset_by_hand(self, tmp_path):
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "fact", "default/ad",
                            "not onset_observed[W]", "--says", "x", "--facts-log",
                            str(tmp_path / "f.jsonl")], capture_output=True, text=True)
        assert r.returncode == 1 and "not recorded by hand" in r.stdout


class TestWorkerHook:
    TOOL = mt.Tool(name="get_events", description="events",
                   input_schema={"type": "object",
                                 "properties": {"namespace": {"type": "string"}}})

    class Session:
        def __init__(self, result):
            self.result = result

        async def call_tool(self, name, arguments):
            return self.result

    def result(self):
        rows = {"result": [{"reason": "BackOff", "object": "Pod/ad-5547bd5bd9-v65gj",
                            "count": 3, "first_seen": "PT1H", "last_seen": "PT1M"}]}
        return mt.CallToolResult(content=[mt.TextContent(type="text", text=json.dumps(rows))],
                                 structured_content=rows)

    def test_facts_go_above_the_result_and_into_the_log(self, tmp_path):
        log = FactLog(tmp_path)
        tool = wrap_mcp_tool(self.TOOL, self.Session(self.result()), prefix="k8s_",
                             record=EvidenceRecord(tmp_path, "s"), facts=log)
        out = asyncio.run(tool.call({"namespace": "default"}))
        assert out[0]["text"].startswith(
            "result 1\nfacts (cite as [F<n>]):\n[F1] events in default go back 1h")
        assert json.loads(out[1]["text"])["result"][0]["count"] == 3
        assert log.facts[1].source == {"tool": "k8s_get_events", "n": 1}

    def test_a_result_with_no_facts_still_carries_its_number(self, tmp_path):
        """A log derives nothing, and is where the agent's own facts come from:
        `kb_query.py fact --from` cites the number (010 §3.4)."""
        empty = mt.CallToolResult(content=[mt.TextContent(type="text", text="a log line")])
        tool = wrap_mcp_tool(self.TOOL, self.Session(empty), prefix="k8s_",
                             record=EvidenceRecord(tmp_path, "s"), facts=FactLog(tmp_path))
        out = asyncio.run(tool.call({"namespace": "default"}))
        assert out[0]["text"] == "result 1" and out[1]["text"] == "a log line"

    def test_the_facts_command_lists_ids_to_cite(self, tmp_path):
        log = FactLog(tmp_path)
        log.add([Fact("default/Deployment/ad/ad", "k", 1, "ad: one", scope="s"),
                 Fact("default/Deployment/cart/cart", "k", 1, "cart: one")])
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "facts",
                            "--workload", "ad", "--facts-log", str(log.path)],
                           capture_output=True, text=True)
        assert r.stdout == "[F1] ad: one (s)\n"

    def test_without_a_log_the_result_is_unchanged(self, tmp_path):
        tool = wrap_mcp_tool(self.TOOL, self.Session(self.result()), prefix="k8s_")
        out = asyncio.run(tool.call({"namespace": "default"}))
        assert len(out) == 1 and "facts" not in out[0]["text"][:10]

    def test_off_by_default(self):
        from k8srca.config import Config

        assert Config.model_fields["facts"].default_factory().enabled is False


def test_durations():
    assert seconds("P3DT20H22M22S") == 3 * 86400 + 20 * 3600 + 22 * 60 + 22
    assert seconds("PT8S") == 8 and seconds("nonsense") is None
    assert human(seconds("P3DT20H22M22S")) == "3d20h"
    assert human(seconds("PT9M22S")) == "9m22s" and human(8) == "8s"
