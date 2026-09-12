"""Change history from the cluster's own record: ReplicaSet revisions.

`inspect_recent_changes` is the weakest action in the RCA skill (001 §6.1) --
"what changed just before this started" is among the highest-value questions
and the one the agent could least often answer.

An upstream chart repository does not answer it. Its history records what the
*project* changed, not what happened to *this* cluster, and a deployment that
tracks upstream loosely will show upstream commits that were never applied here
and miss local changes that were. The two are different questions.

Kubernetes keeps the right record itself. Every update to a Deployment creates a
new ReplicaSet carrying the pod template of that revision, so the ReplicaSets
owned by a Deployment *are* its change log: when each revision appeared, and
what the template looked like. Comparing consecutive revisions gives what
actually changed, in this cluster.

**"Nothing changed" is a result, not a blank.** A workload untouched for months
rules out the entire recent-regression family of hypotheses, which is more
useful than the agent reporting that it could not check.

Read directly with the Kubernetes client rather than through k8stools, which
exposes no ReplicaSet tool. This runs at build time on the host under the same
read-only credential; no agent ever holds it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..config import ArchSource
from .model import Architecture
from .normalise import resources as normalise_resources

# Fields worth diffing between revisions. Anything else is noise in a
# "what changed" answer.
TRACKED = ("image", "resources", "replicas")


def _template_facts(rs: Any) -> dict[str, Any]:
    spec = rs.spec
    containers = (spec.template.spec.containers or []) if spec.template else []
    first = containers[0] if containers else None
    res = None
    if first is not None and first.resources is not None:
        res = {"limits": first.resources.limits, "requests": first.resources.requests}
    return {
        "image": getattr(first, "image", None),
        "resources": normalise_resources(res),
        "replicas": spec.replicas,
    }


def _owner(rs: Any) -> str | None:
    for ref in (rs.metadata.owner_references or []):
        if ref.kind == "Deployment":
            return ref.name
    return None


def _revision(rs: Any) -> int:
    ann = rs.metadata.annotations or {}
    try:
        return int(ann.get("deployment.kubernetes.io/revision", 0))
    except (TypeError, ValueError):
        return 0


def _describe(before: dict, after: dict) -> list[str]:
    changes = []
    for field in TRACKED:
        if before.get(field) != after.get(field):
            changes.append(f"{field}: {before.get(field)} -> {after.get(field)}")
    return changes


def collect_history(source: ArchSource, arch: Architecture, kubeconfig: str | None) -> int:
    from kubernetes import client, config as kube_config

    if kubeconfig:
        kube_config.load_kube_config(config_file=str(kubeconfig))
    else:
        kube_config.load_kube_config()
    apps = client.AppsV1Api()

    now = datetime.now(timezone.utc)
    recorded = 0
    for ns in source.namespaces:
        by_deployment: dict[str, list[Any]] = {}
        for rs in apps.list_namespaced_replica_set(ns).items:
            owner = _owner(rs)
            if owner:
                by_deployment.setdefault(owner, []).append(rs)

        for name, replicasets in by_deployment.items():
            replicasets.sort(key=_revision)
            s = arch.service(name, ns)
            newest = replicasets[-1]
            created = newest.metadata.creation_timestamp
            age_days = (now - created).days if created else None

            s.add("revisions", len(replicasets), "observed", "replicasets")
            if created:
                s.add("last_changed",
                      f"{created.date().isoformat()} ({age_days}d ago)", "observed", "replicasets")
            recorded += 1

            if len(replicasets) >= 2:
                diff = _describe(_template_facts(replicasets[-2]), _template_facts(newest))
                if diff:
                    s.add("last_change_was", "; ".join(diff), "observed", "replicasets")
    arch.sources.append({"type": "change_history", "origin": "replicasets",
                         "namespaces": ",".join(source.namespaces)})
    return recorded
