"""Architecture from a chart or manifest checkout — what the system is *declared* to be.

Rendered charts and plain manifests both reduce to Kubernetes objects, so this
reads YAML documents and extracts the same facts the live source does. The
value is not duplication: it is the *difference*. A chart declaring resources
the running deployment does not have is drift, and drift is diagnostic.

Unrendered Helm templates are skipped rather than guessed at -- point this at
rendered output (`helm template > out.yaml`) or plain manifests. The check is
deliberately narrow: it looks for `{{ }}` only in the *fields being read*, not
anywhere in the file. A rendered manifest can legitimately contain braces in
its data -- the OpenTelemetry demo embeds Grafana dashboards whose queries use
`{{__name__}}` -- and a file-level check discards 20,000 lines of perfectly
good YAML because of a string in a ConfigMap.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .sources import ArchSource
from . import normalise
from .live import _dependency
from .model import Architecture, Evidence

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job"}
TEMPLATE = re.compile(r"\{\{.*?\}\}")




def _templated(*values: Any) -> bool:
    """True if any value we intend to read is still an unrendered template."""
    return any(TEMPLATE.search(str(v)) for v in values if v is not None)


def _documents(path: Path):
    for f in sorted(path.rglob("*.y*ml")):
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        try:
            # Document by document: one unparseable template must not discard
            # the rest of a multi-document file.
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            continue
        for doc in docs:
            if isinstance(doc, dict) and doc.get("kind"):
                yield f, doc


def collect_charts(source: ArchSource, arch: Architecture) -> int:
    if source.path is None:
        return 0
    root = Path(source.path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"chart_repo path does not exist: {root}")

    docs = list(_documents(root))
    # Every Service the chart declares, for reading edges out of declared env.
    service_names = {(d.get("metadata") or {}).get("name") for _, d in docs
                     if d.get("kind") == "Service" and (d.get("metadata") or {}).get("name")}
    found = 0
    for file, doc in docs:
        kind = doc.get("kind")
        name = (doc.get("metadata") or {}).get("name")
        if not name or kind not in WORKLOAD_KINDS | {"Service"}:
            continue
        if _templated(name):
            continue        # unrendered: the name itself is a template expression
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
        if s.workload is None:
            s.workload = kind
        s.add("replicas", spec.get("replicas"), "declared", origin)
        pod = ((spec.get("template") or {}).get("spec")) or {}
        for c in (pod.get("containers") or [])[:1]:
            if _templated(c.get("image")):
                continue
            s.add("image", c.get("image"), "declared", origin)
            s.add("resources", normalise.resources(c.get("resources")), "declared", origin)
            s.add("probes", normalise.probes([p for p in normalise.PROBE_KEYS if c.get(p)]),
                  "declared", origin)
            # Declared edges: the same rule as live ones, on the chart's env.
            # A second, independent source, so an edge the live read misses
            # (or one for a component not deployed) still shows up.
            for env in (c.get("env") or []):
                dep = _dependency(env.get("name", ""), str(env.get("value") or ""),
                                  service_names, name)
                if dep:
                    s.connect(dep, Evidence(env.get("name", ""), "declared", origin))
    arch.sources.append({"type": "chart_repo", "origin": str(root)})
    return found
