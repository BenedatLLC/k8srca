# k8srca

A Kubernetes root-cause analysis agent, built on Claude Managed Agents.

An SRE asks in Slack why a workload is failing. The agent investigates the live
cluster through read-only tools and answers with a ranked diagnosis, citing the
evidence behind every claim and saying plainly what it could not check.

**It is read-only with respect to your cluster by construction** — it observes
and recommends; it cannot apply changes. That property is structural rather than
a matter of prompting, and [§8 of the architecture design](designs/001-architecture.md)
describes the four independent layers that hold it up.

Apache 2.0 licensed. Requires Python 3.12+, Docker, and an Anthropic API key.

---

## What it looks like

The shape of an answer, for a JVM that is killed for memory as it starts
(illustrative, from [design 010 §5.3](designs/010-facts-and-short-answers.md)):

> **@you** `@k8srca` the ad service is crash-looping, what's going on?
>
> **@k8srca** **Most likely:** the `ad` container's 300Mi memory limit is too
> low for the JVM it runs, so it is killed for memory as it starts (very
> likely).
>
> **Why:** every retained termination is OOMKilled[1], and in every sampled
> life memory is at the limit from the first sample or rises to it within
> seconds[2]. The limit equals the request, and the JVM has no heap
> sizing[3][4].
>
> **Ruled out:** an application crash — no exception is logged before the
> kills[5]. A liveness probe — none is configured[6].
>
> **Next steps:** raise `ad`'s memory limit, or cap the JVM's heap with
> `-XX:MaxRAMPercentage`. To rule out a leak, check whether memory keeps
> climbing after startup once it runs with more headroom.
>
> *Evidence*
> [1] ad: last 3 terminations OOMKilled, exit 137 (k8s_get_workload_report)
> [2] ad: memory at the 300Mi limit from the first sample in 182 of 214
> sampled lives; the other 32 reach it within 31s (prom_memory_report)
> …

The answer is a few hundred words. Every figure in it comes from a numbered
fact that code stated from a tool result. The host prints those facts as the
Evidence notes, and checks before posting that every number in the prose is in
a fact the answer cites.

---

## Why

### The problem

The first twenty minutes of a Kubernetes incident are mostly mechanical. Someone
lists pods, checks container statuses, reads events, pulls logs from the
previous instance, looks at the node, and asks whether anything was deployed
recently. It is a well-specified evidence-gathering loop, it is tedious, and it
is being done by the person least able to spare the attention.

The judgment lives in two places: what to look at next, and when to stop. An
agent is good at the loop and passable at the judgment — *if* the method is made
explicit rather than left to instinct. That is the bet this project makes, and
[design 002](designs/002-investigation-model.md) is the method, as
[008](designs/008-conclusions-under-incomplete-evidence.md) and
[010](designs/010-facts-and-short-answers.md) revised it.

### Read-only by construction, not by policy

The obvious next step after "diagnose this" is "now fix it," and we deliberately
do not take it.

An agent that can restart a deployment is in a different risk category from one
that can only read, and it needs a different kind of trust, review and audit.
More practically: during an incident you want a colleague who explains what they
found, not one who changes things while you are reading. So k8srca proposes
changes as text and never applies them.

The guarantee does not rest on the model choosing to behave. Four independent
layers, any one of which would hold alone:

1. **No mutating tools exist.** The MCP server the agent talks to exposes reads
   only. There is no create, patch, delete or exec to call.
2. **The sandbox holds no credentials.** The container that runs agent-authored
   code has no kubeconfig, no `kubectl`, and no cloud CLI. The credential lives
   in a different container.
3. **Egress is confined.** The sandbox can reach the tool server and Anthropic.
   It cannot reach the Kubernetes API, private address ranges, or cloud
   metadata endpoints.
4. **RBAC is the backstop.** The ServiceAccount the tool server uses is granted
   read verbs only, so even a compromise of the layers above cannot write.

### One door to the cluster

