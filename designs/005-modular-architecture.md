# Design 005 — Modular architecture

**Status:** draft, revised 2026-10-03. Nothing here is built yet. The migration in §10
is ordered so each step lands on its own and leaves the system working.

Companion to [001](001-architecture.md) (platform), [002](002-investigation-model.md)
(how the agent reasons), [003](003-operations.md) (running it) and
[004](004-scenario-testing.md) (finding out whether it works). 001 describes the
system as built, on one choice per slot: Slack, Claude Managed Agents, Docker.
This document describes the target: the same system with each slot behind a
contract, so the slots can have several implementations and be developed by
different people. 001 is updated section by section as pieces move. It is not
rewritten up front.

---

## 1. Goals

1. **Components can be developed and tested in isolation.** Someone working on
   the Teams adapter does not need a cluster, Anthropic credentials or Slack.
   Someone working on the architecture generator does not need an agent.
2. **Key slots have several implementations,** chosen per deployment: chat
   (Slack, Teams, A2A), agent runtime (Claude Managed Agents, OpenAI, local),
   sandbox (Docker, Kubernetes), and observability capabilities (metrics,
   tracing, logs).
3. **Components can live outside this repo.** A developer can write a Discord
   adapter, another agent integration or an observability plugin in their own
   repo and plug it in without changing k8srca. That is the test of whether
   the seams are real.
4. **Evaluation at two levels:** components on their own terms (is the
   architecture skill accurate and complete?), and the system end to end on
   captured scenarios (004), run through any runtime.

Decisions taken while drafting are listed in [Appendix A](#appendix-a-decisions-taken).

**Non-goals.** Running two runtimes in one deployment. Automatic discovery of
which plugins a cluster supports (declared for now, §5.4). Versioning in-repo
components independently: k8srca ships as one release that works together
(§11).

---

## 2. Where the coupling is today

The import graph of `src/k8srca` (2026-10-01) shows one dependency running
through nearly everything: the agent platform.

- **Platform calls are spread across seven modules.** Code that calls
  `client.beta.sessions`, `.agents`, `.environments` or skills APIs sits in
  `slack/app.py`, `session.py`, `sync.py`, `kb/skills.py`, `worker/poller.py`,
  `scenario/runner.py` and `cli.py`. The Slack app opens platform sessions
  itself. The scenario runner can only evaluate a Managed Agents session.
- **Chat reads platform events.** `slack/relay.consume` and `session.consume`
  each branch on Managed Agents event types (`agent.custom_tool_use`,
  `session.status_idle` with `requires_action`, `session.thread_created`).
  There is no k8srca-owned notion of a turn, so each consumer re-implements the
  same break rules, and fixes have to land twice. The dropped-stream reconnect
  (2026-09-30) had to be wired into four call sites.
- **Configuration and state are single global schemas.** `k8srca.yaml` mixes
  chat, platform, sandbox and capability settings. `state.json` holds platform
  object IDs.

Two parts are already well separated and serve as precedents:

- **Cluster access** is behind MCP, in its own repo (k8stools), with its own
  release cycle and other users. The rule that only k8stools holds the
  kubeconfig is enforced by a test, not by convention (CLAUDE.md).
- **The architecture builder** (`arch/`) talks only to MCP and to files.

---

## 3. The shape

```
                    ┌─────────────── chat adapters ────────────────┐
 people, channels → │ Slack · Teams · A2A server · (Discord, ext.) │
 and other agents   └──────────────────────┬───────────────────────┘
                                           │ conversations, triggers
                                           ▼
                    ┌──────────────── orchestrator (core) ──────────┐
                    │ conversation ⇄ session mapping, dedup, locks, │
                    │ trigger rules, rendering policy               │
                    └──────────────────────┬────────────────────────┘
                                           │ start/continue → TurnEvents
                                           ▼
                    ┌──────────────── runtime adapters ──────────────┐
                    │ Managed Agents · OpenAI Agents · local loop    │
                    │  control plane            executor (in sandbox)│
                    └───────────┬─────────────────────────────┬──────┘
                                │ provision / reap            │ tool calls
                                ▼                             ▼
                    ┌──────── workload backends ────────┐   ┌── capability plugins ──┐
                    │ Docker · Kubernetes               │   │ skills + MCP servers   │
                    │ sandboxes · MCP services · egress │   │ k8s-core · prometheus ·│
                    └───────────────────────────────────┘   │ tracing · logs · ext.  │
                                                            └────────────────────────┘
   build time:  generators (meta-skills) ── sources ─▶ skill bundles ─▶ plugins
   evaluation:  component evals per generator/plugin · system scenarios via any runtime
   operation:   management API + web UI over the orchestrator's turn store (§9)
```

Five kinds of component, each behind a contract in a small core package:

| Kind | Contract (§) | In this repo | Outside, for example |
|---|---|---|---|
| Chat adapter | §3.1–4.3 | Slack, Teams, A2A | Discord |
| Runtime adapter | §4 | Managed Agents, OpenAI | another agent platform |
| Workload backend | §4.4 | Docker, Kubernetes | |
| Capability plugin | §5 | k8s-core, the generated skills | prometheus, tracing, a vendor's logs |
| Generator (meta-skill) | §6 | cluster-architecture, runbooks | |

The orchestrator is not a slot. It is the core logic that today is spread
between `slack/app.py` and `session.py`: mapping conversations to sessions,
de-duplicating events, per-conversation locking, and the turn break rules.
Those are exactly where the concurrency bugs were (CLAUDE.md: the poller
serialising spawns, two threads sharing a session). Moving them into one place
that every chat adapter shares means fixing them once.

### 3.1 The turn-event model

The central artifact. A k8srca-owned, normalized stream that every runtime
adapter produces and every chat adapter and evaluator consumes:

| Event | Carries |
|---|---|
| `message` | text from the coordinating agent (not from delegated sub-agents) |
| `progress` | a status line: tool used, delegation, phase |
| `tool_call` | tool name, for evals and timing (004 grades the tool list) |
| `error` | a session error, with whether it is billing |
| `done` | why: `end_turn`, `budget`, `terminated`, or `error` |

Rules that today live in each consumer move behind this boundary, into the
runtime adapter:

- **When a turn is over.** On Managed Agents, `status_idle` with
  `requires_action` is not done. That is a platform detail and must not leak
  into chat code.
- **Reconnecting a dropped stream** and replaying missed events (001 §7.2).
- **Event de-duplication** by platform event ID.

A chat adapter therefore cannot get a turn boundary wrong, and a second runtime
cannot reintroduce a bug the first one fixed.

### 3.2 Chat adapter contract

A chat adapter connects one kind of conversation surface to the orchestrator:

- **Inbound:** emits `Conversation(id, origin)` + `Message(text, author)` into
  the orchestrator. The conversation ID is the adapter's own (Slack
  `channel/thread_ts`, Teams conversation ID, A2A task ID).
