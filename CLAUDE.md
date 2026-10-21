# k8srca

Kubernetes root-cause analysis agent built on Claude Managed Agents. An SRE asks
in Slack why something is broken; the agent investigates a live cluster through
read-only tools and answers with evidence.

Design documents are authoritative and should be updated when the code
contradicts them: `designs/001-architecture.md` (platform),
`designs/002-investigation-model.md` (how the agent reasons),
`designs/003-operations.md` (running it), `designs/004-scenario-testing.md`
(how we find out whether it works). `designs/005-modular-architecture.md` is a
draft target architecture, not yet built: 001 describes the system as it is.
`designs/006-holdout-suite.md` (proposed) keeps a scenario suite we never tune
against, authored and run by a colleague who reports scores only.
`designs/007-knowledge-representation.md` (draft) is the diagnostic knowledge
format that replaces 002 §8's: facts, actions, claims, availability per
deployment, mined content and runbooks.
`designs/008-conclusions-under-incomplete-evidence.md` (proposed) replaces
confirmation with ranking. Answers give the most likely cause and its
strength, what is not ruled out, what is ruled out, and next steps. Facts are
scoped to what the record retains, and the grader judges what the capture
allows.
`designs/009-metrics.md` (proposed) adds Prometheus: what a cluster must
scrape, a read-only MCP server of our own (`packages/promtools`), and scenarios
that capture and replay metrics.
`designs/010-facts-and-short-answers.md` (proposed) moves observations into a
facts layer in code, shortens the answer to a few hundred words with footnoted
facts, and grades each scenario against a checklist of statements.

## How we change the agent: docs/process.md

Read it before planning or measuring a change. In short:
- **Plan the measurement before building.** Each step states its hypothesis,
  its expected effect, whether the noise at the planned n can show it, a
  budget, and its done and stop criteria.
- **Tripwires stop work for a checkpoint.** Any one of these:
  - two inconclusive arms in a row;
  - a third change aimed at the same thing;
  - a rule written from one run;
  - the grader or a truth file changed mid-step;
  - the budget is spent;
  - the work has drifted from the design's goal.
- **A checkpoint ends in a decision,** never in another small change: continue,
  change the plan, or close the step.
- **One design at a time, and each one ends:** done, done in part, or
  abandoned. The grader and truth files stay frozen for the length of a
  design.

## Rule: k8srca never touches the Kubernetes API directly

**All cluster access goes through the k8stools MCP server. No part of k8srca may
import the `kubernetes` client, call `load_kube_config`, shell out to `kubectl`,
or read a kubeconfig.** This holds for build-time code (`k8srca arch build`) as
much as for anything the agent drives.

Why, in the order the reasons bite:

1. **One credential, one holder.** The kubeconfig is mounted into the k8stools
   container and nowhere else (001 §8.2). A second code path means a second
   process holding cluster credentials, and the read-only guarantee stops being
   something you can check in one place.
2. **The tool surface is the security boundary.** k8stools exposes no mutating
   tools (001 §8.1). Code that reaches past it is not bound by that, and
   "read-only" becomes a property of whoever wrote the call rather than of the
   system.
3. **Snapshots go stale; tools do not.** Anything k8srca reads at build time is
   frozen into a skill bundle. The same fact behind an MCP tool can be asked
   live, during the investigation that needs it.

**If a capability is missing, add it to k8stools.** That is what happened with
`get_replicaset_summaries`: `arch/history.py` briefly read ReplicaSets with the
Kubernetes client because k8stools had no tool for them. The right fix was the
tool (k8stools 1.2.0), not the shortcut.

The sandbox image deliberately ships no `kubectl`, no kubeconfig and no cloud
CLI (001 §8.2), and the in-process worker scrubs credentials from its
environment before running agent-authored bash. Both are downstream of this
rule; neither substitutes for it.

**One exception: `helm template`**, in kubewiki's `fetch.py` only (packages/kubewiki). It renders a pinned
official chart to manifests for the architecture skill, offline: no cluster
flags, and `KUBECONFIG` pointed at `/dev/null`, so it holds no credential to
reach a cluster with. It is the only helm command either package runs; `helm install`,
`helm template --validate` and every other cluster-facing command stay banned.

**This is enforced by a test**, not by convention:
`tests/test_no_direct_cluster_access.py` walks every module's AST and fails on
an import of `kubernetes`, a call to `load_kube_config`, or a command naming
`kubectl`/`helm`/`oc`, in any call or any argv literal, with the `helm template`
exception above, and kubewiki has the same test for itself
(`packages/kubewiki/tests/test_kubewiki_boundary.py`). It parses rather than greps, so a
docstring explaining the rule does not trip it and a real import cannot hide in
one.

## Project layout

