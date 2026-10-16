"""Mining public debugging guides into the knowledge format (design 007 §8).

The authored layer was written by looking at what our scenarios needed, which
fits the knowledge to the suite. This draws it instead from guides we did not
choose by looking at our failures, listed with their licences in
`docs/rca/mining/sources.yaml`.

The pipeline, per family (a batch of symptoms):

1. **Fetch** each source, raw where one exists, and cache it.
2. **Extract** each source with a model into the §3 format. Every item carries
   a verbatim quote from its source, and an item whose quote is not in the
   source is dropped: the model proposes, the text decides what it said.
3. **Merge** the extractions with a model: one claim per cause, however many
   sources named it. Every merged item names the extracted items it came
   from, and its citations are taken from those, never written afresh.
4. **Check** the draft with the build's own checks (§7), in the context of
   everything already in the knowledge. One repair round sends the problems
   back.

The output is a draft under `.k8srca/kb-mine/draft/`, which is not shipped.
Shipping it is copying the reviewed file into `docs/rca/mined/` (§8.3:
nothing mined ships unreviewed), and the review is the point -- most refute
criteria are the model's, marked `refute_drafted`, because public guides say
what confirms a cause and rarely what rules it out.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from . import knowledge
from .knowledge import (CAPABILITIES, UNDECIDED, Action, AlertMapping, Cite, Claim,
                        Declined, Fact, Knowledge, Layer, Symptom)

MANIFEST = Path("docs/rca/mining/sources.yaml")
CACHE = Path(".k8srca/kb-mine")
DEFAULT_MODEL = "claude-opus-5"
#: Output tokens per call, thinking included. 64k ran out on the first family's
#: merge (11 sources, about 50 claims extracted), mid-JSON.
MAX_TOKENS = 128000

#: Bump when a prompt or schema changes, so cached extractions are redone.
PROMPT_VERSION = 1

_SECTIONS = ("symptoms", "alerts", "facts", "actions", "claims")


# --- the manifest -------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Strict):
    title: str
    publisher: str
    url: str
    fetch: str | None = None
    licence: str
    use: Literal["vendor", "reference"]

    @model_validator(mode="after")
    def _fetchable(self) -> "Source":
        if self.use == "vendor" and not self.fetch:
            raise ValueError(f"{self.title}: a vendored source needs `fetch`")
        return self


class Family(_Strict):
    title: str
    symptoms: list[str]
    scope: str
    sources: list[str]
    #: The vendored alerts this family converts (007 §8.5). Their records are
    #: read as one more source, and the family is not done until every one is
    #: mapped and every hypothesis of each is accounted for.
    alerts: list[str] = []


#: The vendored knowledge base, as a source. Not in the manifest: each family
#: reads its own records from it.
VENDORED_ID = "vendored-kb"
VENDORED = Source(title="k8srca's vendored knowledge base", publisher="vendored",
                  url="vendor/kb/kubernetes_rca_knowledge_base_v2.json",
                  fetch="vendor/kb/kubernetes_rca_knowledge_base_v2.json",
                  licence="as received (vendor/kb/README.md)", use="vendor")


class Manifest(_Strict):
    sources: dict[str, Source]
    families: dict[str, Family]

    @model_validator(mode="after")
    def _resolve(self) -> "Manifest":
        for name, f in self.families.items():
            for sid in f.sources:
                if sid not in self.sources:
                    raise ValueError(f"family {name}: source {sid!r} is not listed")
                if self.sources[sid].use != "vendor":
                    raise ValueError(f"family {name}: {sid} is reference-only; its "
                                     f"licence does not allow extracting it")
        return self


def load_manifest(path: Path = MANIFEST) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text()))


# --- fetching -----------------------------------------------------------------------


@dataclass
class Fetched:
    id: str
    text: str
    retrieved: str
    sha: str


_SKIP = {"script", "style", "nav", "header", "footer", "aside", "svg", "noscript",
         "button", "form", "devsite-toc", "devsite-feedback"}
_BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
          "section", "table", "ul", "ol", "dt", "dd", "blockquote"}


class _Text(HTMLParser):
    def __init__(self, only_article: bool):
        super().__init__()
        self.only_article = only_article
        self.inside = 0 if only_article else 1
        self.skip = 0
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("article", "main") and self.only_article:
            self.inside += 1
        if tag in _SKIP:
            self.skip += 1
        if tag in _BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3", "h4"):
            self.out.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in ("article", "main") and self.only_article and self.inside:
            self.inside -= 1
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self.inside and not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    """The readable text of a rendered page: its article if it has one,
    without navigation, scripts or styling."""
    p = _Text(only_article=bool(re.search(r"<(article|main)\b", html)))
    p.feed(html)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(?m)^\s*-\s*$", "", text)  # list items that held only chrome
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def fetch(sid: str, src: Source, cache: Path = CACHE, refresh: bool = False,
          opener: Any = None) -> Fetched:
    """Download a source once and keep it, with the date it was retrieved
    (provenance, 007 §8.3)."""
    d = cache / "sources"
    text_path, meta_path = d / f"{sid}.txt", d / f"{sid}.json"
    if text_path.exists() and meta_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text())
        return Fetched(sid, text_path.read_text(), meta["retrieved"], meta["sha"])
    request = urllib.request.Request(src.fetch, headers={"User-Agent": "k8srca-kb-mine/1"})
    with (opener or urllib.request.urlopen)(request, timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        ctype = resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else ""
    if "html" in ctype or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        raw = html_to_text(raw)
    sha = hashlib.sha256(raw.encode()).hexdigest()
    retrieved = dt.date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    text_path.write_text(raw)
    meta_path.write_text(json.dumps({"url": src.fetch, "retrieved": retrieved, "sha": sha}))
    return Fetched(sid, raw, retrieved, sha)


def hypothesis_line(alert: str, hypothesis: str) -> str:
    """How a vendored hypothesis is written in the rendered source, and so what
    a claim converting it quotes."""
    return f"{alert} candidate cause: {hypothesis}"


def render_vendored(records: dict[str, dict[str, Any]], alerts: list[str]) -> str:
    """Vendored records as a document to extract from. Diagnosis only: the
    remediation fields are left out (rule 2), and so are the free-text fields
    every record shares word for word, which say nothing about the alert."""
    from .build import split_values

    out = ["# Records from a Kubernetes RCA knowledge base", "",
           "Each record is an alert, the symptom it reports, its candidate causes,",
           "and the evidence its authors associated with it.", ""]
    for a in alerts:
        r = records[a]
        out += [f"## {a}", "", f"{a} reports: {r.get('symptom', '')} "
                               f"(resource: {r.get('resource_type', '')})", ""]
        out += [hypothesis_line(a, h) for h in split_values(r.get("root_causes"))]
        for label, key in (("Supporting evidence", "supporting_evidence"),
                           ("Event evidence", "event_evidence"),
                           ("Metric evidence", "metric_evidence"),
                           ("Correlated alerts", "correlated_alerts")):
            if values := split_values(r.get(key)):
                out.append(f"{a} {label.lower()}: {'; '.join(values)}")
        out.append("")
    return "\n".join(out)


def _vendored(records: dict[str, dict[str, Any]], alerts: list[str]) -> Fetched:
    text = render_vendored(records, alerts)
    return Fetched(VENDORED_ID, text, "as vendored", hashlib.sha256(text.encode()).hexdigest())


# --- quotes ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    """Letters and digits only, so markdown, wrapping and quoting styles do not
    decide whether a quote is in its source."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def quoted(quote: str, source_text: str) -> bool:
    q = _norm(quote)
    return len(q) >= 12 and q in _norm(source_text)


