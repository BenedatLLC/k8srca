"""Architecture model and rendering (multi-source, with provenance)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from k8srca.arch.model import Architecture, Service
from k8srca.arch.render import to_json, topology

SKILL = Path("skills/cluster-architecture")


class TestProvenance:
    def test_observed_beats_declared(self):
        # Live state is authoritative about what is running now, which is what
        # an investigation needs first.
        s = Service(name="x")
        s.add("replicas", 3, "declared", "chart.yaml")
        s.add("replicas", 1, "observed", "k8stools")
        assert s.best("replicas").value == 1

    def test_declared_used_when_nothing_observed(self):
        s = Service(name="x")
        s.add("image", "demo:1", "declared", "chart.yaml")
        assert s.best("image").source == "declared"

    def test_disagreement_is_reported_not_merged(self):
        # Drift between declared and observed is a finding in its own right.
        s = Service(name="x")
        s.add("replicas", 3, "declared", "c")
        s.add("replicas", 1, "observed", "k")
        assert {f.value for f in s.conflicts("replicas")} == {1, 3}

    def test_agreement_is_not_a_conflict(self):
        s = Service(name="x")
        s.add("replicas", 1, "declared", "c")
        s.add("replicas", 1, "observed", "k")
        assert s.conflicts("replicas") == []

    def test_empty_values_are_not_facts(self):
        s = Service(name="x")
        for v in (None, [], {}):
            s.add("k", v, "observed")
        assert s.best("k") is None


class TestDependencies:
    def _arch(self):
        a = Architecture()
        a.service("frontend").depends_on.update({"ad", "cart"})
        a.service("frontend-proxy").depends_on.add("frontend")
        a.service("ad")
        a.service("cart")
        return a

    def test_dependents_are_the_direction_rca_travels(self):
        # A failing dependency explains symptoms in everything upstream.
        assert self._arch().dependents_of("ad") == ["frontend"]

    def test_leaf_has_no_dependents(self):
        assert self._arch().dependents_of("frontend-proxy") == []

    def test_topology_names_entry_points(self):
        assert "frontend-proxy" in topology(self._arch())


class TestRender:
    def test_conflicts_survive_into_the_bundle(self):
        a = Architecture()
        s = a.service("x")
        s.add("replicas", 3, "declared", "c")
        s.add("replicas", 1, "observed", "k")
        entry = to_json(a)["services"]["x"]["facts"]["replicas"]
        assert entry["value"] == 1 and len(entry["conflicts"]) == 2

    def test_records_what_it_was_built_from(self):
        a = Architecture()
        a.sources.append({"type": "live_cluster", "origin": "k8stools"})
        assert to_json(a)["sources"][0]["type"] == "live_cluster"


@pytest.mark.skipif(not (SKILL / "architecture.json").exists(),
                    reason="architecture not built")
class TestQueryTool:
    """arch_query.py ships inside the bundle and must run standalone."""

    def run(self, *args):
        return subprocess.run([sys.executable, str(SKILL / "arch_query.py"), *args],
                              capture_output=True, text=True)

    def test_service_lookup(self):
        db = json.loads((SKILL / "architecture.json").read_text())
        name = sorted(db["services"])[0]
        assert self.run("service", name).returncode == 0

    def test_unknown_service_suggests_alternatives(self):
        r = self.run("service", "definitely-not-a-service")
        assert r.returncode == 1 and "no service" in r.stdout

    def test_blast_is_transitive(self):
        # ad <- frontend <- frontend-proxy: the indirect caller must appear,
        # or the blast radius understates the impact.
        db = json.loads((SKILL / "architecture.json").read_text())
        if "ad" not in db["services"]:
            pytest.skip("demo cluster not present")
        out = self.run("blast", "ad").stdout
        assert "frontend" in out and "indirectly" in out

    def test_drift_explains_itself_with_one_source(self):
        # Silence would read as "no drift"; with a single source drift is
        # undetectable, which is a different statement.
        out = self.run("drift").stdout
        assert "no drift" in out
        db = json.loads((SKILL / "architecture.json").read_text())
        if len({s.get("type") for s in db.get("sources", [])}) < 2:
            assert "cannot be detected" in out

    def test_sources_warns_it_is_a_snapshot(self):
        assert "snapshot" in self.run("sources").stdout
