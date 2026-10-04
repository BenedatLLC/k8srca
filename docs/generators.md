# Generators

How k8srca builds the skills the agent reads, how to run each builder, and how
to tell whether its output is any good.

Design background: [005 §6](../designs/005-modular-architecture.md) (the
contract) and [001 §5](../designs/001-architecture.md) (what each skill is
for).

---

## 1. What a generator is

A **generator** reads sources and writes a **skill bundle**: a directory with a
`SKILL.md` at its root that the agent receives as a skill. It runs at build
time, per deployment, on demand. It is not part of the agent's run-time loop.

That is the difference from a **plugin**, which *delivers* skills and tools at
run time (005 §5). A generator *produces* a skill; a plugin then ships it.
Keeping the two apart is what lets each be tested on its own terms.

There are two today:

| Generator | Writes | From | Command |
| --- | --- | --- | --- |
| `cluster-architecture` | `skills/cluster-architecture/` (gitignored, rebuilt per deployment) | the live cluster, its change history, the official chart and the official docs | `k8srca arch build` |

**`cluster-architecture` is being redesigned as dkgg**, a standalone deployment
knowledge graph generator in this repository: a wiki of what each component is
and how they connect, with no observed state. See
[`packages/dkgg/docs/design.md`](../packages/dkgg/docs/design.md). This document
describes the generator as it is today.
| `k8s-rca` | `skills/k8s-rca/knowledge_base.json` (committed) | the source knowledge base and authored discriminators | `k8srca kb build` |

A third, turning a deployment's runbooks into debugging guides, is planned
(005 §6.1).

---

## 2. The contract

Every generator implements one small interface (`src/k8srca/core/generator.py`):

```python
class Generator(Protocol):
    name: str      # the skill it writes; also the bundle's directory name
    format: int    # output format, bumped when the output's meaning changes
    async def generate(self, cfg, dest: Path) -> GeneratorReport: ...
```

and is always run through `run(generator, cfg, dest)`, never called directly.
`run()` adds two things every generated bundle needs, so no generator has to
remember them:

**A manifest inside the bundle**, `GENERATED.json`:

```json
{
  "content_digest": "7396ff94aa005824",
  "format": 4,
  "generator": "KnowledgeBaseGenerator",
  "k8srca": "0.1.0",
  "name": "k8s-rca"
}
```

It says what produced the bundle, so a change in the agent's behaviour can be
traced to a generator change (`format`, `k8srca`) or to a source change
(`content_digest` moved, generator did not). It deliberately holds **no
timestamp**: `k8srca sync` re-uploads a skill whenever its content changes, and
a timestamp would make every build look like a change.

**A report outside the bundle**, `.k8srca/reports/<name>.json`: summary lines,
counts, the build time, and **warnings** for anything skipped or unresolved. A
generator never drops input silently; it reports it. The report is for you and
for evaluation, and is not shipped to the agent.

Every build prints the report and ends with one line naming the bundle, its
format and its digest:

```
  WARNING  unresolved alert name: NodeNotReady -> NodeNetworkUnavailable
wrote skills/k8s-rca (81 alerts, 195 hypotheses, 7 discriminators, 2 refinements) format 4, digest 7396ff94aa005824
report: .k8srca/reports/k8s-rca.json
```

---

## 3. `cluster-architecture`

What is actually deployed in *this* cluster: every service's image, replicas,
resources, probes and dependencies, what changed recently, what the software's
publishers declared it to be, and what their documentation says each part is
for.

### Sources

Configured in `k8srca.yaml` under `architecture.sources`. Each fact in the
output keeps the source it came from, so where sources disagree, the
disagreement stays visible (`arch_query.py drift`) instead of one silently
winning.

| Source | Answers | Provenance | Needs |
| --- | --- | --- | --- |
| `live_cluster` | how the system works today | `observed` | k8stools running |
| `change_history` | what changed here, and when (ReplicaSet revisions) | `observed` | k8stools running |
| `chart_repo` | what it is declared to be | `declared` | the official chart, pinned (`helm:`), or rendered manifests in a directory (`path:`) |
| `docs` | what each part is *for* | `documented` | the official documentation, pinned (`git:`), or Markdown in a directory (`path:`) |