```
src/k8srca/
  config.py        - k8srca.yaml schema (pydantic)
  settings.py      - secrets from .env; scrub_environment for the worker
  sync.py          - control plane: skills, agents, environment
  tools.py         - MCP tool -> custom-tool declarations, prefixing, manifest hash
  mcp_client.py    - connecting to MCP servers
  bringup.py       - `k8srca up` / `status`: idempotent bring-up and health
  cluster.py       - derived facts: docker gateway, TLS names, tunnels
  session.py       - creating and consuming Managed Agents sessions
  timing.py        - where a session's wall clock went
  evidence.py      - every MCP result saved to the workspace for scripts (design 007 §6.1)
  review.py        - the host's check of a final answer, and its revision turn (007 §6.1)
  core/            - contracts shared by components (design 005); generator.py so far
  evals/           - component evals, scoring a generator without an agent
  arch/            - the cluster-architecture skill: kubewiki behind the generator contract
  kb/              - the RCA knowledge base skill
  slack/           - the Slack orchestrator
  worker/          - sandbox entrypoint and host poller

packages/kubewiki/ - the deployment knowledge graph generator: a standalone
                     package that builds the architecture skill and never
                     imports k8srca (packages/kubewiki/docs/design.md)
```

## Commands

```bash
uv run k8srca up            # network, tunnel, kubeconfig, k8stools -- idempotent
uv run k8srca status        # is a Slack mention going to be answered?
uv run k8srca down          # stop what `up` started
uv run k8srca sync          # apply k8srca.yaml to the Anthropic control plane
uv run k8srca arch build    # build the cluster-architecture skill: kubewiki's wiki (synthesis cached)
uv run k8srca kb build      # normalise the RCA knowledge base
uv run k8srca kb mine <family>  # mine public guides into a draft layer to review (007 §8; ~$5/family)
uv run k8srca eval arch     # score the architecture generator (Docker, no model)
uv run k8srca eval arch --judge  # also check its docs against the cluster (~$0.22/case)
uv run k8srca eval arch --model claude-opus-5  # synthesise the wiki, score kinds (~$1/case, cached)
uv run kubewiki build --config kubewiki.yaml   # kubewiki standalone: build a wiki (packages/kubewiki); --model to synthesise
uv run kubewiki check <wiki-dir>               # its deterministic checks
uv run k8srca poller        # one sandbox container per work item (production)
uv run k8srca worker        # in-process worker (development only -- no isolation)
uv run k8srca slack run     # the Slack orchestrator
uv run k8srca timing        # break down a session's latency
uv run k8srca scenario run <id> --local-skill k8s-rca   # measure a skill change before syncing it
uv run k8srca scenario regrade .k8srca/scenario/runs/<stamp>.jsonl  # grade saved answers again after a grader change
uv run k8srca scenario build-wiki <id>                  # build a scenario's wiki from its capture
uv run k8srca scenario run <id> --arch wiki --save F    # run with the wiki skill instead of the legacy one
uv run k8srca scenario build-metrics-reference <id>     # promtools' reports on a metrics capture, for the grader
uv run k8srca scenario compare F                        # F against the last run, side by side
uv run k8srca holdout doctor --root ~/holdout          # the hold-out kit (design 006): run by its author, not us
```

## Testing

```bash
uv run pytest               # no credentials or cluster needed
```

Tests must be hermetic. Anything describing a real cluster is a build artifact,
not source: `skills/cluster-architecture/` is gitignored and rebuilt, and tests
build the small synthetic architectures they need in code (`sample()` in
`packages/kubewiki/tests/test_wiki.py`).

The exception is `tests/test_e2e_slack.py`, which drives real sessions against
a real cluster and costs real money. It is skipped unless asked for:

```bash
# needs `k8srca up`, `k8srca sync`, and `k8srca poller` running
K8SRCA_TEST_LIVE=1 uv run pytest tests/test_e2e_slack.py -v -s
```

Concurrency is the part of this system that has broken most often -- the
poller serialising spawns, two Slack threads sharing a session -- and none of
it shows up in a single-threaded test. `tests/test_poller_concurrency.py`,
`tests/test_orchestrator.py::TestConcurrentThreads` and
`TestEventDedup::test_exactly_one_racing_caller_wins` all fail if their fix is
reverted; keep them that way rather than relaxing them into smoke tests.

## Things that bite

- **Skill versions must be `"latest"`.** Pinning a `skver_` id looks right and
  silently breaks delivery: the platform rewrites it to a numeric form when
  snapshotting the agent onto a session, and the skills endpoint rejects that.
- **The sandbox runs as the workspace owner.** A bind mount replaces the image's
  `/workspace`, so the Dockerfile's `chown` does not apply and skill download
  fails silently. It no longer fails *unnoticed*: the poller passes
  `K8SRCA_SKILLS` and the worker asserts those skills appeared, raising when none
  did. The check has to watch during the turn, because the SDK deletes each skill
  directory when its toolset context exits — so afterwards a successful download
  and a failed one both leave an empty `skills/` dir, which is what made this
  silent in the first place.
- **Site-specific values live in `.env`, not `k8srca.yaml`.** Tunnel host and
  remote endpoint are infrastructure topology.
- **Port 8000** is the k8stools container. k8stools' own test suite binds the
  same port and will silently talk to the container instead.