# --- what the model is told already exists ----------------------------------------------


def vocabulary(k: Knowledge, tools: dict[str, str]) -> str:
    L = k.layer
    out = ["## Capabilities", ", ".join(sorted(CAPABILITIES)), "",
           "## k8stools tools (the only valid `call` values for requires: k8stools)"]
    out += [f"- {n}: {_first_sentence(d)}" for n, d in sorted(tools.items())]
    out += ["", "## Existing symptoms"]
    out += [f"- {n}: {s.title} -- claims: {', '.join(s.claims)}" for n, s in L.symptoms.items()]
    out += ["", "## Existing claims (do not redefine; extend with `refinements`)"]
    for n, c in L.claims.items():
        refined = f" -- refined by {', '.join(c.refined_by)}" if c.refined_by else ""
        out.append(f"- {n}: {c.title}{refined}")
    out += ["", "## Existing facts (reuse when they mean the same)"]
    out += [f"- {n}[{', '.join(f.params)}]: {f.says}" for n, f in L.facts.items()]
    out += ["", "## Existing actions (reuse when they observe the same)"]
    out += [f"- {n} (requires {a.requires}{', call ' + a.call if a.call else ''}): "
            f"produces {', '.join(a.produces)}" for n, a in L.actions.items()]
    return "\n".join(out)


