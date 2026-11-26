"""The workload report's NetworkPolicy line, split into its effect and the
policies in its closing parentheses (issue #53)."""

import pytest

from k8srca.facts import derive
from k8srca.facts.derive import policy_line_parts

DNS = "denies all egress; none allows port 53, so these pods can't look up names (DNS)"


@pytest.mark.parametrize("line, effect, by", [
    # The effect ends in parentheses of its own: the line used to split there.
    (f"egress: {DNS} (NetworkPolicy/a, created 5m55s ago; NetworkPolicy/b, created 41d ago)",
     DNS, "NetworkPolicy/a, created 5m55s ago; NetworkPolicy/b, created 41d ago"),
    (f"egress: {DNS} (NetworkPolicy/a, NetworkPolicy/b)", DNS, "NetworkPolicy/a, NetworkPolicy/b"),
    # One policy and several, as 4.7.0 writes them (each with its age) and as 4.6.1 did.
    ("ingress: allows all ingress (NetworkPolicy/x, created 5m55s ago)",
     "allows all ingress", "NetworkPolicy/x, created 5m55s ago"),
    ("ingress: allows all ingress (NetworkPolicy/x, created 5m55s ago; NetworkPolicy/y, "
     "created 41d ago)",
     "allows all ingress", "NetworkPolicy/x, created 5m55s ago; NetworkPolicy/y, created 41d ago"),
    ("ingress: allows all ingress (NetworkPolicy/x)", "allows all ingress", "NetworkPolicy/x"),
    ("ingress: allows all ingress (NetworkPolicy/x, NetworkPolicy/y)",
     "allows all ingress", "NetworkPolicy/x, NetworkPolicy/y"),
    # No policy selects the pods for this direction: the group is the reason.
    ("egress: not restricted (no NetworkPolicy selects these pods for egress)",
     "not restricted", "no NetworkPolicy selects these pods for egress"),
    # Nothing in parentheses at the end.
    ("ingress: allows all ingress", "allows all ingress", None),
])
def test_the_line_splits_at_its_last_group(line, effect, by):
    assert policy_line_parts(line) == (line.split(":")[0], effect, by)


@pytest.mark.parametrize("line", [
    "NetworkPolicy/x selects 1 of these 2 pods",
    "no NetworkPolicy (networking.k8s.io) selects these pods",
    "",
])
def test_a_line_that_is_not_a_direction_states_nothing(line):
    assert policy_line_parts(line) is None


def test_an_unbalanced_line_is_kept_whole():
    assert policy_line_parts("egress: denies all egress) (") == \
        ("egress", "denies all egress) (", None)
    assert policy_line_parts("egress: odd)") == ("egress", "odd)", None)


def test_the_facts_value_keeps_the_dns_clause():
    """The value is what the knowledge base and the answer check read."""
    line = f"egress: {DNS} (NetworkPolicy/a, created 5m55s ago; NetworkPolicy/b, created 41d ago)"
    [f] = [f for f in derive("get_workload_report", {"namespace": "shop"},
                             {"workload": "Deployment/cart", "network_policies": [line]})
           if f.key == "network_policy_egress"]
    assert f.value == DNS
    assert f.statement == f"cart's pods, {line}"
