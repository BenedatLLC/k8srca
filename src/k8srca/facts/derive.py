"""Facts from tool results: one function per tool (design 010 §3.3).

Each takes the tool's arguments and its structured result and returns facts
without ids. A derivation states what its result shows, with the scope the
result covers, and nothing else: where a result cannot settle a fact, the fact
is left out rather than guessed. Thresholds are written beside the rule that
uses them.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from .model import Fact, human, seconds

#: A pod named by its ReplicaSet: `<deployment>-<pod-template-hash>-<5 chars>`.
_RS_POD = re.compile(r"(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}")

#: Kubernetes rate-limits event updates (k8stools' note), so an event series
#: still running when read can show a last_seen this old.
_STILL_RUNNING_S = 15 * 60
#: A series that starts within this share of the record's start is taken to
#: run from the start of the record.
_FROM_START = 0.9


def workload_of_pod(pod: str) -> str | None:
    m = _RS_POD.fullmatch(pod or "")
    return m.group(1) if m else None


def _subject(ns: str, kind: str, name: str, container: str | None = None) -> str:
    return f"{ns}/{kind}/{name}" + (f"/{container}" if container else "")


# -- k8stools -----------------------------------------------------------------------

def namespace_health(args: dict, r: dict) -> list[Fact]:
    ns = r.get("namespace") or args.get("namespace", "")
    out = []
    for w in (r.get("workloads") or []) + (r.get("recovering") or []):
        kind, _, name = (w.get("workload") or "/").partition("/")
        lt = w.get("last_termination") or {}
        text = f"{name}: {w.get('ready')}/{w.get('desired')} ready, {w.get('restarts')} restarts"
        if lt:
            text += (f"; last termination exit {lt.get('exit_code')} ({lt.get('reason')}) "
                     f"after running {human(seconds(lt.get('instance_lifetime')))}")
        if lt.get("waiting"):
            text += f"; waiting in {lt['waiting']}"
        out.append(Fact(_subject(ns, kind, name), "workload_health",
                        {"ready": w.get("ready"), "desired": w.get("desired"),
                         "restarts": w.get("restarts")}, text,
                        scope="replica counts and the last termination, as read"))
        out += _rollout(_subject(ns, kind, name), name, w)
    healthy = r.get("healthy") or []
    if healthy:
        out.append(Fact(f"{ns}/namespace", "workloads_ready", len(healthy),
                        f"{len(healthy)} other workloads in {ns} have all their desired "
                        f"replicas ready, no pod waiting for a node, and no failing "
                        f"rollout condition",
                        scope="as read at this moment, not how long they have been so"))
    return out


def _rollout(W: str, name: str, r: dict) -> list[Fact]:
    """Pods with no node, the unbound claims they mount (4.2.0), and a
    Deployment's failing conditions (4.1.0): what keeps a rollout from
    progressing while its old pods still serve."""
    out = []
    if pods := r.get("unscheduled"):
        out.append(Fact(W, "pods_unscheduled", len(pods),
                        f"{len(pods)} pod(s) of {name} have no node: " + "; ".join(pods),
                        scope=f"the ready count ({r.get('ready')}/{r.get('desired')}) "
                              f"is of the pods that are serving",
                        kb=["pod_unscheduled[P]"]))
    out += _claim_lines(W, name, r.get("claims") or [])
    out += [f for line in r.get("placement") or [] if (f := _placement(W, line))]
    for c in r.get("conditions") or []:
        out.append(Fact(W, "rollout_condition", c, f"{name}'s Deployment reports {c}",
                        scope="the Deployment controller's own condition"))
    return out


def events(args: dict, r: dict) -> list[Fact]:
    rows = r.get("result") or []
    ns = args.get("namespace", "")
    out = []
    oldest = max((seconds(e.get("first_seen")) or 0 for e in rows), default=None)
    if oldest:
        kind = f"{args['event_type']} events" if args.get("event_type") else "events"
        out.append(Fact(f"{ns}/events/horizon", "events_horizon", human(oldest),
                        f"{kind} in {ns} go back {human(oldest)}",
                        scope="the oldest record retained, not when any problem began"))
    for e in rows:
        obj = e.get("object") or ""
        kind, _, name = obj.partition("/")
        out.append(_event_fact(ns, kind, name, obj, e))
    return out


def pod_events(args: dict, r: dict) -> list[Fact]:
    """One pod's events: the rows name the pod without its kind."""
    ns, pod = args.get("namespace", ""), args.get("pod_name", "")
    return [_event_fact(ns, "Pod", pod, f"Pod/{pod}", e) for e in r.get("result") or []]


def _event_fact(ns: str, kind: str, name: str, obj: str, e: dict) -> Fact:
    workload = workload_of_pod(name) if kind == "Pod" else None
    subject = _subject(ns, "Deployment", workload) if workload else _subject(ns, kind, name)
    kb = []
    if kind == "Pod" and e.get("reason") == "FailedScheduling":
        kb = ["failed_scheduling_event[P]"] + scheduling_kb(e.get("message"))
    elif kind == "Pod":
        kb = pull_kb(e.get("message")) + volume_kb(e.get("reason"), e.get("message"))
    return Fact(subject, f"event_{e.get('reason')}", e.get("count"),
                _event_text(obj, e), scope="the event record's span", kb=kb)