**Cluster access goes through k8stools only.** The live sources read the cluster
through the k8stools MCP server, never with the Kubernetes client or `kubectl`
(CLAUDE.md, enforced by `tests/test_no_direct_cluster_access.py`). That holds
for every generator.

**Declared and documented sources are official and pinned.** The chart and the
docs should say what the software's publishers declared and documented, at the
version that was deployed, not what we transcribed. Hand-written notes were
tried and removed (2026-10-03): one of them claimed `ad` "never reaches
readiness" (false: it has no readiness probe), five of six scenario runs
repeated it, and nothing checked it.

```yaml
- type: chart_repo
  helm:
    repo: https://open-telemetry.github.io/opentelemetry-helm-charts
    chart: opentelemetry-demo
    version: 0.40.7               # the release that was installed
    release: my-otel-demo         # the deployed release name
    # values: path/to/values.yaml  # if the install's values are known
- type: docs
  git:
    repo: https://github.com/open-telemetry/opentelemetry.io
    ref: d503571bbd711e05b9e6f85966fed6f0e00ba910   # full SHA, no branches
    path: content/en/docs/demo/services
```

`k8srca arch build` fetches each pinned source once into `.k8srca/sources/` and
reads the cache afterwards, so a pinned source never changes underneath a
build and a rebuild needs no network.

- **The chart** is downloaded from the chart repository and rendered with
  `helm template`, offline and with no cluster credential. That is the one
  helm command k8srca runs (CLAUDE.md); it needs the `helm` binary installed.
  Without the install's own values file, it renders the chart's defaults, so a
  declared value can differ from what was deployed for that reason alone.
