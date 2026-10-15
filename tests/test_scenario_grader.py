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

    def _with_spec(self) -> dict:
        capture = json.loads(json.dumps(CAPTURE))
        pod = next(p for p in capture["pods"] if p["summary"]["name"] == AD)
        pod["spec"] = {
            "node_selector": {"kubernetes.io/hostname": "invalid-node"},
            "dns_policy": "ClusterFirst",
            "affinity": None,
            "containers": [{
                "name": "ad",
                "resources": {"requests": {"cpu": "10m", "memory": "225Mi"}, "claims": None},
                "volume_mounts": [{"mount_path": "/var/run/secrets"}],
                "image_pull_policy": "IfNotPresent",
            }],
        }
        return capture

    def test_the_spec_carries_requests_and_scheduling(self):
        """k8stools reports empty requests in container statuses, so the spec
        is the only place a quoted request can be checked; without it three
        answers quoting the real figures were marked down as inventing them."""
        d = digest(self._with_spec())
        spec = next(p for p in d["pods"] if p["summary"]["name"] == AD)["spec"]
        assert spec["node_selector"] == {"kubernetes.io/hostname": "invalid-node"}
        assert spec["containers"][0]["resources"] == {
            "requests": {"cpu": "10m", "memory": "225Mi"}}

    def test_the_spec_drops_mounts_defaults_and_empties(self):
        d = digest(self._with_spec())
        spec = next(p for p in d["pods"] if p["summary"]["name"] == AD)["spec"]
        assert "dns_policy" not in spec and "affinity" not in spec
        assert set(spec["containers"][0]) == {"name", "resources"}


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

    def test_also_accept_reaches_the_grader_with_a_rule_for_it(self):
        """matches_truth is the grader's call, so the field is inert unless it
        is both in the reference and named in the rubric."""
        from k8srca.scenario.grader import build_request
        from k8srca.scenario.model import ScenarioDir

        sd = ScenarioDir("tests/scenarios/jvm-oom-on-startup")
        req = build_request(sd, "a", [])
        assert "also_accept" in req["system"][0]["text"]
        assert '"also_accept": [\n     "refuted"' in req["system"][1]["text"]

    def test_the_rubric_says_replayed_clock_times_are_re_anchored(self):
        """Two runs lost evidence for a correct 63s lifetime quoted in run-time
        clock times the grader then failed to find in the capture."""
        from k8srca.scenario.grader import SYSTEM
        assert "re-anchored" in SYSTEM and "durations are" in SYSTEM

    def test_the_rubric_does_not_claim_log_timestamps_are_verbatim(self):
        """True until k8stools 2.2.0, which re-anchors each line's kubelet
        timestamp. A rubric still saying "verbatim" fails every answer that
        quotes a log line's time correctly."""
        from k8srca.scenario.grader import SYSTEM
        assert "served verbatim" not in SYSTEM
        assert "start of each log line" in SYSTEM


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
        """The KB is the method under test: it stays production's."""
        from k8srca.scenario.runner import scenario_state

        scoped = scenario_state(self._prod(), "env_scenario", tmp_path / "state.json")
        assert scoped.skills["k8s-rca"].skill_id == "skill_prod_kb"

    def test_the_investigator_is_the_scenarios_own(self, tmp_path):
        """Its tools are declared from the k8stools it was synced against; the
        replay's can be newer, and the sandbox refuses a mismatch."""
        from k8srca.scenario.runner import scenario_state

        scoped = scenario_state(self._prod(), "env_scenario", tmp_path / "state.json")
        assert "k8s-investigator" not in scoped.agents

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