- **Outbound:** renders `TurnEvent`s for one conversation: progress as status,
  the final `message` as the answer. Rendering policy (only the coordinator's
  messages reach users, 001 §7.2) is the orchestrator's; how a status line looks
  is the adapter's.
- **Health:** answers `check()` for `k8srca status`.

Several adapters can be active in one deployment (Slack plus A2A). Each owns
its own conversation ID space, so the orchestrator keys sessions by
`(adapter, conversation)`.

**A2A is a chat adapter** in the inbound direction: another agent sends k8srca
a task, and gets turn events back as task updates. k8srca calling *out* to
other agents over A2A is a different thing, a capability (§5), and is out of
scope here.

### 3.3 Triggers: responding to alerts in a channel

The chat slot must support watching a channel and starting an investigation
on its own when a message matches. The motivating case: Alertmanager posts to
`#alerts`, and k8srca answers in the alert's thread without being mentioned.

A trigger is declarative and owned by the orchestrator, not the adapter:

```yaml
triggers:
  - adapter: slack
    channel: alerts
    match:                       # all must hold
      author: alertmanager       # bot or app identity
      text: 'FIRING.*\[(?P<alert>[A-Za-z]+)\]'
    prompt: "Alert {alert} fired: {text}. Why?"
    reply_in: thread
    dedup: { key: '{alert}', window: 15m }   # one investigation per alert storm
    max_concurrent: 3
```

The adapter only reports channel messages, with author and text. Matching,
templating, de-duplication and rate limiting are core, so they work the same
for Slack, Teams or Discord and are tested once against a fake adapter.

Two rules from experience:

- **De-duplication and concurrency limits are mandatory.** An alert storm is
  dozens of near-identical messages in a minute. Without a dedup key and a cap,
  each starts an investigation, and the bill and the channel are both flooded.
- **A resolved alert does not start an investigation.** `RESOLVED` messages
  should post to an existing investigation's thread if one exists, and nothing
  otherwise.

The KB is keyed by alert name (001 §5.1), so an extracted `{alert}` is already
the knowledge base's primary key.

---

## 4. Runtime adapters and sandboxes

### 4.1 Two halves

A runtime adapter has a **control-plane half** (create agents, start and
continue sessions, stream events) and an **executor half** that runs inside
the sandbox and carries out tool calls. Both platforms we target work this way.
They differ in who writes the executor.

### 4.2 The two platforms

As documented on 2026-10-03. The OpenAI column is read from its self-hosted
environments guide and has not been exercised. Verifying it is the first task
of §10 step (c).

| | Claude Managed Agents (today) | OpenAI Agents API, self-hosted |
|---|---|---|
| Who runs the agent loop | Anthropic | OpenAI |
| Executor | **ours**: `k8srca worker`, using the SDK worker | **theirs**: `codex exec-server` |
| How work reaches it | worker polls the environment for work items | executor connects out over WebSocket, receives commands |
| Executor lifetime | one container per work item (001 §7.3) | one executor per session |
| Tools | MCP tools wrapped as custom tools by our worker (001 §4.2) | shell, files and local MCP servers, run by their executor |
| Credential in the sandbox | environment key: can only connect the environment | environment key (`CODEX_API_KEY`): can only connect environments |
| Skills | platform skills, delivered into the sandbox | not described |
| Sub-agents (coordinator → investigator) | stored agents with a roster; each sub-agent its own thread (001 §3.3) | declared per session (`multi_agent: {enabled: true}`, `max_concurrent_subagents`, default 6); harness-supplied tools create, message, wait for and interrupt them |
| What sub-agents can call | any tool given to their agent definition | the parent's MCP tools, credentials and allowed tools; **no function tools** |
| Secrets kept out of the sandbox | vaults substitute credentials at egress, but **not for self-hosted sandboxes** | vaults exist (`api.vaults.*`); whether they inject or substitute is not documented |
| Budgets | per-session spend cap | not described |