def _event_text(obj: str, e: dict) -> str:
    text = (f"{e.get('reason')} on {obj}: {e.get('count')} times, first "
            f"{human(seconds(e.get('first_seen')))} ago, last "
            f"{human(seconds(e.get('last_seen')))} ago")
    if e.get("reason") in ("FailedScheduling", "FailedMount", "FailedAttachVolume",
                           "FailedCreate"):
        text += f" ({_first_sentence(e.get('message'))})"
    elif _PULL_FAILED.match(e.get("message") or ""):
        text += f" ({_first_sentence(e.get('message'), 200)})"
    return text


# -- why a pod is not placed, and why an image is not pulled (007 §11.4 step 3) ---------
#
# What the scheduler's and the kubelet's messages say, in the pod-pending and
# image-and-sandbox families' words. A message is stated as what it says: code
# does not infer the registry's contents from "manifest unknown".

_AVAILABLE = re.compile(r"0/(\d+) nodes are available: (.*)", re.S)
_NODE_REASON = re.compile(r"(\d+) ((?:node\(s\) )?[^,]+?)(?=, \d+ |\. |\.$|$)")


def scheduling_kb(msg: str | None) -> list[str]:
    """A FailedScheduling message's kb lines. What it cites is true; what it
    does not cite is false only when its per-node reasons account for every
    node, so the list is the whole of it (the PVC check, for one, stops before
    any node is weighed)."""
    msg = msg or ""
    kb = []
    if "unbound immediate PersistentVolumeClaims" in msg:
        kb.append("pvc_not_bound[P]")
    m = _AVAILABLE.match(msg.split(" preemption:")[0])
    if not m:
        return kb
    total = int(m.group(1))
    reasons = [(int(n), why) for n, why in _NODE_REASON.findall(m.group(2))]
    complete = total > 0 and sum(n for n, _ in reasons) >= total
    # A reason with no node count is the pod's own, from PreFilter, which ends
    # the attempt before any node is weighed (Scheduling Framework, step 5).
    if not reasons and m.group(2).strip():
        kb.append("scheduling_rejected_before_nodes_weighed[P]")
    elif complete:
        kb.append("not scheduling_rejected_before_nodes_weighed[P]")
    affinity = any("affinity/selector" in why or "node affinity" in why for _, why in reasons)
    tainted = sum(n for n, why in reasons if "taint" in why)
    short = {why.split()[1] for _, why in reasons if why.startswith("Insufficient ")}
    if affinity or complete:
        kb.append(f"{'' if affinity else 'not '}scheduling_message_cites_affinity[P]")
    if tainted or complete:
        kb.append(f"{'' if tainted else 'not '}scheduling_message_cites_taint[P]")
    if complete:
        kb.append(f"{'' if tainted >= total else 'not '}"
                  f"untolerated_noschedule_taint_on_all_candidate_nodes[P]")
    for res in ("cpu", "memory"):
        if res in short or complete:
            kb.append(f"{'' if res in short else 'not '}"
                      f"scheduling_message_cites_insufficient_resource[P, {res}]")
    return kb


_PULL_FAILED = re.compile(r"Failed to pull image|Back-off pulling image|Error: (ErrImagePull|ImagePullBackOff)")
#: What a pull failure's message names, by the family's fact for it.
_PULL_KINDS = {
    "pull_message_names_manifest_not_found": re.compile(r"manifest unknown|manifest for \S+ not found", re.I),
    "pull_message_names_image_not_found": re.compile(r"manifest unknown|not found|name unknown|repository does not exist", re.I),
    "pull_message_names_unauthorized": re.compile(r"\b401\b|unauthorized|authentication required|access denied|failed to fetch (anonymous )?token|may require 'docker login'", re.I),
    "pull_message_names_forbidden": re.compile(r"\b403\b|forbidden", re.I),
    "pull_message_names_connection_failure": re.compile(r"connection refused|i/o timeout|no such host|dial tcp|TLS handshake timeout|network is unreachable", re.I),
}
_NOT_FOUND = {"pull_message_names_manifest_not_found", "pull_message_names_image_not_found"}


def pull_kb(msg: str | None) -> list[str]:
    """A pull failure's kb lines. When the registry's answer names one kind of
    failure, the others are false: it answered with that one."""
    msg = msg or ""
    if not _PULL_FAILED.search(msg):
        return []
    kb = ["image_pull_failure_event[P]"]
    if not msg.startswith("Failed to pull image"):
        return kb
    named = {k for k, rx in _PULL_KINDS.items() if rx.search(msg)}
    kb += [f"{k}[C]" for k in sorted(named)]
    if named and (named <= _NOT_FOUND or len(named) == 1):
        kb += [f"not {k}[C]" for k in sorted(_PULL_KINDS) if k not in named
               and not (k in _NOT_FOUND and named & _NOT_FOUND)]
    return kb


