#!/usr/bin/env python3
"""Query what is deployed in this cluster.

  arch_query.py service checkout   everything known about one service
  arch_query.py deps checkout      what it calls, and what calls it
  arch_query.py blast ad           what degrades if this service fails
  arch_query.py drift              where declared and observed disagree
  arch_query.py list               every service, one line each
  arch_query.py sources            what this was built from, and when

Self-contained: stdlib only, reads architecture.json from its own directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).with_name("architecture.json")


def load() -> dict:
    if not DATA.exists():
        sys.exit(f"architecture not built: {DATA} missing (run `k8srca arch build`)")
    return json.loads(DATA.read_text())


def fmt_fact(key: str, entry: dict) -> str:
    line = f"  {key:16} {entry['value']}"
    if entry["source"] != "observed":
        line += f"   [{entry['source']}]"
    if entry.get("conflicts"):
        others = "; ".join(f"{c['source']}={c['value']}" for c in entry["conflicts"])
        line += f"\n  {'':16} DRIFT: {others}"
    return line


def cmd_service(db: dict, args) -> int:
    svc = db["services"].get(args.name)
    if not svc:
        near = [n for n in db["services"] if args.name.lower() in n.lower()]
        print(f"no service {args.name!r}." + (f" did you mean: {', '.join(near[:8])}" if near
                                              else " try: arch_query.py list"))
        return 1
    print(f"# {svc['name']}  (namespace {svc['namespace']})")
    for key in sorted(svc["facts"]):
        print(fmt_fact(key, svc["facts"][key]))
    if svc["depends_on"]:
        print(f"  {'calls':16} {', '.join(svc['depends_on'])}")
    if svc["called_by"]:
        print(f"  {'called by':16} {', '.join(svc['called_by'])}")
    for n in svc["notes"]:
        print(f"\n  note [{n['source']}] {n['origin']}:\n    {n['text']}")
    return 0


def cmd_deps(db: dict, args) -> int:
    svc = db["services"].get(args.name)
    if not svc:
        print(f"no service {args.name!r}")
        return 1
    print(f"{args.name} calls    : {', '.join(svc['depends_on']) or 'nothing recorded'}")
    print(f"{args.name} called by: {', '.join(svc['called_by']) or 'nothing recorded'}")
    print("\nNOTE: derived from configuration, not from observed traffic. It shows what\n"
          "this service *can* call, not what it called during an incident.")
    return 0


def cmd_blast(db: dict, args) -> int:
    """Transitive callers: everything that will look broken if this fails."""
    if args.name not in db["services"]:
        print(f"no service {args.name!r}")
        return 1
    affected, frontier = set(), [args.name]
    while frontier:
        current = frontier.pop()
        for caller in db["services"].get(current, {}).get("called_by", []):
            if caller not in affected:
                affected.add(caller)
                frontier.append(caller)
    if not affected:
        print(f"nothing recorded as calling {args.name} -- a failure there should not\n"
              f"produce symptoms elsewhere, though the dependency graph is configuration-\n"
              f"derived and may be incomplete.")
        return 0
    print(f"if {args.name} fails, these may show symptoms (transitive callers):\n")
    for name in sorted(affected):
        direct = args.name in db["services"][name]["depends_on"]
        print(f"  {name:24} {'calls it directly' if direct else 'indirectly'}")
    return 0


def cmd_changes(db: dict, args) -> int:
    """When this workload last changed, from the cluster's ReplicaSet history.

    "Nothing has changed" is a real answer: it rules out recent-regression
    hypotheses rather than leaving them open.
    """
    svc = db["services"].get(args.name)
    if not svc:
        print(f"no service {args.name!r}")
        return 1
    facts = svc["facts"]
    last = facts.get("last_changed", {}).get("value")
    revs = facts.get("revisions", {}).get("value")
    what = facts.get("last_change_was", {}).get("value")
    if last is None:
        print(f"{args.name}: no revision history recorded.\n"
              f"Either the change_history source is not configured, or this is not a\n"
              f"Deployment (StatefulSets and DaemonSets keep history differently).")
        return 0
    print(f"{args.name}:")
    print(f"  last changed   {last}")
    print(f"  revisions      {revs}")
    if what:
        print(f"  that change    {what}")
    else:
        print("  that change    no tracked field differed (image, resources, replicas)")
    print("\nIf this is old, a recent regression is not the explanation and should be\n"
          "ruled out rather than left open. Note this covers workload spec changes\n"
          "only -- a ConfigMap edit or a feature flag toggle leaves no revision.")
    return 0


_VERSION = re.compile(r"^v?\d+(?:\.\d+)*")


def _version(tag):
    """The version part of a tag, dropping a per-service suffix.

    These images are tagged `<version>-<service>`, so grouping on the whole tag
    puts every service in a group of its own and collapses nothing -- which is
    how a single chart-wide bump kept being reported as two dozen findings.
    """
    m = _VERSION.match(tag or "")
    return m.group(0) if m else (tag or "")


def _split_ref(ref):
    """An image reference into (name, tag), tolerating a registry port."""
    if not isinstance(ref, str):
        return str(ref), ""
    slash, colon = ref.rfind("/"), ref.rfind(":")
    return (ref[:colon], ref[colon + 1:]) if colon > slash else (ref, "")


#: A label Helm sets from the release name. It differs whenever the release was
#: installed under a different name, which says nothing about any service.
RELEASE_LABEL = "app.kubernetes.io/instance"


def _leaves(value, prefix=""):
    """Flatten a nested value to dotted paths, so a diff can name what moved."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_leaves(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: value}


