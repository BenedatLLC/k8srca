# k8srca

Kubernetes root-cause analysis agent built on Claude Managed Agents. An SRE asks
in Slack why something is broken; the agent investigates a live cluster through
read-only tools and answers with evidence.

## How this project is governed

Read these before changing anything; they bind a coding agent as they bind a
developer:
- `docs/constitution.md`: the rules every change keeps, for the architecture
  (the Kubernetes API only through k8stools, read-only, one holder per
  credential, hermetic tests, ...) and for how we work (plan the measurement
  first, warning signs stop work, a frozen instrument, hold-outs stay unread,
  issues close only when finished, every change reviewed).
- `docs/development.md`: how a change is made, tested, evaluated and reviewed,
  and how to ask k8stools for a capability.
- `docs/process.md`: how a change to the agent is measured. Before a live run
  or another fix, check its warning signs (§2); if one has fired, say so and
  stop for a decision rather than proposing another change.
- `docs/writing.md`: how documents are written, and the front matter every
  design and plan starts with.
- `designs/README.md`: when a change needs a design, the statuses, and the
  index of every design's status. Designs are authoritative: update them when
  the code contradicts them. `designs/001-architecture.md` describes the
  system as it is. The index marks with `*` the one design being measured;
  only it may spend the live-evaluation budget or change the measured agent.
- `plans/README.md`: a design says what and why; its plan
  (`plans/NNN-name.md`, same number and name) holds the steps, each step's
  five measurement fields, the results table and any checkpoint. Look there,
  not in the design, for where the work stands. Run-by-run logs go in
  `docs/journal/`.
- GitHub issues and their relationships (sub-issues, "blocked by") are the
  source of truth for what is open, who owns it and what blocks what.
  `docs/todo.md` maps how the work fits together and holds the order among
  unblocked work; on state, the issues win. `docs/glossary.md`: project terms;
  keep it current when a design adds one.

Do not commit, push, open or close issues, or spend evaluation money unless
asked. Record decisions in the design, plan or issue they apply to, not only
in memory.

When asked to carry a change through: branch from `main` as `NNN-short-name`
(NNN is the issue number), put `Fixes #NNN` in the pull request and fill in
`.github/pull_request_template.md`. When a design's or plan's status changes,
update its front matter (`status`, `last_updated`) and its row in the
`designs/README.md` or `plans/README.md` index in the same change.

## Rule: k8srca never touches the Kubernetes API directly

**All Kubernetes API access goes through the k8stools MCP server. No part of k8srca may
import the `kubernetes` client, call `load_kube_config`, shell out to `kubectl`,
or read a kubeconfig.** This holds for build-time code (`k8srca arch build`) as
much as for anything the agent drives. This is constitution A1, which gives
the reasons: one credential with one holder, k8stools' tool list as the
security boundary, and live tools over stale snapshots.

**If a capability is missing, it goes upstream** (constitution A4): an issue
on k8stools, never a workaround here, even in a build-time script.
`docs/development.md` ("Upstream changes to k8stools") says how to file and
adopt one. The same holds for promtools (`packages/promtools`), which may
call only Prometheus' query endpoints.

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
(`packages/kubewiki/tests/test_kubewiki_boundary.py`), as does promtools
(`packages/promtools/tests/test_promtools_boundary.py`, which also holds it to
GET requests on query endpoints). They parse rather than grep, so a docstring
explaining the rule does not trip them and a real import cannot hide in one.
If one fails, the fix is never to relax it.

## Project layout

```
src/k8srca/
  cli.py           - the `k8srca` command line
  config.py        - k8srca.yaml schema (pydantic)
  settings.py      - secrets from .env; scrub_environment for the worker
  sync.py          - control plane: skills, agents, environment
  state.py         - resolved control-plane IDs, written by sync (.k8srca/state.json, gitignored)
  runtime.py       - what each long-running process loaded at start, so `status` can tell stale
  sandbox.py       - identifying the sandbox image by its contents, not its tag
  tools.py         - MCP tool -> custom-tool declarations, prefixing, manifest hash
  mcp_client.py    - connecting to MCP servers
  bringup.py       - `k8srca up` / `status`: idempotent bring-up and health
  cluster.py       - derived facts: docker gateway, TLS names, tunnels
  session.py       - creating and consuming Managed Agents sessions
  timing.py        - where a session's wall clock went
  evidence.py      - every MCP result saved to the workspace for scripts (design 007 §6.1)
  review.py        - the host's check of a final answer, and its revision turn (007 §6.1)
  facts/           - the facts layer: tool results stated as facts by code (010 §3)
  core/            - contracts shared by components (design 005): the generator contract
  evals/           - component evals: the architecture generator and the answer writer, no agent
  scenario/        - the scenario suite: record, replay, run, grade, compare; the hold-out kit
  arch/            - the cluster-architecture skill: kubewiki behind the generator contract
  kb/              - the RCA knowledge base skill
  slack/           - the Slack orchestrator
  worker/          - sandbox entrypoint and host poller

packages/kubewiki/ - the deployment knowledge graph generator: a standalone
                     package that builds the architecture skill and never
                     imports k8srca (packages/kubewiki/docs/design.md)
packages/promtools/ - the Prometheus MCP server: query endpoints only (design 009)

agents/            - the coordinator's and investigator's system prompts
designs/, plans/   - designs and their plans, each with an index in its README
docs/              - governance, setup guides, glossary, todo.md, journal/
```

