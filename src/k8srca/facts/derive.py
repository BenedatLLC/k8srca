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
    """Pods with no node, and a Deployment's failing conditions (k8stools 4.1.0):
    what keeps a rollout from progressing while its old pods still serve."""
    out = []
    if pods := r.get("unscheduled"):
        out.append(Fact(W, "pods_unscheduled", len(pods),
                        f"{len(pods)} pod(s) of {name} have no node: " + "; ".join(pods),
                        scope=f"the ready count ({r.get('ready')}/{r.get('desired')}) "
                              f"is of the pods that are serving"))
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
        workload = workload_of_pod(name) if kind == "Pod" else None
        subject = _subject(ns, "Deployment", workload) if workload else _subject(ns, kind, name)
        out.append(Fact(subject, f"event_{e.get('reason')}", e.get("count"),
                        _event_text(obj, e), scope="the event record's span"))
    return out


def _event_text(obj: str, e: dict) -> str:
    text = (f"{e.get('reason')} on {obj}: {e.get('count')} times, first "
            f"{human(seconds(e.get('first_seen')))} ago, last "
            f"{human(seconds(e.get('last_seen')))} ago")
    if e.get("reason") in ("FailedScheduling", "FailedMount", "FailedCreate"):
        text += f" ({_first_sentence(e.get('message'))})"
    return text


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
                              f"{human(seconds(fs.get('last_seen')))} ago"))
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
    pct = u.get("memory_percent_of_limit")
    if pct is None:
        return []
    cname = u.get("container")
    kb = ["memory_limit_plausibly_reached[C]"] if pct >= 90 else []
    return [Fact(f"{W}/{cname}", "memory_usage", pct,
                 f"{cname} used {u.get('memory')} of its memory limit ({pct:g}%) in a sample "
                 f"{human(seconds(u.get('sampled')))} ago",
                 scope="one sample, not a peak", kb=kb)]


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
            out.append(Fact(f"{W}/{cname}", "runtime_sizing", found,
                            f"{cname} sets memory sizing: {', '.join(found)}",
                            scope="the env and args the pod spec states",
                            kb=["runtime_sizing_flags_set[C]"]))
        elif not indirect:
            out.append(Fact(f"{W}/{cname}", "runtime_sizing", [],
                            f"{cname}'s pod spec sets no heap or memory sizing in its env "
                            f"or args" + (f" ({'; '.join(shown)})" if shown else ""),
                            scope="what the pod spec states: variables the image sets are "
                                  "not shown here",
                            kb=["not runtime_sizing_flags_set[C]"]))
    if sel := r.get("node_selector"):
        out.append(Fact(W, "node_selector", sel,
                        f"{pod} requires a node labelled "
                        + ", ".join(f"{k}={v}" for k, v in sel.items())))
    return out


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
