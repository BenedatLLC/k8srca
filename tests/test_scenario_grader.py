"""Rubric grader, hermetic parts (design 004 §6.2).

Grading calls a model and costs money, so what is tested here is everything
around the call: what the grader is shown, what it is not, and how verdicts
fold into dimensions.
"""

import json
from pathlib import Path

import pytest

from k8srca.scenario.grader import Grade, GapVerdict, RivalVerdict, TrapVerdict, digest

CAPTURE = json.loads(Path("tests/fixtures/capture.json").read_text())
AD = "ad-5547bd5bd9-v65gj"
HEALTHY = "checkout-546dd7dbd9-6vbdp"


class TestDigest:
    def test_unhealthy_pods_keep_their_logs(self):
        d = digest(CAPTURE)
        pod = next(p for p in d["pods"] if p["summary"]["name"] == AD)
        assert isinstance(pod["logs"], dict) and pod["logs"]["ad"]

    def test_healthy_unmentioned_pods_drop_their_logs(self):
        """Twenty healthy pods' logs are most of the file and check nothing."""
        d = digest(CAPTURE)
        pod = next(p for p in d["pods"] if p["summary"]["name"] == HEALTHY)
        assert isinstance(pod["logs"], str) and "omitted" in pod["logs"]

    def test_a_dropped_log_says_so(self):
        """An omission must not read as an empty log -- a grader would treat
        "no logs" as evidence that nothing was written."""
        d = digest(CAPTURE)
        pod = next(p for p in d["pods"] if p["summary"]["name"] == HEALTHY)
        assert pod["logs"] != "" and "omitted" in pod["logs"]

    def test_a_mentioned_pod_keeps_its_logs(self):
        d = digest(CAPTURE, mentioned=f"the truth names {HEALTHY} explicitly")
        pod = next(p for p in d["pods"] if p["summary"]["name"] == HEALTHY)
        assert isinstance(pod["logs"], dict)

    def test_every_pod_keeps_its_structure(self):
        """Entity and numeric claims are checked against this, so it is kept
        for every pod regardless of whether the logs were."""
        d = digest(CAPTURE)
        assert len(d["pods"]) == len(CAPTURE["pods"])
        for pod in d["pods"]:
            assert pod["summary"] and pod["container_statuses"]

    def test_identical_previous_logs_become_a_marker(self):
        """During CrashLoopBackOff both calls serve the same instance; saying so
        is more useful than printing the same bytes twice."""
        d = digest(CAPTURE)
        pod = next(p for p in d["pods"] if p["summary"]["name"] == AD)
        assert "identical to current logs" in pod["previous_logs"]["ad"]

    def test_long_logs_are_tailed_with_a_marker(self):
        capture = json.loads(json.dumps(CAPTURE))
        pod = next(p for p in capture["pods"] if p["summary"]["name"] == AD)
        pod["logs"]["ad"] = "\n".join(f"line {i}" for i in range(200))
        d = digest(capture, log_lines=5)
        got = next(p for p in d["pods"] if p["summary"]["name"] == AD)["logs"]["ad"]
        assert "195 earlier lines omitted" in got and got.endswith("line 199")

    def test_the_digest_is_much_smaller_than_the_capture(self):
        assert len(json.dumps(digest(CAPTURE))) < len(json.dumps(CAPTURE))


class TestDimensions:
    def _grade(self, **kw) -> Grade:
        base = dict(cause_correct=True, cause_note="", evidence_supported=True,
                    evidence_note="")
        return Grade(**{**base, **kw})

    def test_an_undispositioned_rival_fails_the_dimension(self):
        g = self._grade(rivals=[RivalVerdict(id="a", dispositioned=False,
                                             disposition_given="mentioned_only",
                                             matches_truth=False, note="")])
        assert g.dimensions()["rivals"] is False

    def test_a_dispositioned_rival_with_the_wrong_verdict_still_fails(self):
        """Naming a rival is not dispositioning it correctly."""
        g = self._grade(rivals=[RivalVerdict(id="a", dispositioned=True,
                                             disposition_given="weakened",
                                             matches_truth=False, note="")])
        assert g.dimensions()["rivals"] is False

    def test_unsupported_claims_fail_evidence_even_when_otherwise_supported(self):
        g = self._grade(unsupported_claims=["the pod restarted 9999 times"])
        assert g.dimensions()["evidence"] is False

    def test_absent_dimensions_are_none_not_false(self):
        """A scenario with no traps has not failed the traps dimension; §6.4
        reports '-' so a missing dimension cannot read as a regression."""
        dims = self._grade().dimensions()
        assert dims["traps"] is None and dims["rivals"] is None
        assert dims["restraint"] is None

    def test_all_handled_traps_pass(self):
        g = self._grade(traps=[TrapVerdict(id="t", handled=True, note="")])
        assert g.dimensions()["traps"] is True

    def test_an_unreported_gap_fails(self):
        g = self._grade(gaps=[GapVerdict(id="g", reported_unavailable=False, note="")])
        assert g.dimensions()["gaps"] is False


class TestReference:
    def test_the_reference_is_stable_across_answers(self):
        """It is the cached prefix -- varying it pays full price every run."""
        from k8srca.scenario.grader import build_request
        from k8srca.scenario.model import ScenarioDir

        sd = ScenarioDir("tests/scenarios/jvm-oom-on-startup")
        a = build_request(sd, "one answer", ["t"])
        b = build_request(sd, "an entirely different answer", ["u"])
        assert a["system"][1]["text"] == b["system"][1]["text"]
        assert a["system"][1]["cache_control"] == {"type": "ephemeral"}

    def test_the_architecture_skill_is_in_the_reference(self):
        """Without it every arch_query citation grades as a fabrication."""
        from k8srca.scenario.grader import build_request
        from k8srca.scenario.model import ScenarioDir

        sd = ScenarioDir("tests/scenarios/jvm-oom-on-startup")
        if sd.architecture() is None:
            pytest.skip("scenario has no architecture snapshot")
        assert "architecture_skill" in build_request(sd, "a", [])["system"][1]["text"]