Every piece of cluster access goes through a single MCP server
([k8stools](https://github.com/BenedatLLC/k8stools), a separate open-source
project). No part of k8srca imports the Kubernetes client, shells out to
`kubectl`, or reads a kubeconfig — and that is enforced by a test that walks
every module's syntax tree, not by convention.

Three reasons, in the order they bite. One credential, one holder, so the
security property can be checked in one place. The tool surface *is* the
boundary, so code that reaches past it is not bound by it. And snapshots go
stale while tools do not — anything read at build time freezes, but the same
fact behind a tool can be asked during the investigation that needs it.

When a capability is missing, the fix is to add it to the tool server. That is
how `get_replicaset_summaries` came about: the agent needed deploy history, and
the shortcut of reading ReplicaSets directly was the wrong answer.

### A strong coordinator and a cheap specialist

A coordinator on a stronger model plans and concludes; a specialist on a cheaper
one does the high-volume reading and reports back.

The split is by **data volume, not data source.** Reading a thousand lines of
logs into the coordinator's context to find four relevant ones is how an
investigation runs out of room to think. The specialist greps and returns
findings; the coordinator keeps a handful of cheap, high-signal lookups for
itself so it need not delegate trivia. Both models are configurable per agent,
because the expense-versus-context tradeoff is a deployment decision rather than
an architectural one.

### Ranked, not confirmed

The agent never sees a system's whole history. Kubernetes keeps a container's
last termination, not the ones before it; events last hours to days; logs
rotate. So the agent does not try to *confirm* a cause. It ranks the
candidates on the facts it has, and says how strong the leader is: *very
likely*, *likely* or *possible*
([design 008](designs/008-conclusions-under-incomplete-evidence.md)).

A candidate is **ruled out** only by a fact that excludes it. One that nothing
decided is not listed as a rival: it appears as a next step, with the check
that would settle it.

### Facts from code, judgment from the model

The agent failed most often where it derived low-level facts by hand: counts,
durations, "every instance", how far back a record goes. So that work moved
into code ([design 010](designs/010-facts-and-short-answers.md)).

Deterministic extractors read each tool result and state facts with their
scope, such as "events cover the last 10 days" or "the last termination only:
Kubernetes keeps no earlier ones". The model chooses what to call next, which
cause the facts point to, and how to say it. The model records only what needs
reading text, such as a log that stops mid-startup, and those facts are marked
as its own.

### Why the plumbing looks the way it does

The cluster you want to investigate is almost certainly not reachable from the
public internet, and the tools that read it must run somewhere that can reach
it. So tool execution is **self-hosted**: Anthropic runs the agent loop, you run
the container that executes the tools.

That also keeps privileges separated between the two host processes. The
orchestrator holds the Slack tokens and an Anthropic API key but cannot claim
work items. The poller can claim work items but holds no API key — because it
spawns containers that run model-authored bash, and an API key within their
reach would be a key the agent could use.

---

## How it fits together

```mermaid
flowchart TB
    SRE["SRE, in a Slack thread"]

    subgraph anthropic["Anthropic — runs the agent loop"]
        SESS["Session<br/>coordinator · Claude Sonnet 5<br/>specialist thread · Claude Haiku 4.5"]
        SKILLS["Skills<br/>k8s-rca: loop, answer shape, knowledge base<br/>cluster-architecture: kubewiki's wiki"]
    end

    subgraph yours["Your infrastructure — runs the tools"]
        ORCH["Orchestrator · k8srca slack run<br/>Slack tokens + API key<br/>answer checks, Evidence notes"]
        POLL["Poller · k8srca poller<br/>environment key only"]
        SBX["Sandbox container, one per work item<br/>worker + facts layer<br/>no credentials · egress-confined"]
        K8ST["k8stools MCP server<br/>read-only tools<br/>sole holder of the kubeconfig"]
        PROM["promtools MCP server<br/>read-only Prometheus<br/>optional, off by default"]
    end

    CLUSTER["Your Kubernetes cluster<br/>read-only ServiceAccount"]

    SRE <--> ORCH
    ORCH -->|"create session, stream events"| SESS
    SESS -->|"work item"| POLL
    POLL -->|"docker run"| SBX
    SKILLS -.->|"downloaded per session"| SBX
    SBX <-->|"tool results with facts"| SESS
    SBX -->|"MCP over HTTP"| K8ST
    SBX -.->|"MCP over HTTP"| PROM
    K8ST -->|"read verbs only"| CLUSTER
    PROM -.->|"queries only"| CLUSTER
```

What happens when someone asks a question:

1. **The orchestrator** sees the mention over Slack's Socket Mode, finds or
   creates a session for that thread, and starts streaming events back into a
   single Slack message it keeps editing.
2. **Anthropic runs the agent loop.** The coordinator plans, and may spawn a
   specialist thread on a cheaper model for high-volume reads. When the agent
   calls a tool, a *work item* appears on your environment's queue.
3. **The poller** claims the work item and spawns a sandbox container for it.
   Containers are per work item, not per turn: one lives for a session's whole
   active period plus a short idle grace, so a conversation with pauses spans
   several.
4. **Inside the sandbox**, the worker acts as an MCP client to k8stools (and
   promtools, where metrics are enabled) and serves those tools back to the
   agent. The facts layer reads each result as it passes, states its facts, and
   puts them above the raw result.
5. **k8stools** is the only process holding a kubeconfig. It answers with read
   verbs only.
6. **The answer** comes back through the same event stream. Before it is
   posted, the host checks it and turns its fact markers into numbered
   Evidence notes. A failed check sends it back for one revision. The answer
   then replaces the placeholder message in the Slack thread. A thread is one
   investigation; follow-ups continue it with the prior context intact.

### The agent, in layers

| Layer | What it is | Where |
| --- | --- | --- |
| **Tools** | Read-only MCP servers. k8stools' composite tools (`get_namespace_health`, `get_workload_report`) return one workload's state in one call; promtools' reports do the same for metrics | [k8stools](https://github.com/BenedatLLC/k8stools), `packages/promtools` |
| **Facts** | Deterministic extractors, one per tool result shape. Each fact carries a statement, a scope and its knowledge-base terms, and is logged to `evidence/facts.jsonl` | `src/k8srca/facts/` |
| **Knowledge** | Symptoms, their candidate causes, the facts that support or rule out each, and the actions that observe those facts. Authored, or mined from public debugging guides and reviewed. `kb_query.py open` ranks the candidates from the facts so far | `docs/rca/`, `skills/k8s-rca/` |
| **The agent** | A short coordinator prompt (routing, delegation, Slack form) and a SKILL.md holding the loop: look up the symptom, rank, check, stop, answer | `agents/`, `skills/k8s-rca/SKILL.md` |
| **Host checks** | Before posting: every marker resolves, the answer cites a fact, it stays within 300 words, and every number is in a cited fact | `src/k8srca/review.py` |
| **Architecture** | What this deployment is made of and what calls what, with no live state, built by kubewiki from the cluster, the official chart and the official docs | `packages/kubewiki`, `skills/cluster-architecture/` |

The knowledge base ranks four families of symptoms so far: pods restarting,
pods pending, images that cannot be pulled, and slow requests
([design 007](designs/007-knowledge-representation.md)). For the vendored
alerts not yet converted, `lookup` returns their candidate causes without a
ranking, and the agent ranks them itself by the same rule.

---

## Documentation

| Guide | Covers |
| --- | --- |
| [Installation](docs/installation.md) | **Start here.** Zero to running, plus the full config reference |
| [Slack app setup](docs/slack-app-setup.md) | Creating the Slack app, scopes, events, verification |
| [Cluster setup](docs/cluster-setup.md) | Read-only ServiceAccount, minting a credential, troubleshooting |
| [Generators](docs/generators.md) | How the skills are built, and how each builder is evaluated |
| [How we change the agent](docs/process.md) | Planning measurements, tripwires, checkpoints |

The design documents are the reasoning behind the code, and are kept current
with it:

| Doc | Covers |
| --- | --- |
| [001 — Architecture](designs/001-architecture.md) | Platform: Managed Agents, self-hosted sandboxes, MCP wiring, Slack, security |
| [002 — Investigation model](designs/002-investigation-model.md) | How the agent reasons: hypotheses, evidence, stopping criteria |
| [003 — Operations](designs/003-operations.md) | Observability, GitOps, Kubernetes deployment |
| [004 — Scenario testing](designs/004-scenario-testing.md) | How we find out whether it works: captured scenarios, replay, checklist grading (done in part) |
| [005 — Modular architecture](designs/005-modular-architecture.md) | The target: each slot behind a contract. Generators and their evals are built; the rest is not |
| [006 — Hold-out suite](designs/006-holdout-suite.md) | Scenarios we never tune against, run by someone else (done in part, on hold) |
| [007 — Knowledge representation](designs/007-knowledge-representation.md) | Facts, actions, claims, availability per deployment, mined content and runbooks (in progress) |
| [008 — Conclusions under incomplete evidence](designs/008-conclusions-under-incomplete-evidence.md) | Ranking in place of confirmation; strength; scoped facts (done in part) |
| [009 — Metrics](designs/009-metrics.md) | Prometheus: what a cluster must scrape, promtools, and scenarios that replay metrics (done in part) |
| [010 — Facts and short answers](designs/010-facts-and-short-answers.md) | The facts layer, the short footnoted answer, checklist grading (done) |
| [kubewiki](packages/kubewiki/docs/design.md) | The deployment knowledge-graph generator behind the architecture skill |

---

## Setup

What you need first: Python 3.12+ and [`uv`](https://docs.astral.sh/uv/), Docker,
an `ANTHROPIC_API_KEY`, a Slack workspace you can install an app into, and
`kubectl` access to the cluster you want to investigate.

[docs/installation.md](docs/installation.md) is the complete version. This is
the shape of it.

### 1. A least-privilege credential — do this first

```bash
kubectl apply -f rbac/k8srca-readonly.yaml   # k8srca-reader: get/list/watch, no secrets
./rbac/make-reader-kubeconfig.sh             # -> ~/.kube/k8srca-reader.yaml
```

The script mints a bound, time-limited token rather than a permanent Secret,
and prints a verification block whose `no` answers are the point:

```
  create  pods: no      delete  pods: no      patch   pods: no
  list    pods: yes     get     pods/log: yes get     secrets: no
```

`k8srca.yaml` already points `cluster_access.kubeconfig` at that path, so
there is nothing to edit unless you put it somewhere else. The credential is
configured there rather than in `.env` because `k8srca up` derives the
container-side copy from it. [docs/cluster-setup.md](docs/cluster-setup.md)
covers rotation, tunnelling to a cluster that only listens on localhost, and
the failure modes.

> **The kubeconfig you mount is the whole ballgame.** k8stools exposes no
> mutating tools and the sandbox never sees the credential, so an
> over-privileged kubeconfig is not immediately exploitable — but it removes the
> RBAC backstop that makes the read-only guarantee hold if the other layers
> fail. If you bring your own, check it:
> `kubectl auth can-i create pods -A --kubeconfig <file>` must say `no`.

### 2. Configure and bring up the local pieces

```bash
uv sync
cp .env.example .env          # Anthropic keys and Slack tokens
uv run k8srca up              # network, tunnel, kubeconfig, k8stools — idempotent
./systemd/install.sh          # units for up, tunnel, poller and orchestrator
```

The units are how the two long-running processes survive a logout or a reboot.
Without them `k8srca status` can read entirely green while the bot ignores
you — `up` starts neither the poller nor the orchestrator.

`up` is idempotent and doubles as a diagnostic. It derives what it can at run
time — Docker gateway address, TLS server name, uid — so the only site-specific
configuration is the cluster access block in `k8srca.yaml`.

### 3. Confine the sandbox's egress

```bash
sudo ./docker/egress-rules.sh apply    # needs root; `up` reports if it is missing
./docker/verify-egress.sh              # confirms the sandbox cannot reach the API server
```

### 4. Build the knowledge, provision the agents

```bash
uv run k8srca kb build        # build the RCA knowledge base into the k8s-rca skill
uv run k8srca arch build      # describe your deployment: inventory, chart, docs
uv run k8srca sync --dry-run  # resolve tool routing, make no writes
uv run k8srca sync            # create or update the environment and agents
```

Metrics are optional and off by default (`metrics.enabled` in `k8srca.yaml`).
Turning them on needs a Prometheus that scrapes what
[design 009 §3](designs/009-metrics.md) lists, and a promtools container
pointed at it. `k8srca up` does not start promtools yet.
[docs/cluster-setup.md](docs/cluster-setup.md) has the Prometheus side.

`sync` is idempotent: it creates on the first run and updates in place after,
producing a new agent version whenever something changes. Sessions pin their
version at creation, so in-flight investigations are unaffected. Resolved IDs
land in `.k8srca/state.json`, which is gitignored because it is
per-deployment.

### 5. Run it

```bash
uv run k8srca sandbox build   # image tagged with the commit it was built from

# terminal 1 — tool execution
uv run k8srca poller          # one sandbox container per work item

# terminal 2 — Slack
uv run k8srca slack run
```

Then `/invite @k8srca` into a channel and mention it. A Slack thread is one
investigation; follow-ups in that thread continue it. Set
`SLACK_ALLOWED_CHANNELS` so it only answers in the channels you expect; anyone
in the workspace can also DM it.

Useful while things are settling:

```bash
uv run k8srca status          # is a Slack mention going to be answered?
uv run k8srca slack check     # token, scopes, channel, event delivery
uv run k8srca sandbox show    # which image will a new session pin?
uv run k8srca timing          # where the last session's wall clock went
uv run k8srca down            # stop what `up` started
```

You can also drive a session without Slack:

```bash
uv run k8srca session "why is payment-api crash-looping in prod?"
uv run k8srca session --resume <id> "what changed recently?"
```

---

## Development

**No cluster is needed.** k8stools has a mock mode that serves realistic static
data, which is enough for everything except cluster-specific reasoning.

**What to install.** On Linux, with `/bin/bash` at that exact path:

| | What needs it |
| --- | --- |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Everything. It installs Python and the dependencies. |
| [Docker Engine](https://docs.docker.com/engine/install/) with [Compose v2](https://docs.docker.com/compose/install/linux/) | The k8stools container, real or mock, and the sandbox image. Scenario replays and `eval arch` run k8stools and promtools in containers. |
| [git](https://git-scm.com/downloads) | The clone, and the worktrees measurements run from. |
| [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/) | You use it, not k8srca: for the read-only credential, and to inject a scenario's fault. |
| [helm](https://helm.sh/docs/intro/install/) | kubewiki's `helm template` (the one exception to the no-cluster rule), which renders the pinned charts for `arch build` and `scenario build-wiki`. |
| [GitHub CLI](https://cli.github.com/) (`gh`), optional | Filing issues. Changes to k8stools go through an issue on its repository. |
| OpenSSH client, optional | `k8srca up`'s tunnel, when the cluster's API server is reached over SSH. |

`uv run pytest` needs only uv. Anything that calls a model also needs an
Anthropic API key in `.env` ([installation](docs/installation.md)).

```bash
uv sync --extra dev

# A mock MCP server — no cluster, no kubeconfig
docker compose -f docker/compose.yaml --profile mock up -d k8stools-mock
sed -i 's|http://k8stools:8000/mcp|http://127.0.0.1:8009/mcp|' k8srca.yaml

uv run k8srca kb build        # normalize the KB into the skill bundle
uv run k8srca tools list      # the tool surface as the model will see it
uv run k8srca tools validate  # schema legality, collisions, per-agent routing
uv run pytest                 # unit tests — hermetic, no credentials, no cluster
```

Measuring a change to the agent, with captured scenarios replayed through
k8stools (and promtools, for the ones with metrics), costs real money:

```bash
uv run k8srca scenario run <id> --local-skill k8s-rca --n 3   # graded on the scenario's checklist
uv run k8srca eval answer     # the answer writer alone, on fixed facts (~$0.10/answer)
uv run k8srca eval arch       # the architecture generator alone (Docker, no model)
```

Read [docs/process.md](docs/process.md) before planning one.

`tools validate` runs the same checks `sync` does without touching the Anthropic
API: schema legality, cross-server name collisions, and whether each agent's
tool groups resolve.

### Testing

`uv run pytest` is hermetic and needs nothing. Two suites are opt-in because
they are not:

```bash
# against a live MCP server
K8SRCA_TEST_MCP_URL=http://127.0.0.1:8009/mcp uv run pytest tests/test_live_mcp.py

# end to end: real sessions, real containers, real money
# needs `k8srca up`, `k8srca sync`, and `k8srca poller` running
K8SRCA_TEST_LIVE=1 uv run pytest tests/test_e2e_slack.py -v -s
```

Concurrency is the part of this system that has broken most often — the poller
serialising container spawns, two Slack threads sharing a session — and none of
it shows up in a single-threaded test. Those regression tests are written so
that reverting the fix fails them; please keep them that way rather than
relaxing them into smoke tests.

### A note on `k8srca worker`

`k8srca worker` runs the worker in-process on your host rather than in a
container. It is the development shape and it is **not isolated**: it executes
agent-authored bash directly on your machine with your filesystem access.
Credentials are scrubbed from its environment, but that is defence in depth, not
a sandbox. Use `k8srca poller` against anything real.

---

## Status

Running against a real OpenTelemetry-demo cluster through the containerised
path. All four layers of the read-only guarantee are verified by measurement,
including egress confinement.

- [x] RBAC manifest, k8stools container, compose
- [x] Config schema, MCP tool declaration generation, manifest hashing
- [x] Worker-side tool wrapper (prefixed name → unprefixed remote call)
- [x] `k8srca sync`: agent and environment provisioning
- [x] Sandbox image, spawn script, `k8srca poller`: containerised, multi-turn
- [x] Egress rules, applied and verified against a real cluster
- [x] Slack orchestrator, session map, concurrent threads
- [x] Cluster architecture skill: kubewiki's wiki of the deployment
- [x] Scenario suite: ten captured scenarios, replayed, graded on checklists, with a baseline ([004](designs/004-scenario-testing.md))
- [x] Facts layer, short footnoted answers, answer checks ([010](designs/010-facts-and-short-answers.md))
- [x] Ranking in place of confirmation ([008](designs/008-conclusions-under-incomplete-evidence.md))
- [x] Knowledge format, build checks, `open`'s ranking, and the mining pipeline; four families mined ([007](designs/007-knowledge-representation.md))
- [x] promtools and metrics replay in the scenario suite ([009](designs/009-metrics.md)); off by default in production
- [x] Generators behind one contract, with component evals ([005](designs/005-modular-architecture.md) step a)
- [ ] The remaining vendored alerts converted (18 of 81 so far), and runbooks ([007](designs/007-knowledge-representation.md) §8.5, §9)
- [ ] Latency and throttling reports for metrics ([009](designs/009-metrics.md))
- [ ] The rest of 005: plugins, Kubernetes deployment, other runtimes and chat adapters
- [ ] Durable investigation record (002 L2): prerequisite for deploying to Kubernetes
- [ ] Alert-driven investigations, cross-session memory

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