def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:240]


# --- schemas ------------------------------------------------------------------------------


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "required": list(props), "properties": props}


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}
_ALTS = {"type": "array", "items": _STRS}


def _schema(provenance: dict[str, Any]) -> dict[str, Any]:
    """The extraction and merge outputs share a shape; only provenance differs
    (a quote from the source, or the extracted items merged)."""
    def item(props):
        return {"type": "array", "items": _obj({**props, **provenance})}

    return _obj({
        "symptoms": item({"name": _STR, "title": _STR, "claims": _STRS}),
        "alerts": item({"name": _STR, "symptom": _STR}),
        "facts": item({"name": _STR, "params": _STRS, "says": _STR,
                       "functional": {"type": "boolean"}}),
        "actions": item({"name": _STR,
                         "requires": {"type": "string", "enum": sorted(CAPABILITIES)},
                         "call": _STR, "read": _STR, "produces": _STRS,
                         "cost": {"type": "integer"},
                         "quality": {"type": "string",
                                     "enum": ["complete", "partial", "point"]},
                         "limits": _STR}),
        "claims": item({"name": _STR, "title": _STR, "confirm": _ALTS, "refute": _ALTS,
                        "supports": _STRS, "refined_by": _STRS, "note": _STR,
                        "refute_drafted": {"type": "boolean"}}),
        "refinements": {"type": "array", "items": _obj({"claim": _STR, "subclaims": _STRS})},
        "exclusive": {"type": "array", "items": _obj({"a": _STR, "b": _STR})},
    })


EXTRACT_SCHEMA = _schema({"quote": _STR})
#: `from` is one string, "source:name, source:name", not a list or objects.
#: Structured output compiles the schema to a grammar, and the API refuses one
#: past a size limit: EXTRACT_SCHEMA is just under it, and a list in place of
#: the string -- even of plain strings -- tips the merge schema over. Anything
#: added to `_schema` has to be tried against the API, not only the tests.
MERGE_SCHEMA = _schema({"from": _STR})


_FORMAT = """\
The format (design 007 §3):

- A **symptom** is what someone asking can see (`pod-restarting`). It lists its
  candidate causes, which are claims.
- A **claim** is a candidate cause (`oom-kill`, `missing-dependency`). It is
  never observed; facts confirm or refute it. `confirm` and `refute` are lists
  of alternatives; each alternative is a list of conditions that must all hold.
  Every claim needs at least one refute alternative. `supports` lists
  conditions that are evidence for it but not decisive. `refined_by` lists
  sub-causes that confirming it opens up (what *kind* of OOM).
- A **fact** is an observable statement with parameters, true or false of the
  cluster: `last_exit_code[Container, Code]`. `says` is one plain sentence with
  `{Param}` placeholders. `functional` is true when the fact has one value at a
  time (one last exit code).
- An **action** is how facts are observed: the capability it `requires`, the
  tool it `call`s (for k8stools, a name from the tool list; otherwise ""),
  what to `read` in the result, the facts it `produces`, a small integer
  `cost` (1 cheap, 3 expensive), and `quality` -- complete, partial (part of
  the history), or point (one sample now) -- with `limits` saying what it
  cannot show.
- A **condition** is `fact[Arg, ...]`, optionally prefixed by `not ` (checked
  and found false -- never "not looked at"), optionally followed by
  comparisons on its variables: `last_exit_code[C, Code], Code != 137`.
  Variables: C container, P pod, W workload, N node, S service. A condition
  that cannot be said that way may be plain prose, but prefer facts.
- `refinements` adds sub-causes to an existing claim you must not redefine.
  `exclusive` pairs two sibling claims that cannot both be the cause.
- An **alert** maps an alert name onto a symptom.

Names: claims and actions in kebab-case, facts in snake_case.
"""

