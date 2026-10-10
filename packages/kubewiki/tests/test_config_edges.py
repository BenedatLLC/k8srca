"""Edges from mounted configuration, headless Services, and the reference
diagram check (the step after synthesis, 2026-10-04)."""

from __future__ import annotations

import textwrap

import yaml

from kubewiki import otelcol, wiki
from kubewiki.charts import collect_charts
from kubewiki.eval import Reference, Truth, check_reference, diagram_dependencies
from kubewiki.model import Architecture, Evidence
from kubewiki.sources import ArchSource

COLLECTOR = textwrap.dedent("""\
    exporters:
      debug: {}
      opensearch:
        http:
          endpoint: http://opensearch:9200
      otlp/jaeger:
        endpoint: jaeger:4317
      otlphttp/unused:
        endpoint: http://grafana:3000
    service:
      pipelines:
        logs: {exporters: [opensearch, debug]}
        traces: {exporters: [otlp/jaeger, debug]}
    """)


class TestCollectorConfig:
    def test_only_exporters_in_a_pipeline_and_their_endpoints(self):
        assert otelcol.exporter_endpoints(COLLECTOR) == [
            ("opensearch", "http://opensearch:9200"), ("otlp/jaeger", "jaeger:4317")]

    def test_anything_else_has_no_endpoints(self):
        assert otelcol.exporter_endpoints("a: [unclosed") == []
        assert otelcol.exporter_endpoints("server:\n  port: 80\n") == []

    def test_a_chart_collector_names_its_backends(self, tmp_path):
        docs = [
            {"kind": "ConfigMap", "metadata": {"name": "otel-collector-agent"},
             "data": {"relay": COLLECTOR}},
            {"kind": "DaemonSet", "metadata": {"name": "otel-collector-agent"},
             "spec": {"template": {"spec": {
                 "containers": [{"name": "c", "image": "collector:1"}],
                 "volumes": [{"name": "cfg", "configMap": {"name": "otel-collector-agent"}}]}}}},
            *({"kind": "Service", "metadata": {"name": n}, "spec": {"ports": [{"port": 1}]}}
              for n in ("opensearch", "jaeger", "grafana")),
        ]
        (tmp_path / "all.yaml").write_text(yaml.safe_dump_all(docs))
        arch = Architecture()
        collect_charts(ArchSource(type="chart_repo", path=tmp_path), arch)
        edges = arch.services["otel-collector-agent"].edges
        assert sorted(edges) == ["jaeger", "opensearch"]
        assert edges["opensearch"] == [Evidence("otel-collector-agent/exporters.opensearch",
                                                "declared", "all.yaml", via="config")]

    def test_config_evidence_reaches_synthesis_as_config_not_env(self, tmp_path):
        from kubewiki import synthesis as syn

        a = Architecture()
        a.service("collector").workload = "DaemonSet"
        a.service("opensearch").workload = "StatefulSet"
        a.service("collector").connect(
            "opensearch", Evidence("cm/exporters.opensearch", "declared", "x", via="config"))
        inp = syn.inputs(wiki.graph(a))
        edge = next(c for c in inp["components"] if c["name"] == "collector")["edges"][0]
        assert edge == {"to": "opensearch", "vars": [], "config": ["cm/exporters.opensearch"]}
        assert "config:cm/exporters.opensearch" in syn._allowed_cites(inp, None)


def _headless_arch(extra_match: bool = False) -> Architecture:
    a = Architecture()
    sel = {"app": "opensearch"}
    os_ = a.service("opensearch")
    os_.workload = "StatefulSet"
    os_.add("selector", sel, "observed", "k8stools")
    h = a.service("opensearch-headless")
    h.headless = True
    h.add("selector", dict(reversed(list(sel.items()))), "declared", "chart.yaml")
    a.service("logs").connect("opensearch-headless", Evidence("OS_URL", "observed", "k8stools"))
    if extra_match:
        other = a.service("opensearch-2")
        other.workload = "StatefulSet"
        other.add("selector", sel, "observed", "k8stools")
    return a


class TestHeadlessFold:
    def test_folded_into_the_workload_it_selects(self):
        a = _headless_arch()
        assert a.fold_headless() == [("opensearch-headless", "opensearch")]
        assert "opensearch-headless" not in a.services
        assert a.services["opensearch"].aliases == ["opensearch-headless"]
        assert list(a.services["logs"].edges) == ["opensearch"]
        assert a.services["logs"].depends_on == {"opensearch"}

    def test_ambiguous_selector_is_left_alone(self):
        a = _headless_arch(extra_match=True)
        assert a.fold_headless() == []
        assert "opensearch-headless" in a.services

    def test_a_normal_service_is_never_folded(self):
        a = _headless_arch()
        a.services["opensearch-headless"].headless = False
        assert a.fold_headless() == []

    def test_the_page_names_the_alias(self, tmp_path):
        a = _headless_arch()
        a.fold_headless()
        wiki.write(a, tmp_path)
        assert "**Also reached as:** `opensearch-headless`" in \
            (tmp_path / "components" / "opensearch.md").read_text()


