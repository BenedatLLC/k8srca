"""The cluster-architecture eval's truth and scoring (005 §8.1).

Hermetic: tiny captures and skills built here. Running a whole case needs a
k8stools replay and is `k8srca eval arch` in k8srca, not a test here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dkgg import normalise
from dkgg.eval import Truth, derive_declared, derive_observed, load_case, score


def pod(name, rs_hash, image, ns="default", limits=None, liveness=False):
    c = {"image": image, "resources": {"limits": limits or {}}}
    if liveness:
        c["liveness_probe"] = {"tcp_socket": {"port": 80}}
    return {"summary": {"name": name, "namespace": ns},
            "labels": {"pod-template-hash": rs_hash}, "spec": {"containers": [c]}}


def capture(**kw):
    base = {
        "services": [{"name": "ad", "namespace": "default", "type": "ClusterIP",
                      "ports": [{"port": 8080}], "selector": {"app": "ad"}},
                     {"name": "kubernetes", "namespace": "default", "type": "ClusterIP",
                      "ports": [{"port": 443}], "selector": None}],
        "deployments": [{"name": "ad", "namespace": "default",
                         "total_replicas": 1, "ready_replicas": 1}],
        "replicasets": [{"name": "ad-aaa111", "namespace": "default",
                         "owner_deployment": "ad", "revision": 1}],
        "pods": [pod("ad-aaa111-x1y2z", "aaa111", "ad:1", limits={"memory": "300Mi"})],
    }
    base.update(kw)
    return base


def fact(value, source="observed", conflicts=None):
    f = {"value": value, "source": source, "origin": "k8stools"}
    if conflicts:
        f["conflicts"] = conflicts
    return f


class TestDerivedTruth:
    def test_reads_the_current_revision_not_the_first_pod(self):
        """Mid-rollout a workload has pods of two revisions. The truth is the
        newest ReplicaSet's, whichever pod a generator happens to meet first."""
        cap = capture(
            replicasets=[{"name": "ad-aaa111", "namespace": "default",
                          "owner_deployment": "ad", "revision": 1},
                         {"name": "ad-bbb222", "namespace": "default",
                          "owner_deployment": "ad", "revision": 2}],
            pods=[pod("ad-aaa111-x1y2z", "aaa111", "ad:1"),
                  pod("ad-bbb222-q9w8e", "bbb222", "ad:2")])
        assert derive_observed(cap, ["default"])["ad"]["image"] == "ad:2"

    def test_empty_values_are_not_facts(self):
        """The generator records none for them, by design."""
        truth = derive_observed(capture(), ["default"])
        assert "selector" not in truth["kubernetes"]
        assert truth["kubernetes"]["ports"] == [443]

    def test_absent_probes_are_a_fact(self):
        assert derive_observed(capture(), ["default"])["ad"]["probes"] == ["none configured"]

    def test_other_namespaces_are_ignored(self):
        cap = capture()
        cap["services"].append({"name": "other", "namespace": "kube-system", "type": "ClusterIP"})
        assert "other" not in derive_observed(cap, ["default"])

    def test_declared_reads_workloads_and_services(self, tmp_path):
        (tmp_path / "chart.yaml").write_text("""\
kind: Deployment
metadata: {name: ad}
spec: {template: {spec: {containers: [{image: "ad:0", resources: {limits: {memory: 300Mi}}}]}}}
---
kind: Service
metadata: {name: jaeger-agent}
---
kind: Deployment
metadata: {name: templated}
spec: {template: {spec: {containers: [{image: "{{ .Values.image }}"}]}}}
""")
        declared = derive_declared(tmp_path)
        assert declared["ad"]["image"] == "ad:0"
        assert declared["jaeger-agent"] == {}
        assert "templated" not in declared, "unrendered templates are skipped, not guessed"


def skill(**services):
    return {"services": services}


GOOD_AD = {"facts": {"image": fact("ad:1"), "replicas": fact(1), "ready_replicas": fact(1),
                     # normalised as the generator stores it: requests default to limits
                     "resources": fact(normalise.resources({"limits": {"memory": "300Mi"}})),
                     "probes": fact(["none configured"]), "type": fact("ClusterIP"),
                     "ports": fact([8080]), "selector": fact({"app": "ad"})},
           "depends_on": ["flagd"]}
