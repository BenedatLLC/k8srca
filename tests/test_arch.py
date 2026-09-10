"""Architecture model and rendering (multi-source, with provenance)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from k8srca.arch.model import Architecture, Service
from k8srca.arch.render import to_json, topology

# The query tool is framework code; the bundle it ships in is a build artifact
# describing one deployment's cluster. Tests run the script against a synthetic
# fixture they own, so they neither depend on a built bundle nor assert
# anything about somebody's real topology.
QUERY = Path("src/k8srca/arch/templates/arch_query.py")
FIXTURE = Path("tests/fixtures/architecture.json")


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


class TestQueryTool:
    """arch_query.py must run standalone: it ships inside a skill bundle whose
    only Python is the interpreter."""

    @pytest.fixture(autouse=True)
    def bundle(self, tmp_path):
        """A throwaway bundle: the real script beside the fixture data."""
        (tmp_path / "arch_query.py").write_text(QUERY.read_text())
        (tmp_path / "architecture.json").write_text(FIXTURE.read_text())
        self.dir = tmp_path

    def run(self, *args):
        return subprocess.run([sys.executable, str(self.dir / "arch_query.py"), *args],
                              capture_output=True, text=True)

    def test_service_lookup_reports_facts(self):
        out = self.run("service", "api").stdout
        assert "example/api:2.1" in out and "128Mi" in out

    def test_unknown_service_suggests_alternatives(self):
        r = self.run("service", "ap")
        assert r.returncode == 1 and "api" in r.stdout

    def test_blast_is_transitive(self):
        # store <- api <- edge: the indirect caller must appear, or the blast
        # radius understates the impact.
        out = self.run("blast", "store").stdout
        assert "api" in out and "edge" in out and "indirectly" in out

    def test_blast_says_so_when_nothing_depends_on_it(self):
        assert "nothing recorded" in self.run("blast", "orphan").stdout

    def test_drift_is_reported(self):
        out = self.run("drift").stdout
        assert "api.replicas" in out and "declared=2" in out and "observed=1" in out

    def test_drift_explains_itself_when_undetectable(self, tmp_path):
        # Silence would read as "no drift"; with one source drift cannot be
        # detected at all, which is a different statement.
        db = json.loads(FIXTURE.read_text())
        db["sources"] = [{"type": "live_cluster", "origin": "fixture"}]
        for svc in db["services"].values():
            for fact in svc["facts"].values():
                fact.pop("conflicts", None)
        (self.dir / "architecture.json").write_text(json.dumps(db))
        out = self.run("drift").stdout
        assert "no drift" in out and "cannot be detected" in out

    def test_missing_data_file_fails_loudly(self, tmp_path):
        (self.dir / "architecture.json").unlink()
        r = self.run("list")
        assert r.returncode != 0 and "arch build" in (r.stdout + r.stderr)

    def test_sources_warns_it_is_a_snapshot(self):
        assert "snapshot" in self.run("sources").stdout


class TestChartParsing:
    """Reading declared state from rendered manifests."""

    def _write(self, tmp_path, text, name="m.yaml"):
        (tmp_path / name).write_text(text)
        return tmp_path

    def test_braces_in_data_do_not_discard_the_file(self, tmp_path):
        # The demo embeds Grafana dashboards containing "{{__name__}}". A
        # file-level template check threw away 20,000 lines of valid YAML.
        from k8srca.arch.charts import collect_charts
        from k8srca.config import ArchSource

        self._write(tmp_path, """
apiVersion: v1
kind: ConfigMap
metadata: {name: dash}
data:
  d.json: '{"legendFormat": "{{__name__}}"}'
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: api}
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: api
          image: example/api:1
""")
        arch = Architecture()
        assert collect_charts(ArchSource(type="chart_repo", path=tmp_path), arch) == 1
        assert arch.services["api"].best("image").value == "example/api:1"

    def test_unrendered_templates_are_skipped(self, tmp_path):
        from k8srca.arch.charts import collect_charts
        from k8srca.config import ArchSource

        self._write(tmp_path, """
apiVersion: apps/v1
kind: Deployment
metadata: {name: "{{ .Release.Name }}-api"}
spec:
  template:
    spec:
      containers: [{name: api, image: "{{ .Values.image }}"}]
""")
        arch = Architecture()
        collect_charts(ArchSource(type="chart_repo", path=tmp_path), arch)
        assert "api" not in arch.services

    def test_declared_and_observed_agree_when_nothing_drifted(self, tmp_path):
        # The manifest omits requests; the running pod reports them. Both
        # sources must normalise to the same thing or every container drifts.
        from k8srca.arch import normalise
        from k8srca.arch.charts import collect_charts
        from k8srca.config import ArchSource

        self._write(tmp_path, """
apiVersion: apps/v1
kind: Deployment
metadata: {name: api}
spec:
  template:
    spec:
      containers:
        - name: api
          image: example/api:1
          resources: {limits: {memory: 120Mi}}
""")
        arch = Architecture()
        arch.service("api").add(
            "resources",
            normalise.resources({"limits": {"memory": "120Mi"}, "requests": {"memory": "120Mi"}}),
            "observed", "k8stools")
        collect_charts(ArchSource(type="chart_repo", path=tmp_path), arch)
        assert arch.services["api"].conflicts("resources") == []