_RULES = """\
Rules:

1. Generic only: what is true of any Kubernetes cluster. Leave out cloud
   consoles, provider CLIs, product-specific add-ons and anything about one
   provider's managed nodes, unless the same check exists in plain Kubernetes.
2. Diagnosis only. No remediation, no fixes, no "increase the limit".
3. Causes, not symptoms. "Container exits non-zero" is a symptom; "the
   process cannot reach a dependency it needs at startup" is a cause.
4. Reuse the existing vocabulary. If an existing fact, action or claim means
   the same thing, use its exact name and do not define it again. Never
   redefine an existing claim: add sub-causes to it with `refinements`, and
   add new candidates to an existing symptom by listing that symptom with
   only the new claims.
5. Actions must be ones the capabilities can make. A step that needs a shell
   in the container, access to the node, or a cloud console requires
   `human`. Never invent a k8stools tool: if none fits, the action requires
   `human` or another capability.
6. Say what evidence decides a question, never which field or source to
   believe. "Exit code 137" is a condition; "the reason field is unreliable,
   trust the exit code" is pre-deciding, and is forbidden.
7. Lifetime is not restart interval. How long one container instance runs
   before it dies is different from how often the container restarts, which
   the kubelet's back-off largely sets. Never state one as the other.
"""

EXTRACT_SYSTEM = f"""You extract diagnostic knowledge about Kubernetes from one public
troubleshooting guide into a fixed, structured format. An investigation agent
will use it to list the candidate causes of a symptom and to rule each one in
or out with evidence it can actually gather.

{_FORMAT}
{_RULES}
8. Only what this source says. Every item has `quote`: one or two sentences
   copied exactly, character for character, from the source, that the item
   rests on. An item you cannot quote, leave out. The exception is refute
   criteria, which guides rarely state: you may draft refute alternatives
   that follow from the source's confirm side or from how the cause works,
   and set `refute_drafted` true when you do.
9. Stay inside the family's scope. Return empty lists for a source that has
   nothing in scope.
"""

#: Added to the extraction prompt for the vendored records (007 §8.5).
VENDORED_NOTE = """
This source is records from a knowledge base we are converting into the format,
not a guide. Converting it means:

- Every "<Alert> candidate cause: <cause>" line is a hypothesis to convert.
  Extract each as a claim whose `quote` is that line, exactly -- unless an
  existing claim is the same cause, in which case leave it out: review maps it
  to the existing claim.
- Map every alert to a symptom (an existing one where it fits), with the
  alert's "<Alert> reports:" line as the quote. Its candidate causes go under
  that symptom, or under a claim the symptom reaches as refinements.
- The records name causes, not how to tell them apart. Confirm criteria come
  from the evidence lines and from how the cause works; mark drafted refutes.
"""

MERGE_SYSTEM = f"""You merge diagnostic knowledge that a model extracted, separately,
from several public Kubernetes troubleshooting guides, into one consistent
set in a fixed format.

{_FORMAT}
{_RULES}
8. One claim per cause. Where sources named the same cause differently, keep
   one name and combine their conditions. Where they gave it different
   criteria, keep each criterion as its own alternative.
9. Every item has `from`: the extracted items it was made from, as a
   comma-separated list of "source-id:item-name", exactly as they appear in
   the input (`k8s-debug-pods:application-crash, gke-crashloopbackoff:app-crash`). An item
   with no extracted source is not allowed: do not add knowledge the sources
   did not provide, except refute alternatives, which you may draft and mark
   with `refute_drafted`.
10. Every name a claim, symptom or action uses must resolve: to an item in
    your output or in the existing vocabulary. Define every fact you use.
11. Keep `vendored-kb` items in `from` wherever you merge one: that is how the
    conversion of the vendored records is traced, hypothesis by hypothesis.
"""


