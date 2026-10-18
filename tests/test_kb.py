"""Knowledge-base normalization (design 002 §8)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import yaml

from k8srca.kb import build as build_mod
from k8srca.kb import knowledge
from k8srca.kb.build import build, split_values

SKILL = Path("skills/k8s-rca")
KB_JSON = SKILL / "knowledge_base.json"


class TestSplitValues:
    def test_splits_on_semicolon_and_trims(self):
        assert split_values("a; b ;c") == ["a", "b", "c"]

    def test_drops_empties(self):
        assert split_values("a;;b;") == ["a", "b"]

    def test_handles_missing(self):
        assert split_values(None) == [] and split_values("") == []


class TestBuild:
    def test_every_alert_keeps_its_hypotheses(self):
        data, report = build()
        crash = data["alerts"]["CrashLoopBackOff"]
        # The source packs several candidate causes into one ';'-joined field;
        # splitting them is what makes them discriminable.
        assert "OOM" in crash["hypotheses"]
        assert len(crash["hypotheses"]) == 4
        assert report.hypotheses > report.alerts

    def test_correlation_graph_is_symmetric(self):
        # The source records correlation one way only.
        data, _ = build()
        for name, a in data["alerts"].items():
            for other in a["related"]:
                assert name in data["alerts"][other]["related"], f"{name}<->{other} asymmetric"

    def test_unresolved_references_are_reported_not_dropped(self):
        # A dangling name is a typo in the source; silently dropping it leaves
        # a dead end nobody can see.
        _, report = build()
        assert any("NodeNetworkUnavailable" in u for u in report.unresolved)

    def test_group_is_not_used_as_a_causal_parent(self):
        # causal_parent is a coarse label: one value covers half the base, so
        # it must not be presented as a causal edge.
        data, _ = build()
        sizes = {g: len(v) for g, v in data["groups"].items()}
        assert max(sizes.values()) > 30  # the catch-all really is that big
        assert "related" in data["alerts"]["CrashLoopBackOff"]


class TestQueryTool:
    """kb_query.py must run standalone -- it ships inside the skill bundle."""

    def run(self, *args):
        return subprocess.run([sys.executable, str(SKILL / "kb_query.py"), *args],
                              capture_output=True, text=True)

    @pytest.mark.skipif(not KB_JSON.exists(), reason="knowledge_base.json not built")
    def test_lookup_reports_hypotheses_as_hypotheses(self):
        r = self.run("lookup", "CrashLoopBackOff")
        assert r.returncode == 0
        assert "HYPOTHESES" in r.stdout and "OOM" in r.stdout

    @pytest.mark.skipif(not KB_JSON.exists(), reason="knowledge_base.json not built")
    def test_unknown_alert_suggests_alternatives(self):
        r = self.run("lookup", "CrashLoop")
        assert r.returncode == 1 and "CrashLoopBackOff" in r.stdout

    @pytest.mark.skipif(not KB_JSON.exists(), reason="knowledge_base.json not built")
    def test_related_warns_when_graph_is_sparse(self):
        # 46 of 81 alerts have no correlations; silence would read as "nothing
        # is related", which is the wrong conclusion.
        data = json.loads(KB_JSON.read_text())
        isolated = next(n for n, a in data["alerts"].items() if not a["related"])
        r = self.run("related", isolated)
        assert "sparse" in r.stdout.lower()

    @pytest.mark.skipif(not KB_JSON.exists(), reason="knowledge_base.json not built")
    def test_search_finds_by_symptom_text(self):
        r = self.run("search", "memory")
        assert r.returncode == 0 and "match" in r.stdout


def k8stools_tool_names() -> set[str]:
    """The tools k8stools serves. Its tool library, imported for names only:
    nothing here calls a tool or loads a kubeconfig."""
    from k8stools.k8s_tools import TOOLSET_NAMES

    return set(TOOLSET_NAMES["all"])


MINIMAL = {
    "symptoms": {"restarting": {"title": "restarts", "claims": ["oom", "crash"]}},
    "alerts": {"CrashLoopBackOff": {"symptom": "restarting"}},
    "facts": {"exit_code": {"params": ["Container", "Code"], "says": "exit code is {Code}"},
              "growing": {"params": ["Container"], "says": "memory grows"}},
    "actions": {"read-state": {"requires": "k8stools", "call": "get_workload_report",
                               "produces": ["exit_code"]},
                "series": {"requires": "prometheus", "call": "query_range",
                           "produces": ["growing"]}},
    "claims": {"oom": {"title": "OOM", "confirm": ["exit_code[C, 137]"],
                       "refute": ["exit_code[C, X], X != 137"]},
               "crash": {"title": "crash", "confirm": ["growing[C]"],
                         "refute": ["not growing[C]"]}},
}


def write_layer(directory: Path, data: dict, name: str = "k.yaml") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(yaml.safe_dump(data))
    return directory


def with_changes(**changes) -> dict:
    import copy

    data = copy.deepcopy(MINIMAL)
    for path, value in changes.items():
        node = data
        *parents, leaf = path.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return data


def problems(tmp_path, data, **kw) -> list[str]:
    k = knowledge.load(write_layer(tmp_path / "k", data))
    return knowledge.check(k, **kw).problems


class TestKnowledgeChecks:
    """Design 007 §7: what the build refuses."""

    def test_a_sound_layer_checks_clean(self, tmp_path):
        assert problems(tmp_path, MINIMAL) == []

    def test_a_claim_with_no_refutation_is_refused(self, tmp_path):
        """A claim that can only be confirmed invites confirmation-seeking."""
        got = problems(tmp_path, with_changes(claims__oom__refute=[]))
        assert any("oom: no `refute`" in p for p in got)

    def test_a_fact_name_that_does_not_resolve_is_refused(self, tmp_path):
        got = problems(tmp_path, with_changes(claims__oom__confirm=["exit_cod[C, 137]"]))
        assert any("'exit_cod' does not resolve" in p for p in got)

    def test_the_wrong_number_of_arguments_is_refused(self, tmp_path):
        got = problems(tmp_path, with_changes(claims__oom__confirm=["exit_code[C]"]))
        assert any("takes 2" in p for p in got)

    def test_a_fact_no_action_produces_is_refused(self, tmp_path):
        """It would be a criterion nobody can check."""
        data = with_changes(facts__orphan={"params": ["C"], "says": "x"})
        assert any("orphan: no action produces it" in p for p in problems(tmp_path, data))

    def test_an_unknown_capability_is_refused(self, tmp_path):
        data = with_changes(actions__series__requires="prom")
        assert any("capability 'prom'" in p for p in problems(tmp_path, data))

    def test_a_k8stools_call_must_be_a_k8stools_tool(self, tmp_path):
        data = with_changes()
        data["actions"]["read-state"]["call"] = "get_workload_reprot"
        got = problems(tmp_path, data, known_tools=k8stools_tool_names())
        assert any("'get_workload_reprot' is not a k8stools tool" in p for p in got)

    def test_exclusive_claims_must_be_siblings(self, tmp_path):
        """Exclusion is between rivals; a claim and its refinement are not rivals."""
        data = with_changes(exclusive=[["oom", "sub"]])
        data["claims"]["sub"] = {"title": "sub", "confirm": ["growing[C]"],
                                 "refute": ["not growing[C]"]}
        data["claims"]["oom"]["refined_by"] = ["sub"]
        assert any("not siblings" in p for p in problems(tmp_path, data))

    def test_a_refinement_cycle_is_refused(self, tmp_path):
        data = with_changes()
        data["claims"]["oom"]["refined_by"] = ["crash"]
        data["claims"]["crash"]["refined_by"] = ["oom"]
        assert any("refinement cycle" in p for p in problems(tmp_path, data))

    def test_only_a_sub_cause_can_be_uncommon(self, tmp_path):
        data = with_changes()
        data["claims"]["crash"]["prevalence"] = "uncommon"
        assert any("refines nothing" in p for p in problems(tmp_path, data))
        data["claims"]["oom"]["refined_by"] = ["crash"]
        data["symptoms"]["restarting"]["claims"] = ["oom"]
        assert not any("refines nothing" in p for p in problems(tmp_path, data))

    def test_refines_alert_must_name_a_vendored_alert(self, tmp_path):
        data = with_changes()
        data["claims"]["oom"]["refines_alert"] = "NoSuchAlert"
        got = problems(tmp_path, data, legacy_alerts={"OOMKilled"})
        assert any("'NoSuchAlert' is not a knowledge-base alert" in p for p in got)

    def test_a_name_defined_in_two_files_is_refused(self, tmp_path):
        d = write_layer(tmp_path / "k", MINIMAL, "a.yaml")
        write_layer(d, {"facts": {"growing": {"params": ["C"], "says": "y"}}}, "b.yaml")
        with pytest.raises(knowledge.KnowledgeError, match="defined in both"):
            knowledge.load(d)

    def test_prose_conditions_are_counted_not_refused(self, tmp_path):
        data = with_changes(claims__oom__confirm=["the heap grows until it dies"])
        k = knowledge.load(write_layer(tmp_path / "k", data))
        r = knowledge.check(k)
        assert r.problems == [] and r.prose == ["oom: the heap grows until it dies"]

    def test_a_claim_only_an_absent_capability_can_decide_is_reported(self, tmp_path):
        """007 §4.2: it starts as could-not-check, and the build says which."""
        data = with_changes(claims__crash__refute=["growing[C]"])
        k = knowledge.load(write_layer(tmp_path / "k", data))
        assert knowledge.check(k).undecidable == ["crash"]

    def test_the_build_fails_on_a_problem(self, tmp_path):
        d = write_layer(tmp_path / "k", with_changes(claims__oom__refute=[]))
        with pytest.raises(knowledge.KnowledgeError):
            build_mod.build(knowledge_dir=d)


def timed() -> dict:
    """MINIMAL with a claim that blames a change (007 §3.4)."""
    data = with_changes()
    data["facts"]["onset_observed"] = {"params": ["W"], "says": "the onset is observed"}
    data["facts"]["changed_before_onset"] = {"params": ["T", "Kind"],
                                             "says": "{Kind} changed just before the onset"}
    data["actions"]["read-events"] = {"requires": "k8stools", "call": "get_events",
                                      "produces": ["onset_observed"]}
    data["actions"]["read-history"] = {"requires": "k8stools", "call": "get_workload_history",
                                       "produces": []}
    data["actions"]["audit"] = {"requires": "audit-log", "produces": []}
    data["changes"] = {"image": {"title": "the image", "takes_effect": "as pods roll",
                                 "seen_by": ["read-history"]},
                       "config": {"title": "a ConfigMap", "takes_effect": "at next read",
                                  "seen_by": ["audit"]}}
    data["symptoms"]["restarting"]["claims"].append("release")
    data["claims"]["release"] = {
        "title": "release",
        "confirm": [["onset_observed[W]", "changed_before_onset[W, image]"]],
        "refute": [["onset_observed[W]", "not changed_before_onset[W, image]"]]}
    return data


class TestTiming:
    """007 §3.4: a change is evidence only against an observed onset."""

    def test_a_sound_timed_claim_checks_clean(self, tmp_path):
        assert problems(tmp_path, timed()) == []

    def test_a_change_without_the_onset_is_refused(self, tmp_path):
        """What let a revision 163 days old be 'confirmed as the trigger'."""
        data = timed()
        data["claims"]["release"]["confirm"] = ["changed_before_onset[W, image]"]
        assert any("without onset_observed" in p for p in problems(tmp_path, data))

    def test_a_negated_onset_does_not_count(self, tmp_path):
        data = timed()
        data["claims"]["release"]["refute"] = [
            ["not onset_observed[W]", "not changed_before_onset[W, image]"]]
        assert any("without onset_observed" in p for p in problems(tmp_path, data))

    def test_a_change_kind_must_resolve(self, tmp_path):
        data = timed()
        data["claims"]["release"]["confirm"] = [
            ["onset_observed[W]", "changed_before_onset[W, imgae]"]]
        assert any("'imgae', which does not resolve" in p for p in problems(tmp_path, data))

    def test_a_change_is_not_a_support(self, tmp_path):
        data = timed()
        data["claims"]["oom"]["supports"] = ["changed_before_onset[W, image]"]
        assert any("belongs in a confirm alternative" in p for p in problems(tmp_path, data))

    def test_a_coined_timing_fact_is_refused(self, tmp_path):
        data = timed()
        data["facts"]["deployment_change_precedes_restart_spike"] = {
            "params": ["W"], "says": "a rollout landed just before the restarts"}
        data["actions"]["read-history"]["produces"] = ["deployment_change_precedes_restart_spike"]
        assert any("looks like a timing fact" in p for p in problems(tmp_path, data))

    def test_an_action_names_its_kinds_rather_than_producing_the_change(self, tmp_path):
        data = timed()
        data["actions"]["read-history"]["produces"] = ["changed_before_onset"]
        assert any("seen_by" in p for p in problems(tmp_path, data))

    def test_a_kind_is_observable_only_by_what_sees_it(self, tmp_path):
        """Rollout history cannot see a ConfigMap edit: a config change is a gap
        with k8stools alone even though k8stools sees image changes."""
        data = timed()
        data["claims"]["release"]["confirm"] = [
            ["onset_observed[W]", "changed_before_onset[W, config]"]]
        data["claims"]["release"]["refute"] = [
            ["onset_observed[W]", "not changed_before_onset[W, config]"]]
        k = knowledge.load(write_layer(tmp_path / "k", data))
        assert knowledge.check(k).undecidable == ["crash", "release"]


class TestShippedKnowledge:
    """What is in docs/rca/knowledge/ now."""

    def test_it_checks_clean_against_the_real_tool_names(self):
        k = knowledge.load()
        data, _ = build_mod.build()
        r = knowledge.check(k, legacy_alerts=set(data["alerts"]),
                            known_tools=k8stools_tool_names())
        assert r.problems == []
        assert r.prose == []

    def test_the_conversion_dropped_no_candidate(self):
        """Every vendored hypothesis of a converted alert is in `converts`, as a
        claim the alert's symptom reaches or a reasoned decline (007 §8.5). A
        candidate lost in conversion is one the agent stops disposing of -- the
        failure this whole layer exists for. The build enforces it; this pins
        that the shipped knowledge passes, and that nothing is declined yet."""
        data, report = build_mod.build()
        assert {"CrashLoopBackOff", "OOMKilled"} <= set(report.converted)
        k = knowledge.load_layers([knowledge.MINED, knowledge.KNOWLEDGE])
        hyps = {n: a["hypotheses"] for n, a in data["alerts"].items()}
        assert knowledge.check(k, legacy_alerts=set(hyps), legacy_hypotheses=hyps).problems == []
        for alert in ("CrashLoopBackOff", "OOMKilled"):
            assert set(k.layer.converts[alert]) == set(hyps[alert])
            assert all(isinstance(c, str) for c in k.layer.converts[alert].values())

    def test_oom_narrows_into_the_kinds_of_oom(self):
        """`refines` from discriminators.yaml, carried over: accepting "OOM" opens
        which kind, rather than ending the investigation at the family name."""
        K = build_mod.build()[0]["knowledge"]
        assert set(K["claims"]["oom-kill"]["refined_by"]) == set(
            K["symptoms"]["container-oom-killed"]["claims"])
        # LivenessProbeFailed is converted now (mined, 007 §8.5): its causes are
        # refinements, not a pointer to the vendored record.
        assert K["claims"]["failed-liveness-probe"]["refines_alert"] is None
        assert "probe-too-aggressive" in K["claims"]["failed-liveness-probe"]["refined_by"]

    def test_oom_is_decided_by_the_exit_code_not_the_reason(self):
        """The authored discriminator keyed on exit 137. Keying on `reason`
        would pre-decide the discrepancy scenario 1 exists to measure."""
        c = knowledge.load().layer.claims["oom-kill"]
        assert "last_exit_code[C, 137]" in c.confirm[0]
        assert "reason" not in yaml.safe_dump(c.model_dump()).lower()

    def test_no_claim_pre_decides_the_question_it_asks(self):
        """A claim says what evidence would decide a question. It must not
        resolve one in advance.

        Learned twice, at a batch each. A note explaining that
        `lastState.terminated.reason` is unreliable -- true, and generic -- drove
        `traps` from 6/6 to 4/6 both when it sat in a cluster's architecture
        notes and when it sat in the discriminators. Telling the agent a
        discrepancy is expected removes its reason to report that this instance
        shows one. Mined text included: a guide is as likely to say it as we are.
        """
        k = knowledge.load_layers([knowledge.MINED, knowledge.KNOWLEDGE])
        texts = [c.note for c in k.layer.claims.values()]
        texts += [f.says for f in k.layer.facts.values()]
        texts += [a.read + a.limits for a in k.layer.actions.values()]
        for text in texts:
            for phrase in ("is fully consistent with", "is not reliably",
                           "treat the exit code as the signal"):
                assert phrase not in text.lower(), phrase


class TestShippedMined:
    """What is in docs/rca/mined/ now (007 §8)."""

    def test_the_layers_check_clean_against_the_real_tool_names(self):
        data, _ = build_mod.build()
        k = knowledge.load_layers([knowledge.MINED, knowledge.KNOWLEDGE])
        hyps = {n: a["hypotheses"] for n, a in data["alerts"].items()}
        r = knowledge.check(k, legacy_alerts=set(hyps), legacy_hypotheses=hyps,
                            known_tools=k8stools_tool_names())
        assert r.problems == []

    def test_every_mined_item_quotes_its_source(self):
        """Nothing mined without provenance: an item with no quote is knowledge
        nobody can trace to a source, which is what we wrote by hand."""
        mined = knowledge.load(knowledge.MINED).layer
        assert mined.claims, "no mined layer"
        for section in ("symptoms", "facts", "actions", "claims"):
            for name, item in getattr(mined, section).items():
                assert item.sources, f"{section} {name} has no source"
                assert all(c.quote.strip() for c in item.sources), f"{section} {name}"

    def test_mined_sources_are_in_the_manifest(self):
        from k8srca.kb import mine

        listed = set(mine.load_manifest().sources) | {mine.VENDORED_ID}
        mined = knowledge.load(knowledge.MINED).layer
        for section in ("symptoms", "alerts", "facts", "actions", "claims"):
            for name, item in getattr(mined, section).items():
                for c in item.sources:
                    assert c.source in listed, f"{section} {name}: {c.source}"


class TestQueryToolKnowledge:
    """kb_query.py over the converted alerts."""

    def run(self, *args, capabilities: str | None = None):
        import os

        env = {k: v for k, v in os.environ.items() if k != "K8SRCA_CAPABILITIES"}
        if capabilities is not None:
            env["K8SRCA_CAPABILITIES"] = capabilities
        return subprocess.run([sys.executable, str(SKILL / "kb_query.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_a_converted_alert_answers_from_its_symptom(self):
        r = self.run("lookup", "CrashLoopBackOff")
        assert r.returncode == 0
        assert "alert CrashLoopBackOff maps here" in r.stdout
        assert "ruled out if" in r.stdout and "get_logs_for_pod_and_container" in r.stdout
        # The vendored record's remediation still comes with it.
        assert "remediation" in r.stdout

    def test_lookup_is_brief_and_points_at_claim(self):
        """Every candidate in a line or two; notes and full criteria wait for
        `claim`. A 28 KB lookup had answers echoing its text as observations."""
        out = self.run("lookup", "CrashLoopBackOff").stdout
        assert "note:" not in out and "refuted when" not in out
        assert "kb_query.py claim <id>" in out
        assert len(out) < 12_000

    def test_lookup_puts_the_cheapest_test_first(self):
        out = self.run("lookup", "CrashLoopBackOff").stdout
        # oom-kill is decided by the exit code (cost 1); one needing prometheus
        # cannot be decided here and goes last.
        assert out.index("[oom-kill]") < out.index("[bad-config]")
        assert out.index("[bad-config]") < out.index("[startup-resource-starvation]")
        assert "cannot be decided here (needs prometheus)" in out

    def test_actions_sharing_a_tool_are_one_check(self):
        out = self.run("lookup", "CrashLoopBackOff").stdout.split("how to check")[1]
        assert out.count("  get_events  (k8stools)") == 1

    def test_claim_gives_the_full_criteria_and_notes(self):
        r = self.run("claim", "bad-config", "oom-kill")
        assert r.returncode == 0
        assert "refuted when" in r.stdout and "note: Where the configuration history" in r.stdout
        assert "[oom-kill]" in r.stdout
        assert self.run("claim", "no-such-claim").returncode == 1

    def test_a_change_comes_with_the_timing_rule(self):
        """007 §3.4: the agent is told an old change with an unseen onset is
        could-not-check, and when each kind of change takes effect."""
        out = self.run("claim", "application-release").stdout
        assert "onset is observed" in out and "could-not-check" in out
        assert "the image: As the rollout replaces pods" in out

    def test_a_symptom_and_its_alert_name_give_the_same_candidates(self):
        by_symptom = self.run("lookup", "container-oom-killed").stdout
        by_alert = self.run("lookup", "OOMKilled").stdout
        assert by_symptom.split("how to check")[0].split("\n", 1)[1] in by_alert

    def test_refine_lists_the_sub_causes_as_refinements_not_rivals(self):
        r = self.run("refine", "oom-kill")
        assert r.returncode == 0
        assert "memory leak" in r.stdout and "not rival" in r.stdout

    def test_the_timing_rule_goes_with_claims_resting_on_the_onset(self):
        """memory-limit-too-low is refuted by a healthy period with an observed
        onset. Answers refuted it on an event window read as the onset, without
        a change in sight to print the rule beside (007 §11.2)."""
        out = self.run("refine", "oom-kill").stdout
        assert "first_seen says the failures were already happening then" in out
        # A duration answers repeated as fact, wrong where a series recurs.
        assert "about an hour" not in self.run("lookup", "CrashLoopBackOff").stdout

    def test_uncommon_sub_causes_wait_until_asked_for(self):
        """With six under OOM, answers covered the rare sub-causes in place of
        memory-leak and workload-spike (007 §11.2)."""
        assert "+3 sub-causes (+3 uncommon)" in self.run("lookup", "CrashLoopBackOff").stdout
        out = self.run("refine", "oom-kill").stdout
        shown, held = out.split("uncommon, not shown:")
        assert "[memory-leak]" in shown and "[workload-spike]" in shown
        assert "[cache-growth]" not in shown and "[cache-growth]" in held
        assert "refine oom-kill --all" in held
        assert "  - cache growth  [cache-growth]" in self.run("refine", "oom-kill", "--all").stdout

    def test_what_needs_an_absent_capability_says_so(self):
        """007 §4.2: unavailable is a gap, not an absence."""
        out = self.run("claim", "memory-leak").stdout
        assert "cannot be checked here: needs prometheus" in out
        assert "could-not-check, never refuted" in out

    def test_a_deployment_with_prometheus_can_check_it(self):
        """What the poller passes in K8SRCA_CAPABILITIES (007 §4.1)."""
        without = self.run("claim", "memory-leak", capabilities="k8stools").stdout
        out = self.run("claim", "memory-leak", capabilities="k8stools,prometheus").stdout
        assert "needs prometheus" in without
        # What only a person can check stays a gap whatever the deployment has.
        assert "needs prometheus" not in out

    def test_k8stools_is_assumed_even_when_unnamed(self):
        """Every deployment has it; an env naming only something else must not
        make the cluster's own checks look unavailable."""
        out = self.run("lookup", "CrashLoopBackOff", capabilities="prometheus").stdout
        unavailable = out.split("not available in this deployment")[-1]
        assert "get_pod_spec" not in unavailable

    def test_an_unconverted_alert_still_answers_from_its_record(self):
        r = self.run("lookup", "PodPending")
        assert r.returncode == 0 and "no authored discriminator" in r.stdout

    def test_search_finds_a_symptom(self):
        assert "symptom pod-restarting" in self.run("search", "restarts").stdout


