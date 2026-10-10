"""OpenTelemetry Collector configuration: where a collector sends telemetry.

A collector names its backends in its config, not its environment, so the env
rule that finds every other edge finds none of these.
"""

from __future__ import annotations

from typing import Any

import yaml


def exporter_endpoints(text: str) -> list[tuple[str, str]]:
    """(exporter, endpoint) for each exporter a pipeline uses.

    Not a collector config, or not parseable: no endpoints. Exporters defined
    but in no pipeline send nothing, so they are not edges.
    """
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("exporters"), dict):
        return []
    pipelines = ((cfg.get("service") or {}).get("pipelines")) or {}
    used = {e for p in pipelines.values() if isinstance(p, dict)
            for e in p.get("exporters") or []}
    out = []
    for name in sorted(used):
        for endpoint in _endpoints(cfg["exporters"].get(name)):
            out.append((name, endpoint))
    return out


def _endpoints(node: Any, depth: int = 0) -> list[str]:
    """`endpoint` / `endpoints` values, at the top or one level down
    (`otlphttp: {endpoint}`, `opensearch: {http: {endpoint}}`)."""
    if not isinstance(node, dict) or depth > 1:
        return []
    found = []
    for key, value in node.items():
        if key == "endpoint" and isinstance(value, str):
            found.append(value)
        elif key == "endpoints" and isinstance(value, list):
            found += [v for v in value if isinstance(v, str)]
        elif isinstance(value, dict):
            found += _endpoints(value, depth + 1)
    return found
