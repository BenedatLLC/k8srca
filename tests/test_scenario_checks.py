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
        result = checks.numeric_claims("It has 4312 restarts.", CAPTURE)
        assert not result.passed
        assert "4312" in result.findings[0].detail

    def test_thousands_separators_are_understood(self):
        assert checks.numeric_claims("It restarted 1,960 times.", CAPTURE).passed

    def test_restart_count_phrasing(self):
        assert checks.numeric_claims("restart count: 1960", CAPTURE).passed

    def test_an_answer_with_no_numbers_passes(self):
        assert checks.numeric_claims("The container is out of memory.", CAPTURE).passed


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

    def test_too_much_money_fails(self):
        assert not checks.budget(1, 0.90, Budget(max_usd=0.25)).passed

    def test_inside_budget_passes(self):
        assert checks.budget(10, 0.10, Budget()).passed


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
