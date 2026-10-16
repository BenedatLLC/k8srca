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
        assert "refuted when" in r.stdout and "get_logs_for_pod_and_container" in r.stdout
        # The vendored record's remediation still comes with it.
        assert "remediation" in r.stdout

    def test_a_symptom_and_its_alert_name_give_the_same_candidates(self):
        by_symptom = self.run("lookup", "container-oom-killed").stdout
        by_alert = self.run("lookup", "OOMKilled").stdout
        assert by_symptom.split("how to check")[0].split("\n", 1)[1] in by_alert

    def test_refine_lists_the_sub_causes_as_refinements_not_rivals(self):
        r = self.run("refine", "oom-kill")
        assert r.returncode == 0
        assert "memory leak" in r.stdout and "not rival" in r.stdout

    def test_what_needs_an_absent_capability_says_so(self):
        """007 §4.2: unavailable is a gap, not an absence."""
        out = self.run("refine", "oom-kill").stdout
        assert "cannot be checked here: needs prometheus" in out
        assert "could-not-check, never refuted" in out

    def test_a_deployment_with_prometheus_can_check_it(self):
        """What the poller passes in K8SRCA_CAPABILITIES (007 §4.1)."""
        without = self.run("refine", "oom-kill", capabilities="k8stools").stdout
        out = self.run("refine", "oom-kill", capabilities="k8stools,prometheus").stdout
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
