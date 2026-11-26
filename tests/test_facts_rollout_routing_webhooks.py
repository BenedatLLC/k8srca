"""Facts from what k8stools 4.7.0 added: a Deployment's rollout strategy, a
Service's routing, and admission webhooks (issues #26, #27, #28).

No scenario capture records these yet (a capture made before 4.7.0 replays
them as unknown), so the results here come from k8stools itself: its mock
cluster, served by the same models the server serves, and lines in the format
its composites write.
"""

import pytest
from k8stools import k8s_tools, mock_tools

from k8srca.facts import derive
from k8srca.facts.derive import _TAKES_ALL

W = "shop/Deployment/cart"


def report(**fields) -> list:
    r = {"workload": "Deployment/cart", **fields}
    return derive("get_workload_report", {"namespace": "shop", "name": "cart"}, r)


def one(facts, key):
    [f] = [f for f in facts if f.key == key]
    return f


class TestRolloutStrategy:
    LINE = ("RollingUpdate: maxUnavailable 100%, maxSurge 0; of 1 replica, at most 1 "
            "unavailable and 0 extra during an update")

    def test_the_strategy_is_a_fact_of_the_workload(self):
        f = one(report(rollout_strategy=self.LINE), "rollout_strategy")
        assert f.subject == W
        assert f.statement == f"cart's rollout strategy is {self.LINE}"
        assert f.value == {"strategy": self.LINE, "takes_every_replica": False}
        assert "not that one is under way" in f.scope

    def test_k8stools_note_says_when_an_update_can_stop_every_replica(self):
        f = one(report(rollout_strategy=self.LINE, notes=[k8s_tools.NOTE_ROLLOUT_TAKES_ALL]),
                "rollout_strategy")
        assert f.value["takes_every_replica"] is True
        assert f.statement.endswith("take every replica out of service at once")

    def test_the_note_is_recognised_as_k8stools_writes_it(self):
        """The fact reads the note's words; this fails if k8stools rewords it."""
        assert _TAKES_ALL.search(k8s_tools.NOTE_ROLLOUT_TAKES_ALL)

    def test_an_unknown_strategy_states_nothing(self):
        """A capture from before 4.7.0: unknown, not the default."""
        facts = report(notes=[k8s_tools.NOTE_STRATEGY_NOT_RECORDED])
        assert not [f for f in facts if f.key == "rollout_strategy"]


class TestServiceRouting:
    def test_a_local_policy_names_the_nodes_with_a_ready_endpoint(self):
        f = one(report(service_routing=[
            "Service/otel-collector: internalTrafficPolicy Local; ready endpoints on node "
            "minikube"]), "service_routing")
        assert f.subject == W
        assert f.value == {"service": "otel-collector", "ready_nodes": ["minikube"],
                           "routing": "internalTrafficPolicy Local; ready endpoints on node "
                                      "minikube"}
        assert f.statement == ("Service otel-collector, which sends traffic to cart's pods, "
                               "has internalTrafficPolicy Local; ready endpoints on node "
                               "minikube")
        assert "the callers' nodes are not in this result" in f.scope

    def test_several_nodes_and_none(self):
        f = one(report(service_routing=[
            "Service/a: externalTrafficPolicy Local; ready endpoints on nodes n1, n2"]),
            "service_routing")
        assert f.value["ready_nodes"] == ["n1", "n2"]
        f = one(report(service_routing=[
            "Service/a: internalTrafficPolicy Local; no ready endpoints"]), "service_routing")
        assert f.value["ready_nodes"] == []

    def test_routing_that_is_not_local_claims_nothing_about_nodes(self):
        f = one(report(service_routing=["Service/a: sessionAffinity ClientIP"]),
                "service_routing")
        assert "ready_nodes" not in f.value
        assert "Local" not in f.scope

    def test_one_fact_for_each_service(self):
        facts = report(service_routing=["Service/a: sessionAffinity ClientIP",
                                        "Service/b: internalTrafficPolicy Local; "
                                        "ready endpoints' nodes not known"])
        assert [f.value["service"] for f in facts if f.key == "service_routing"] == ["a", "b"]


class TestWebhookLines:
    MUTATING = ("MutatingWebhookConfiguration/mem-override: webhook memory.example.com; "
                "failurePolicy Fail; objectSelector app=cart; backend "
                "webhooks/mem-svc:443/mutate (1 ready)")

    def test_a_mutating_webhook_called_for_the_workloads_pods(self):
        f = one(report(admission_webhooks=[self.MUTATING]), "admission_webhook")
        assert f.subject == W
        assert f.value == {"kind": "MutatingWebhookConfiguration",
                           "configuration": "mem-override", "webhook": "memory.example.com",
                           "failure_policy": "Fail"}
        assert f.statement == (
            "MutatingWebhookConfiguration mem-override (webhook memory.example.com) is called "
            "when cart's pods are created: failurePolicy Fail; objectSelector app=cart; "
            "backend webhooks/mem-svc:443/mutate (1 ready)")
        assert "does not record what a mutating webhook changed" in f.scope

    def test_a_validating_webhook_whose_backend_cannot_answer(self):
        f = one(report(admission_webhooks=[
            "ValidatingWebhookConfiguration/policy: webhook check.example.com; failurePolicy "
            "Fail; backend policy/svc:443 (no ready endpoints); the API server refuses these "
            "pod creates"]), "admission_webhook")
        assert f.value["kind"] == "ValidatingWebhookConfiguration"
        assert f.statement.endswith("the API server refuses these pod creates")
        assert "cannot change it" in f.scope

    def test_none_called_is_stated_only_when_the_report_says_so(self):
        f = one(report(admission_webhooks=[
            "no admission webhook is called when these pods are created"]),
            "admission_webhooks_called")
        assert f.value == 0
        assert f.statement == "no admission webhook is called when cart's pods are created"
        # Unknown (forbidden, or a capture from before 4.7.0): the report has no lines.
        assert not [f for f in report(notes=[k8s_tools.NOTE_MUTATING_NOT_RECORDED])
                    if f.key.startswith("admission_webhook")]

    def test_a_line_in_another_format_states_nothing(self):
        assert not [f for f in report(admission_webhooks=["something else entirely"])
                    if f.key.startswith("admission_webhook")]


class TestK8stoolsOwnResults:
    """The mock cluster's results, as the server serialises them."""

    @pytest.fixture
    def facts(self):
        r = mock_tools.get_workload_report("ad", "default", "Deployment")
        return derive("get_workload_report", {"namespace": "default", "name": "ad"},
                      r.model_dump(mode="json"))

    def test_the_reports_strategy_line(self, facts):
        f = one(facts, "rollout_strategy")
        assert f.subject == "default/Deployment/ad"
        assert f.value["strategy"].startswith("RollingUpdate: maxUnavailable 25%, maxSurge 25%")
        assert f.value["takes_every_replica"] is False

    def test_the_reports_webhook_line(self, facts):
        f = one(facts, "admission_webhook")
        assert f.value == {"kind": "MutatingWebhookConfiguration",
                           "configuration": "opentelemetry-operator-mutation",
                           "webhook": "mpod.kb.io", "failure_policy": "Ignore"}
        assert f.statement.endswith("not found); pod creates go ahead without it")
