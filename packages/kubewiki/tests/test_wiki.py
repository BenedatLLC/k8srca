"""The deterministic wiki (docs/design.md §3, migration step 2)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from kubewiki import wiki
from kubewiki.model import Architecture, Evidence, Fact
from kubewiki.verify import check


def sample() -> Architecture:
    a = Architecture()
    cart = a.service("cart")
    cart.workload = "Deployment"
    cart.add("image", "demo:2.2.0-cart", "declared", "chart.yaml")
    cart.add("image", "demo:2.2.0-cart", "observed", "k8stools")
    cart.add("ready_replicas", 0, "observed", "k8stools")
    cart.add("resources", {"limits": {"memory": "160Mi"}}, "declared", "chart.yaml")
    cart.connect("valkey-cart", Evidence("VALKEY_ADDR", "observed", "k8stools"))
    cart.connect("valkey-cart", Evidence("VALKEY_ADDR", "declared", "chart.yaml"))
    cart.notes.append(Fact("Keeps carts, in Valkey.", "documented", "cart/index.md"))
    checkout = a.service("checkout")
    checkout.workload = "Deployment"
    checkout.connect("cart", Evidence("CART_ADDR", "observed", "k8stools"))
    a.service("valkey-cart").workload = "Deployment"
    a.sources.append({"type": "live_cluster", "origin": "k8stools", "namespaces": "default"})
    a.sources.append({"type": "change_history", "origin": "k8stools", "namespaces": "default"})
    a.sources.append({"type": "chart_repo", "origin": "/cache/x", "pinned": "demo 1.0 (repo)"})
    return a


@pytest.fixture
def built(tmp_path) -> Path:
    wiki.write(sample(), tmp_path, today=date(2026, 10, 4))
    return tmp_path


class TestContent:
    def test_no_observed_state_is_written(self, built):
        """Live state belongs to the cluster, asked for when it is needed."""
        text = "\n".join(p.read_text() for p in built.rglob("*") if p.is_file())
        assert "ready_replicas" not in text
        g = json.loads((built / "graph.json").read_text())
        assert "ready_replicas" not in json.dumps(g)

    def test_declared_configuration_cites_the_chart(self, built):
        page = (built / "components" / "cart.md").read_text()
        assert "- image: demo:2.2.0-cart [chart: chart.yaml]" in page

    def test_documentation_is_quoted_with_its_source(self, built):
        page = (built / "components" / "cart.md").read_text()
        assert "> Keeps carts, in Valkey." in page and "[docs: cart/index.md]" in page

    def test_an_edge_keeps_every_piece_of_evidence(self, built):
        g = json.loads((built / "graph.json").read_text())
        edge = next(e for e in g["edges"] if (e["from"], e["to"]) == ("cart", "valkey-cart"))
        assert {ev["source"] for ev in edge["evidence"]} == {"observed", "declared"}

    def test_diagrams_are_drawn_from_the_graph(self, built):
        index = (built / "index.md").read_text()
        assert "```mermaid" in index and "checkout --> cart" in index
        page = (built / "components" / "cart.md").read_text()
        assert 'checkout["checkout"] --> cart' in page
        assert 'cart --> valkey_cart["valkey-cart"]' in page

    def test_sources_name_the_pin_and_omit_unused_sources(self, built):
        """change_history is state: the wiki does not use it, so does not list it."""
        sources = (built / "sources.md").read_text()
        assert "demo 1.0 (repo)" in sources and "/cache/x" not in sources
        assert "change_history" not in sources

    def test_kinds_wait_for_synthesis(self, built):
        g = json.loads((built / "graph.json").read_text())
        assert {c["kind"] for c in g["components"].values()} == {wiki.UNCLASSIFIED}


class TestDeterminism:
    def test_an_unchanged_build_is_byte_identical(self, built, tmp_path_factory):
        before = {p.relative_to(built): p.read_bytes() for p in built.rglob("*") if p.is_file()}
        wiki.write(sample(), built, today=date(2026, 10, 5))
        after = {p.relative_to(built): p.read_bytes() for p in built.rglob("*") if p.is_file()}
        assert after == before, "a rebuild with no change must not touch anything"

    def test_a_change_is_logged(self, built):
        a = sample()
        a.service("cart").connect("flagd", Evidence("FLAGD_HOST", "observed", "k8stools"))
        a.service("flagd").workload = "Deployment"
        wiki.write(a, built, today=date(2026, 10, 5))
        log = (built / "log.md").read_text()
        assert "## 2026-10-05" in log and "added component flagd" in log
        assert "added edge cart -> flagd" in log
        assert log.index("2026-10-05") < log.index("2026-10-04"), "newest first"

    def test_a_removed_component_loses_its_page(self, built):
        a = sample()
        del a.services["checkout"]
        wiki.write(a, built, today=date(2026, 10, 5))
        assert not (built / "components" / "checkout.md").exists()


class TestCheck:
    def test_a_built_wiki_passes(self, built):
        assert check(built) == []

    def test_a_page_naming_an_edge_the_graph_lacks(self, built):
        page = built / "components" / "cart.md"
        page.write_text(page.read_text().replace(
            "**Connects:** ", "**Connects:** [flagd](flagd.md) via unclassified; ", 1))
        rows = {f.row for f in check(built)}
        assert "edges" in rows and "links" in rows

    def test_a_missing_page(self, built):
        (built / "components" / "cart.md").unlink()
        assert any(f.row == "inventory" and "cart has no page" in f.detail for f in check(built))

    def test_an_uncited_statement(self, built):
        page = built / "components" / "cart.md"
        page.write_text(page.read_text().replace(
            "## Declared configuration\n", "## Declared configuration\n\nIt is fast.\n", 1))
        assert any(f.row == "citations" and "It is fast." in f.detail for f in check(built))

    def test_not_a_wiki(self, tmp_path):
        assert [f.row for f in check(tmp_path)] == ["graph"]


class TestQueryTool:
    def run(self, built, *args) -> str:
        r = subprocess.run([sys.executable, str(built / "wiki.py"), *args],
                           capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr

    def test_blast_walks_upstream(self, built):
        out = self.run(built, "blast", "valkey-cart")
        assert "cart  (directly)" in out and "checkout  (2 hops away)" in out

    def test_path(self, built):
        assert "checkout -> cart -> valkey-cart" in self.run(built, "path", "checkout", "valkey-cart")

    def test_an_unknown_component_suggests_one(self, built):
        assert "Did you mean: cart" in self.run(built, "deps", "car")


class TestConfig:
    def test_sources_follow_the_config(self, tmp_path):
        from kubewiki.cli import load_config

        path = tmp_path / "kubewiki.yaml"
        path.write_text("cluster: {mcp: 'http://x/mcp', namespaces: [shop]}\n"
                        "docs: {path: docs}\n")
        cfg = load_config(path)
        types = [s.type for s in cfg.sources()]
        assert types == ["live_cluster", "docs"]
        assert cfg.sources(["other"])[0].namespaces == ["other"], "--namespace overrides"
        assert cfg.servers()[0].url == "http://x/mcp"

    def test_an_unknown_key_is_an_error(self, tmp_path):
        import pydantic

        from kubewiki.cli import load_config

        path = tmp_path / "kubewiki.yaml"
        path.write_text("clster: {mcp: 'http://x/mcp'}\n")
        with pytest.raises(pydantic.ValidationError):
            load_config(path)


class TestDeclaredEdges:
    def test_a_chart_states_edges_and_workload_types(self, tmp_path):
        from kubewiki.charts import collect_charts
        from kubewiki.sources import ArchSource

        (tmp_path / "c.yaml").write_text("""\
kind: Service
metadata: {name: valkey-cart}
---
kind: Deployment
metadata: {name: cart}
spec:
  template:
    spec:
      containers:
        - image: demo:cart
          env:
            - {name: VALKEY_ADDR, value: "valkey-cart:6379"}
            - {name: API_KEY, value: "valkey-cart"}
---
kind: StatefulSet
metadata: {name: db}
spec: {template: {spec: {containers: [{image: postgres}]}}}
""")
        a = Architecture()
        collect_charts(ArchSource(type="chart_repo", path=tmp_path), a)
        assert a.services["cart"].edges["valkey-cart"] == [
            Evidence("VALKEY_ADDR", "declared", "c.yaml")]
        assert a.services["cart"].depends_on == set(), "declared edges stay out of the legacy skill"
        assert a.services["db"].workload == "StatefulSet"