OpenAI's sub-agents share the parent's environment and filesystem: "Creating a
subagent does not create another environment." Their events arrive on the
session stream as `agent.session.subagent.created` and as turn items of type
`create_subagent_call`, `send_subagent_input_call`, `wait_for_subagents_call`
and `interrupt_subagent_call`.

What follows from the table:

- **The workload backend must not assume an executor.** It runs whatever
  executor image and command the runtime adapter gives it, with the isolation
  rules of §7. Docker and Kubernetes then work for both runtimes.
- **The lifecycles differ.** Per work item for Managed Agents, per session for
  OpenAI, so the backend contract is `provision(spec) → handle` / `reap(handle)`
  with the runtime adapter deciding when.
- **MCP placement differs.** Our worker is an MCP *client* that reaches
  k8stools over the sandbox network. OpenAI's executor uses MCP servers
  configured in its environment. Either way k8stools stays a separate
  container holding the only cluster credential. What changes is who opens the
  connection.
- **Tools must be MCP to reach OpenAI sub-agents.** Our Managed Agents worker
  presents MCP tools as *custom* tools, the function-tool equivalent (001
  §3.2). OpenAI sub-agents do not support function tools, so on OpenAI the
  investigator only sees k8stools if the executor reaches it as an MCP server.
  That fits the service placement in §5.5. The custom-tool wrapping stays
  inside the Managed Agents adapter.
- **Both platforms can delegate, in different shapes.** Managed Agents stores
  agents and a roster; OpenAI declares sub-agents per session and supplies the
  delegation tools itself. The coordinator/investigator split (001 §3.3)
  carries over in intent. Agent definitions (system prompts, tool groups,
  delegation) stay in runtime-specific configuration rather than a common shape
  one platform cannot express, and the turn-event model maps both platforms'
  delegation events to `progress`.
- **Skills portable as files.** If a runtime has no skills API, the adapter
  mounts skill bundles into the workspace and points the agent at them in its
  instructions. Skill bundles stay plain directories with `SKILL.md` (§5.2)
  either way.

### 4.3 A local runtime

"Locally hosted" means a k8srca-owned agent loop over a model API, with the
same executor and sandbox as the other runtimes. It is the reference
implementation of the runtime contract and the one the fakes are modelled on.
Not scheduled (§10); listed so the contract is not shaped around two hosted
platforms only.

### 4.4 Workload backend contract: sandboxes and MCP services

```
provision(ExecutorSpec) -> Handle     # image, command, env, mounts, network
status(Handle) -> running | exited(code) | failed(reason)
reap(Handle)                          # stop, collect logs, delete
check() -> list[Finding]              # for `k8srca status`, incl. §7 checks

deploy_service(ServiceSpec) -> Endpoint   # a long-running MCP server: image,
                                          # secret mounts, network, health check
remove_service(name)
```

The same backend runs two kinds of workload. **Sandboxes** are short-lived and
per work item or session. **MCP services** are long-running and hold
credentials: k8stools today, and any plugin whose tools need a secret the
sandbox must not see (§5.5). Running both through one backend is what lets a
plugin's MCP server be "just an image" in its manifest, deployed the same way
on Docker and Kubernetes.

- **Docker:** today's `docker/spawn.sh`, the poller's `spawn()`, and
  `egress-rules.sh` for sandboxes; Compose services, like today's
  `k8stools`, for MCP services.
- **Kubernetes:** a Pod (or Job) per handle in a dedicated namespace, a
  NetworkPolicy instead of iptables egress rules, and resource limits from the
  spec. MCP services are a Deployment, a Service and a NetworkPolicy each,
  admitting only the sandbox namespace. The orchestrator and the runtime
  control plane run as Deployments next to them (§10 step b).

---

## 5. Capability plugins

### 5.1 What a plugin is

A plugin is a **capability bundle** the agent uses at run time:

- zero or more **skills** (directories with `SKILL.md` and files);
- zero or more **MCP servers**: image, tool allowlist and named tool groups,
  how it receives its credential;
- a **config schema** for the deployment-specific values it needs (a
  Prometheus URL, a tenant);
- **agent wiring hints:** which tool groups a coordinator should hold, whether
  it warrants its own investigator (001 §11 "Observability MCP");
- **its own evals** (§8.1).

`k8s-core` is a plugin like any other, always enabled: the `k8s-rca` skill
and knowledge base, and k8stools. Observability sources are plugins enabled per
deployment.

### 5.2 Manifest

```yaml
# k8srca-plugin.yaml, at the root of the plugin's path
name: prometheus
version: 0.3.0
requires: { k8srca: ">=0.9" }
skills:
  - path: skills/promql-guide
mcp:
  - name: prometheus
    placement: service           # or sandbox; see §5.5
    image: ghcr.io/example/prom-mcp:1.4.2
    prefix: prom_
    groups:
      triage: [query_instant, list_alerts]
      full: "*"
    credential: { secret: prometheus-token, mount: /run/secrets/token }
    read_only: true              # asserted, and checked (§7)
config:
  url: { type: string, required: true }
evals:
  - path: evals/
```

### 5.3 Referencing external plugins

A deployment names plugins by source. In-repo plugins by name, external ones by
git repo, path and ref:

