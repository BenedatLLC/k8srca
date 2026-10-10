"""The synthesis stage: a model writes the prose and classifies (design §5.1).

The model receives the collected graph, reduced to what is safe to send
(principle 7): component names, workload types, declared configuration, the
documentation leads, and each edge with the *names* of the variables that
produced it, never their values. It returns, for each component, a kind, a
purpose, the effect of its failure, and a kind for each of its edges, every
statement citing the inputs it rests on.

The model proposes; it does not decide what exists (principle 3). `validate`
rejects output that adds or drops a component or an edge, uses a kind outside
the fixed sets, or cites an input that was not given. One repair round sends
the errors back; a result that still fails is returned with its errors, and the
build reports them rather than writing silent mistakes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .providers import Provider, Usage
from .review import Review

#: Bump when the prompt or schema changes, so a cached synthesis is redone.
PROMPT_VERSION = 3

COMPONENT_KINDS = ["service", "datastore", "queue", "cache", "feature-flags", "gateway",
                   "ui", "telemetry", "load-generator", "job", "external"]
EDGE_KINDS = ["sync-call", "async-event", "datastore", "cache", "feature-flags",
              "telemetry", "route", "load"]
DERIVED = {"edges", "callers"}

_STATEMENT = {
    "type": "object", "additionalProperties": False, "required": ["text", "cites"],
    "properties": {"text": {"type": "string"},
                   "cites": {"type": "array", "items": {"type": "string"}}},
}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["overview", "components"],
    "properties": {
        "overview": {"type": "array", "items": _STATEMENT},
        "components": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "kind", "kind_cites", "purpose", "if_it_fails", "edges"],
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": COMPONENT_KINDS},
                "kind_cites": {"type": "array", "items": {"type": "string"}},
                "purpose": {"type": "array", "items": _STATEMENT},
                "if_it_fails": {"type": "array", "items": _STATEMENT},
                "edges": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["to", "kind", "soft", "cites"],
                    "properties": {
                        "to": {"type": "string"},
                        "kind": {"type": "string", "enum": EDGE_KINDS},
                        "soft": {"type": "boolean"},
                        "cites": {"type": "array", "items": {"type": "string"}},
                    }}},
            }}},
    },
}

SYSTEM = """You describe a software deployment for engineers and agents who will investigate
it when something breaks. You are given its components, how they connect, what
its official chart declares, and its official documentation. You write what each
component is and what its failure does to the others.

Rules:

1. Describe the system, never how to diagnose it. No "check", "investigate",
   "look at", "first", no ranked causes, no troubleshooting steps. Diagnosis
   belongs elsewhere.
2. No live state. Nothing about whether something is running, ready, healthy
   or restarting: you are not told, and it changes.
3. Every statement cites the inputs it rests on, in `cites`, using exactly:
     docs:<origin>      a documentation page, by the origin given
     chart:<origin>     the declared configuration, by the origin given
     env:<VAR>          an environment variable named in a component's edges
     config:<KEY>       a configuration key named in a component's edges
     derived:edges      the connections given
     derived:callers    the callers given
     review             a reviewed decision given in `review`
   A statement you cannot cite, leave out. Prefer fewer, sourced statements.
4. purpose: one to three statements, from the documentation where it exists;
   from the connections and declared configuration otherwise (cite those).
5. if_it_fails: the effect on other components. Quote or paraphrase the
   documentation where it states the effect; otherwise derive it from the
   callers and the edge kinds (derived:callers). Never advice. When a component
   is reached only through a proxy route, say what its loss means for the
   users of that route, not only for the proxy.
6. kind says what a component is for in this system, not what technology it
   is: a database that stores telemetry is telemetry; Redis or Valkey holding
   state the callers cannot do without is still a cache if the documentation
   calls it one, with edges that are not soft. One of: service (serves requests), datastore (persistent state),
   queue (a message broker), cache (fast state callers can do without),
   feature-flags (configuration with defaults), gateway (routes or proxies
   traffic), ui (an operator or user interface), telemetry (receives or stores
   traces, metrics, logs), load-generator (synthetic traffic), job (runs to
   completion), external (outside the application: the platform, a third party).
7. Classify every edge given, and only those, in `edges`:
     sync-call      a request the caller waits on
     async-event    publishing to or consuming from a queue
     datastore      reading and writing persistent state
     cache          a cache the caller can fall back from
     feature-flags  reading flag values, with defaults on failure
     telemetry      sending traces, metrics or logs
     route          a proxy forwarding requests on a path to the target; the
                    proxy keeps serving its other paths when the target fails
     load           synthetic traffic
   soft = true when the caller carries on without the target (feature flags with
   defaults, telemetry, a cache it can bypass); false otherwise.
8. overview: two to five statements on what the system is and where its traffic
   comes from, cited.
