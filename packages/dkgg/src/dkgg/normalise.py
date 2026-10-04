"""Canonical forms, applied to every source before facts are compared.

Drift is only meaningful if the same value from two sources compares equal.
These normalisations exist because it otherwise does not:

* Manifests use camelCase (`livenessProbe`); the Kubernetes Python client
  reports snake_case (`liveness_probe`).
* `cpu: 1` and `cpu: 1000m` are the same quantity spelled two ways.
* A container declaring `limits` and omitting `requests` is given
  `requests = limits` by the API server, so the manifest and the running pod
  legitimately differ on paper.

Each of these produced false drift on real data, and each must be applied to
**every** source. Normalising one side only moves the false positive rather
than removing it.
"""

from __future__ import annotations

import re
from typing import Any

CPU_QUANTITY = re.compile(r"^(\d+(?:\.\d+)?)$")

PROBE_KEYS = {
    "livenessProbe": "liveness_probe",
    "readinessProbe": "readiness_probe",
    "startupProbe": "startup_probe",
    "liveness_probe": "liveness_probe",
    "readiness_probe": "readiness_probe",
    "startup_probe": "startup_probe",
}


def quantity(key: str, value: Any) -> Any:
    """Canonicalise a Kubernetes resource quantity."""
    if key == "cpu" and isinstance(value, (str, int, float)):
        m = CPU_QUANTITY.match(str(value))
        if m:
            return f"{int(float(m.group(1)) * 1000)}m"
    return value


def resources(res: dict | None) -> dict:
    """Canonical `{requests, limits}`, with the API server's own defaulting applied."""
    def canon(block: Any) -> Any:
        if not isinstance(block, dict):
            return block
        return {k: quantity(k, v) for k, v in sorted(block.items())}

    res = res or {}
    limits = canon(res.get("limits"))
    requests = canon(res.get("requests"))
    if limits and not requests:
        requests = dict(limits)
    return {"requests": requests, "limits": limits}


def probes(present: list[str]) -> list[str]:
    """Canonical, sorted probe names. Empty means none configured, which is a finding."""
    return sorted({PROBE_KEYS.get(p, p) for p in present}) or ["none configured"]