# --- extraction and merge ---------------------------------------------------------------


@dataclass
class Extraction:
    source: str
    items: dict[str, Any]
    kept: int = 0
    dropped: list[str] = field(default_factory=list)
    usd: float | None = None
    cached: bool = False


def _family_brief(name: str, family: Family) -> str:
    return (f"Family: {name} ({family.title})\n"
            f"Symptoms it extends: {', '.join(family.symptoms)}\n"
            f"Scope: {family.scope}\n")


def extract(provider: Any, family_name: str, family: Family, src: Source,
            fetched: Fetched, vocab: str, cache: Path = CACHE) -> Extraction:
    key = hashlib.sha256(json.dumps(
        [PROMPT_VERSION, provider.model, fetched.sha,
         family.model_dump(exclude={"alerts"}), vocab],
        sort_keys=True).encode()).hexdigest()[:16]
    path = cache / "extract" / f"{family_name}-{fetched.id}-{key}.json"
    if path.exists():
        raw, usd, cached = json.loads(path.read_text()), None, True
    else:
        note = VENDORED_NOTE if fetched.id == VENDORED_ID else ""
        prompt = (f"{_family_brief(family_name, family)}\n"
                  f"# What already exists\n\n{vocab}\n\n"
                  f"# Source `{fetched.id}`: {src.title} ({src.publisher})\n{note}\n"
                  f"<source>\n{fetched.text}\n</source>\n")
        raw, usage = provider.complete(EXTRACT_SYSTEM, prompt, EXTRACT_SCHEMA, "extraction")
        usd, cached = usage.usd, False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=1))
    out = Extraction(fetched.id, {k: v for k, v in raw.items() if k not in _SECTIONS},
                     usd=usd, cached=cached)
    for section in _SECTIONS:
        keep = []
        for item in raw.get(section, []):
            if quoted(item.get("quote", ""), fetched.text):
                keep.append(item)
            else:
                out.dropped.append(f"{section} {item.get('name')}: quote not in source")
        out.items[section] = keep
        out.kept += len(keep)
    return out


def to_layer(merged: dict[str, Any],
             extractions: dict[str, Extraction]) -> tuple[Layer, list[str]]:
    """The merge output as a Layer, its citations taken from the extracted items
    it names. An item none of whose `from` entries resolves is dropped."""
    index = {(sid, section, item["name"]): item["quote"]
             for sid, e in extractions.items()
             for section in _SECTIONS for item in e.items.get(section, [])}
    notes: list[str] = []
    L = Layer()

    def cites(section: str, item: dict[str, Any]) -> list[Cite]:
        out = []
        for ref in filter(None, (r.strip() for r in item.get("from", "").split(","))):
            sid, _, name = ref.partition(":")
            q = index.get((sid, section, name))
            if q is None:
                notes.append(f"{section} {item['name']}: from {ref} "
                             f"does not name an extracted item")
            elif not any(c.source == sid and c.quote == q for c in out):
                out.append(Cite(source=sid, quote=q))
        if not out:
            notes.append(f"{section} {item['name']}: dropped, no source")
        return out

    for s in merged.get("symptoms", []):
        if c := cites("symptoms", s):
            L.symptoms[s["name"]] = Symptom(title=s["title"], claims=s["claims"], sources=c)
    for a in merged.get("alerts", []):
        if c := cites("alerts", a):
            L.alerts[a["name"]] = AlertMapping(symptom=a["symptom"], sources=c)
    for f in merged.get("facts", []):
        if c := cites("facts", f):
            L.facts[f["name"]] = Fact(params=f["params"], says=f["says"],
                                      functional=f["functional"], sources=c)
    for a in merged.get("actions", []):
        if c := cites("actions", a):
            L.actions[a["name"]] = Action(
                requires=a["requires"], produces=a["produces"], call=a["call"] or None,
                read=a["read"], cost=max(1, a["cost"]), quality=a["quality"],
                limits=a["limits"], sources=c)
    for cl in merged.get("claims", []):
        if c := cites("claims", cl):
            L.claims[cl["name"]] = Claim(
                title=cl["title"], confirm=[_alt(a) for a in cl["confirm"]],
                refute=[_alt(a) for a in cl["refute"]], supports=cl["supports"],
                refined_by=cl["refined_by"], note=cl["note"],
                refute_drafted=cl["refute_drafted"], sources=c)
    for r in merged.get("refinements", []):
        L.refinements.setdefault(r["claim"], [])
        L.refinements[r["claim"]] += [s for s in r["subclaims"]
                                      if s not in L.refinements[r["claim"]]]
    L.exclusive = [[e["a"], e["b"]] for e in merged.get("exclusive", [])]
    return L, notes


