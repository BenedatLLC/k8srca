"""Deterministic scenario checks (design 004 §6.2).

The closed-world check is the one S1 gates on, so most of this file is about
the two ways it can be useless: missing a fabricated name, or flagging ordinary
prose until everyone learns to ignore it.
"""

import json
from pathlib import Path

import pytest

from k8srca.scenario import checks
from k8srca.scenario.model import Budget

CAPTURE = json.loads((Path("tests/fixtures/capture.json")).read_text())

REAL_POD = "ad-5547bd5bd9-v65gj"
REAL_IMAGE = "ghcr.io/open-telemetry/demo:2.2.0-ad"


class TestClosedWorld:
    def test_a_fabricated_pod_name_is_caught(self):
        """The S1 gate: an invented pod must not pass."""
        answer = f"The pod ad-7fd9c4b8x2-q4t7z is out of memory."
        result = checks.closed_world(answer, CAPTURE)
        assert not result.passed
        assert "ad-7fd9c4b8x2-q4t7z" in result.findings[0].detail

    def test_real_entities_pass(self):
        answer = (f"{REAL_POD} is in CrashLoopBackOff; the ad container was "
                  f"killed running {REAL_IMAGE}.")
        assert checks.closed_world(answer, CAPTURE).passed

    def test_ordinary_prose_is_not_flagged(self):
        """False positives are how a check gets ignored.

        Hyphenated English, dotted prose and bare service names all look like
        object names to a naive matcher.
        """
        answer = (
            "The out-of-memory kill is well-documented. A read-only, "
            "non-blocking health-check on the ad service would have caught it. "
            "See runbooks/jvm-tuning.md and the follow-up in section 3.2. "
            "This is a memory-limit problem, not a workload-spike one."
        )
        result = checks.closed_world(answer, CAPTURE)
        assert result.passed, [f.detail for f in result.findings]

    def test_a_fabricated_image_tag_is_caught(self):
        answer = "It is running ghcr.io/open-telemetry/demo:2.9.9-ad."
        result = checks.closed_world(answer, CAPTURE)
        assert not result.passed
        assert "2.9.9-ad" in result.findings[0].detail

    def test_a_real_replicaset_name_passes(self):
        assert checks.closed_world("ReplicaSet ad-5547bd5bd9 owns it.", CAPTURE).passed

    def test_bare_service_names_are_not_checked(self):
        """`ad` and `checkout` are English; only generated names are gated."""
        assert checks.closed_world("ad calls checkout, which calls cart.", CAPTURE).passed


class TestNumericClaims:
    def test_a_real_restart_count_passes(self):
        assert checks.numeric_claims("It has 1960 restarts.", CAPTURE).passed

    def test_a_fabricated_restart_count_is_caught(self):
        result = checks.numeric_claims(f"{REAL_POD} has 4312 restarts.", CAPTURE)
        assert not result.passed
        assert "4312" in result.findings[0].detail

    def test_a_container_name_is_enough_to_attribute(self):
        result = checks.numeric_claims("The ad container has 4312 restarts.", CAPTURE)
        assert not result.passed

    def test_an_unattributed_number_is_left_alone(self):
        """A derived bound is not a claim about any container.

        A correct answer computed "154 days at the 5m backoff cap would produce
        on the order of ~44,000 restarts" to argue the looping is intermittent,
        and the check failed it on that sentence.
        """
        answer = ("At that cap, 154 days of continuous looping would produce on the "
                  "order of ~44,000 restarts. `ad` has 1960, well below.")
        assert checks.numeric_claims(answer, CAPTURE).passed

    def test_thousands_separators_are_understood(self):
        assert checks.numeric_claims("It restarted 1,960 times.", CAPTURE).passed

    def test_restart_count_phrasing(self):
        assert checks.numeric_claims("restart count: 1960", CAPTURE).passed

    def test_an_answer_with_no_numbers_passes(self):
        assert checks.numeric_claims("The container is out of memory.", CAPTURE).passed


