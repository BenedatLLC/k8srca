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
| `cluster-architecture` | `skills/cluster-architecture/` (gitignored, rebuilt per deployment) | the live cluster, its change history, rendered manifests, operator notes | `k8srca arch build` |
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
resources, probes and dependencies, what changed recently, and what operators
know that the cluster cannot say.

### Sources

Configured in `k8srca.yaml` under `architecture.sources`. Each fact in the
output keeps the source it came from, so where sources disagree, the
disagreement stays visible (`arch_query.py drift`) instead of one silently
winning.

| Source | Answers | Provenance | Needs |
| --- | --- | --- | --- |
| `live_cluster` | how the system works today | `observed` | k8stools running |
| `change_history` | what changed here, and when (ReplicaSet revisions) | `observed` | k8stools running |
| `chart_repo` | what it is declared to be | `declared` | rendered manifests (`helm template > out.yaml`) or plain YAML in a directory |
| `docs` | what each part is *for*, and what happens to the system when it fails | `documented` | operator notes, one Markdown file per service plus `_system.md` |

**Cluster access goes through k8stools only.** The live sources read the cluster
through the k8stools MCP server, never with the Kubernetes client or `kubectl`
(CLAUDE.md, enforced by `tests/test_no_direct_cluster_access.py`). That holds
for every generator.

**Rules for operator notes** (`docs/architecture/<deployment>/`). Notes are the
only hand-written source, so the only one that can be wrong without anything
noticing:

- **Describe the system, not how to diagnose it.** No "check this first", no
  ranked causes. Generic RCA knowledge belongs in `k8s-rca`. Removing diagnostic
  guidance from these notes raised scenario trap scores from 2/6 to 6/6.
- **Do not describe current failure behaviour.** The live source states it, and
  it goes stale. A note claiming `ad` "never reaches readiness" (false: it has
  no readiness probe) was repeated by five of six scenario runs before it was
  removed.
- **Where a note and the cluster disagree, the cluster is right.** The skill
  says so to the agent, too.

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
uv run pytest tests/test_arch.py tests/test_kb.py tests/test_history.py
```

These check that each generator honours the contract, that the architecture
generator merges sources with provenance and reports drift, and that the
knowledge base resolves its links and reproduces exactly.

**3. Run the scenario suite** with the new bundle ([004](../designs/004-scenario-testing.md)).
This is the end-to-end question, whether the agent diagnoses better or worse,
but it is slow, costs money, and mixes the generator's quality with everything
else the agent does.

**The architecture generator's own eval is being built** (005 §8.1, migration
step a.2). It will score a generated skill against ground truth for two
installs, on completeness, accuracy, invented facts and drift detection,
without running an agent. This section will describe how to run it once it
lands.

---

## 6. Adding a generator

1. Implement `name`, `format` and `generate(cfg, dest)` in a module beside the
   code it wraps. `arch/generator.py` and `kb/generator.py` are the examples.
   `generate` writes the bundle and returns a `GeneratorReport`; put anything
   skipped or unresolved in `warnings`.
2. Wire a command through `cli._generate`, which runs it through `run()` and
   prints the result.
3. Add it to `tests/test_generators.py`: it builds a manifested bundle from
   test sources, and its `format` matches what it writes.
4. Read live data only through an MCP server. A generator that needs a
   capability the server lacks gets it added there, not worked around
   (CLAUDE.md).