```yaml
plugins:
  - k8s-core
  - name: prometheus
    git: https://github.com/example/k8srca-prometheus
    path: plugin/                # where k8srca-plugin.yaml lives
    ref: v0.3.0                  # tag or commit; never a branch in production
    config: { url: http://prometheus.monitoring:9090 }
```

`k8srca plugins sync` fetches each ref into a local cache, validates the
manifest and config, and records the resolved commit in a lockfile so a
deployment is reproducible. Fetching is a build step, never something that
happens at run time.

**Code components are different from plugins.** A plugin is content and
container images. A chat adapter, runtime adapter or workload backend from
outside this repo is Python code, distributed as a package that registers
under an entry-point group (`k8srca.chat`, `k8srca.runtime`, `k8srca.workloads`)
and is selected by name in configuration. Keeping the two mechanisms separate
keeps plugins free of code that runs in k8srca's own process.

### 5.4 Presence is declared

A deployment lists the plugins it enables. k8srca does not probe the cluster
to discover a Prometheus. A declared plugin whose MCP server does not answer
fails `k8srca status`, the same way an unreachable k8stools does today.

---

### 5.5 Where a plugin's tools run

A plugin's tools run in one of two places, and the deciding question is the
credential.

| Placement | What runs where | When it is allowed |
|---|---|---|
| **In the sandbox** | a client library or MCP client inside the agent's sandbox (a Prometheus client, say) | the endpoint needs no secret, or the provider keeps the secret out of the sandbox (substituted at egress, never readable there) |
| **As a service** | the plugin's own MCP server, run by the workload backend as an MCP service (§4.4), holding the credential | any credential the sandbox must not see. k8stools is the model |

The first is simpler and is what a plugin should use when it can. It is not
available on our current setup for anything that needs a secret: Managed Agents
vaults keep secrets out of the sandbox by substituting them at egress, and that
is **not supported for self-hosted sandboxes**. A secret placed in a
self-hosted sandbox some other way is plain text to anything the agent runs
there, including under prompt injection. Until a provider offers substitution
for self-hosted sandboxes, a plugin that needs a secret runs as a service.

The manifest says which (`placement: sandbox | service`). Validation rejects a
`sandbox` plugin that declares a credential the active runtime cannot keep out
of the sandbox, **unless the deployment records an exception**:

```yaml
plugins:
  - name: prometheus
    placement: sandbox
    config: { url: http://prometheus.monitoring:9090 }
    secret_in_sandbox:
      secret: { name: prometheus-readonly, key: token }
      reason: query-only token; metrics are already visible to every SRE
      reviewed: jfischer, 2026-10-03
```

Exceptions are decided case by case, not by plugin default. The criteria:

- **The credential is read-only at its issuer**, scoped there (a query-only
  Prometheus token), not merely read-only because our tools happen only to read.
- **The data behind it is acceptable to disclose.** Assume a secret in the
  sandbox is disclosed to anyone who can read the conversation or the provider's
  session history: whatever the agent can read, it can write into an answer.
  Egress control (§7.2) limits where the secret can be *used* from the sandbox,
  not whether it can be *revealed*.
- **Rotation is cheap**, and its endpoint is on the egress allowlist.

Accepted exceptions are listed by `k8srca status` and in the management UI, so
they are seen again rather than forgotten.

## 6. Generators (meta-skills)

### 6.1 Not plugins

*The cluster-architecture generator is becoming **kubewiki**, the first standalone
component: a package in this repository with its own version and PyPI release,
runnable without k8srca, and forbidden by test from importing it
([`packages/kubewiki/docs/design.md`](../packages/kubewiki/docs/design.md)).*

A generator is a **build-time** component: it reads sources and *produces* a
skill bundle, which a plugin then delivers. The two have different lifecycles
(a generator runs per deployment, on a schedule or on demand; a plugin is
loaded at run time), different inputs (a cluster, charts, runbooks), and
different evals (§8.1). Calling both "plugins" would blur exactly the
distinction that makes each testable.

Two generators are planned:

- **cluster-architecture** (exists, `arch/`): reads the live cluster through
  MCP, rendered charts, operator notes and change history, and writes the
  `cluster-architecture` skill.
- **runbooks**: reads a deployment's runbooks and writes skills structured as
  debugging guides.

### 6.2 Contract

*Landed 2026-10-03 (step a.1): `src/k8srca/core/generator.py`, with
`arch/generator.py` and `kb/generator.py` behind it.*

```
Generator:  name, format
            generate(cfg, dest) -> GeneratorReport
run(generator, cfg, dest) -> (SkillBundle, GeneratorReport)
```

A generator only writes its bundle and says what it did. `run()` adds what
every generated bundle needs and no generator should have to remember:

- **A manifest in the bundle** (`GENERATED.json`): skill name, generator,
  output `format`, k8srca release, and a digest of the bundle's content. It
  holds no timestamp, because sync re-uploads a bundle whenever its digest
  moves; a timestamp would re-upload an unchanged skill on every build and
  churn committed bundles.
- **A report outside the bundle** (`.k8srca/reports/<name>.json`): summary
  lines, counts, the build time, and **warnings** for inputs that were skipped
  or could not be resolved. It is for people and for the generator's evals
  (§8.1), not for the agent, so it is not shipped.

What each output fact came from (`observed`, `declared`, `documented`) is the
generator's own output, not the report's: the cluster-architecture skill
already carries `source` and `origin` on every fact, which is what makes its
evals possible.