class TestOpen:
    """`kb_query.py open --facts` (007 §6.1): every candidate's status from the
    facts recorded so far, three-valued, sub-causes included."""

    JVM = """\
# ad, as an agent would record it from the jvm capture
last_exit_code[C, 137]                 @1
killed_on_every_life[C]                @1
not completes_startup_each_life[C]     @6
not liveness_probe_configured[C]       @1
not process_logged_fatal_error[C]      @6
memory_limit_plausibly_reached[C]
"""

    def open(self, tmp_path, facts, symptom="CrashLoopBackOff"):
        f = tmp_path / "ad.facts"
        f.write_text(facts)
        r = subprocess.run([sys.executable, str(SKILL / "kb_query.py"), "open", symptom,
                            "--facts", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    @staticmethod
    def status_of(out, cid):
        line = next(ln for ln in out.splitlines() if f"[{cid}]" in ln and " - " in ln)
        return line.split("- ", 1)[1].split()[0]

    def test_the_jvm_case(self, tmp_path):
        """The sub-causes answers kept leaving out arrive with their verdicts,
        without a `refine` the agent may skip (007 §11.2)."""
        out = self.open(tmp_path, self.JVM)
        assert self.status_of(out, "oom-kill") == "CONFIRMED"
        assert self.status_of(out, "memory-leak") == "REFUTED"
        assert self.status_of(out, "workload-spike") == "REFUTED"
        # Killed on every life before finishing startup: below the startup
        # footprint, whatever the history (the second confirm alternative).
        assert self.status_of(out, "memory-limit-too-low") == "CONFIRMED"
        assert self.status_of(out, "application-crash") == "REFUTED"
        assert self.status_of(out, "failed-liveness-probe") == "REFUTED"
        # Needs a change against an observed onset: timing is not recorded by hand.
        assert self.status_of(out, "application-release") == "COULD-NOT-CHECK"
        assert len(out) < 12_000

    def test_the_dispositions_owed_come_last(self, tmp_path):
        """Answers reported the confirmed sub-cause and dropped the siblings
        refuted beside it; `open` now ends by naming them (007 §11.2)."""
        out = self.open(tmp_path, self.JVM)
        tail = out[out.index("the answer owes a disposition"):]
        assert ("refuted beside the confirmed memory-limit-too-low: memory-leak, "
                "workload-spike") in tail
        assert "  refuted: " in tail and "application-crash" in tail.split("  refuted: ")[1]
        assert "could-not-check: " in tail and "application-release" in tail

    def test_unknown_is_not_false(self, tmp_path):
        """With nothing recorded, no `not X` holds: absence is not evidence."""
        out = self.open(tmp_path, "")
        assert "REFUTED" not in out and "CONFIRMED" not in out

    def test_timing_facts_are_refused_from_a_recorded_file(self, tmp_path):
        out = self.open(tmp_path, "onset_observed[W]\nchanged_before_onset[W, image]\n")
        assert out.count("is not recorded by hand") == 2
        assert self.status_of(out, "application-release") == "COULD-NOT-CHECK"

    def test_a_refuted_cause_opens_no_sub_causes(self, tmp_path):
        out = self.open(tmp_path, "not liveness_probe_configured[C]\n")
        assert self.status_of(out, "failed-liveness-probe") == "REFUTED"
        assert "[probe-too-aggressive]" not in out

    def test_a_functional_fact_has_one_value(self, tmp_path):
        """Exit 137 recorded makes `last_exit_code[C, 0]` false, not unknown."""
        sys.path.insert(0, str(SKILL))
        import kb_query as Q
        K = Q.load()["knowledge"]
        store, problems = Q.parse_facts(K, "last_exit_code[C, 137]\n")
        cond = {"fact": "last_exit_code", "args": ["C", "0"], "negated": False, "compare": []}
        assert not problems and Q._value(K, store, cond, {})[0][0] == Q.F

    def test_role_arguments_take_their_letter(self, tmp_path):
        out = self.open(tmp_path, "last_exit_code[ad, 137]\nnot completes_startup_each_life[Container]\n",
                        symptom="OOMKilled")
        assert self.status_of(out, "memory-leak") == "REFUTED"

    def test_both_sides_holding_is_a_conflict(self, tmp_path):
        """A confirm and a refute both holding means a recorded fact is wrong."""
        out = self.open(tmp_path, "init_container_crashloopbackoff[C]\ninit_containers_finished[P]\n")
        assert self.status_of(out, "failing-init-container") == "CONFLICTING"

    def test_a_contradiction_in_the_file_is_refused(self, tmp_path):
        out = self.open(tmp_path, "liveness_probe_configured[C]\nnot liveness_probe_configured[C]\n")
        assert "contradicts an earlier line" in out

    def test_an_unknown_fact_is_refused_with_a_suggestion(self, tmp_path):
        out = self.open(tmp_path, "limit_plausibly_reached[C]\n")
        assert "no fact 'limit_plausibly_reached'" in out
        assert "memory_limit_plausibly_reached" in out