def _alt(conditions: list[str]) -> str | list[str]:
    return conditions[0] if len(conditions) == 1 else conditions


# --- the run ---------------------------------------------------------------------------------


@dataclass
class MineReport:
    family: str
    model: str
    extractions: list[Extraction] = field(default_factory=list)
    merge_notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    prose: list[str] = field(default_factory=list)
    undecidable: list[str] = field(default_factory=list)
    overridden: list[str] = field(default_factory=list)
    usd: float = 0.0
    unknown_cost: bool = False
    repaired: bool = False
    draft: Path | None = None
    layer: Layer | None = None

    def render(self) -> str:
        lines = [f"family {self.family}, model {self.model}"]
        for e in self.extractions:
            cost = "cached" if e.cached else (f"${e.usd:.2f}" if e.usd is not None else "$?")
            lines.append(f"  {e.source:<30} {e.kept:>3} items, "
                         f"{len(e.dropped)} dropped  {cost}")
            lines += [f"      dropped {d}" for d in e.dropped]
        if self.layer is not None:
            L = self.layer
            drafted = sum(1 for c in L.claims.values() if c.refute_drafted)
            lines.append(f"  merged: {len(L.symptoms)} symptoms, {len(L.claims)} claims "
                         f"({drafted} with drafted refutes), {len(L.facts)} facts, "
                         f"{len(L.actions)} actions, {len(L.alerts)} alerts"
                         + (" (after one repair round)" if self.repaired else ""))
        if self.layer is not None:
            for alert, table in self.layer.converts.items():
                todo = [h for h, c in table.items() if isinstance(c, Declined)
                        and c.declined.startswith(UNDECIDED)]
                done = [f"{h} -> {c}" for h, c in table.items() if isinstance(c, str)]
                lines.append(f"  converts {alert}: {'; '.join(done) or 'nothing yet'}"
                             + (f"; UNDECIDED {', '.join(todo)}" if todo else ""))
        lines += [f"  note     {n}" for n in self.merge_notes]
        lines += [f"  PROBLEM  {p}" for p in self.problems]
        if self.prose:
            lines.append(f"  prose    {len(self.prose)} conditions not in fact form")
        if self.undecidable:
            lines.append(f"  undecidable with k8stools alone: {', '.join(self.undecidable)}")
        lines += [f"  overridden {o}" for o in self.overridden]
        cost = f"${self.usd:.2f}" + ("+ (some costs unknown)" if self.unknown_cost else "")
        lines.append(f"  cost {cost}")
        if self.draft:
            lines.append(f"  draft {self.draft}")
        return "\n".join(lines)


