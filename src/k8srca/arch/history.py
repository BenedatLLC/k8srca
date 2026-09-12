"""Change history from the cluster's own record: ReplicaSet revisions.

`inspect_recent_changes` is the weakest action in the RCA skill (001 §6.1) --
"what changed just before this started" is among the highest-value questions
and the one the agent could least often answer.

An upstream chart repository does not answer it. Its history records what the
*project* changed, not what happened to *this* cluster, and a deployment that
tracks upstream loosely will show upstream commits that were never applied here
and miss local changes that were.

Kubernetes keeps the right record itself. Every update to a Deployment creates a
new ReplicaSet carrying the pod template of that revision, so the ReplicaSets
owned by a Deployment *are* its change log.

**"Nothing changed" is a result, not a blank.** A workload untouched for months
rules out the entire recent-regression family of hypotheses, which is more
useful than the agent reporting that it could not check.

Read through the k8stools MCP server like every other cluster access. k8srca
never talks to the Kubernetes API or runs kubectl itself -- see CLAUDE.md. An
earlier version of this module did, because k8stools had no ReplicaSet tool;
`get_replicaset_summaries` landed in k8stools 1.2.0 and removed the reason.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from ..config import ArchSource, McpServer
from ..mcp_client import connect
from ..tools import wrap_mcp_tool
from .model import Architecture

# Fields worth diffing between revisions. Anything else is churn that would
# bury the signal in a "what changed" answer.
TRACKED = ("images", "desired_replicas")

# k8stools renders durations as ISO-8601, e.g. "P145DT8H3M".
ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>[\d.]+)S)?)?$"
)


def parse_age(value: Any) -> timedelta | None:
    """Parse an age field into a timedelta, tolerating shapes we do not expect."""
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    if not isinstance(value, str):
        return None
    m = ISO_DURATION.match(value)
    if not m:
        return None
    parts = {k: float(v) for k, v in m.groupdict().items() if v}
    return timedelta(days=parts.get("days", 0), hours=parts.get("hours", 0),
                     minutes=parts.get("minutes", 0), seconds=parts.get("seconds", 0))


def describe(before: dict, after: dict) -> list[str]:
    changes = []
    for field in TRACKED:
        if before.get(field) != after.get(field):
            changes.append(f"{field}: {before.get(field)} -> {after.get(field)}")
    return changes


async def collect_history(source: ArchSource, arch: Architecture,
                          server: McpServer) -> int:
    async with connect(server.for_host()) as srv:
        tool = next((t for t in srv.tools if t.name == "get_replicaset_summaries"), None)
        if tool is None:
            raise RuntimeError(
                "k8stools exposes no get_replicaset_summaries tool; change_history "
                "needs k8stools >= 1.2.0. Upgrade the k8stools container, or drop the "
                "change_history source from k8srca.yaml."
            )
        wrapped = wrap_mcp_tool(tool, srv.session, prefix=server.prefix)

        recorded = 0
        for ns in source.namespaces:
            out = await wrapped.call({"namespace": ns})
            rows = []
            for block in out:
                if isinstance(block, dict) and block.get("type") == "text":
                    try:
                        rows.append(json.loads(block["text"]))
                    except json.JSONDecodeError:
                        continue

            by_deployment: dict[str, list[dict]] = {}
            for row in rows:
                owner = row.get("owner_deployment")
                if owner:
                    by_deployment.setdefault(owner, []).append(row)

            for name, revisions in by_deployment.items():
                # k8stools sorts oldest-first, but do not depend on it: the
                # diff below is meaningless in the wrong order.
                revisions.sort(key=lambda r: (r.get("revision") is None, r.get("revision") or 0))
                s = arch.service(name, ns)
                newest = revisions[-1]

                s.add("revisions", len(revisions), "observed", "k8stools")
                age = parse_age(newest.get("age"))
                if age is not None:
                    s.add("last_changed", f"{age.days}d ago", "observed", "k8stools")
                recorded += 1

                if len(revisions) >= 2:
                    diff = describe(revisions[-2], newest)
                    if diff:
                        s.add("last_change_was", "; ".join(diff), "observed", "k8stools")

    arch.sources.append({"type": "change_history", "origin": server.name,
                         "namespaces": ",".join(source.namespaces)})
    return recorded