GOOD_K8S = {"facts": {"type": fact("ClusterIP"), "ports": fact([443])}, "depends_on": []}


class TestScore:
    def observed(self):
        return derive_observed(capture(), ["default"])

    def test_a_faithful_skill_scores_full_marks(self):
        s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S), self.observed(),
                  Truth(dependencies={"ad": ["flagd"]}))
        assert set(s.rates().values()) <= {1.0, None}
        assert not (s.facts_missing or s.facts_wrong or s.services_invented)

    def test_a_wrong_value_is_named(self):
        bad = {**GOOD_AD, "facts": {**GOOD_AD["facts"], "image": fact("ad:9")}}
        s = score("c", skill(ad=bad, kubernetes=GOOD_K8S), self.observed(), Truth())
        assert s.facts_wrong == ["ad.image: skill 'ad:9', capture 'ad:1'"]
        assert s.rates()["fact_accuracy"] < 1

    def test_a_pod_named_as_a_service_is_invented(self):
        """The DaemonSet case: a pod name the generator failed to strip."""
        s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S,
                             **{"otel-collector-agent-fxhxp": GOOD_K8S}),
                  self.observed(), Truth())
        assert s.services_invented == ["otel-collector-agent-fxhxp"]

    def test_a_declared_service_that_is_not_deployed_is_drift_not_invention(self):
        s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S, **{"jaeger-agent": GOOD_K8S}),
                  self.observed(), Truth(), declared={"jaeger-agent": {}})
        assert s.services_invented == [] and s.services_declared_only == ["jaeger-agent"]

    def test_dependencies_against_reviewed_truth(self):
        s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S), self.observed(),
                  Truth(dependencies={"ad": ["flagd", "valkey-cart"]}))
        assert s.deps_missed == ["ad -> valkey-cart"]
        assert s.rates()["dependency_recall"] == 0.5

    def test_ignored_targets_count_neither_way(self):
        withsink = {**GOOD_AD, "depends_on": ["flagd", "otel-collector"]}
        s = score("c", skill(ad=withsink, kubernetes=GOOD_K8S), self.observed(),
                  Truth(dependencies={"ad": ["flagd"]}, ignore_dependencies=["otel-collector"]))
        assert s.deps_extra == [] and s.deps_missed == []

    def test_drift_must_surface_as_a_conflict(self):
        declared = {"ad": {"image": "ad:0"}}
        silent = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S), self.observed(),
                       Truth(), declared=declared)
        assert silent.conflicts_missed == ["ad.image"]
        reported = {**GOOD_AD, "facts": {**GOOD_AD["facts"], "image": fact(
            "ad:1", conflicts=[{"value": "ad:0", "source": "declared", "origin": "chart"}])}}
        caught = score("c", skill(ad=reported, kubernetes=GOOD_K8S), self.observed(),
                       Truth(), declared=declared)
        assert caught.conflicts_missed == [] and caught.rates()["drift_recall"] == 1.0

    def test_unreviewed_truth_says_so(self):
        s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S), self.observed(), Truth())
        assert s.truth_reviewed is None





def test_a_case_with_an_unknown_key_is_rejected(tmp_path):
    """A source field the case model does not carry must fail, not vanish: the
    eval once read `helm:` and `git:` as nothing and would have run without
    the chart or the docs."""
    import pydantic

    (tmp_path / "case.yaml").write_text(
        "name: x\ncapture: c.json\nnamespaces: [default]\n"
        "sources:\n  - type: docs\n    gitt: {repo: r, ref: r, path: p}\n")
    (tmp_path / "truth.yaml").write_text("{}\n")
    with pytest.raises(pydantic.ValidationError):
        load_case(tmp_path)


