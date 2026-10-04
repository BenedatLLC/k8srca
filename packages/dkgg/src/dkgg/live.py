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
from urllib.parse import urlsplit
from typing import Any

from . import normalise
from .mcp import connect
from .model import Architecture
from .sources import Server

# Env values that reference another service, e.g. "cart:8080",
# "http://shipping:8080". The demo wires its call graph this way and so do
# most Helm-templated stacks.
ADDR_HINT = re.compile(r"(_ADDR|_HOST|_URL|_ENDPOINT|_SERVICE|_BROKER)$", re.I)

# Never copy a value that looks like a credential into a skill bundle: skills
# are downloaded into sandboxes and are readable by the agent. Matched as whole
# `_`-separated words of the name: as a bare substring, KEY matched VALKEY_ADDR,
# and cart's own datastore was skipped as a secret (found by `k8srca eval arch`).
SECRET_HINT = re.compile(
    r"(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|KEY|APIKEY|CREDENTIALS?|AUTH|"
    r"AUTHORIZATION)(_|$)", re.I)


def _workload(pod: dict, rs_owner: dict[str, str | None]) -> str:
    """The workload a pod belongs to.

    From its controlling owner (`PodSummary.owner`, k8stools 2.3.0), not its
    name: a DaemonSet pod is `<name>-<5 chars>`, which no suffix rule can tell
    from other names, and guessing recorded `otel-collector-agent-fxhxp` as a
    service with the DaemonSet's facts attached (found by `k8srca eval arch`).
    A Deployment's pods are owned by a ReplicaSet, whose `owner_deployment`
    names the Deployment. Without an owner -- a capture from before 2.3.0, or
    a bare pod -- the name rule is all there is.
    """
    owner = pod.get("owner") or ""
    kind, _, name = owner.partition("/")
    if kind == "ReplicaSet" and rs_owner.get(name):
        return rs_owner[name]
    if kind in ("DaemonSet", "StatefulSet", "ReplicaSet", "Job") and name:
        return name
    return re.sub(r"-[a-z0-9]{6,10}-[a-z0-9]{5}$|-[0-9]+$", "", pod["name"])


def _url_host(value: str) -> str | None:
    """The host of a URL-shaped value (`postgres://u:p@postgresql/db`), or None.

    Only the host is read; credentials in the URL never leave this function.
    """
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    return parts.hostname if parts.scheme and parts.netloc else None


def _dependency(name: str, value: str, services: set[str], exclude: str) -> str | None:
    """The Service an env var points at, if any.

    Two ways to qualify: a name in the address family (`*_ADDR`, `*_URL`, ...)
    whose value names a Service, or a URL-shaped value whose host *is* a Service,
    whatever the variable is called. The second catches connection strings
    (`DB_CONNECTION_STRING=postgres://...@postgresql/otel`) that the name rule
    missed, without widening the name rule to every variable that mentions one.
    """
    if not value or SECRET_HINT.search(name):
        return None
    dep = _referenced_service(value, services, exclude)
    if dep and ADDR_HINT.search(name):
        return dep
    host = _url_host(value)
    if host and host in services and host != exclude:
        return host
    return None


def _referenced_service(value: str, services: set[str], exclude: str) -> str | None:
    for name in services:
        if name == exclude:
            continue
        if re.search(rf"(^|[^A-Za-z0-9-]){re.escape(name)}([^A-Za-z0-9-]|$)", value):
            return name
    return None


async def collect(server: Server, namespaces: list[str], arch: Architecture) -> Architecture:
    async with connect(server.url, server.timeout_s) as (tools, call):

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

            # DaemonSets (k8stools 2.3.0). Desired and ready counts stand in for
            # replicas: one pod per eligible node.
            if "get_daemonset_summaries" in tools:
                for ds in await call("get_daemonset_summaries", namespace=ns):
                    s = arch.service(ds["name"], ns)
                    s.add("replicas", ds.get("desired_number_scheduled"), "observed", "k8stools")
                    s.add("ready_replicas", ds.get("number_ready"), "observed", "k8stools")

            # ReplicaSet -> Deployment, so a pod's owner names its workload.
            rs_owner = {}
            if "get_replicaset_summaries" in tools:
                rs_owner = {r["name"]: r.get("owner_deployment")
                            for r in await call("get_replicaset_summaries", namespace=ns)}

            # One representative pod per workload carries the container detail:
            # image, resources, probes, and the env that reveals dependencies.
            seen: set[str] = set()
            for pod in pods:
                workload = _workload(pod, rs_owner)
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
                        dep = _dependency(env.get("name", ""), str(env.get("value") or ""),
                                          names, workload)
                        if dep:
                            s.depends_on.add(dep)
                if spec.get("node_selector"):
                    s.add("node_selector", spec["node_selector"], "observed", "k8stools")
                if spec.get("service_account_name"):
                    s.add("service_account", spec["service_account_name"], "observed", "k8stools")

    arch.sources.append({"type": "live_cluster", "origin": server.name,
                         "namespaces": ",".join(namespaces)})
    return arch
