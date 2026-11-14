"""--hide-label: a harness whose view hides objects by label gets a wiki that never names them."""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from kubewiki import wiki
from kubewiki.charts import collect_charts
from kubewiki.cli import _hide_labels
from kubewiki.model import Architecture
from kubewiki.sources import ArchSource

HIDE = {"opentelemetry.io/name": {"load-generator"}}


def _deployment(name, labels=None, env=None):
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "labels": labels or {}},
            "spec": {"replicas": 1, "template": {"spec": {"containers": [
                {"name": name, "image": f"demo:{name}", "env": env or []}]}}}}


def _service(name, labels=None):
    return {"apiVersion": "v1", "kind": "Service",
            "metadata": {"name": name, "labels": labels or {}},
            "spec": {"ports": [{"port": 8080}], "selector": {"app": name}}}


@pytest.fixture
def chart(tmp_path):
    gen = {"opentelemetry.io/name": "load-generator"}
    docs = [
        _deployment("load-generator", gen, [{"name": "TARGET_ADDR", "value": "proxy:8080"}]),
        _service("load-generator", gen),
        _deployment("proxy", {}, [{"name": "LOADGEN_ADDR", "value": "load-generator:8089"}]),
        _service("proxy"),
        _deployment("cart", {"opentelemetry.io/name": "cart"}),
        _service("cart"),
    ]
    (tmp_path / "chart.yaml").write_text(yaml.safe_dump_all(docs))
    return tmp_path


def _build(chart, hide=None):
    arch = Architecture()
    collect_charts(ArchSource(type="chart_repo", path=chart), arch, hide)
    return arch


def test_a_hidden_component_is_absent_and_the_rest_untouched(chart, tmp_path):
    arch = _build(chart, HIDE)
    assert {k if isinstance(k, str) else k[-1] for k in arch.services} == {"proxy", "cart"}
    out = tmp_path / "wiki"
    wiki.write(arch, out, today=date(2026, 11, 11))
    text = "\n".join(p.read_text() for p in out.rglob("*") if p.is_file() and p.name not in {"sources.md", "log.md"})
    text = text.replace(wiki.graph(arch)["sources"][0]["left_out"], "")   # the record itself
    assert "load-generator" not in text
    assert not (out / "components" / "load-generator.md").exists()
    assert (out / "components" / "proxy.md").exists() and (out / "components" / "cart.md").exists()


def test_other_components_draw_no_edge_to_it(chart):
    """proxy's env names load-generator; with it hidden, that is not an edge."""
    graph = wiki.graph(_build(chart, HIDE))
    assert all("load-generator" not in (e["from"], e["to"]) for e in graph["edges"])


def test_without_the_option_nothing_changes(chart):
    graph = wiki.graph(_build(chart))
    assert "load-generator" in graph["components"]
    assert any(e["from"] == "proxy" and e["to"] == "load-generator" for e in graph["edges"])


def test_the_omission_is_recorded_not_silent(chart, tmp_path):
    out = tmp_path / "wiki"
    wiki.write(_build(chart, HIDE), out, today=date(2026, 11, 11))
    assert "load-generator (opentelemetry.io/name=load-generator)" in (out / "log.md").read_text()
    assert "left_out=load-generator" in (out / "sources.md").read_text()


def test_a_label_value_that_does_not_match_hides_nothing(chart):
    graph = wiki.graph(_build(chart, {"opentelemetry.io/name": {"something-else"}}))
    assert "load-generator" in graph["components"]


def test_the_flag_parses_key_value_and_repeats():
    assert _hide_labels(["app=a", "app=b", "job=workload"]) == {"app": {"a", "b"}, "job": {"workload"}}
    assert _hide_labels(None) == {}
    with pytest.raises(SystemExit):
        _hide_labels(["no-equals"])