def _check(draft: Layer, family: str, knowledge_dir: Path, legacy_alerts: set[str],
           tools: dict[str, str],
           legacy_hypotheses: dict[str, list[str]] | None = None,
           ) -> tuple[Knowledge, knowledge.CheckReport]:
    """The draft in place: under the authored layer, beside the rest of the mined
    one (minus any earlier version of this family)."""
    mined = knowledge.load(knowledge_dir.parent / knowledge.MINED.name,
                           exclude=(f"{family}.yaml",))
    k = knowledge.combine([
        ("mined", mined),
        ("mined", Knowledge(layer=draft, sources=[f"{family}.yaml"],
                            origin={f"{sec}:{n}": f"{family}.yaml" for sec in _SECTIONS
                                    for n in getattr(draft, sec)})),
        (knowledge_dir.name, knowledge.load(knowledge_dir)),
    ])
    return k, knowledge.check(k, legacy_alerts=legacy_alerts, known_tools=set(tools) or None,
                              legacy_hypotheses=legacy_hypotheses)


def derive_converts(draft: Layer, k: Knowledge, alerts: list[str],
                    hypotheses: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """What each hypothesis of the family's alerts became, read off the draft's
    citations: a claim quoting a hypothesis's line converts it. A hypothesis
    already in some layer's table keeps that entry. One nothing cites, and no
    claim is titled after, is left undecided for review (`UNDECIDED`)."""
    L = k.layer
    out: dict[str, dict[str, Any]] = {}
    for a in alerts:
        reach = knowledge.reachable(L, L.alerts[a].symptom) if a in L.alerts else set()
        for h in hypotheses.get(a, []):
            if h in L.converts.get(a, {}) and h not in draft.converts.get(a, {}):
                continue
            line = _norm(hypothesis_line(a, h))
            citing = [n for n, c in L.claims.items()
                      if any(x.source == VENDORED_ID and line in _norm(x.quote)
                             for x in c.sources)]
            titled = [n for n, c in L.claims.items() if c.title.lower() == h.lower()]
            best = sorted(citing, key=lambda n: n not in reach) or sorted(
                titled, key=lambda n: n not in reach)
            out.setdefault(a, {})[h] = best[0] if best else Declined(
                declined=f"{UNDECIDED}: no claim cites this hypothesis; name the claim "
                         f"it became, or decline it with a reason")
    return out


def _merge(provider: Any, prompt: str, cache: Path, spend: Any) -> dict[str, Any]:
    """One merge call, cached by its whole prompt: the largest call in the
    pipeline, and the one most worth not repeating after a fix downstream."""
    key = hashlib.sha256(json.dumps([PROMPT_VERSION, provider.model, MERGE_SYSTEM, prompt])
                         .encode()).hexdigest()[:16]
    path = cache / "merge" / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    merged, usage = provider.complete(MERGE_SYSTEM, prompt, MERGE_SCHEMA, "merge")
    spend(usage.usd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=1))
    return merged