def _diff_summary(key, dec, obs):
    """Name only what differs.

    Printing both sides of a nested value dumps two dicts per line and buries
    the one field that changed -- a memory limit moving from 400Mi to 600Mi is
    the finding, and it was arriving wrapped in the four keys that did not move.
    """
    if not isinstance(dec, dict) or not isinstance(obs, dict):
        return f"{key} {dec} -> {obs}", []
    dl, ol = _leaves(dec), _leaves(obs)
    changed = sorted(k for k in set(dl) | set(ol) if dl.get(k) != ol.get(k))
    if not changed:
        return f"{key} (no effective difference)", []
    parts = "; ".join(f"{k} {dl.get(k, '-')} -> {ol.get(k, '-')}" for k in changed[:3])
    more = f" (+{len(changed) - 3} more)" if len(changed) > 3 else ""
    return f"{key}: {parts}{more}", changed


def _classify(key, entry):
    """What kind of disagreement this is, and what to group it by.

    Returns (kind, group_key, detail). `kind` is "substituted" when declared and
    observed name different images -- that is not a version skew and calling it
    drift invites an answer to treat a deliberate choice as a defect. Otherwise
    it is "skew", grouped by the transition, so a bump applied to the whole chart
    collapses to one statement instead of one per service.
    """
    obs = next((c["value"] for c in entry["conflicts"] if c["source"] == "observed"), None)
    dec = next((c["value"] for c in entry["conflicts"] if c["source"] == "declared"), None)
    if obs is None or dec is None:
        return "other", f"{key}", f"{key}: " + "; ".join(
            f"{c['source']}={c['value']}" for c in entry["conflicts"])
    if key == "image":
        dn, dt = _split_ref(dec)
        on, ot = _split_ref(obs)
        if dn != on:
            return ("substituted", f"image!{dn}>{on}",
                    f"image substituted -- declared {dec}, running {obs}")
        dv, ov = _version(dt), _version(ot)
        return "skew", f"image {dv} -> {ov}", f"image {dv} -> {ov}"

    detail, changed = _diff_summary(key, dec, obs)
    if changed and all(c.endswith(RELEASE_LABEL) or c == RELEASE_LABEL for c in changed):
        # The release was installed under a different name than the chart's
        # default. Every selector and label in the chart differs as a result,
        # for every service, and none of it is about any service.
        return "release-name", f"{key} release name", detail
    return "skew", f"{key} {detail}", detail


