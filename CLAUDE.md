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
uv run k8srca arch build    # build the cluster-architecture skill
uv run k8srca kb build      # normalise the RCA knowledge base
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
```

## Testing

```bash
uv run pytest               # no credentials or cluster needed
```

Tests must be hermetic. Anything describing a real cluster is a build artifact,
not source: `skills/cluster-architecture/` is gitignored and rebuilt, and tests
run against `packages/kubewiki/tests/fixtures/architecture.json`, which they own.

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