def run(family_name: str, *, provider: Any, tools: dict[str, str],
        manifest: Manifest | None = None, knowledge_dir: Path = knowledge.KNOWLEDGE,
        records: dict[str, dict[str, Any]] | None = None, cache: Path = CACHE,
        refresh: bool = False, max_usd: float = 10.0, opener: Any = None) -> MineReport:
    """`records` is the vendored knowledge base by alert name; by default, the
    file `kb build` reads."""
    from .build import SOURCE, split_values

    if records is None:
        records = {r["alert_name"]: r for r in json.loads(SOURCE.read_text())}
    legacy_alerts = set(records)
    hypotheses = {a: split_values(r.get("root_causes")) for a, r in records.items()}
    manifest = manifest or load_manifest()
    if family_name not in manifest.families:
        raise KeyError(f"no family {family_name!r}; have {', '.join(manifest.families)}")
    family = manifest.families[family_name]
    if unknown := [a for a in family.alerts if a not in records]:
        raise KeyError(f"family {family_name}: not vendored alerts: {', '.join(unknown)}")
    report = MineReport(family_name, provider.model)

    base = knowledge.combine([
        ("mined", knowledge.load(knowledge_dir.parent / knowledge.MINED.name,
                                 exclude=(f"{family_name}.yaml",))),
        (knowledge_dir.name, knowledge.load(knowledge_dir))])
    vocab = vocabulary(base, tools)

    def spend(usd: float | None) -> None:
        if usd is None:
            report.unknown_cost = True
        else:
            report.usd += usd
        if report.usd > max_usd:
            raise RuntimeError(f"spent ${report.usd:.2f}, over --max-usd {max_usd}")

    fetched = {sid: fetch(sid, manifest.sources[sid], cache, refresh, opener)
               for sid in family.sources}
    sources = {sid: manifest.sources[sid] for sid in family.sources}
    # The vendored records still to convert: an alert some layer already maps
    # was converted already, and re-extracting it would only propose rivals to
    # its reviewed claims.
    pending = [a for a in family.alerts if a not in base.layer.alerts]
    if pending:
        fetched[VENDORED_ID] = _vendored(records, pending)
        sources[VENDORED_ID] = VENDORED
    extractions: dict[str, Extraction] = {}
    for sid in sources:
        e = extract(provider, family_name, family, sources[sid], fetched[sid],
                    vocab, cache)
        if not e.cached:
            spend(e.usd)
        extractions[sid] = e
        report.extractions.append(e)

    payload = {sid: {s: e.items[s] for s in _SECTIONS} | {
                   "refinements": e.items.get("refinements", []),
                   "exclusive": e.items.get("exclusive", [])}
               for sid, e in extractions.items()}
    prompt = (f"{_family_brief(family_name, family)}\n"
              f"# What already exists\n\n{vocab}\n\n"
              f"# Extracted, per source\n\n```json\n{json.dumps(payload, indent=1)}\n```\n")
    merged = _merge(provider, prompt, cache, spend)
    layer, notes = to_layer(merged, extractions)
    # Repair what the merge can fix; the conversion table is filled in after.
    k, checked = _check(layer, family_name, knowledge_dir, legacy_alerts, tools)

    if checked.problems:
        repair = (prompt + "\n# Your previous output\n\n```json\n" + json.dumps(merged)
                  + "\n```\n\n# It fails these checks. Return the corrected output in full."
                  "\n\n" + "\n".join(f"- {p}" for p in checked.problems + notes))
        merged = _merge(provider, repair, cache, spend)
        layer, notes = to_layer(merged, extractions)
        k, checked = _check(layer, family_name, knowledge_dir, legacy_alerts, tools)
        report.repaired = True

    layer.converts = derive_converts(layer, k, family.alerts, hypotheses)
    k, checked = _check(layer, family_name, knowledge_dir, legacy_alerts, tools,
                        legacy_hypotheses=hypotheses)
    unmapped = [a for a in family.alerts if a not in k.layer.alerts]
    checked.problems += [f"family alert {a} is not mapped to a symptom" for a in unmapped]

    report.layer, report.merge_notes = layer, notes
    report.problems, report.prose = checked.problems, checked.prose
    report.undecidable = checked.undecidable
    report.overridden = k.overridden
    report.draft = write_draft(family_name, layer, sources, fetched, provider.model,
                               report, family, cache)
    return report


# --- the draft ------------------------------------------------------------------------------


def write_draft(family_name: str, layer: Layer, sources: dict[str, Source],
                fetched: dict[str, Fetched], model: str, report: MineReport,
                family: Family, cache: Path = CACHE) -> Path:
    head = [f"# Mined: {family.title} (design 007 §8).",
            "#",
            f"# DRAFT, written by `k8srca kb mine {family_name}` with {model} on "
            f"{dt.date.today().isoformat()}.",
            "# Review it, then copy it to docs/rca/mined/ to ship it. Look hardest at",
            "# claims with `refute_drafted: true`: their refute side is the model's,",
            "# not the sources'.",
            "#",
            "# Sources (each item's `sources` quotes the passage it rests on):"]
    for sid, s in sources.items():
        head.append(f"#   {sid}: {s.publisher}, \"{s.title}\", {s.url}")
        head.append(f"#     {s.licence}, retrieved {fetched[sid].retrieved}")
    if report.problems:
        head += ["#", "# The build checks still fail:"] + [f"#   {p}" for p in report.problems]
    body = layer.model_dump(exclude_defaults=True, mode="json")
    path = cache / "draft" / f"{family_name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(head) + "\n\n" + yaml.safe_dump(
        body, sort_keys=False, width=88, allow_unicode=True))
    return path