### 6.3 Generated skills are versioned artifacts

A generated skill is versioned and stored like an external plugin's skill, not
like source. It is gitignored today (CLAUDE.md), and scenario tests pin a
snapshot beside their capture (004). The target: each generation writes a
content digest and the generator's own version into the bundle, and a
deployment records which bundle it serves. A regression can then be traced to
a generator change or a source change.

Where bundles are stored is open (§12). The options:

| Store | For | Against |
|---|---|---|
| The provider's skill store (Managed Agents skills API, today) | already used; versions built in | a delivery mechanism, not storage: provider-specific, and OpenAI has no equivalent described |
| An OCI registry (bundles pushed as artifacts) | content-addressed, signed, same registry as the images; natural on Kubernetes | one more artifact type to tool for; less natural on a laptop |
| An object store bucket, keyed by digest | simple, cheap | per-cloud; no history or metadata without a convention |
| A git repo per deployment | reviewable diffs of what the agent will be told; history; fits GitOps | generated content is large and noisy; a commit per regeneration |
| k8srca's own database | one place with the management UI (§9) | backups and migration become ours |

The leaning: store bundles content-addressed behind a small `BundleStore`
interface, with a local directory for development and an OCI registry for
Kubernetes. Uploading to a provider's skill store becomes a delivery step at
sync time, not the place a bundle lives. A deployment that wants to review
what changed can additionally export to a git repo.

### 6.4 Regeneration schedule

Generated skills go stale (001 §5.2). Each generator has a schedule per
deployment (on demand, or every N hours) set in configuration or the management
UI (§9). A regeneration that changes the bundle's digest is recorded with
what changed, so the UI can show "architecture skill updated 2h ago: 3
workloads changed".

### 6.5 Generators stay inside the cluster-access rule

The architecture generator reads the cluster only through k8stools (CLAUDE.md,
`test_no_direct_cluster_access.py`). That holds for every generator: a
generator that needs live data gets it through an MCP plugin, so it is bound by
the same read-only tool surface as the agent.

---

## 7. Security invariants, generalized

Today's rules (001 §8) are about one credential and one sandbox. With several
plugins and backends they become contracts every component must meet:

1. **One credential, one holder, per data source.** Each data source's
   credential is mounted into its own MCP server's container and nowhere else.
   Not the orchestrator, not another plugin's server, and not the sandbox
   unless a reviewed exception says so (§5.5).
2. **Plugins are read-only, and that is checked.** `read_only: true` in a
   manifest is a claim. The plugin's evals (§8.1) include a check that no
   declared tool mutates state, and k8s-core keeps the AST test that bans
   direct cluster access.
3. **Every workload backend passes the same confinement suite** for its
   sandboxes: no kubeconfig or cloud CLI in the image, no route to the host or
   the API server, egress limited to the allowlist (§7.2), and credentials
   removed from the environment. `k8srca status` runs it on every backend, as it
   does for Docker today.
4. **The executor's platform key can only connect environments.** True of
   both platforms as documented. A runtime adapter that needs a broader key in
   the sandbox does not meet the contract.

---

### 7.1 Secrets management

| Secret | Held by | Docker | Kubernetes |
|---|---|---|---|
| Provider API key (control plane) | orchestrator | `.env`, read by `settings.py` | Secret, mounted into the orchestrator only |
| Executor key (environment key) | each sandbox | passed by the poller as an env var | Secret, mounted into sandbox Pods |
| Chat tokens (Slack, Teams) | orchestrator | `.env` | Secret, orchestrator only |
| Target-cluster credential | k8stools only | kubeconfig file bind-mounted `:ro` (today) | Secret mounted into k8stools only. k8srca runs in another cluster, so this is a token for the target, not an in-cluster ServiceAccount |
| Plugin data-source credentials | that plugin's MCP service (§5.5) | mounted into its container | Secret mounted into its Deployment |
| Model API keys (local runtime) | the runtime control plane | `.env` | Secret |

Configuration never contains secret values, only references:

```yaml
secret: { name: prometheus-token, key: token }
```

The workload backend resolves a reference to a mount: a file under a secrets
directory on Docker, a Kubernetes Secret (which an external-secrets operator or
a cloud secret manager can fill) on Kubernetes. Two rules hold on both:

- **Each secret is mounted into exactly one kind of workload.** The backend's
  `check()` lists every workload's mounts and fails if a secret appears in two,
  or in a sandbox when it is neither the executor key nor a recorded exception
  (§5.5). This is invariant 1 made testable.
- **Secrets are write-only from outside.** The management UI (§9) can set or
  rotate a secret and never displays one.

What differs between the backends is storage and delivery, not the rules.
Docker has no secret store of its own, so a `.env` file and a secrets
directory with tight permissions are the floor. Kubernetes Secrets are only
base64 unless etcd encryption is enabled. A production deployment should use
etcd encryption or an external secret manager, and the Helm chart should
support both.

### 7.2 Egress allowlist

Today's sandbox egress is a **deny-list**: `docker/egress-rules.sh` blocks the
host, private ranges, cloud metadata and the API server, and allows the rest of
the public internet. Its header says so: "It is not an allowlist." The target
is an allowlist on both backends.

- **Granularity is host and port.** With HTTPS, a proxy sees the destination
  host (CONNECT, or the TLS SNI) but not the path. Filtering paths would mean
  intercepting TLS with our own CA, which breaks provider certificate checks
  and is fragile. Not done.
