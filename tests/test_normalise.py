"""Canonical forms shared by every architecture source.

Every case here produced a false drift entry against the real OpenTelemetry
demo. Drift is only useful if it is true: a report that is mostly false
positives stops being read, which is worse than not producing one.
"""

from k8srca.arch import normalise


class TestResources:
    def test_requests_default_to_limits(self):
        # The API server does this, so a manifest declaring only limits does
        # not actually differ from the running pod.
        out = normalise.resources({"limits": {"memory": "120Mi"}})
        assert out["requests"] == {"memory": "120Mi"}

    def test_explicit_requests_are_left_alone(self):
        out = normalise.resources({"limits": {"memory": "1Gi"}, "requests": {"memory": "512Mi"}})
        assert out["requests"] == {"memory": "512Mi"}

    def test_cpu_quantities_compare_equal(self):
        # cpu: 1 and cpu: 1000m are the same request spelled two ways.
        a = normalise.resources({"requests": {"cpu": "1"}})
        b = normalise.resources({"requests": {"cpu": "1000m"}})
        assert a == b

    def test_fractional_cpu(self):
        assert normalise.quantity("cpu", "0.5") == "500m"

    def test_millicores_pass_through(self):
        assert normalise.quantity("cpu", "250m") == "250m"

    def test_memory_is_not_touched(self):
        # Memory suffixes are not interchangeable the way cpu's are; rewriting
        # them would invent differences rather than remove them.
        assert normalise.quantity("memory", "100Mi") == "100Mi"

    def test_key_order_does_not_matter(self):
        a = normalise.resources({"limits": {"memory": "1Gi", "cpu": "1"}})
        b = normalise.resources({"limits": {"cpu": "1000m", "memory": "1Gi"}})
        assert a == b

    def test_empty_is_stable(self):
        assert normalise.resources(None) == {"requests": None, "limits": None}


class TestProbes:
    def test_camel_and_snake_case_agree(self):
        # Manifests say livenessProbe; the Python client says liveness_probe.
        assert normalise.probes(["livenessProbe"]) == normalise.probes(["liveness_probe"])

    def test_order_does_not_matter(self):
        assert (normalise.probes(["readinessProbe", "livenessProbe"])
                == normalise.probes(["liveness_probe", "readiness_probe"]))

    def test_absence_is_recorded_explicitly(self):
        # "none configured" is a finding; an empty list would read as unknown.
        assert normalise.probes([]) == ["none configured"]

    def test_duplicates_collapse(self):
        assert normalise.probes(["livenessProbe", "liveness_probe"]) == ["liveness_probe"]

    def test_unknown_names_survive(self):
        assert "somethingElse" in normalise.probes(["somethingElse"])