# -- why a claim does not bind, and why a volume is not attached or mounted (step 4) ---
#
# In the storage family's words. A claim's newest event says where it waits;
# a class's existence is stated only as the message states it, since no tool
# lists StorageClasses.

#: What a claim's event says, by the family's fact for it, in the PV
#: controller's and external-provisioner's words.
_CLAIM_NOT_FOUND = re.compile(r'storageclass\.storage\.k8s\.io "[^"]*" not found')
_CLAIM_NO_CLASS = re.compile(r"no persistent volumes available for this claim and no "
                             r"storage class is set", re.I)
_CLAIM_KINDS = ("claim_event_storage_class_not_found", "claim_event_no_volume_and_no_class",
                "claim_event_provisioner_error", "claim_event_waiting_external_provisioner",
                "claim_event_waiting_for_first_consumer")
#: Kinds that settle the rest: with no class to provision from, no
#: provisioner runs, and none waits for a consumer.
_CLAIM_SETTLED = {"claim_event_storage_class_not_found", "claim_event_no_volume_and_no_class"}
#: An object name's rule, which every StorageClass's name meets.
_DNS_SUBDOMAIN = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")
_PROVISIONER = re.compile(r"external provisioner ['\"]([^'\"]+)['\"]")
_CAPACITY = re.compile(r"insufficient capacity|out of capacity|ResourceExhausted|quota|"
                       r"not enough space|no space left", re.I)


def claim_event_kind(reason: str | None, msg: str | None) -> str | None:
    msg = msg or ""
    if reason == "ProvisioningFailed":
        return ("claim_event_storage_class_not_found" if _CLAIM_NOT_FOUND.search(msg)
                else "claim_event_provisioner_error")
    if reason == "FailedBinding" and _CLAIM_NO_CLASS.search(msg):
        return "claim_event_no_volume_and_no_class"
    return {"ExternalProvisioning": "claim_event_waiting_external_provisioner",
            "WaitForFirstConsumer": "claim_event_waiting_for_first_consumer"}.get(reason or "")


def claim_kb(storage_class: str | None, events: list[dict], complete: bool) -> list[str]:
    """An unbound claim's kb lines, from its class and its events, newest first.
    The newest event's kind is true. The others are false when that kind
    settles them, or when `complete` (the claim's recent events are given, not
    only its newest) and every one names the same kind: a CSI provisioner that fails alternates its
    ProvisioningFailed with the controller's ExternalProvisioning.

    No class (None) is not stated: k8stools shows an unset class and an empty
    one alike."""
    kb = []
    if storage_class:
        valid = len(storage_class) <= 253 and _DNS_SUBDOMAIN.fullmatch(storage_class)
        kb += [f"claim_storage_class_requested[P, {storage_class}]",
               f"{'not ' if valid else ''}storage_class_name_invalid_dns_subdomain[P]"]
    if not events:
        return kb
    newest = events[0]
    kind = claim_event_kind(newest.get("reason"), newest.get("message"))
    if kind is None:
        return kb
    kb.append(f"{kind}[P]")
    if kind == "claim_event_provisioner_error" and _CAPACITY.search(newest.get("message") or ""):
        kb.append("claim_event_names_insufficient_capacity[P]")
    if m := _PROVISIONER.search(newest.get("message") or ""):
        kb.append(f"storage_class_provisioner[P, {m.group(1)}]")
    kinds = {claim_event_kind(e.get("reason"), e.get("message")) for e in events}
    if kind in _CLAIM_SETTLED or (complete and kinds == {kind}):
        kb += [f"not {k}[P]" for k in _CLAIM_KINDS if k != kind]
    return kb


#: A composite's claim line: 'PersistentVolumeClaim/cache: Pending, storage
#: class fast-ssd; ProvisioningFailed: storageclass ... not found' (k8stools 4.2.0).
_CLAIM_LINE = re.compile(r"PersistentVolumeClaim/([^:]+): (\w+)(?:, storage class (\S+))?"
                         r"; (?:(\w+): (.*)|no events recorded.*)", re.S)


def _claim_lines(W: str, name: str, lines: list[str]) -> list[Fact]:
    """The unbound claims a workload's unscheduled pods mount, each with its
    newest event: so only a kind that settles the rest states them false."""
    out = []
    for line in lines:
        m = _CLAIM_LINE.fullmatch(line)
        if not m:
            continue
        claim, status, cls, reason, msg = m.groups()
        events = [{"reason": reason, "message": msg}] if reason else []
        out.append(Fact(W, "claim_unbound", claim,
                        f"a pod of {name} mounts the PersistentVolumeClaim {claim}, which is "
                        f"{status}" + (f", for StorageClass {cls}" if cls else "")
                        + (f"; its newest event: {reason}: {_first_sentence(msg, 200)}"
                           if reason else "; no events of it are retained"),
                        scope="the claim's status and its newest event",
                        kb=["pvc_not_bound[P]"] + claim_kb(cls, events, complete=False)))
    return out