- **No route out except the proxy.** The sandbox network has no external route
  (`internal: true` on Docker; a default-deny egress NetworkPolicy on
  Kubernetes). Its only exit is an egress proxy that allows listed hosts. MCP
  services are reached directly on the internal network. This is enforced by
  the network, not by cooperation: agent-run code that ignores `HTTPS_PROXY`
  has no route.
- **The allowlist is composed, never hand-written:** the runtime adapter's
  endpoints (the provider API, or the executor's WebSocket endpoint), the
  endpoints of plugins placed in the sandbox (§5.5), and nothing else. A
  plugin's manifest declares its endpoints; the deployment's configuration fills
  in its URL.
- **The confinement suite tests both directions:** every declared endpoint is
  reachable through the proxy, and an arbitrary public host, the host, private
  ranges and metadata are not.

One thing to verify per runtime: that its executor honours `HTTPS_PROXY`. The
Managed Agents SDK worker's HTTP client does. OpenAI's `codex exec-server`, a
WebSocket client, is unknown until the prototype (§10 c).

What an allowlist does not stop: the agent writing what it has read into its
own messages, which reach session history and the chat. It bounds where data
can be sent, not what the agent can reveal (§5.5).

## 8. Evaluation

### 8.1 Component evals

Each generator and plugin ships evals that run without an agent:

- **cluster-architecture generator.** *Landed 2026-10-03 (step a.2) as
  `k8srca eval arch`; how to run it is in `docs/generators.md` §5.* Built
  against a captured cluster whose ground truth is known. Scores:
  - **completeness:** every workload, service, dependency edge and resource
    setting in the capture appears;
  - **accuracy:** every observed fact matches the capture, every declared fact
    matches the charts;
  - **no invention:** no fact without a source;
  - **drift detection:** declared-vs-observed conflicts are reported.

  The false operator note found on 2026-09-30 (a claim that `ad` never reaches
  readiness, repeated by five of six scenario runs) is the case it must catch:
  a *documented* fact that the *observed* state contradicts. *Landed
  2026-10-03 as `k8srca eval arch --judge`:* free coverage (which workloads
  have a page) plus a paid judge (one model call per case) that reports claims
  a recorded fact directly contradicts. Calibrated on planted claims, it catches
  those; the readiness note itself is false only through Kubernetes semantics,
  not any recorded fact, and the judge is deliberately held to facts
  (docs/generators.md §5).

  **Two installs, not one.** Scored only against our own OTel-demo capture, the
  generator could be fitted to our install without anyone noticing. The
  ITBench-Lite import (004 §12.1) supplies a second: a third party's install of
  the same application, with several pulls before the fault. Building the skill
  from a pre-fault pull with only `live_cluster` and `change_history` is the
  generator's job in production (the skill predates the incident), and the two
  installs already disagree where they should: ITBench's `ad` limit is 450Mi,
  our notes say 300Mi. The eval runs against both.
- **runbook generator.** Scored on faithfulness (every step in the output
  traces to the runbook), coverage (every runbook step appears), and structure
  (a debugging guide, not a copy).
- **Plugins.** A tool contract test against a recorded fixture (each tool
  answers with its declared schema), the read-only check from §7, and, for
  skills, a check that every referenced file exists.

### 8.2 Contract suites

Each contract ships an in-memory fake and a test suite every implementation
must pass:

| Contract | Suite covers |
|---|---|
| Runtime | turn boundaries, reconnect and replay, dedup, concurrent conversations, billing errors |
| Chat | rendering a turn, conversation identity, trigger reporting |
| Workload | provision/reap, MCP service deploy/health, confinement and secret placement (§7, §7.1) |
| Orchestrator | locking, dedup, triggers, against fake chat and fake runtime |

The tests CLAUDE.md asks to keep failing-if-reverted (the concurrency tests,
`test_exactly_one_racing_caller_wins`) move into the runtime and orchestrator
suites. They are not Slack tests.

### 8.3 System scenarios across runtimes

004's scenarios run through the runtime contract, not through Managed Agents
directly. The same capture, skill snapshot and answer key can then compare
runtimes and models. A baseline already pins capture, skill, answer key and
grader. It must also pin **runtime and model**, or a comparison across
runtimes reads as an agent regression.

### 8.4 Imported scenarios and external benchmarks

004 §12 reviewed four public benchmarks (2026-09-30) and decided how each is
used: ITBench-Lite scenarios are **imported**, SREGym is a **periodic live
exam** once a metrics source exists, Cloud-OpsBench is a coverage checklist,
and NOFire is not used. In the modular design those decisions land as follows.

- **We import captures, never scoring.** An imported scenario is a capture
  plus a `truth.yaml` re-derived from it, graded by the same rubric as our own.
  ITBench's ground truth for the first import was wrong twice (it omitted
  `cart`, which has the same fault, and described an outage the capture shows
  did not happen), so a source's answer key is an input to the review, never
  a substitute for it.
- **The converter is an external component, in k8stools.** It turns
  ITBench-Lite's raw API objects into a k8stools capture by running
  k8stools' own `capture_state` against them. It imports the `kubernetes`
  client, so it cannot live in k8srca (CLAUDE.md); it belongs beside
  `k8s-capture-state`. That is goal 3 of §1 in practice: a component k8srca
  depends on, developed and released elsewhere.