def test_a_conflict_between_equal_values_is_invented_drift():
    """Six selectors were reported as drift with identical values on both sides."""
    sel = {"app": "ad"}
    same = {**GOOD_AD, "facts": {**GOOD_AD["facts"], "selector": fact(sel, conflicts=[
        {"value": sel, "source": "observed", "origin": "k8stools"},
        {"value": dict(sel), "source": "declared", "origin": "chart"}])}}
    s = score("c", skill(ad=same, kubernetes=GOOD_K8S),
              derive_observed(capture(), ["default"]), Truth())
    assert s.conflicts_spurious == ["ad.selector: every source says {'app': 'ad'}"]
    assert s.rates()["drift_precision"] == 0.0


def test_a_daemonset_is_known_from_its_pods():
    """The capture has no DaemonSet objects (no k8stools tool lists them); the
    pod's `pod-template-generation` label identifies one."""
    ds = pod("otel-collector-agent-fxhxp", "", "collector:1")
    ds["labels"] = {"pod-template-generation": "1"}
    truth = derive_observed(capture(pods=capture()["pods"] + [ds]), ["default"])
    assert truth["otel-collector-agent"]["image"] == "collector:1"
    assert "otel-collector-agent-fxhxp" not in truth


class TestDocs:
    def test_a_workload_without_a_page_is_undocumented(self):
        noted = {**GOOD_AD, "notes": [{"text": "Serves ads.", "origin": "ad.md"}]}
        s = score("c", skill(ad=noted, kubernetes=GOOD_K8S),
                  derive_observed(capture(), ["default"]), Truth())
        assert s.docs_workloads == 1 and s.docs_undocumented == []
        other = {**GOOD_K8S, "notes": [{"text": "The API.", "origin": "k.md"}]}
        bare = score("c", skill(ad=GOOD_AD, kubernetes=other),
                     derive_observed(capture(), ["default"]), Truth())
        assert bare.docs_undocumented == ["ad"]
        assert bare.rates()["doc_coverage"] == 0.0

    def test_consistency_is_none_until_judged(self):
        s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S),
                  derive_observed(capture(), ["default"]), Truth())
        assert s.rates()["doc_consistency"] is None

    def test_the_judge_reads_every_page_and_reports_contradictions(self):
        from types import SimpleNamespace

        from dkgg.eval import DocContradiction, DocVerdict, judge_docs

        sent = {}

        class Client:
            class messages:
                @staticmethod
                def parse(**kw):
                    sent.update(kw)
                    verdict = DocVerdict(pages_checked=2, contradictions=[DocContradiction(
                        page="ad.md", quote="ad never reaches readiness",
                        fact="ad.ready_replicas = 1", why="it is ready")])
                    return SimpleNamespace(parsed_output=verdict,
                                           usage=SimpleNamespace(input_tokens=1_000_000,
                                                                 output_tokens=0))

        sk = {"services": {"ad": {**GOOD_AD, "notes": [
                  {"text": "ad never reaches readiness", "origin": "ad.md"}]}},
              "general": [{"text": "How it fits together.", "origin": "_index.md"}]}
        pages, found, usd = judge_docs(sk, Client(), "claude-opus-5")
        assert pages == 2
        assert found == ["ad.md: 'ad never reaches readiness' -- ad.ready_replicas = 1 (it is ready)"]
        assert usd == 5.0
        user = sent["messages"][0]["content"]
        assert 'origin="ad.md"' in user and 'origin="_index.md"' in user
        assert "never reaches readiness" not in sent["system"][1]["text"], \
            "the notes being judged must not also appear as observed facts"

    def test_consistency_counts_pages_not_claims(self):
        s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S),
                  derive_observed(capture(), ["default"]), Truth())
        s.docs_pages_judged = 4
        s.docs_contradictions = ["ad.md: 'x' -- f (w)", "ad.md: 'y' -- f (w)",
                                 "cart.md: 'z' -- f (w)"]
        assert s.rates()["doc_consistency"] == 0.5


def test_a_skill_without_documentation_is_not_scored_on_it():
    s = score("c", skill(ad=GOOD_AD, kubernetes=GOOD_K8S),
              derive_observed(capture(), ["default"]), Truth())
    assert s.rates()["doc_coverage"] is None
