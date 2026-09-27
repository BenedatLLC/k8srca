"""Knowledge-base normalization (design 002 §8)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import yaml

from k8srca.kb import build as build_mod
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


@pytest.mark.skipif(not Path("background/kubernetes_rca_knowledge_base_v2.json").exists(),
                    reason="source KB not present (background/ is gitignored)")
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


class TestDiscriminators:
    """Authored discriminators (002 §5.4, §11.3).

    The vendored records name candidate causes and nothing about telling them
    apart, so `would_confirm` was left to the model to invent at investigation
    time -- 002 §11.3 calls that the weakest link. The measured symptom is
    `rivals` reading 0/6 in four consecutive n=6 batches, unmoved by anything
    else tried.
    """

    def write(self, tmp_path, data) -> Path:
        p = tmp_path / "discriminators.yaml"
        p.write_text(yaml.safe_dump(data))
        return p

    def test_an_authored_discriminator_attaches_to_its_hypothesis(self, tmp_path):
        d = self.write(tmp_path, {"OOMKilled": {"memory leak": {
            "would_confirm": "usage climbs", "would_refute": "dies at startup"}}})
        data, report = build_mod.build(discriminators=d)
        got = data["alerts"]["OOMKilled"]["discriminators"]["memory leak"]
        assert got["would_refute"] == "dies at startup"
        assert got["source"] == "authored"
        assert report.discriminators == 1

    def test_a_hypothesis_that_does_not_resolve_is_reported_not_dropped(self, tmp_path):
        """A typo would otherwise attach the criterion to nothing, and the
        hypothesis it was written for keeps being invented instead."""
        d = self.write(tmp_path, {"OOMKilled": {"memroy leak": {
            "would_confirm": "x", "would_refute": "y"}}})
        _, report = build_mod.build(discriminators=d)
        assert report.unresolved_discriminators == ["OOMKilled -> 'memroy leak'"]
        assert report.discriminators == 0

    def test_an_alert_that_does_not_resolve_is_reported(self, tmp_path):
        d = self.write(tmp_path, {"NoSuchAlert": {"whatever": {
            "would_confirm": "x", "would_refute": "y"}}})
        _, report = build_mod.build(discriminators=d)
        assert report.unresolved_discriminators == ["alert NoSuchAlert"]

    def test_hypotheses_without_a_discriminator_are_counted(self, tmp_path):
        """The count is the measure of how far this layer has to go."""
        d = self.write(tmp_path, {"OOMKilled": {"memory leak": {
            "would_confirm": "x", "would_refute": "y"}}})
        _, report = build_mod.build(discriminators=d)
        assert report.undiscriminated > 100
        assert "hypotheses without one" in report.render()

    def test_an_absent_file_is_legitimate(self, tmp_path):
        data, report = build_mod.build(discriminators=tmp_path / "nope.yaml")
        assert report.discriminators == 0
        assert data["discriminator_source"] is None

    def test_a_malformed_file_fails_loudly(self, tmp_path):
        p = tmp_path / "d.yaml"
        p.write_text("- not a mapping\n")
        with pytest.raises(ValueError, match="mapping"):
            build_mod.build(discriminators=p)

    def test_the_shipped_layer_resolves_completely(self):
        """Every discriminator we have written names a real hypothesis."""
        _, report = build_mod.build()
        assert report.unresolved_discriminators == []
        assert report.discriminators >= 7

    def test_every_shipped_discriminator_has_a_refutation(self):
        """A criterion that only confirms invites the confirmation-seeking
        behaviour 002 §6 exists to avoid."""
        data, _ = build_mod.build()
        for alert in data["alerts"].values():
            for name, d in (alert.get("discriminators") or {}).items():
                assert d.get("would_refute"), f"{name} has no would_refute"
                assert d.get("would_confirm"), f"{name} has no would_confirm"

    def test_no_discriminator_pre_decides_the_question_it_asks(self):
        """A discriminator says what evidence would decide a question. It must
        not resolve one in advance.

        Learned twice, at a batch each. A note explaining that
        `lastState.terminated.reason` is unreliable -- true, and generic -- drove
        `traps` from 6/6 to 4/6 both when it sat in a cluster's architecture
        notes and when it sat here. Telling the agent a discrepancy is expected
        removes its reason to report that this instance shows one.
        """
        data, _ = build_mod.build()
        for alert in data["alerts"].values():
            for name, d in (alert.get("discriminators") or {}).items():
                text = " ".join(str(v) for k, v in d.items() if k != "source").lower()
                for phrase in ("is fully consistent with",
                               "is not reliably",
                               "treat the exit code as the signal"):
                    assert phrase not in text, (
                        f"{name} pre-decides rather than discriminates: {phrase!r}")
