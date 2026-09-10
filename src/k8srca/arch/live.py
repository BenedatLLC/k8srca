"""Architecture from live cluster state, via k8stools.

Answers "how does this system work *today*". Needs no permissions beyond the
read-only ClusterRole already in use, and cannot go stale, because it is read
at build time from the running system.

What it cannot supply: why the system is shaped this way, who owns a service,
what to do when one breaks. Those come from charts and documentation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..config import McpServer
from ..mcp_client import connect
from ..tools import wrap_mcp_tool
from . import normalise
from .model import Architecture

# Env values that reference another service, e.g. "cart:8080",
# "http://shipping:8080". The demo wires its call graph this way and so do
# most Helm-templated stacks.
ADDR_HINT = re.compile(r"(_ADDR|_HOST|_URL|_ENDPOINT|_SERVICE|_BROKER)$", re.I)

# Never copy a value that looks like a credential into a skill bundle: skills
# are downloaded into sandboxes and are readable by the agent.
SECRET_HINT = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY|CREDENTIAL|AUTH)", re.I)


def _referenced_service(value: str, services: set[str], exclude: str) -> str | None:
    for name in services:
        if name == exclude:
            continue
        if re.search(rf"(^|[^A-Za-z0-9-]){re.escape(name)}([^A-Za-z0-9-]|$)", value):
            return name
    return None


async def collect(server: McpServer, namespaces: list[str], arch: Architecture) -> Architecture:
    async with connect(server.for_host()) as srv:
        tools = {t.name: wrap_mcp_tool(t, srv.session, prefix=server.prefix)
                 for t in srv.tools}

        async def call(name: str, **kwargs: Any) -> list[dict]:
            out = await tools[name].call(kwargs)
            rows = []
            for block in out:
                if isinstance(block, dict) and block.get("type") == "text":
                    try:
                        rows.append(json.loads(block["text"]))
                    except json.JSONDecodeError:
                        continue
            return rows

        for ns in namespaces:
            services = await call("get_service_summaries", namespace=ns)
            names = {s["name"] for s in services}
            deployments = {d["name"]: d for d in await call("get_deployment_summaries", namespace=ns)}
            pods = await call("get_pod_summaries", namespace=ns)

            for svc in services:
                s = arch.service(svc["name"], ns)
                s.add("type", svc.get("type"), "observed", "k8stools")
                s.add("ports", [p.get("port") for p in (svc.get("ports") or [])],
                      "observed", "k8stools")
                s.add("selector", svc.get("selector"), "observed", "k8stools")

            for name, dep in deployments.items():
                s = arch.service(name, ns)
                s.add("replicas", dep.get("total_replicas"), "observed", "k8stools")
                s.add("ready_replicas", dep.get("ready_replicas"), "observed", "k8stools")

            # One representative pod per workload carries the container detail:
            # image, resources, probes, and the env that reveals dependencies.
            seen: set[str] = set()
            for pod in pods:
                workload = re.sub(r"-[a-z0-9]{6,10}-[a-z0-9]{5}$|-[0-9]+$", "", pod["name"])
                if workload in seen:
                    continue
                seen.add(workload)
                specs = await call("get_pod_spec", pod_name=pod["name"], namespace=ns)
                if not specs:
                    continue
                spec = specs[0]
                s = arch.service(workload, ns)
                for c in (spec.get("containers") or [])[:1]:
                    s.add("image", c.get("image"), "observed", "k8stools")
                    s.add("resources", normalise.resources(c.get("resources")),
                          "observed", "k8stools")
                    probes = normalise.probes([p for p in normalise.PROBE_KEYS if c.get(p)])
                    # Recorded even when empty: normalise.probes returns
                    # ["none configured"], because absence is a finding and
                    # would otherwise read as "unknown".
                    s.add("probes", probes, "observed", "k8stools")
                    for env in (c.get("env") or []):
                        value = str(env.get("value") or "")
                        if not value or SECRET_HINT.search(env.get("name", "")):
                            continue
                        dep = _referenced_service(value, names, workload)
                        if dep and ADDR_HINT.search(env.get("name", "")):
                            s.depends_on.add(dep)
                if spec.get("node_selector"):
                    s.add("node_selector", spec["node_selector"], "observed", "k8stools")
                if spec.get("service_account_name"):
                    s.add("service_account", spec["service_account_name"], "observed", "k8stools")

    arch.sources.append({"type": "live_cluster", "origin": server.name,
                         "namespaces": ",".join(namespaces)})
    return arch