class TestRestartTotals:
    """A pod's and a workload's restarts are sums, and cite as such.

    From a 3.0.0 batch: "`ad`'s dependency `flagd` is healthy (1/1, 26
    restarts)" failed twice. 26 is flagd's pod total (13 + 13 over two
    containers), which k8stools' composites report, and the check blamed `ad`.
    """

    CAPTURE = {
        "pods": [
            {"summary": {"name": "ad-5547bd5bd9-v65gj", "restarts": 3200},
             "container_statuses": [{"container_name": "ad", "restart_count": 3200}]},
            {"summary": {"name": "flagd-5ff58bc756-7jw6p", "restarts": 26},
             "container_statuses": [{"container_name": "flagd", "restart_count": 13},
                                    {"container_name": "flagd-ui", "restart_count": 13}]},
            {"summary": {"name": "cart-6d8f7b9c4d-x7k2p", "restarts": 2},
             "container_statuses": [{"container_name": "cart", "restart_count": 2}]},
            {"summary": {"name": "cart-6d8f7b9c4d-qzmbt", "restarts": 3},
             "container_statuses": [{"container_name": "cart", "restart_count": 3}]},
        ],
        "deployments": [{"name": "ad"}, {"name": "flagd"}, {"name": "cart"}],
    }

    def test_the_batch_sentence_passes(self):
        answer = "- `ad`'s dependency `flagd` is healthy (1/1, 26 restarts) -- not a cause."
        result = checks.numeric_claims(answer, self.CAPTURE)
        assert result.passed, [f.detail for f in result.findings]

    def test_a_workload_total_passes(self):
        assert checks.numeric_claims("cart has 5 restarts across its pods.", self.CAPTURE).passed

    def test_workloads_group_by_owner_when_the_capture_has_one(self):
        capture = {"pods": [
            {"summary": {"name": "agent-x1", "owner": "DaemonSet/agent", "restarts": 4},
             "container_statuses": [{"container_name": "agent", "restart_count": 4}]},
            {"summary": {"name": "agent-y2", "owner": "DaemonSet/agent", "restarts": 6},
             "container_statuses": [{"container_name": "agent", "restart_count": 6}]},
        ]}
        assert 10 in checks.restart_counts(capture)

    def test_a_fabricated_count_is_blamed_on_the_nearest_name(self):
        answer = "`ad` depends on `flagd`, which has 27 restarts."
        result = checks.numeric_claims(answer, self.CAPTURE)
        assert not result.passed
        assert "'flagd'" in result.findings[0].detail


class TestRequiredTools:
    def test_a_missing_required_tool_fails(self):
        result = checks.required_tools(["get_pod_summaries"], ["get_replicaset_summaries"])
        assert not result.passed

    def test_the_worker_prefix_is_ignored(self):
        """The worker registers k8s_-prefixed names; truth speaks tool names."""
        result = checks.required_tools(["k8s_get_replicaset_summaries"],
                                       ["get_replicaset_summaries"])
        assert result.passed


class TestBudget:
    def test_too_many_tool_calls_fails(self):
        assert not checks.budget(30, 0.01, Budget(max_tool_calls=25)).passed

    def test_too_much_money_is_advisory_not_a_failure(self):
        """Cost varied 3.4x across 13 runs of one unchanged scenario while calls
        varied 2.3x, and the two decouple -- so a dollar cap fails correct
        answers on an expensive day."""
        result = checks.budget(1, 0.90, Budget(max_usd=0.25))
        assert result.passed
        assert result.advisories and "0.90" in result.advisories[0].detail

    def test_a_cost_overrun_still_gets_reported(self):
        assert checks.budget(1, 0.90, Budget(max_usd=0.25)).advisories

    def test_calls_still_gate_even_when_cost_is_fine(self):
        result = checks.budget(99, 0.01, Budget(max_tool_calls=25))
        assert not result.passed

    def test_inside_budget_passes_with_no_advisory(self):
        result = checks.budget(10, 0.10, Budget())
        assert result.passed and not result.advisories


class TestMustIdentify:
    def test_a_missing_term_fails(self):
        result = checks.must_identify("Something broke.", ["memory limit"])
        assert not result.passed

    def test_matching_is_case_insensitive(self):
        assert checks.must_identify("The Memory Limit is 300Mi.", ["memory limit"]).passed

    def test_a_short_term_is_matched_on_word_boundaries(self):
        """`ad` must not be found inside "read", or truth can never name it."""
        result = checks.must_identify("I read the logs and loaded the page.", ["ad"])
        assert not result.passed

    def test_a_short_term_matches_when_actually_named(self):
        assert checks.must_identify("The ad container died.", ["ad"]).passed

    def test_a_quantity_term_matches(self):
        assert checks.must_identify("limit is 300Mi", ["300Mi"]).passed
