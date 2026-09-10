"""Architecture from a chart or manifest checkout — what the system is *declared* to be.

Rendered charts and plain manifests both reduce to Kubernetes objects, so this
reads YAML documents and extracts the same facts the live source does. The
value is not duplication: it is the *difference*. A chart declaring resources
the running deployment does not have is drift, and drift is diagnostic.

Templated Helm charts with unrendered `{{ ... }}` are skipped rather than
guessed at; point this at rendered output (`helm template > out.yaml`) or at
plain manifests.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ..config import ArchSource
from .model import Architecture

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job"}
TEMPLATE = re.compile(r"\{\{.*?\}\}")


def _documents(path: Path):
    for f in sorted(path.rglob("*.y*ml")):
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if TEMPLATE.search(text):
            continue      # unrendered template; guessing at values would be worse than skipping
        try:
            for doc in yaml.safe_load_all(text):
                if isinstance(doc, dict) and doc.get("kind"):
                    yield f, doc
        except yaml.YAMLError:
            continue


def collect_charts(source: ArchSource, arch: Architecture) -> int:
    if source.path is None:
        return 0
    root = Path(source.path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"chart_repo path does not exist: {root}")

    found = 0
    for file, doc in _documents(root):
        kind = doc.get("kind")
        name = (doc.get("metadata") or {}).get("name")
        if not name or kind not in WORKLOAD_KINDS | {"Service"}:
            continue
        origin = str(file.relative_to(root))
        ns = (doc.get("metadata") or {}).get("namespace", "default")
        s = arch.service(name, ns)
        found += 1

        if kind == "Service":
            spec = doc.get("spec") or {}
            s.add("ports", [p.get("port") for p in (spec.get("ports") or [])], "declared", origin)
            s.add("selector", spec.get("selector"), "declared", origin)
            continue

        spec = (doc.get("spec") or {})
        s.add("replicas", spec.get("replicas"), "declared", origin)
        pod = ((spec.get("template") or {}).get("spec")) or {}
        for c in (pod.get("containers") or [])[:1]:
            s.add("image", c.get("image"), "declared", origin)
            res = c.get("resources") or {}
            s.add("resources", {"requests": res.get("requests"),
                                "limits": res.get("limits")}, "declared", origin)
            probes = [p for p in ("livenessProbe", "readinessProbe", "startupProbe") if c.get(p)]
            s.add("probes", probes or ["none configured"], "declared", origin)
    arch.sources.append({"type": "chart_repo", "origin": str(root)})
    return found