def cmd_drift(db: dict, args) -> int:
    """Disagreements between declared and observed, grouped so one fact is one line.

    Reported per service, a single chart-wide version bump becomes a separate
    "finding" for every service it touched -- true of the healthy ones too, and
    therefore useless for telling a broken service from a working one. Answers
    cited it as cluster-wide evidence and were right about the fact and wrong
    about its significance. Grouping makes the shape visible: what is shared is
    a property of the deployment, and only what is *specific* can discriminate.
    """
    groups = {}
    for name, svc in sorted(db["services"].items()):
        for key, entry in sorted(svc["facts"].items()):
            if not entry.get("conflicts"):
                continue
            kind, group, detail = _classify(key, entry)
            groups.setdefault((kind, group), {"detail": detail, "services": []})
            groups[(kind, group)]["services"].append(name)

    if not groups:
        srcs = {s.get("type") for s in db.get("sources", [])}
        print("no drift found." + ("" if len(srcs) > 1 else
              f"\nNOTE: only one source ({', '.join(srcs) or 'none'}) was used, so drift"
              "\ncannot be detected -- it needs at least two to compare."))
        return 0

    release = {k: v for k, v in groups.items() if k[0] == "release-name"}
    rest = {k: v for k, v in groups.items() if k[0] != "release-name"}
    specific = {k: v for k, v in rest.items() if len(v["services"]) == 1}
    shared = {k: v for k, v in rest.items() if len(v["services"]) > 1}

    if specific:
        print("Specific to one service -- the only kind that can discriminate:")
        for (kind, _), v in sorted(specific.items(), key=lambda kv: kv[1]["services"]):
            print(f"  {v['services'][0]}.{v['detail']}")
    if shared:
        print("\nShared across services -- a property of the deployment, not an")
        print("explanation of any one failure:")
        for (kind, _), v in sorted(shared.items(), key=lambda kv: -len(kv[1]["services"])):
            names = ", ".join(v["services"][:6])
            more = f", +{len(v['services']) - 6} more" if len(v["services"]) > 6 else ""
            print(f"  {v['detail']:<34} {len(v['services'])} services: {names}{more}")

    if release:
        affected = sorted({n for v in release.values() for n in v["services"]})
        print(f"\nRelease-name labels differ on {len(affected)} service(s): the chart's")
        print("default release name is not the one it was installed under. Not a defect.")

    subs = [v for (kind, _), v in groups.items() if kind == "substituted"]
    if subs:
        print("\n\"substituted\" means declared and observed are different images, not")
        print("different versions of one -- usually a deliberate choice (a stock upstream")
        print("image in place of a demo build), so it is not a defect to report.")
    print("\nCite drift only where it is specific to the service you are investigating")
    print("and plausibly connected to the symptom.")
    return 0


def cmd_list(db: dict, args) -> int:
    for name, svc in sorted(db["services"].items()):
        img = svc["facts"].get("image", {}).get("value", "")
        tag = str(img).rsplit(":", 1)[-1] if img else ""
        print(f"  {name:26} {tag:22} calls={len(svc['depends_on']):2} "
              f"called_by={len(svc['called_by'])}")
    print(f"\n{len(db['services'])} services")
    return 0


def cmd_sources(db: dict, args) -> int:
    print(f"built at {db.get('built_at')}")
    for s in db.get("sources", []):
        print(f"  {s.get('type'):14} {s.get('origin','')} {s.get('namespaces','')}")
    print("\nThis is a snapshot. For anything that may have changed since, check the\n"
          "live cluster with your Kubernetes tools rather than trusting this file.")
    return 0


def cmd_intent(db: dict, args) -> int:
    """Documentation about the system rather than about one service.

    Kept separate from `service` because it answers a different question: not
    what a component is configured to be, but what it is *for* and what
    "broken" means for it. Both are needed and neither substitutes.
    """
    general = db.get("general") or []
    if not general:
        print("no system documentation in this build.\n"
              "The `docs` architecture source is not configured or matched nothing;\n"
              "see the architecture block in k8srca.yaml.")
        return 1
    for g in general:
        print(f"# [{g['source']}] {g['origin']}\n{g['text']}\n")
    with_notes = sorted(n for n, s in db["services"].items() if s.get("notes"))
    if with_notes:
        print(f"Per-service notes also exist for: {', '.join(with_notes)}")
        print("Read one with: arch_query.py service <name>")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, needs_arg in [("service", cmd_service, True), ("deps", cmd_deps, True),
                                ("blast", cmd_blast, True), ("changes", cmd_changes, True),
                                ("drift", cmd_drift, False), ("intent", cmd_intent, False),
                                ("list", cmd_list, False), ("sources", cmd_sources, False)]:
        sp = sub.add_parser(name)
        if needs_arg:
            sp.add_argument("name")
        sp.set_defaults(fn=fn)
    args = p.parse_args()
    return args.fn(load(), args)


if __name__ == "__main__":
    sys.exit(main())