#: A composite's placement line (k8stools 4.3.0): 'ad-x: 0 of 5 nodes match
#: its nodeSelector and required node affinity', or 'ad-x: no nodeSelector or
#: required node affinity, so all nodes match (5)', either followed by '; of
#: those, 1 has a NoSchedule or NoExecute taint it doesn't tolerate'.
_PLACEMENT = re.compile(r"(?P<pod>[^:]+): (?:(?P<n>\d+) of (?P<m>\d+) nodes match its nodeSelector "
                        r"and required node affinity|no nodeSelector or required node affinity, "
                        r"so all nodes match \((?P<all>\d+)\))(?:; of those, (?P<r>\d+) ha(?:s|ve) "
                        r"a NoSchedule or NoExecute taint.*)?", re.S)


def placement_kb(line: str) -> list[str] | None:
    """A placement line's kb lines, or None for one k8stools did not evaluate.
    The count is k8stools' own reading of the selector and tolerations, so it
    settles them, but nothing about resources, ports or volumes."""
    m = _PLACEMENT.fullmatch(line or "")
    if not m:
        return None
    if m["all"]:
        admitted = int(m["all"])
        kb = ["not node_selector_or_affinity_set[P]",
              "not no_node_matches_node_selector_or_affinity[P]"]
    else:
        admitted = int(m["n"])
        kb = ["node_selector_or_affinity_set[P]",
              f"{'' if admitted == 0 else 'not '}no_node_matches_node_selector_or_affinity[P]"]
    if admitted and m["r"] is not None:
        kb.append(f"{'' if int(m['r']) == admitted else 'not '}"
                  f"untolerated_noschedule_taint_on_all_candidate_nodes[P]")
    return kb


def _placement(W: str, line: str) -> Fact | None:
    kb = placement_kb(line)
    if kb is None:
        return None
    pod, _, rest = line.partition(": ")
    return Fact(W, "placement", rest, f"{pod}: {rest}",
                scope="k8stools' own evaluation of its nodeSelector, required node affinity "
                      "and tolerations against the nodes as read, not the scheduler's",
                kb=kb)


#: What a mount failure's message names, by the family's fact for it.
_MOUNT_KINDS = {
    "mount_message_names_csi_error": re.compile(r"rpc error|CSI driver|registered CSI drivers", re.I),
    "mount_message_names_permission_denied": re.compile(r"permission denied|access denied|"
                                                        r"\b403\b|forbidden", re.I),
    "mount_message_names_missing_volume": re.compile(r'(configmap|secret|persistentvolumeclaim)s? '
                                                     r'"[^"]*" not found', re.I),
}


def volume_kb(reason: str | None, msg: str | None) -> list[str]:
    """A pod's attach or mount failure's kb lines. When its message names one
    kind of failure, the others are false, as with a pull failure."""
    msg = msg or ""
    if reason == "FailedAttachVolume":
        kb = ["failed_attach_volume_event[P]"]
        if msg.startswith("Multi-Attach error"):
            kb.append("multi_attach_error_event[P]")
        return kb
    if reason != "FailedMount":
        return []
    kb = ["mount_failure_event[P]"]
    if "applyFSGroup failed" in msg:
        kb.append("mount_message_names_applyfsgroup_failure[P]")
    if "timed out waiting for the condition" in msg and "Unable to attach or mount" in msg:
        kb.append("volume_mount_timeout_message[P]")
    named = {k for k, rx in _MOUNT_KINDS.items() if rx.search(msg)}
    kb += [f"{k}[P]" for k in sorted(named)]
    if len(named) == 1:
        kb += [f"not {k}[P]" for k in sorted(_MOUNT_KINDS) if k not in named]
    return kb


def _first_sentence(msg: str | None, limit: int = 160) -> str:
    msg = (msg or "").strip()
    cut = msg.find(". ")
    msg = msg[:cut] if 0 < cut else msg
    return msg if len(msg) <= limit else msg[:limit - 1] + "…"


def node_summaries(args: dict, r: Any) -> list[Fact]:
    nodes = r.get("result") if isinstance(r, dict) else r
    out, bad = [], []
    for n in nodes or []:
        cond = n.get("conditions") or {}
        wrong = [c for c, v in cond.items()
                 if (c == "Ready" and v != "True") or (c != "Ready" and v == "True")]
        if wrong:
            bad.append(n)
            out.append(Fact(f"node/{n.get('name')}", "node_conditions",
                            {c: cond[c] for c in wrong},
                            f"node {n.get('name')}: " + ", ".join(f"{c} is {cond[c]}"
                                                                  for c in wrong),
                            scope="conditions as read"))
    if nodes and not bad:
        out.append(Fact("cluster/nodes", "nodes_ready", len(nodes),
                        f"all {len(nodes)} nodes are Ready, with no memory, disk or PID "
                        f"pressure", scope="conditions as read"))
    return out


