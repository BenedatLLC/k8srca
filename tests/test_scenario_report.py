"""Suite reporting (design 004 §6.4).

Pass rates against a baseline, not a score. The distinctions that matter are
between a dimension that failed, one the scenario never exercised, and one that
disagreed with itself across runs -- collapsing any pair of those produces a
report nobody can act on.
"""

from dataclasses import dataclass, field
from pathlib import Path

from k8srca.scenario import report as rp
from k8srca.scenario.checks import CheckResult, Finding
from k8srca.scenario.grader import Grade


@dataclass
class FakeRun:
    scenario_id: str = "s"
    tool_calls: list = field(default_factory=list)
    usd: float = 0.1
    checks: CheckResult = field(default_factory=CheckResult)
    grade: Grade | None = None


def grade(**kw) -> Grade:
    base = dict(cause_correct=True, cause_note="", evidence_supported=True,
                evidence_note="")
    return Grade(**{**base, **kw})


class TestAggregate:
    def test_a_dimension_that_always_passes_is_n_over_n(self):
        runs = [FakeRun(grade=grade()) for _ in range(3)]
        agg = rp.aggregate(runs)["s"]
        assert agg.cell("cause") == "3/3"

    def test_a_dimension_that_sometimes_passes_shows_the_rate(self):
        runs = [FakeRun(grade=grade(cause_correct=(i != 1))) for i in range(3)]
        assert rp.aggregate(runs)["s"].cell("cause") == "2/3"

    def test_an_unexercised_dimension_is_a_dash_not_a_zero(self):
        """A scenario with no traps has not failed traps 0/3."""
        agg = rp.aggregate([FakeRun(grade=grade()) for _ in range(3)])["s"]
        assert agg.cell("traps") == "-"

    def test_budget_failures_are_counted_separately(self):
        checks = CheckResult([Finding("budget", "too many calls")])
        agg = rp.aggregate([FakeRun(checks=checks, grade=grade())])["s"]
        assert agg.budget_failures == 1

    def test_tool_call_and_cost_ranges_are_kept(self):
        runs = [FakeRun(tool_calls=["a"] * n, usd=u)
                for n, u in ((10, 0.2), (30, 0.5))]
        agg = rp.aggregate(runs)["s"]
        assert agg.tool_calls == [10, 30] and agg.usd == [0.2, 0.5]


class TestVarianceNote:
    def test_a_split_dimension_is_flagged(self):
        runs = [FakeRun(grade=grade(cause_correct=(i != 1))) for i in range(3)]
        note = rp.variance_note(rp.aggregate(runs)["s"])
        assert note and "cause" in note

    def test_a_unanimous_dimension_is_not_flagged(self):
        runs = [FakeRun(grade=grade()) for _ in range(3)]
        assert rp.variance_note(rp.aggregate(runs)["s"]) is None

    def test_a_unanimous_failure_is_not_flagged_as_unstable(self):
        """0/3 is a finding, not noise -- it is exactly what a suite is for."""
        runs = [FakeRun(grade=grade(cause_correct=False)) for _ in range(3)]
        assert rp.variance_note(rp.aggregate(runs)["s"]) is None

    def test_a_single_run_cannot_be_unstable(self):
        assert rp.variance_note(rp.aggregate([FakeRun(grade=grade())])["s"]) is None


class TestBaseline:
    def test_a_round_trip_preserves_the_dimensions(self, tmp_path):
        aggs = rp.aggregate([FakeRun(grade=grade()) for _ in range(3)])
        path = tmp_path / "baseline.json"
        rp.save_baseline(aggs, path)
        assert rp.load_baseline(path)["s"]["dimension"]["cause"] == [3, 3]

    def test_a_missing_baseline_loads_as_none(self, tmp_path):
        assert rp.load_baseline(tmp_path / "nope.json") is None

    def test_an_unchanged_scenario_reports_no_delta(self, tmp_path):
        aggs = rp.aggregate([FakeRun(grade=grade()) for _ in range(3)])
        path = tmp_path / "b.json"
        rp.save_baseline(aggs, path)
        assert rp.delta(aggs["s"], rp.load_baseline(path)["s"]) == "="

    def test_a_regression_names_the_dimension(self, tmp_path):
        good = rp.aggregate([FakeRun(grade=grade()) for _ in range(3)])
        path = tmp_path / "b.json"
        rp.save_baseline(good, path)
        worse = rp.aggregate([FakeRun(grade=grade(cause_correct=(i != 0)))
                              for i in range(3)])
        assert "cause" in rp.delta(worse["s"], rp.load_baseline(path)["s"])

    def test_a_scenario_absent_from_the_baseline_is_new(self):
        aggs = rp.aggregate([FakeRun(grade=grade())])
        assert rp.delta(aggs["s"], None) == "new"


def test_the_table_has_a_row_per_scenario():
    runs = [FakeRun(scenario_id="a", grade=grade()),
            FakeRun(scenario_id="b", grade=grade())]
    lines = rp.table(rp.aggregate(runs))
    assert len(lines) == 4  # header, rule, two rows
    assert "a" in lines[2] and "b" in lines[3]


class TestPollerImagePinning:
    """A suite must not depend on the working tree holding still.

    The sandbox tag is a hash of the tree, each run spawns a fresh
    `k8srca poller`, and a suite takes tens of minutes. Left to derive its own
    tag, a poller started after an edit looks for an image nobody built -- an
    n=3 run returned one result this way.
    """

    def _argv(self, monkeypatch, tmp_path, **kw):
        import subprocess

        from k8srca.scenario import runner

        seen = {}

        class FakeProc:
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): pass
            def kill(self): pass

        def fake_popen(argv, **_):
            seen["argv"] = argv
            (tmp_path / "poller.log").write_text('{"event": "poller_start"}')
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        runner.start_poller(tmp_path / "state.json", network="net",
                            env_key_var="KEY", log=tmp_path / "poller.log", **kw)
        return seen["argv"]

    def test_a_pinned_image_is_passed_through(self, monkeypatch, tmp_path):
        argv = self._argv(monkeypatch, tmp_path, sandbox_image="k8srca/sandbox:abc123")
        assert "--image" in argv
        assert argv[argv.index("--image") + 1] == "k8srca/sandbox:abc123"

    def test_without_a_pin_the_poller_derives_its_own(self, monkeypatch, tmp_path):
        """Production keeps the existing behaviour; only the suite pins."""
        assert "--image" not in self._argv(monkeypatch, tmp_path)

    def test_the_scenario_environment_and_network_are_passed(self, monkeypatch, tmp_path):
        argv = self._argv(monkeypatch, tmp_path)
        assert argv[argv.index("--network") + 1] == "net"
        assert argv[argv.index("--env-key-var") + 1] == "KEY"