class TestLocalSkill:
    """`--local-skill`: measure a working-tree skill without touching production's."""

    class Client:
        def __init__(self):
            self.created, self.versioned = [], []
            outer = self

            class Versions:
                def create(self, skill_id, files):
                    outer.versioned.append(skill_id)
                    return type("V", (), {"id": f"v{len(outer.versioned) + 1}"})()

            class Skills:
                versions = Versions()

                def create(self, files, display_name):
                    outer.created.append(display_name)
                    return type("S", (), {"id": "skill_scn_kb", "latest_version_id": "v1"})()

            self.beta = type("B", (), {"skills": Skills()})()

    def skill(self, tmp_path, text="# k8s-rca"):
        d = tmp_path / "skills" / "k8s-rca"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(text)
        return d

    def prod(self):
        from k8srca.state import SkillState, State

        return State(environment_id="env_prod",
                     skills={"k8s-rca": SkillState("skill_prod_kb", "v1", "d2")})

    def test_its_own_object_never_a_version_of_productions(self, tmp_path):
        from k8srca.scenario.runner import scenario_state, upload_local_skill

        client, path = self.Client(), tmp_path / "scn" / "state.json"
        scoped = scenario_state(self.prod(), "env_scenario", path)
        upload_local_skill("k8s-rca", self.skill(tmp_path), scoped, client=client,
                           path=path, log=lambda _: None)
        assert client.created == ["k8s-rca"] and client.versioned == []
        assert scoped.skills["k8s-rca"].skill_id == "skill_scn_kb"

    def test_a_changed_skill_versions_the_scenarios_copy(self, tmp_path):
        from k8srca.scenario.runner import scenario_state, upload_local_skill

        client, path = self.Client(), tmp_path / "scn" / "state.json"
        for text in ("# one", "# two"):
            scoped = scenario_state(self.prod(), "env_scenario", path)
            upload_local_skill("k8s-rca", self.skill(tmp_path, text), scoped, client=client,
                               path=path, log=lambda _: None)
            scoped.save(path)
        assert client.created == ["k8s-rca"] and client.versioned == ["skill_scn_kb"]

    def test_the_next_run_without_it_inherits_productions_again(self, tmp_path):
        from k8srca.scenario.runner import scenario_state, upload_local_skill

        client, path = self.Client(), tmp_path / "scn" / "state.json"
        scoped = scenario_state(self.prod(), "env_scenario", path)
        upload_local_skill("k8s-rca", self.skill(tmp_path), scoped, client=client,
                           path=path, log=lambda _: None)
        scoped.save(path)
        again = scenario_state(self.prod(), "env_scenario", path)
        assert again.skills["k8s-rca"].skill_id == "skill_prod_kb"


class TestRestraint:
    """`restraint` applies where truth.yaml says, not where the grader infers it."""

    def _sd(self, restraint):
        from types import SimpleNamespace

        from k8srca.scenario.model import Restraint

        truth = SimpleNamespace(restraint=Restraint(note=restraint) if restraint else None)
        return SimpleNamespace(truth=truth)

    def _grade(self, **kw) -> Grade:
        return Grade(**{**dict(cause_correct=True, cause_note="", evidence_supported=True,
                               evidence_note=""), **kw})

    def test_a_verdict_on_an_ordinary_scenario_is_dropped(self):
        from k8srca.scenario.grader import settle_restraint

        g = settle_restraint(self._grade(restraint_ok=False), self._sd(None))
        assert g.dimensions()["restraint"] is None

    def test_a_missing_verdict_on_a_restraint_scenario_fails(self):
        from k8srca.scenario.grader import settle_restraint

        g = settle_restraint(self._grade(), self._sd("ad is crash-looping, unrelated"))
        assert g.dimensions()["restraint"] is False and "no restraint verdict" in g.restraint_note

    def test_a_verdict_on_a_restraint_scenario_stands(self):
        from k8srca.scenario.grader import settle_restraint

        g = settle_restraint(self._grade(restraint_ok=True), self._sd("unrelated"))
        assert g.dimensions()["restraint"] is True

    def test_truth_takes_a_restraint_block(self):
        from k8srca.scenario.model import Truth

        t = Truth.model_validate({"id": "x", "capture": {"captured_at": "t"},
                                  "cause": {"summary": "nothing explains it"},
                                  "restraint": {"note": "two unrelated crash loops"}})
        assert t.restraint.expect == "declined"