## Commands

The ones used most; `uv run k8srca --help` lists the rest. Which evaluation
to run when, and what each costs, is in `docs/development.md` ("Evaluations,
cheapest first").

```bash
uv run k8srca up            # network, tunnel, kubeconfig, k8stools -- idempotent
uv run k8srca status        # is a Slack mention going to be answered?
uv run k8srca down          # stop what `up` started
uv run k8srca sync          # apply k8srca.yaml to the Anthropic control plane
uv run k8srca arch build    # build the cluster-architecture skill: kubewiki's wiki (synthesis cached)
uv run k8srca kb build      # normalise the RCA knowledge base
uv run k8srca kb mine <family>  # mine public guides into a draft layer to review (007 §8; ~$5/family)
uv run k8srca eval arch     # score the architecture generator (Docker, no model)
uv run k8srca eval answer   # the answer writer on fixed facts, graded on checklists (010 §8; ~$0.10/answer)
uv run k8srca eval arch --judge  # also check its docs against the cluster (~$0.22/case)
uv run k8srca eval arch --model claude-opus-5  # synthesise the wiki, score kinds (~$1/case, cached)
uv run kubewiki build --config kubewiki.yaml   # kubewiki standalone: build a wiki (packages/kubewiki); --model to synthesise
uv run kubewiki check <wiki-dir>               # its deterministic checks
uv run k8srca poller        # one sandbox container per work item (production)
uv run k8srca worker        # in-process worker (development only -- no isolation)
uv run k8srca slack run     # the Slack orchestrator
uv run k8srca timing        # break down a session's latency
uv run k8srca scenario fetch                            # captures, skills, metrics from k8srca-scenario-data (issue #7); --replace when one is stale
uv run k8srca scenario publish <id>                     # put a scenario's capture, skills and metrics there
uv run k8srca scenario run <id> --local-skill k8s-rca   # measure a skill change before syncing it (graded on checklist.yaml; --rubric adds 008's rubric)
uv run k8srca scenario regrade .k8srca/scenario/runs/<stamp>.jsonl  # grade saved answers again after a grader change
uv run k8srca scenario build-wiki <id>                  # build a scenario's wiki from its capture
uv run k8srca scenario run <id> --arch wiki --save F    # run with the wiki skill instead of the legacy one
uv run k8srca scenario build-metrics-reference <id>     # promtools' reports on a metrics capture, for the grader
uv run k8srca scenario checklist F --grades 3              # grade saved answers against checklist.yaml (010 §7); per-item agreement
uv run k8srca scenario record-results <id>              # the composite tools' results on a replay: facts-layer fixtures
uv run k8srca scenario rescore F                        # saved checklist verdicts scored on today's checklists; no grader calls
uv run k8srca scenario compare F                        # F against the last run, side by side
uv run k8srca scenario baseline --agreement G           # the last run becomes the baseline; G: `scenario checklist F --grades 3` on its answers
uv run k8srca holdout doctor --root ~/holdout          # the hold-out kit (design 006): run by its author, not us
```

## Testing

```bash
uv run pytest               # no credentials or cluster needed
```

Scenario captures and pinned skills live in `BenedatLLC/k8srca-scenario-data`
(issue #7). Tests never fetch them: a test that reads one skips, saying so,
until `uv run k8srca scenario fetch` has been run once.

Tests must be hermetic. Anything describing a real cluster is a build artifact,
not source: `skills/cluster-architecture/` is gitignored and rebuilt, and tests
build the small synthetic architectures they need in code (`sample()` in
`packages/kubewiki/tests/test_wiki.py`).

Two suites are not hermetic and are skipped unless asked for:
`tests/test_live_mcp.py`, against a running MCP server (set
`K8SRCA_TEST_MCP_URL`), and `tests/test_e2e_slack.py`, which drives real
sessions against a real cluster and costs real money:

```bash
# needs `k8srca up`, `k8srca sync`, and `k8srca poller` running
K8SRCA_TEST_LIVE=1 uv run pytest tests/test_e2e_slack.py -v -s
```

If a test fails because of your change, fix the code, not the test, unless
the test itself is wrong. That applies most to the concurrency tests
(`tests/test_poller_concurrency.py`,
`tests/test_orchestrator.py::TestConcurrentThreads`,
`TestEventDedup::test_exactly_one_racing_caller_wins`): each fails if its fix
is reverted, so keep them that way rather than relaxing them into smoke tests
(`docs/development.md`, "Testing").

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
