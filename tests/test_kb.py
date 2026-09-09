"""Knowledge-base normalization (design 002 §8)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