def workload_report(args: dict, r: dict) -> list[Fact]:
    ns = args.get("namespace", "")
    kind, _, name = (r.get("workload") or "/").partition("/")
    W = _subject(ns, kind, name)
    out: list[Fact] = []
    horizon = None
    for note in r.get("notes") or []:
        if m := re.search(r"Event records start (\S+) ago", note):
            horizon = _human_to_seconds(m.group(1))
    events = {e.get("reason"): e for e in r.get("events") or []}
    for c in r.get("containers") or []:
        out += _container_spec(W, c)
    for inst in r.get("instances") or []:
        out += _instance(W, name, inst, events, horizon)
    for u in r.get("usage") or []:
        out += _usage(W, u)
    out += _rollout(W, name, r)
    if fs := events.get("FailedScheduling"):
        out.append(Fact(W, "pod_unschedulable", fs.get("count"),
                        f"a pod of {name} cannot be scheduled: "
                        f"{_first_sentence(fs.get('message'))}",
                        scope=f"{fs.get('count')} FailedScheduling events, last "
                              f"{human(seconds(fs.get('last_seen')))} ago",
                        kb=["failed_scheduling_event[P]"] + scheduling_kb(fs.get("message"))))
    images = {c.get("container"): c.get("image") for c in r.get("containers") or []}
    for e in r.get("events") or []:
        if (msg := e.get("message") or "").startswith("Failed to pull image"):
            out.append(Fact(W, "image_pull_failed", e.get("count"),
                            f"a pod of {name} failed to pull its image {e.get('count')} times, "
                            f"last {human(seconds(e.get('last_seen')))} ago: "
                            f"{_first_sentence(msg, 200)}",
                            scope="the event record's span", kb=pull_kb(msg)))
        elif kb := volume_kb(e.get("reason"), msg):
            out.append(Fact(W, "volume_failed", e.get("count"),
                            f"a pod of {name} reports {e.get('reason')} {e.get('count')} times, "
                            f"last {human(seconds(e.get('last_seen')))} ago: "
                            f"{_first_sentence(msg, 200)}",
                            scope="the event record's span", kb=kb))
    for inst in r.get("instances") or []:
        out += _never_started(W, inst, images.get(inst.get("container")))
    if ch := r.get("last_change"):
        text = (f"{name}'s pod template last changed {human(seconds(ch.get('age')))} ago "
                f"(revision {ch.get('revision')})")
        if ch.get("changes"):
            text += ": " + "; ".join(ch["changes"])
        out.append(Fact(W, "last_change", ch.get("age"), text,
                        scope="when it changed, not how long the workload has been healthy"))
    logs = r.get("logs") or []
    for cname in sorted({entry.get("container") for entry in logs if entry.get("container")}):
        prev = any(entry.get("previous") for entry in logs if entry.get("container") == cname)
        out.append(Fact(f"{W}/{cname}", "log_horizon", "current+previous" if prev else "current",
                        f"{cname}'s logs are kept for the current instance"
                        + (" and the previous one" if prev else "") + " only",
                        scope="Kubernetes keeps no logs of earlier instances"))
    return out


_WAITING = re.compile(r"Waiting \((\w+)\)")


def _never_started(W: str, inst: dict, image: str | None) -> list[Fact]:
    """A container on a node that has never run: what it waits in, and the
    image it waits for. A crash-looping one has run, and is pod-restarting's."""
    m = _WAITING.fullmatch(inst.get("state") or "")
    if not m or inst.get("restarts") or inst.get("last_termination") or inst.get("started"):
        return []
    cname, pod, reason = inst.get("container"), inst.get("pod"), m.group(1)
    kb = [f"waiting_reason[C, {reason}]", "pod_scheduled_but_container_waiting[P]"]
    if image:
        kb.append(f"image_reference_in_spec[C, {image}]")
    return [Fact(f"{W}/{cname}", "waiting", reason,
                 f"{cname} in {pod} has never started: it is waiting in {reason}"
                 + (f" for the image {image}" if image else ""),
                 scope="the container's state as read", kb=kb)]


def _human_to_seconds(text: str) -> float | None:
    parts = re.findall(r"(\d+)([dhms])", text)
    return sum(int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u] for n, u in parts) or None


def _container_spec(W: str, c: dict) -> list[Fact]:
    cname = c.get("container")
    C = f"{W}/{cname}"
    out = []
    lim = (c.get("limits") or {}).get("memory")
    req = (c.get("requests") or {}).get("memory")
    if lim:
        eq = lim == req
        out.append(Fact(C, "memory_limit", lim,
                        f"{cname}: memory limit {lim}, "
                        + ("equal to its request" if eq else f"request {req or 'not set'}"),
                        kb=[f"{'' if eq else 'not '}limit_equals_request[C, memory]"]))
    else:
        out.append(Fact(C, "memory_limit", None, f"{cname}: no memory limit set"))
    probes = c.get("probes") or []
    if probes == ["none configured"] or not probes:
        out.append(Fact(C, "probes", [], f"{cname}: no probes configured",
                        kb=["not liveness_probe_configured[C]"]))
    else:
        live = any(p.lower().startswith("liveness") for p in probes)
        out.append(Fact(C, "probes", probes, f"{cname}: probes " + "; ".join(probes),
                        kb=[f"{'' if live else 'not '}liveness_probe_configured[C]"]))
    return out


