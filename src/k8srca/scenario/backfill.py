"""Add workload histories to a capture taken before k8stools recorded them.

A capture from before k8stools 2.4.0 has no `workload_histories`, so on replay
`get_workload_history` falls back to images-only revisions rebuilt from its
ReplicaSet records (`complete: false`). Re-recording would fix that and move
everything else -- restart counts, lifetimes, event windows -- invalidating a
reviewed truth. A workload's history is a record of the past, though: as long as
nothing in it happened after the capture, the history read today is the history
the capture's world had, with every age shifted back to the capture's instant.

So this reads each captured workload's history through k8stools (the MCP
server, never the Kubernetes API -- CLAUDE.md), shifts its ages to
`captured_at`, and refuses the whole merge if any revision or ConfigMap write
falls after the capture: that would splice two worlds together, which a
scenario exists not to do (004 §6.1).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

#: A workload history's Duration fields, by where they sit: k8stools stores a
#: Duration field `x` as `x_seconds` before `captured_at`.
_REVISION_DURATIONS = ("age",)
_CONFIG_DURATIONS = ("age", "last_written")
_KINDS = (("deployments", "Deployment"), ("statefulsets", "StatefulSet"),
          ("daemonsets", "DaemonSet"))

_ISO = re.compile(r"^(-)?P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?"
                  r"(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


class BackfillError(RuntimeError):
    pass


def duration_seconds(text: str) -> float:
    """An ISO 8601 duration as k8stools serialises one (`P170DT6H7M26S`)."""
    m = _ISO.match(text or "")
    if not m or text in ("P", "PT"):
        raise BackfillError(f"not a duration: {text!r}")
    sign, days, hours, minutes, seconds = m.groups()
    total = (float(days or 0) * 86400 + float(hours or 0) * 3600
             + float(minutes or 0) * 60 + float(seconds or 0))
    return -total if sign else total


def to_capture(history: dict, read_at: datetime, captured_at: datetime) -> dict:
    """One `get_workload_history` result as a capture record at `captured_at`.

    Raises BackfillError if anything in it is newer than the capture.
    """
    shift = (read_at - captured_at).total_seconds()
    where = f"{history.get('kind')}/{history.get('name')}"

    def shifted(record: dict, fields: tuple[str, ...], what: str) -> dict:
        out = {k: v for k, v in record.items() if k not in fields}
        for f in fields:
            value = record.get(f)
            if value is None:
                out[f"{f}_seconds"] = None
                continue
            at_capture = duration_seconds(value) - shift
            if at_capture < 0:
                raise BackfillError(
                    f"{where}: {what} {f} is {-at_capture:.0f}s after the capture, so its "
                    f"history is not the captured world's; re-record instead")
            out[f"{f}_seconds"] = round(at_capture)
        return out

    record = {k: v for k, v in history.items() if k not in ("revisions", "config")}
    record["revisions"] = [shifted(r, _REVISION_DURATIONS, f"revision {r.get('revision')}")
                           for r in history.get("revisions") or []]
    record["config"] = [shifted(c, _CONFIG_DURATIONS, f"{c.get('kind')} {c.get('name')}")
                        for c in history.get("config") or []]
    if not record.get("complete", False):
        raise BackfillError(f"{where}: the server's history is not complete")
    return record


def workloads(capture: dict) -> list[tuple[str, str, str]]:
    """(kind, name, namespace) of every workload the capture holds."""
    return [(kind, w["name"], w["namespace"])
            for key, kind in _KINDS for w in capture.get(key) or []]


async def backfill(capture: dict, call, now=lambda: datetime.now(timezone.utc)) -> dict:
    """`capture` with `workload_histories` read through `call` (an MCP caller).

    All or nothing: a single newer-than-capture item refuses the merge.
    """
    if capture.get("workload_histories"):
        raise BackfillError("the capture already has workload histories")
    captured_at = datetime.fromisoformat(capture["captured_at"].replace("Z", "+00:00"))
    records = []
    for kind, name, namespace in workloads(capture):
        read_at = now()
        found = await call("get_workload_history", name=name, namespace=namespace, kind=kind)
        if not found:
            raise BackfillError(f"{kind}/{name}: no history returned (does it still exist?)")
        records.append(to_capture(found[0], read_at, captured_at))
    out = dict(capture)
    out["workload_histories"] = records
    out["workload_histories_backfilled"] = {
        "read_at": now().isoformat(timespec="seconds"),
        "note": "read after the capture and shifted to captured_at; nothing in them "
                "is newer than the capture (k8srca scenario backfill-histories)",
    }
    return out