- **Comparisons are ablations, not leaderboards.** Public leaderboards run bare
  models in a fixed harness; k8srca is a system whose value is the tools and
  skills around the model, so the numbers are not comparable and we do not
  report them. The measurements that mean something are ablations on the same
  scenarios: skills on vs off, k8stools pulling from a capture vs the same data
  dumped into the workspace (the direct test of pull-not-push), and, with this
  design, **one runtime against another** (§8.3).
- **A live exam is a deployment, not a fixture.** SREGym runs problems on a
  live cluster, which is what the scenario suite deliberately avoids (004 §9).
  In this design it is an ordinary deployment pointed at the exam cluster:
  k8stools gets that cluster's read-only credential and nothing else does, the
  architecture generator runs **before** the fault is injected, and the
  conversation arrives through the runtime contract like any other. Diagnosis
  only: k8srca does not mitigate, so the mitigation phase is skipped. SREGym
  ships its own MCP servers, including a `kubectl` one; that one is not used,
  because cluster access is k8stools, read-only, by rule. Its Prometheus, Loki
  and Jaeger servers are a ready test bed for observability plugins (§5).
- **Several imports wait on the first observability plugin.** ITBench-Lite's
  feature-flag scenarios and SREGym's harder problems are visible mostly in
  metrics and traces. 004 schedules that as S4 (a Prometheus source). In this
  design it is the first capability plugin after `k8s-core`, and the natural
  one to build in its own repository against the plugin contract (§5.2), to
  prove that contract on a real case. It is not in §10's order; it is tracked
  in 004's plan.

---

## 9. Management UI

A web UI for operating a deployment. It talks to a **management API** served by
the orchestrator process, and that API is the only thing the UI uses, so
everything it can do can also be scripted or done from the CLI.

| Area | What it does |
|---|---|
| Configuration | edit trigger rules (§3.3), generator schedules (§6.4), plugin settings |
| Skills and plugins | add a plugin by git repo/path/ref (§5.3), enable or disable it, regenerate a generated skill and see what changed |
| Statistics | active conversations, conversations over a chosen window, errors, cost, turn latency, by adapter and by trigger |
| Drill-down | a conversation's turns and tool calls, with a link to the provider's console for the session (`session.console_url` already builds Managed Agents links) |
| Health | `k8srca status`, rendered: adapters, runtime, workloads, MCP services, confinement checks |
| Secrets | set and rotate (§7.1); values never shown |

What it needs underneath:

- **A store.** Today conversation state is SQLite in `slack/sessions.py`, and
  statistics do not exist. The orchestrator records each turn (start, end,
  outcome, cost, tool count, trigger) in a store, SQLite for one-node Docker and
  PostgreSQL on Kubernetes. The same records feed a Prometheus `/metrics`
  endpoint, so dashboards and alerts on k8srca itself do not need the UI.
- **Authentication and roles.** OIDC sign-in. Changing triggers or adding
  plugins changes what runs and what it costs, so viewing and editing are
  separate roles.
- **A decision about where configuration lives** (§12). Today it is
  `k8srca.yaml`. A UI that edits configuration either writes that file (or
  opens a pull request against it), or makes the store authoritative. The
  leaning: infrastructure choices (which adapters, runtime, backend) stay in the
  file; operational settings (triggers, schedules, enabled plugins) live in the
  store, are audited, and can be exported.

## 10. Migration, in order

Each step leaves the system working and has an exit test.

### (a) Generators out, with their own evals

1. Move `arch/` and `kb/` behind the generator contract (§6.2), each writing a
   versioned bundle and a report.
2. Build the cluster-architecture component eval (§8.1) against two
   installs: the `jvm-oom-on-startup` capture and the
   `itbench-33-invalid-node-selector` pre-fault pull, each with a ground-truth
   file like 004's `truth.yaml`.
3. Make `k8s-core` the first plugin (§5): the `k8s-rca` skill plus k8stools.

**Exit:** `k8srca arch build` output is scored by its own eval without an
agent, and the scenario suite still passes on the bundle it produces.

### (b) Kubernetes deployment

k8srca runs in a different cluster from the one it investigates, as
[003 §4.1](003-operations.md) already recommends (a separate ops cluster, because
k8srca hosted in the cluster it diagnoses is least available exactly when it is
needed). For development, that is a local minikube hosting k8srca,
investigating the existing target cluster. 003 §4 is the starting design for
this step: its component mapping (§4.2), externalizing the workspace first
(§4.4) and sandbox hardening (§4.5) carry over, rebased onto the workload
backend contract.

1. Extract the workload backend contract (§4.4) around today's Docker code.
   Replace the egress deny-list with the allowlist proxy (§7.2) on Docker
   first, where the confinement suite already runs, so the Kubernetes backend
   implements a contract that is already tested.
2. Write the Kubernetes backend: Pod per work item, NetworkPolicy, a namespace
   of its own.
3. Package the orchestrator, poller and k8stools as Deployments (a Helm chart).
   k8stools gets the target cluster's read-only credential as a Secret (§7.1);
   the read-only ClusterRole (001 §8.4) lives in the *target* cluster.

**Exit:** the confinement suite (§7) passes on both backends, and a Slack
mention is answered from an in-cluster deployment.

### (c) OpenAI