def _instance(W: str, name: str, inst: dict, events: dict, horizon: float | None) -> list[Fact]:
    cname, pod = inst.get("container"), inst.get("pod")
    C = f"{W}/{cname}"
    out = [Fact(C, "restarts", inst.get("restarts"),
                f"{pod} has restarted {inst.get('restarts')} times since it was created "
                f"{human(seconds(inst.get('pod_age')))} ago",
                scope="a count with no times in it, so not a rate")]
    lt = inst.get("last_termination") or {}
    if not lt:
        return out
    code, reason = lt.get("exit_code"), lt.get("reason")
    life = seconds(lt.get("instance_lifetime"))
    text = (f"{cname}'s last termination: exit {code} ({lt.get('exit_meaning')}), reason "
            f"{reason}, after running {human(life)}")
    if lt.get("restart_gap"):
        text += f"; the next start came {human(seconds(lt['restart_gap']))} later"
    out.append(Fact(C, "last_termination", {"exit_code": code, "reason": reason}, text,
                    scope="the last termination only: Kubernetes keeps no earlier ones",
                    kb=[f"last_exit_code[C, {code}]", f"last_termination_reason[C, {reason}]"]))
    killed = code == 137 or reason == "OOMKilled"
    back = events.get("BackOff")
    if killed and back and horizon:
        first, last = seconds(back.get("first_seen")), seconds(back.get("last_seen"))
        if first and last is not None and first >= _FROM_START * horizon \
                and last <= _STILL_RUNNING_S:
            out.append(Fact(
                C, "killed_in_every_retained_life", True,
                f"every termination the record keeps for {cname} was a kill: the last one "
                f"(exit {code}), and back-off from {human(first)} ago, where the event record "
                f"starts, to {human(last)} ago",
                scope="the last termination and the span of the event record",
                kb=["killed_in_every_retained_life[C]"]))
            out.append(Fact(
                W, "onset", "before the event record",
                f"when {name}'s failures began is not visible: back-off is already present "
                f"where the event record starts, {human(horizon)} ago",
                scope="the event record", kb=["not onset_observed[W]"]))
    return out


def _usage(W: str, u: dict) -> list[Fact]:
    cname = u.get("container")
    ago = human(seconds(u.get("sampled")))
    out = []
    pct = u.get("memory_percent_of_limit")
    if pct is not None:
        kb = ["memory_limit_plausibly_reached[C]"] if pct >= 90 else []
        out.append(Fact(f"{W}/{cname}", "memory_usage", pct,
                        f"{cname} used {u.get('memory')} of its memory limit ({pct:g}%) in a "
                        f"sample {ago} ago", scope="one sample, not a peak", kb=kb))
    if u.get("cpu_millicores") is not None:
        bounds = [f"{pct:g}% of its {what}" for what, pct in
                  (("request", u.get("cpu_percent_of_request")),
                   ("limit", u.get("cpu_percent_of_limit"))) if pct is not None]
        limited = u.get("cpu_limit_millicores") is not None
        out.append(Fact(f"{W}/{cname}", "cpu_usage", u["cpu_millicores"],
                        f"{cname} used {u.get('cpu')} CPU in a sample {ago} ago, "
                        + (", ".join(bounds) if bounds else "with no CPU request or limit set"),
                        scope="one sample over a minute, not a peak",
                        kb=[f"{'' if limited else 'not '}cpu_limit_set[C]"]))
    return out