9. Respect `review`: its kinds and edge kinds are decided; its rejected claims
   must not appear in any form."""


@dataclass
class Synthesis:
    data: dict
    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)
    digest: str = ""
    model: str = ""


def inputs(g: dict, review: Review | None = None) -> dict:
    """What the model is given: the graph, reduced to what is safe to send."""
    components = []
    for name, c in sorted(g["components"].items()):
        components.append({
            "name": name,
            "workload": c.get("workload"),
            "aliases": c.get("aliases") or [],
            "declared": [{"fact": k, "value": f["value"], "origin": f["origin"]}
                         for k, f in sorted((c.get("declared") or {}).items())],
            "docs": [{"origin": d["origin"], "text": d["text"]} for d in c.get("docs") or []],
            "edges": [{"to": e["to"],
                       "vars": sorted({ev["var"] for ev in e["evidence"]
                                       if ev.get("var") and ev.get("via", "env") == "env"}),
                       "config": sorted({ev["var"] for ev in e["evidence"]
                                         if ev.get("via") == "config"})}
                      for e in g["edges"] if e["from"] == name],
            "callers": sorted({e["from"] for e in g["edges"] if e["to"] == name}),
        })
    out = {"components": components,
           "system_docs": [{"origin": d["origin"], "text": d["text"]}
                           for d in g.get("general_docs") or []]}
    if review is not None:
        out["review"] = review.for_prompt()
    return out


def digest(inp: dict, model: str) -> str:
    material = json.dumps({"inputs": inp, "model": model, "prompt": PROMPT_VERSION,
                           "schema": SCHEMA, "system": SYSTEM}, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _allowed_cites(inp: dict, comp: dict | None) -> set[str]:
    allowed = {f"derived:{d}" for d in DERIVED}
    if "review" in inp:
        allowed.add("review")
    pool = inp["components"] if comp is None else [comp]
    for c in pool:
        allowed |= {f"docs:{d['origin']}" for d in c["docs"]}
        allowed |= {f"chart:{f['origin']}" for f in c["declared"]}
        allowed |= {f"env:{v}" for e in c["edges"] for v in e["vars"]}
        allowed |= {f"config:{k}" for e in c["edges"] for k in e.get("config") or []}
    allowed |= {f"docs:{d['origin']}" for d in inp["system_docs"]}
    return allowed


def validate(data: dict, inp: dict) -> list[str]:
    errors = []
    by_name = {c["name"]: c for c in inp["components"]}
    got = {c.get("name"): c for c in data.get("components") or []}
    errors += [f"component {n} is missing" for n in sorted(set(by_name) - set(got))]
    errors += [f"component {n} was not given" for n in sorted(set(got) - set(by_name))]
    every = _allowed_cites(inp, None)

    def cites_ok(where: str, cites: list[str], allowed: set[str], required: bool = True):
        if required and not cites:
            errors.append(f"{where}: no citation")
        for c in cites:
            if c not in allowed:
                errors.append(f"{where}: cites {c!r}, which was not given")

    for s in data.get("overview") or []:
        cites_ok("overview", s.get("cites") or [], every)
    for name, out in got.items():
        comp = by_name.get(name)
        if comp is None:
            continue
        allowed = _allowed_cites(inp, comp) | {f"docs:{d['origin']}" for d in inp["system_docs"]}
        cites_ok(f"{name}.kind", out.get("kind_cites") or [], allowed)
        for section in ("purpose", "if_it_fails"):
            for s in out.get(section) or []:
                cites_ok(f"{name}.{section}", s.get("cites") or [], allowed)
        want = {e["to"] for e in comp["edges"]}
        have = [e.get("to") for e in out.get("edges") or []]
        errors += [f"{name}: edge to {t} is not classified" for t in sorted(want - set(have))]
        errors += [f"{name}: edge to {t} was not given" for t in sorted(set(have) - want)]
        for e in out.get("edges") or []:
            cites_ok(f"{name} -> {e.get('to')}", e.get("cites") or [], allowed)
    return errors


def synthesize(provider: Provider, inp: dict) -> Synthesis:
    """One call, validated; one repair round if the first answer is invalid."""
    prompt = ("Here is the deployment. Write the overview, and for every component its "
              "kind, purpose, effect of failure and edge kinds.\n\n"
              f"<deployment>\n{json.dumps(inp, indent=1, sort_keys=True)}\n</deployment>")
    data, usage = provider.complete(SYSTEM, prompt, SCHEMA, "deployment_wiki")
    errors = validate(data, inp)
    if errors:
        repair = (prompt + "\n\nYour previous answer had these problems. Return the whole "
                  "answer again with them fixed:\n" + "\n".join(f"- {e}" for e in errors)
                  + f"\n\n<previous>\n{json.dumps(data)}\n</previous>")
        data, second = provider.complete(SYSTEM, repair, SCHEMA, "deployment_wiki")
        usage = Usage(usage.input_tokens + second.input_tokens,
                      usage.output_tokens + second.output_tokens,
                      None if usage.usd is None or second.usd is None
                      else round(usage.usd + second.usd, 4))
        errors = validate(data, inp)
    return Synthesis(data=data, usage=usage, errors=errors,
                     digest=digest(inp, getattr(provider, "model", "")),
                     model=getattr(provider, "model", ""))


def estimate_usd(inp: dict, model: str) -> float | None:
    """A rough upper bound before calling: ~4 characters a token, and up to
    ~600 output tokens a component, doubled for a possible repair round."""
    from .providers import price

    input_tokens = (len(SYSTEM) + len(json.dumps(inp))) // 4
    output_tokens = 600 * len(inp["components"]) + 500
    one = price(model, input_tokens, output_tokens)
    return None if one is None else round(2 * one, 2)


def apply_review(data: dict, review: Review | None) -> dict:
    """Reviewed decisions win over the model's (design §5.4)."""
    if review is None:
        return data
    for c in data.get("components") or []:
        if c["name"] in review.component_kinds:
            c["kind"] = review.component_kinds[c["name"]]
            c["kind_cites"] = ["review"]
        for e in c.get("edges") or []:
            forced = review.edge_kind(c["name"], e["to"])
            if forced is not None:
                e["kind"], e["soft"], e["cites"] = forced[0], forced[1], ["review"]
    return data