1. Verify §4.2's OpenAI column by prototype: an executor in a sandbox, one
   session, one tool call through k8stools.
2. Extract the turn-event model (§3.1) and the runtime contract around today's
   Managed Agents code: `session.py`, `sync.py`, the poller. Chat adapters and
   the scenario runner consume `TurnEvent`s.
3. Write the OpenAI runtime adapter.

**Exit:** `jvm-oom-on-startup` runs n=6 on both runtimes, against one capture
and answer key, with runtime and model pinned in each baseline.

Step (c) is where the runtime contract is most likely to change shape, which is
why its first task is a prototype, not the extraction.

### (d) Teams

1. Extract the chat contract (§3.2) and the orchestrator (§3) out of
   `slack/`, with triggers (§3.3).
2. Write the Teams adapter.

**Exit:** the orchestrator suite passes against fakes, and both adapters pass
the chat suite.

Triggers (§3.3) can land with (d) or earlier, on Slack alone. They need the
orchestrator extracted, not a second adapter.

### (e) Management UI

After the orchestrator owns conversation state (step d), since statistics come
from its turn records. The management API can start earlier, with
`k8srca status` and statistics, and the UI follows it.

---

## 11. Repository layout (target)

```
k8srca/
├── src/k8srca/
│   ├── core/                 # contracts, TurnEvent, config composition, fakes
│   ├── orchestrator/         # sessions, locks, dedup, triggers
│   ├── chat/{slack,teams,a2a}/
│   ├── runtime/{managed_agents,openai}/
│   ├── workloads/{docker,kubernetes}/   # sandboxes and MCP services
│   ├── management/           # management API, turn store, metrics
│   ├── ui/                   # web UI, built against the management API
│   ├── generators/{architecture,runbooks}/
│   ├── plugins/              # manifest loading, git fetch, lockfile
│   └── evals/{components,scenarios}/
├── plugins/k8s-core/         # the in-repo plugin: k8srca-plugin.yaml, skills/
├── deploy/{docker,helm}/
└── tests/contracts/          # suites every implementation runs
```

Boundaries inside the package are enforced the way the cluster-access rule is:
an AST test that fails if `chat/` imports `runtime/` internals, or if anything
but `runtime/managed_agents/` imports `anthropic`'s agent APIs. A boundary that
is only a directory name will not survive several developers.

---

## 12. Open questions

Resolved since the first draft (2026-10-03): OpenAI supports sub-agents (§4.2);
k8srca runs in a separate cluster (§10 b); a plugin's tools run in the sandbox
only when no secret is exposed there (§5.5).

1. **Where trigger rules come from** (§3.3): the deployment's configuration, or
   the plugin whose alerts they are. Leaning toward the deployment's
   configuration; to revisit.
2. **Where generated skills are stored** (§6.3). Leaning toward a
   content-addressed store, local directory plus OCI registry.
3. **Where configuration lives once the UI edits it** (§9): the file, the
   store, or a split. Leaning toward the split.
4. **The rest of the OpenAI column** of §4.2: how the self-hosted executor is
   told about MCP servers, whether vaults substitute at egress, budgets, and
   whether the executor works through an egress proxy (§7.2).
   Answered by the prototype in §10 (c).
5. **Third-party MCP service images** (§5.5) run with a credential inside our
   deployment. What does a plugin have to provide to be trusted with one: a
   signed image, a pinned digest, a read-only test against a fixture (§8.1)?
6. **Provider-side secret substitution for self-hosted sandboxes.** If a
   provider adds it, plugins that need a secret could move into the sandbox.
   Worth asking Anthropic and OpenAI whether it is planned.

---

## Appendix A. Decisions taken

From the discussion that started this document (2026-10-01 to 10-03):

| Question | Decision |
|---|---|
| What counts as "the OpenAI equivalent" | OpenAI's Agents API with a self-hosted environment (§4.2) |
| Several chat frontends at once? | Yes: two of the same kind are unlikely, but Slack plus A2A is a real case |
| Channel monitoring | The chat slot must support watching a channel and responding to matching alerts automatically (§3.3) |
| Where components live | Core components in this repo, one release. External components (k8stools, third-party plugins, a Discord adapter) in their own repos, versioned independently |
| How external plugins are referenced | A git repo, a path inside it, and a ref (§5.3) |
| Plugin presence | Declared per deployment. Automatic configuration later, perhaps |
| Generated skills | Versioned independently of k8srca, like external plugins (§6.3) |
| Order | Meta-skills and their evals, then Kubernetes deployment, then OpenAI, then Teams (§10) |
| Where k8srca runs | In a cluster other than the one it investigates. Tested with k8srca in a local minikube investigating the existing target cluster |
| Where a plugin's tools run | In the sandbox when no secret is exposed there; otherwise as a credential-holding service, like k8stools (§5.5) |
| Secrets in the sandbox | Only by a reviewed exception, decided case by case (for example a query-only Prometheus token) (§5.5) |
| Sandbox egress | An allowlist of hosts through an egress proxy, replacing today's deny-list (§7.2) |
| External benchmarks | Import ITBench-Lite captures with re-derived truth; SREGym as a periodic live exam after the first metrics plugin; Cloud-OpsBench as a checklist; NOFire not used. Decided in 004 §12 (c73e49c, 2026-10-01) (§8.4) |
| A management UI | In scope: configuration, skills and plugins, statistics, links to the provider console (§9) |