- **The docs** are a shallow, sparse git checkout of one path at one commit.
  Point at a website's *source* rather than the site: it can be pinned.
  Hugo pages are read as the agent should see them (front matter reduced to a
  title, shortcodes removed, and only the **lead section** kept: the text
  before the first `##` heading, which says what a component is and what it
  talks to; the rest of an official page is mostly how it is built and
  instrumented); a page named for a Service attaches to it
  (`cart/index.md` is `cart`'s), and the rest is general documentation.

**Finding the version that was deployed.** The live cluster usually says,
without help:

- **The install date** is the age of the oldest ReplicaSet revision of a
  workload, and of the node.
- **The chart version:** sub-chart pods carry `helm.sh/chart` (for the demo,
  `grafana-10.5.8`, `opensearch-3.4.0`, `prometheus-28.2.0`). The chart
  repository's `index.yaml` lists each release's sub-chart versions; the
  releases that match, narrowed by the install date, are the candidates.
- **The release name:** `app.kubernetes.io/instance` on the same pods.
- **The docs commit:** the last commit to the docs path before the install
  date (`gh api "repos/<org>/<repo>/commits?path=<path>&until=<date>"`).

### Output

```
skills/cluster-architecture/
  SKILL.md            # how to use the files below
  architecture.json   # every service, facts per source with provenance
  topology.md         # the dependency graph, readable
  arch_query.py       # queries: service, deps, blast, changes, drift, intent, list, sources
  GENERATED.json      # the manifest (§2)
```

### Running it

```bash
uv run k8srca up            # the live sources need k8stools
uv run k8srca arch build    # writes skills/cluster-architecture/
uv run k8srca sync          # delivers the new skill to the agents
```

`--dest DIR` writes elsewhere. `--config FILE` uses another configuration.

`k8srca scenario record` rebuilds this skill first and pins a copy beside the
capture, because the skill is a second observed read of the cluster and must
describe the same moment ([004 §6](../designs/004-scenario-testing.md)). Pass
`--no-arch-build` only if you know the existing build is current.

Rebuild after anything that changes what is deployed. The skill does not
refresh itself; a regeneration schedule is planned (005 §6.4).

---

## 4. `k8s-rca`

Generic Kubernetes root-cause knowledge: alerts, their candidate causes, how to
tell the causes apart, and which cause decomposes into which alert.

### Sources

| Source | Where | Notes |
| --- | --- | --- |
| The knowledge base | `background/kubernetes_rca_knowledge_base_v2.json` | **Not in the repository** (`background/` is gitignored). Without it `kb build` fails, and its tests skip |
| Authored discriminators | `docs/rca/discriminators.yaml` | How to tell candidate causes apart, and `refines` links from a cause to the alert that decomposes it |

### Output

The generator writes one file, `knowledge_base.json`, into a bundle whose other
files are written by hand and committed:

```
skills/k8s-rca/
  SKILL.md            # hand-written
  kb_query.py         # hand-written
  knowledge_base.json # generated
  GENERATED.json      # the manifest, covering the whole bundle
```

The manifest covers the hand-written files too, because the agent receives the
whole bundle.

### Running it

```bash
uv run k8srca kb build      # rewrites skills/k8s-rca/knowledge_base.json
uv run k8srca sync          # delivers it
```

`--source FILE` reads another knowledge base. `--dest DIR` names the bundle
directory.

The build is **reproducible**: regenerating from unchanged sources produces a
byte-identical bundle, and a test holds it to that. So `git diff
skills/k8s-rca` after a build shows exactly what a source change did to what
the agent will read.

Warnings name the source's own mistakes, such as an alert whose correlations
point at a name that does not exist. They are reported, not fixed: the source
file is not ours to edit, and silently dropping the edge would hide it.

---

## 5. Evaluating a generator

Three levels, from cheapest to most realistic.

**1. Read the report.** Every build prints its warnings and saves them to
`.k8srca/reports/<name>.json`. A new warning after a source change is the first
thing to look at.

**2. Run the tests** (no cluster, no credentials, no cost):

```bash
uv run pytest tests/test_generators.py    # the contract, both generators
uv run pytest packages/dkgg/tests           # the architecture generator (dkgg)
uv run pytest tests/test_kb.py               # the knowledge base
```

These check that each generator honours the contract, that the architecture
generator merges sources with provenance and reports drift, and that the
knowledge base resolves its links and reproduces exactly.

**3. Run the scenario suite** with the new bundle ([004](../designs/004-scenario-testing.md)).
This is the end-to-end question, whether the agent diagnoses better or worse,
but it is slow, costs money, and mixes the generator's quality with everything
else the agent does.

### The architecture generator's eval

`k8srca eval arch` scores the cluster-architecture generator on its own, with no
agent and, by default, no model call (005 §8.1). It needs Docker, for the replay, and costs
nothing unless `--model` (synthesis, cached by its inputs under
`.k8srca/synthesis/`, so a re-run is free) or `--judge` is given. A case's
`review.yaml`, if it has one, is applied as `dkgg build` would.

```bash
uv run k8srca eval arch                         # every case
uv run k8srca eval arch itbench-33-pre-fault    # one case
uv run k8srca eval arch --detail 20             # list more items per finding
uv run k8srca eval arch --model claude-opus-5   # synthesise, and score kinds (~$1/case, cached)
```

For each **case** (an install, under `tests/evals/architecture/<case>/`) it
replays the case's capture through k8stools as if it were the live cluster,
builds **dkgg's wiki** against it with the case's sources, runs `dkgg check` on
it, and scores it. It scores the wiki, not the legacy skill k8srca still syncs,
because the wiki is what the agent will read once dkgg replaces the skill. The
wikis and a `results.json` land in `.k8srca/evals/architecture/<timestamp>/`.

| Score | Means |
| --- | --- |
| `inventory_completeness` | every workload and Service in the capture is a component |
| `declared_completeness` | every image and resource setting the chart declares is in the wiki |
| `declared_accuracy` | each of those equals what the chart says |
| `dependency_recall`, `dependency_precision` | edges against the case's reviewed truth |
| `doc_coverage` | every running workload has a documentation page attached (free; `-` when the case has no docs source) |
| `doc_consistency` | no page, quoted or generated, makes a claim the observed facts contradict (`--judge` only) |
| `kind_accuracy`, `edge_kind_accuracy` | component and edge kinds against the case's truth (`--model` only) |

and lists what it found: components **invented** (nothing behind them),
**declared, not deployed** (in the chart only: drift, not a failure), declared
configuration missing or wrong, dependencies missed or extra, undocumented
workloads, contradictions, and any `dkgg check` finding. There is no observed-
fact accuracy and no drift score: the wiki holds no observed state.

**Two kinds of truth.** Most of it is *derived* from the case's own inputs by a
different path than the generator takes: facts straight from the capture JSON
(images from each Deployment's current ReplicaSet, not from whichever pod the
generator met first), declared facts straight from the chart YAML. It needs no
review. Which environment references are real dependencies cannot be derived
that way, so each case has a hand-reviewed `truth.yaml`, which also holds the
component and edge kinds synthesis is scored against (components and edges it
does not list are not scored). The eval prints
`dependency truth: NOT REVIEWED` until someone has confirmed it and filled in
`reviewed: {by, on}`.

**Checking the truth itself.** A truth drafted the way the generator works
shares its blind spots: both read env, so both missed the databases named in
key=value connection strings, and recall read 100%. So a case may name a
`reference`, the system's own dependency diagram (for the demo,
`docs/demo/architecture.md`, pinned with the docs), and every run compares the
truth with it over the components the install has. Each difference is either
explained in `truth.yaml`'s `reference_differences` or printed as
unexplained; an explanation for a difference that no longer exists is printed
too. This checks the truth, not the generator, and costs nothing.

