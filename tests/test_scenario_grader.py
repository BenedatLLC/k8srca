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


class TestScenarioStateIsolation:
    """A scenario run must not touch production's skill or agent.

    Agents reference skills at "latest" (sync.resolve_skills), so publishing a
    scenario's frozen cluster as a new version of the production skill repoints
    the Slack bot at it, silently. This happened once.
    """

    def _prod(self):
        from k8srca.state import AgentState, SkillState, State
        return State(
            environment_id="env_prod",
            agents={"rca-coordinator": AgentState("agent_prod_coord", 12, "m", "sonnet"),
                    "k8s-investigator": AgentState("agent_prod_inv", 9, "m", "haiku")},
            skills={"cluster-architecture": SkillState("skill_prod_arch", "v1", "d1"),
                    "k8s-rca": SkillState("skill_prod_kb", "v1", "d2")},
        )

    def test_the_first_run_does_not_inherit_the_production_skill(self, tmp_path):
        """The bug: an empty scenario file left production's id in place, so the
        upload added a version to production's skill instead of making one."""
        from k8srca.scenario.runner import scenario_state

        scoped = scenario_state(self._prod(), "env_scenario", tmp_path / "state.json")
        assert "cluster-architecture" not in scoped.skills

    def test_the_first_run_does_not_inherit_the_production_coordinator(self, tmp_path):
        from k8srca.scenario.runner import scenario_state

        scoped = scenario_state(self._prod(), "env_scenario", tmp_path / "state.json")
        assert "rca-coordinator" not in scoped.agents

    def test_unscoped_objects_are_inherited(self, tmp_path):
        """The KB is the method under test, and the investigator carries no
        cluster view -- both stay production's."""
        from k8srca.scenario.runner import scenario_state

        scoped = scenario_state(self._prod(), "env_scenario", tmp_path / "state.json")
        assert scoped.skills["k8s-rca"].skill_id == "skill_prod_kb"
        assert scoped.agents["k8s-investigator"].id == "agent_prod_inv"

    def test_a_later_run_reuses_the_scenarios_own_objects(self, tmp_path):
        from k8srca.scenario.runner import scenario_state
        from k8srca.state import AgentState, SkillState, State

        path = tmp_path / "state.json"
        State(environment_id="env_scenario",
              agents={"rca-coordinator": AgentState("agent_scn_coord", 3, "m", "sonnet")},
              skills={"cluster-architecture": SkillState("skill_scn_arch", "v7", "d9")},
              ).save(path)
        scoped = scenario_state(self._prod(), "env_scenario", path)
        assert scoped.skills["cluster-architecture"].skill_id == "skill_scn_arch"
        assert scoped.agents["rca-coordinator"].id == "agent_scn_coord"

    def test_the_scenario_environment_is_used(self, tmp_path):
        from k8srca.scenario.runner import scenario_state

        scoped = scenario_state(self._prod(), "env_scenario", tmp_path / "state.json")
        assert scoped.environment_id == "env_scenario"


class TestBystanderLogs:
    """Every pod keeps a log tail (004 §6.2).

    Dropping healthy pods' logs made claims about them unverifiable, and the
    grader reported unverifiable as unsupported -- so the evidence dimension
    read 0/3 across an n=3 run while measuring this scoping choice rather than
    the agent.
    """

    def test_a_healthy_pod_keeps_a_logs_mapping(self):
        """Present-and-empty, not absent.

        checkout's logs really are empty in this capture, and that is itself a
        fact worth being able to check -- "nothing was logged" and "I was not
        shown the logs" are different claims.
        """
        d = digest(CAPTURE)
        pod = next(p for p in d["pods"] if p["summary"]["name"] == HEALTHY)
        assert isinstance(pod["logs"], dict) and "checkout" in pod["logs"]

    def test_a_bystander_with_real_logs_keeps_them(self):
        capture = json.loads(json.dumps(CAPTURE))
        pod = next(p for p in capture["pods"] if p["summary"]["name"] == HEALTHY)
        pod["logs"]["checkout"] = "\n".join(f"line {i}" for i in range(50))
        d = digest(capture)
        got = next(p for p in d["pods"] if p["summary"]["name"] == HEALTHY)
        assert got["logs"]["checkout"].strip().endswith("line 49")

    def test_a_bystanders_tail_is_shorter_than_a_relevant_pods(self):
        capture = json.loads(json.dumps(CAPTURE))
        for pod in capture["pods"]:
            for name in (pod.get("logs") or {}):
                pod["logs"][name] = "\n".join(f"line {i}" for i in range(100))
        d = digest(capture, log_lines=25, bystander_log_lines=8)
        relevant = next(p for p in d["pods"] if p["summary"]["name"] == AD)
        bystander = next(p for p in d["pods"] if p["summary"]["name"] == HEALTHY)
        assert len(list(relevant["logs"].values())[0].splitlines()) > \
               len(list(bystander["logs"].values())[0].splitlines())

    def test_a_claim_about_a_bystanders_logs_is_now_checkable(self):
        """"flagd's logs are clean" must be answerable from the digest."""
        d = digest(CAPTURE)
        assert all(isinstance(p["logs"], dict) for p in d["pods"])


class TestClaimSplit:
    def test_unverifiable_claims_do_not_fail_evidence(self):
        """A kubelet timing quirk is not a fabrication."""
        g = Grade(cause_correct=True, cause_note="", evidence_supported=True,
                  evidence_note="",
                  unverifiable_claims=["the JVM typically needs 400Mi"])
        assert g.dimensions()["evidence"] is True

    def test_unsupported_claims_still_fail_evidence(self):
        g = Grade(cause_correct=True, cause_note="", evidence_supported=True,
                  evidence_note="", unsupported_claims=["pod zz-1 is crash-looping"])
        assert g.dimensions()["evidence"] is False