#: Heap and arena sizing, by the variable or argument that carries it.
_SIZING = re.compile(r"-Xmx\S+|-XX:MaxRAMPercentage=\S+|-XX:MaxRAM=\S+|--max-old-space-size=\S+")
_SIZING_ENV = {"GOMEMLIMIT", "MALLOC_ARENA_MAX"}
_JVM_ENV = {"JAVA_TOOL_OPTIONS", "JAVA_OPTS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "NODE_OPTIONS"}


def pod_spec(args: dict, r: dict) -> list[Fact]:
    ns, pod = args.get("namespace", ""), args.get("pod_name", "")
    workload = workload_of_pod(pod)
    W = _subject(ns, "Deployment", workload) if workload else _subject(ns, "Pod", pod)
    out = []
    for c in r.get("containers") or []:
        cname = c.get("name")
        env = c.get("env") or []
        found = [f"{e['name']}={e['value']}" for e in env
                 if e.get("name") in _SIZING_ENV and e.get("value")]
        for e in env:
            if e.get("name") in _JVM_ENV:
                found += _SIZING.findall(e.get("value") or "")
        found += _SIZING.findall(" ".join((c.get("command") or []) + (c.get("args") or [])))
        indirect = bool(c.get("env_from")) or any(
            e.get("value_from") and e.get("name") in _JVM_ENV | _SIZING_ENV for e in env)
        shown = [f"{e['name']} is {e['value']!r}" for e in env
                 if e.get("name") in _JVM_ENV and e.get("value")]
        if found:
            # GOMEMLIMIT is the Go runtime's memory limit. Only ever stated true:
            # a program can also set it in code, which no spec shows.
            limit = any(f.startswith("GOMEMLIMIT=") for f in found)
            out.append(Fact(f"{W}/{cname}", "runtime_sizing", found,
                            f"{cname} sets memory sizing: {', '.join(found)}",
                            scope="the env and args the pod spec states",
                            kb=["runtime_sizing_flags_set[C]"]
                               + (["runtime_memory_limit_configured[C]"] if limit else [])))
        elif not indirect:
            out.append(Fact(f"{W}/{cname}", "runtime_sizing", [],
                            f"{cname}'s pod spec sets no heap or memory sizing in its env "
                            f"or args" + (f" ({'; '.join(shown)})" if shown else ""),
                            scope="what the pod spec states: variables the image sets are "
                                  "not shown here",
                            kb=["not runtime_sizing_flags_set[C]"]))
    required = ((r.get("affinity") or {}).get("node_affinity") or {}).get(
        "required_during_scheduling_ignored_during_execution")
    # Only for a pod with no node: another pod of the workload, already placed,
    # may come from a template without the selector.
    kb = [] if r.get("node_name") else ["node_selector_or_affinity_set[P]"]
    if sel := r.get("node_selector"):
        out.append(Fact(W, "node_selector", sel,
                        f"{pod} requires a node labelled "
                        + ", ".join(f"{k}={v}" for k, v in sel.items()), kb=kb))
    if required:
        out.append(Fact(W, "node_affinity", required,
                        f"{pod} carries a required node affinity rule", kb=kb))
    if not r.get("node_name") and r.get("volumes") is not None and not any(
            v.get("persistent_volume_claim") for v in r.get("volumes") or []):
        out.append(Fact(W, "mounts_pvc", False,
                        f"{pod} has no node and mounts no PersistentVolumeClaim",
                        kb=["not pvc_not_bound[P]"]))
    if not sel and not required and not r.get("node_name"):
        out.append(Fact(W, "node_selector", None,
                        f"{pod} has no node and sets neither a nodeSelector nor a "
                        f"required node affinity", kb=["not node_selector_or_affinity_set[P]"]))
    return out


def pvc_summaries(args: dict, r: dict) -> list[Fact]:
    """Claims that are not bound, against the pods that mount them."""
    out = []
    for c in r.get("result") or []:
        if c.get("status") == "Bound":
            continue
        ns = c.get("namespace") or args.get("namespace", "")
        for pod in c.get("mounted_by") or []:
            workload = workload_of_pod(pod)
            W = _subject(ns, "Deployment", workload) if workload else _subject(ns, "Pod", pod)
            out.append(Fact(W, "pvc_not_bound", c.get("name"),
                            f"{pod} mounts the PersistentVolumeClaim {c.get('name')}, which is "
                            f"{c.get('status')}, with no volume, for StorageClass "
                            f"{c.get('storage_class') or 'none'}" + _claim_events_text(c),
                            scope="the claim's status and its recent events, as read",
                            kb=["pvc_not_bound[P]"] + claim_kb(c.get("storage_class"),
                                                               c.get("events") or [],
                                                               complete=True)))
    return out


def _claim_events_text(c: dict) -> str:
    """k8stools 4.2.0 gives an unbound claim its events, newest first."""
    if "events" not in c:
        return ""
    if not (evs := c["events"]):
        return "; no events of it are retained"
    e = evs[0]
    return (f"; its newest event: {e.get('reason')}, {e.get('count')} times, last "
            f"{human(seconds(e.get('last_seen')))} ago: {_first_sentence(e.get('message'), 200)}")


# -- promtools ----------------------------------------------------------------------

def _metrics_horizon(r: dict) -> Fact | None:
    if not r.get("window"):
        return None
    asked = r.get("window_asked")
    return Fact("metrics/horizon", "metrics_horizon", r["window"],
                f"metrics cover the last {r['window']}"
                + (f" of the {asked} asked for" if asked and asked != r["window"] else ""),
                scope="the oldest sample retained, not when any problem began")


def _row_subject(ns: str, row: dict) -> tuple[str, str]:
    pod = row.get("pod") or ""
    workload = workload_of_pod(pod) or pod
    W = _subject(ns, "Deployment", workload)
    return W, (f"{W}/{row['container']}" if row.get("container") else W)


def memory_report(args: dict, r: dict) -> list[Fact]:
    ns = args.get("namespace", "")
    out = [h] if (h := _metrics_horizon(r)) else []
    window = r.get("window", "")
    for row in r.get("rows") or []:
        _, S = _row_subject(ns, row)
        who = row.get("container") or row.get("pod")
        if row.get("limit_mib") and row.get("peak_of_limit") is not None:
            share = row["peak_of_limit"]
            out.append(Fact(S, "memory_peak", share,
                            f"{who}: memory peaked at {row['peak_mib']:g}Mi of its "
                            f"{row['limit_mib']:g}Mi limit ({share:.0%}) over {window}",
                            scope="sampled: a peak between samples is not seen",
                            kb=["memory_limit_plausibly_reached[C]"] if share >= 0.9 else []))
        out += _lives(S, who, row.get("lives") or {}, window)
    return out


#: A climb to the limit this fast is startup, not growth within a life.
_STARTUP_CLIMB_S = 60
#: Lives that reach the limit at startup, as a share of lives with a shape,
#: needed to say memory does not grow within a life.
_AT_STARTUP_SHARE = 0.9


def _lives(S: str, who: str, lv: dict, window: str) -> list[Fact]:
    n, shaped = lv.get("count") or 0, lv.get("with_two_or_more_samples") or 0
    if not n:
        return []
    if not shaped:
        return [Fact(S, "lives", lv,
                     f"{who}: none of the {n} lives in the last {window} has two memory "
                     f"samples (median life {lv.get('lifetime_median')}), so none shows a "
                     f"shape", scope="lives shorter than the scrape interval show no shape")]
    near, rise = lv.get("starting_near_limit") or 0, lv.get("rising_to_limit") or 0
    within = _human_to_seconds(lv.get("rising_reached_limit_within") or "")
    text = (f"{who}: of the {n} lives in the last {window}, {shaped} have two or more "
            f"memory samples; in {near} memory is near the limit from the first sample")
    if rise:
        text += f", {rise} rise to it within {lv.get('rising_reached_limit_within')}"
    text += f", and {lv.get('never_near_limit') or 0} stay clear of it"
    kb = []
    at_startup = near + (rise if within is not None and within <= _STARTUP_CLIMB_S else 0)
    if at_startup >= _AT_STARTUP_SHARE * shaped:
        kb = ["not memory_grows_within_life[C]"]
    return [Fact(S, "lives", lv, text,
                 scope="lives with fewer than two samples show no shape", kb=kb)]


def restart_report(args: dict, r: dict) -> list[Fact]:
    ns = args.get("namespace", "")
    out = [h] if (h := _metrics_horizon(r)) else []
    window = r.get("window", "")
    for row in r.get("rows") or []:
        W, _ = _row_subject(ns, row)
        cname = row.get("container")
        C = f"{W}/{cname}"
        out.append(Fact(C, "restart_rate", row.get("restarts"),
                        f"{cname}: {row.get('restarts'):g} restarts in the last {window}, "
                        f"{row.get('per_hour_min'):g} to {row.get('per_hour_max'):g} an hour, "
                        f"with {row.get('hours_without_restart')} hours without one",
                        scope="restarts closer together than the scrape interval are not all "
                              "seen"))
        minutes = row.get("last_reason_minutes") or {}
        total = sum(minutes.values())
        if total:
            top, top_m = max(minutes.items(), key=lambda kv: kv[1])
            kb = ["killed_in_every_retained_life[C]"] if (
                top == "OOMKilled" and top_m >= 0.95 * total
                and row.get("hours_without_restart") == 0) else []
            out.append(Fact(C, "last_reason_share", minutes,
                            f"{cname}: the last termination reason was {top} for "
                            f"{top_m:g} of the {total:g} minutes metrics cover",
                            scope="the latest reason at each scrape, not every kill", kb=kb))
        if row.get("onset_in_window") is False and (row.get("restart_count_at_first_sample")
                                                     or 0) > 0:
            out.append(Fact(C, "onset", "before the metrics",
                            f"{cname} was already restarting at the first sample of the "
                            f"{window} metrics cover ({row['restart_count_at_first_sample']:g} "
                            f"restarts by then): no healthy period before the failures is "
                            f"visible", scope="the metrics' window",
                            kb=["not onset_observed[W]", "not healthy_period_before_kills[C]"]))
    return out


#: Derivations by the tool's name on its server (unprefixed).
DERIVATIONS: dict[str, Callable[[dict, Any], list[Fact]]] = {
    "get_namespace_health": namespace_health,
    "get_events": events,
    "get_pod_events": pod_events,
    "get_pvc_summaries": pvc_summaries,
    "get_node_summaries": node_summaries,
    "get_workload_report": workload_report,
    "get_pod_spec": pod_spec,
    "memory_report": memory_report,
    "restart_report": restart_report,
}


def derive(tool: str, arguments: dict, result: Any) -> list[Fact]:
    """Facts from one tool result. `tool` is the server's name for it."""
    fn = DERIVATIONS.get(tool)
    if fn is None or not isinstance(result, (dict, list)):
        return []
    return fn(arguments or {}, result)