DIAGRAM = textwrap.dedent("""\
    Some prose.

    ```mermaid
    graph TD
    cart --> cache
    checkout -->|TCP| queue
    queue -->|TCP| accounting
    frontend ---->|gRPC| cart
    ghost --> cart
    ```

    ```mermaid
    graph TD
    later --> ignored
    ```
    """)
REF = Reference(git={"repo": "r", "ref": "0" * 40, "path": "a.md"},
                aliases={"cache": "valkey-cart", "queue": "kafka"}, reverse_from=["queue"])


class TestReferenceDiagram:
    def test_arrows_aliased_and_consumers_reversed(self):
        assert diagram_dependencies(DIAGRAM, REF) == {
            ("cart", "valkey-cart"), ("checkout", "kafka"), ("accounting", "kafka"),
            ("frontend", "cart"), ("ghost", "cart")}

    def test_unexplained_differences_only_between_this_installs_components(self):
        truth = Truth(dependencies={"cart": ["valkey-cart", "flagd"],
                                    "checkout": ["kafka"], "frontend": ["cart"]},
                      reference_differences={"cart -> flagd": "FLAGD_HOST",
                                             "frontend -> cart": "stale"})
        comps = {"cart", "valkey-cart", "flagd", "checkout", "kafka", "accounting", "frontend"}
        assert check_reference(truth, diagram_dependencies(DIAGRAM, REF), comps) == [
            "accounting -> kafka: in the diagram, not the truth",
            "frontend -> cart: listed as a difference, but they agree",
        ]


def test_the_live_source_reads_a_mounted_collector_config(monkeypatch):
    """Through get_configmap, whose `name` argument once collided with the
    caller's own and was swallowed: the live path found no config edges."""
    import asyncio
    import contextlib

    from kubewiki import live
    from kubewiki.sources import Server

    responses = {
        "get_service_summaries": [{"name": n, "cluster_ip": "10.0.0.1"}
                                  for n in ("opensearch", "jaeger")],
        "get_deployment_summaries": [],
        "get_daemonset_summaries": [{"name": "agent"}],
        "get_pod_summaries": [{"name": "agent-x1y2z", "owner": "DaemonSet/agent"}],
        "get_pod_spec": [{"containers": [{"image": "collector:1"}],
                          "volumes": [{"name": "cfg", "config_map": {"name": "agent-cm"}},
                                      {"name": "tmp", "config_map": None}]}],
    }
    calls = []

    @contextlib.asynccontextmanager
    async def fake_connect(url, timeout_s):
        async def call(tool, /, **kwargs):
            calls.append((tool, kwargs))
            if tool == "get_configmap":
                return [{"name": kwargs["name"], "data": {"relay": COLLECTOR}}]
            return responses[tool]
        yield set(responses) | {"get_configmap"}, call

    monkeypatch.setattr(live, "connect", fake_connect)
    arch = asyncio.run(live.collect(Server(name="c", url="http://x"), ["default"], Architecture()))
    assert ("get_configmap", {"name": "agent-cm", "namespace": "default"}) in calls
    assert sorted(arch.services["agent"].edges) == ["jaeger", "opensearch"]
    assert arch.services["agent"].edges["jaeger"][0].via == "config"


class TestTraces:
    from kubewiki.eval import Traces as _T

    TRACES = _T(file="deps.json", aliases={"frontend-web": "frontend"}, queues=["kafka"],
                untraced=["postgresql", "flagd"])

    def check(self, rows, **truth):
        from kubewiki.eval import check_traces, trace_dependencies

        comps = {"frontend", "checkout", "cart", "accounting", "kafka", "postgresql",
                 "flagd", "ad"}
        return check_traces(Truth(**truth), trace_dependencies({"data": rows}, self.TRACES),
                            self.TRACES, comps)

    def test_agreement_through_aliases_queues_and_untraced_targets(self):
        rows = [{"parent": "frontend-web", "child": "checkout", "callCount": 9},
                {"parent": "checkout", "child": "accounting", "callCount": 4},
                {"parent": "checkout", "child": "checkout", "callCount": 1}]
        assert self.check(rows, dependencies={
            "frontend": ["checkout"], "checkout": ["kafka", "flagd"],
            "accounting": ["kafka", "postgresql"]}) == []

    def test_both_directions_of_disagreement_and_stale_explanations(self):
        rows = [{"parent": "frontend", "child": "ad", "callCount": 3}]
        assert self.check(rows, dependencies={"frontend": ["cart"]},
                          trace_differences={"cart -> flagd": "old"}) == [
            "frontend -> ad: traced (3 calls), not in the truth",
            "frontend -> cart: in the truth, no traced calls",
            "cart -> flagd: listed as a difference, but they agree",
        ]


def test_reviewed_as_yaml_reads_it():
    """Unquoted, `on` is YAML 1.1's True and the date a date."""
    t = Truth.model_validate(yaml.safe_load("reviewed: {by: jfischer, on: 2026-10-04}"))
    assert (t.reviewed.by, t.reviewed.on) == ("jfischer", "2026-10-04")