def test_the_digest_carries_the_usage_sample():
    """Usage figures an answer cites must be checkable (nothing-is-wrong, 2026-10-15)."""
    capture = {**CAPTURE, "metrics": {"available": True, "containers": [
        {"pod": "checkout-x", "container": "checkout", "memory": "19Mi"}]}}
    assert digest(capture)["metrics"]["containers"][0]["memory"] == "19Mi"


def test_the_digest_carries_rollout_history_without_label_churn():
    """A true claim from k8s_get_workload_report must be checkable (2026-10-15)."""
    capture = {**CAPTURE, "workload_histories": [{"kind": "Deployment", "name": "checkout",
               "config": [{"name": "big"}], "revisions": [{"revision": 2, "changes": [
                   {"field": "containers[checkout].env[GOMEMLIMIT]", "change": "added"}],
                   "metadata_changes": [{"field": "metadata.labels[x]"}]}]}]}
    history = digest(capture)["workload_histories"][0]
    assert history["revisions"][0]["changes"][0]["change"] == "added"
    assert "metadata_changes" not in history["revisions"][0] and "config" not in history


class TestMajority:
    """Several gradings of one answer, each dimension by vote (004 §6.4)."""

    def _g(self, *, evidence=True, cause=True, claims=0, note=""):
        return Grade(cause_correct=cause, cause_note=note, evidence_supported=evidence,
                     evidence_note="", unsupported_claims=[f"c{i}" for i in range(claims)])

    def test_each_dimension_takes_the_majority(self):
        from k8srca.scenario.grader import majority

        g = majority([self._g(evidence=False), self._g(), self._g(cause=False)])
        dims = g.dimensions()
        assert dims["evidence"] is True and dims["cause"] is True
        assert g.votes["evidence"] == [False, True, True]

    def test_unsupported_claims_fail_evidence_in_each_vote(self):
        from k8srca.scenario.grader import majority

        g = majority([self._g(claims=1), self._g(claims=2), self._g()])
        assert g.dimensions()["evidence"] is False

    def test_the_shown_member_agrees_with_the_verdict(self):
        """The notes printed must explain the verdict reported, not the dissent."""
        from k8srca.scenario.grader import majority

        g = majority([self._g(cause=False, note="dissent"), self._g(note="a"),
                      self._g(note="b", claims=3)])
        assert g.cause_correct and g.cause_note == "a"

    def test_a_tie_fails(self):
        from k8srca.scenario.grader import majority

        assert majority([self._g(evidence=False), self._g()]).dimensions()["evidence"] is False

    def test_an_absent_dimension_stays_absent(self):
        from k8srca.scenario.grader import majority

        assert majority([self._g(), self._g(), self._g()]).dimensions()["restraint"] is None

    def test_the_panel_size_is_part_of_the_grader(self):
        from k8srca.scenario.grader import apparatus_digest

        assert apparatus_digest(grades=1) != apparatus_digest(grades=3)

    def test_grade_runs_the_panel_and_votes(self, monkeypatch):
        from types import SimpleNamespace

        from k8srca.scenario import grader

        verdicts = iter([False, True, True])
        monkeypatch.setattr(grader, "build_request", lambda *a: {"system": "", "messages": []})
        client = SimpleNamespace(messages=SimpleNamespace(parse=lambda **kw: SimpleNamespace(
            parsed_output=self._g(evidence=next(verdicts)))))
        sd = SimpleNamespace(truth=SimpleNamespace(restraint=None))
        g = grader.grade(sd, "answer", [], client=client, grades=3)
        assert sorted(g.votes["evidence"]) == [False, True, True]
        assert g.dimensions()["evidence"] is True