A case may also carry `traces`: Jaeger's service dependencies
(`/api/dependencies`), saved into the case directory, compared with the truth
the same way, with differences explained in `trace_differences`. Traces show
what really called what, which configuration cannot, but not datastores,
caches or flag and telemetry backends (listed as `untraced`), and a broker only
as producer -> consumer (listed in `queues`). They are truth for the eval, never
an input to the generator.

**The cases:**

| Case | Install | Sources |
| --- | --- | --- |
| `otel-demo-2026-10-03` | ours, captured with k8stools 2.3.0: DaemonSets listed, every pod's owner | live, the official chart and docs (pinned) |
| `itbench-33-pre-fault` | ITBench-Lite's, before the fault | live |

Two installs, because one lets a generator be fitted to it unnoticed. The
ITBench capture predates k8stools 2.3.0 and has no pod owners, so it also
exercises the generator's fallback of grouping pods by name.

**Checking the documentation** (`--judge`). Documentation is free text, so
whether it contradicts the cluster needs a model to read it. With `--judge`,
each case makes one call to the grader's model (about $0.22 for 21 pages),
giving it every page and the observed facts, and asking only for claims about
this deployment's state (readiness, probes, resources, images, replicas, ports,
dependencies) that a recorded fact directly contradicts. Instrumentation detail,
uncheckable claims and chart drift are explicitly not failures. Each finding
quotes the page and names the fact.

Calibrated on 2026-10-03 against the real official docs (21 pages: no
contradictions) and five planted claims. It flagged "ad runs with a 1Gi memory
limit" (observed 300Mi) and "cart stores carts in PostgreSQL" (it calls
valkey-cart), and left alone "ad is written in Java" (uncheckable) and
"accounting consumes from kafka" (true). **It does not catch everything a
person would.** The historic false note "`ad` never reaches readiness" was not
flagged: in that capture `ad` really was not ready, and the claim is false only
through Kubernetes semantics (no readiness probe means ready as soon as it
runs), which no recorded fact states. The judge is held to recorded facts on
purpose; one that reasons its way to plausible contradictions raises false
alarms, and then nobody reads it.

**Adding a case:** a directory with `case.yaml` (the capture, namespaces and
sources, pinned like production's), the capture or a relative path to one,
and a `truth.yaml` of dependencies. Draft the dependencies from every
environment value that names another Service, note the variable each edge came
from, and have someone review them. The hermetic tests check that every
committed case loads and that the files it names exist.

---

## 6. Adding a generator

1. Implement `name`, `format` and `generate(cfg, dest)` in a module beside the
   code it wraps. `kb/generator.py` is the example; `arch/generator.py` is
   the other shape, an adapter fitting a standalone package (dkgg) to the
   contract.
   `generate` writes the bundle and returns a `GeneratorReport`; put anything
   skipped or unresolved in `warnings`.
2. Wire a command through `cli._generate`, which runs it through `run()` and
   prints the result.
3. Add it to `tests/test_generators.py`: it builds a manifested bundle from
   test sources, and its `format` matches what it writes.
4. Read live data only through an MCP server. A generator that needs a
   capability the server lacks gets it added there, not worked around
   (CLAUDE.md).
