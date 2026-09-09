# k8srca — Kubernetes Root Cause Analysis Agent

**Design document 001 — Architecture**
Status: Draft for review · Date: 2026-09-08 · Rev 7
Companions: [002 — Investigation model](002-investigation-model.md) · [003 — Operations](003-operations.md)

---

## 1. Purpose and scope

k8srca lets an SRE ask, in Slack, *"why is `payment-api` crash-looping in `prod`?"* and get back a
ranked, evidence-cited root-cause analysis — then keep asking follow-up questions in the same thread.

The agent is **strictly read-only with respect to the cluster**. It observes, correlates, reasons, and
*recommends*. It never applies a change. Section 8 describes the four independent layers that enforce
this rather than relying on prompt instructions alone.

### v1 scope

| In | Out (deferred) |
| --- | --- |
| Slack chat: assistant pane + `@k8srca` in channels | Automatic response to Alertmanager alerts (v2) |
| Read-only Kubernetes state via `k8stools` MCP | Cross-session memory / knowledge synthesis (v2) |
| Coordinator + specialist agents, per-agent model | Git access to Helm charts (v3 — extension point defined) |
| RCA knowledge base (81 rules) as an agent skill | Prometheus / Loki / Tempo MCP (v3 — extension point defined) |
| Deployment-architecture docs + runbooks as a skill | Multi-cluster |
| Declarative config for agents, environment, skills | |

The optional integrations were explicitly deferred, but §11 fixes the seams so adding them is
additive rather than a rewrite.

---

## 2. Key architectural findings

Four facts about Claude Managed Agents (CMA) drive the whole design. The first three contradict the
starting assumptions in `background/Claude-Self-Hosted-Sandboxes-Explained.md`, which is a
Gemini-authored summary and is wrong on specifics.

**F1 — MCP servers declared on an agent are dialed from *Anthropic's* side, not from your sandbox.**
A `url` entry in the agent's `mcp_servers` array must be reachable from Anthropic's network. Pointing
it at `http://localhost:8000/mcp` silently fails. The documented remedy for a private server is to
**make the worker the MCP client** and declare the server's tools as `custom` tools on the agent
(§4.2). Anthropic's [MCP tunnels](https://platform.claude.com/docs/en/agents-and-tools/mcp-tunnels/overview)
are the alternative, but they are a research preview requiring `cloudflared`, an Anthropic proxy, and a
registered CA cert — disproportionate for a local cluster, and rejected for v1.

**F2 — Self-hosted environments accept *only* `memory_store` resources.**
`file` and `github_repository` resources are rejected with a 400: *"Environment env_… is a self-hosted
environment. `resources` are not supported with self-hosted environments."* So the RCA knowledge base
and architecture docs **cannot** be delivered as mounted files, and the Helm repo cannot be delivered
as a `github_repository` mount. They must arrive as **Skills** (which the worker does download, into
`{workdir}/skills/<name>/`) or be baked into the sandbox image. We use Skills — §5.

**F3 — Tool inputs and outputs still flow to Anthropic's control plane.**
Self-hosting moves *tool execution* into your infrastructure; it does not keep tool results local. The
model has to see results to reason about them. Pod logs, ConfigMap contents, and event streams pulled
by the agent are transmitted to and processed by Anthropic. This is fine and expected, but it must be
stated plainly (§8.4) because "self-hosted sandbox" invites the opposite assumption.

**F4 — Multiagent threads share one container, but not one context.**
A coordinator agent can delegate to rostered agents inside a single session. Each runs in its own
**thread** with its own conversation history, **model**, system prompt, tools, MCP servers, and
skills — but all threads share the container and filesystem. That is what makes the coordinator /
specialist split in §3.3 possible without a second sandbox: one session, one SSE stream, one worker,
N models. Each thread is billed at its own model's rates.

The corollary that shapes the design: **subagents see none of the coordinator's conversation.** Every
delegated task must carry its own paths, scope, and required report format. Delegation is a
round-trip plus a re-briefing, which is why §3.3 splits by data volume rather than by data source.

---

## 3. System architecture

```
  ┌─────────────────────────── Anthropic control plane ────────────────────────────┐
  │  Agent configs (versioned)  Session orchestration   Work queue per environment │
  │  Skills store               Event log / SSE                                    │
  │                                                                                │
  │   thread: coordinator ──delegates──▶ thread: k8s-investigator                  │
  │   (rca-coordinator, Sonnet 5)        (k8s-investigator, Haiku 4.5)             │
  └───────▲───────────────────────────▲────────────────────────────┬───────────────┘
          │ REST + SSE                │ outbound long-poll         │ tool call
          │ (API key)                 │ (environment key)          │
══════════╪═══════════════════════════╪════════════════════════════╪═══════════════
          │        Your machine       │                            │
  ┌───────┴────────────┐    ┌─────────┴──────────┐                 │
  │ Orchestrator       │    │ Host poller        │                 │
  │ (Slack Bolt,       │    │ ant beta:worker    │                 │
  │  Socket Mode)      │    │   poll --on-work   │                 │
  │                    │    └─────────┬──────────┘                 │
  │ thread_ts ⇄ session│              │ docker run --rm            │
  │ SQLite map         │              ▼                            ▼
  └───────▲────────────┘    ┌───────────────────────────────────────────────┐
          │                 │ Session sandbox (one per TURN; /workspace is  │
          │                 │ bind-mounted per SESSION — §3.2)              │
          │                 │ EnvironmentWorker.handle_item()               │
          │                 │ serves BOTH threads — shared filesystem       │
          │                 │  ├── built-in toolset: bash/read/grep/glob    │
          │                 │  ├── wrapped MCP tools: union of all      ──┐ │
          │                 │  │   servers (k8s_*, later prom_*)          │ │
          │                 │  └── /workspace/skills/{k8s-rca,            │ │
          │                 │       cluster-architecture}                 │ │
          │                 └────────────────────────────────────────────┼─┘
          │                                     docker net: k8srca-net   │
  ┌───────┴─────────┐                       ┌────────────────────────────▼─┐
  │  Slack          │                       │ k8stools MCP server          │
  │  (workspace)    │                       │ streamable-http :8000        │
  └─────────────────┘                       │ KUBECONFIG (read-only SA)    │
                                            └──────────────┬───────────────┘
                                                           │ HTTPS, read-only RBAC
                                                           ▼
                                                    Kubernetes cluster
```

### 3.1 Processes

| Process | Runs as | Responsibility |
| --- | --- | --- |
| **Orchestrator** | host, long-lived | Slack Bolt app in Socket Mode. Owns the Slack↔session mapping, creates sessions, streams events, renders to Slack. Holds the **API key**. |
| **Host poller** | host, long-lived | `ant beta:worker poll --on-work spawn.sh`. Claims work items (one per turn), spawns a container per work item with the session's host workspace bind-mounted. Holds the **environment key**. |
| **Session sandbox** | container, per turn | `EnvironmentWorker.handle_item()` with built-in tools + the union of all wrapped MCP tools. Serves **every thread** in the session (§3.3). Exits when the session run completes. |
| **k8stools MCP** | container, long-lived | `k8s-mcp-server --transport=streamable-http`. The **only** process holding a kubeconfig. |

Splitting the orchestrator from the poller matters: the orchestrator's org-scoped API key must never
be present on a host where agent-authored `bash` can read it. The self-hosted-sandbox docs call this
out explicitly for control-plane calls.

### 3.2 Ephemeral containers, session-scoped workspace

**A work item covers one *contiguous active period* of a session — neither one turn nor the whole
session.** *(Measured, Rev 5. Earlier revisions of this document got this wrong in both directions.)*

The worker claims a work item and holds the container open across turns while the session stays
active, plus `max_idle` (default 60 s) after it goes idle. Once that elapses the container exits;
the next message enqueues a **new** work item, and the poller spawns a **new container for the same
session**.

Measured on a three-turn session: turns 1 and 2 were served by one container (one `spawn` event);
after a 60 s pause it exited with code 0, and turn 3 produced a second `spawn` — same session id,
same workspace directory, same pinned image.

Two consequences:

- **The container is not a reliable per-turn or per-session unit.** An interactive Slack thread with
  natural pauses will cross the idle boundary repeatedly, so a long investigation spans several
  containers.
- **The session-scoped workspace mount is therefore load-bearing, not an optimisation.** Anything
  written to `/workspace` must survive a container that has already exited.

Container-per-turn is what we want for isolation, but a purely ephemeral filesystem would be wrong:
skills would re-download every turn, and nothing the agent writes — including the investigation
record of [Design 002](002-investigation-model.md) — would survive to the next question. The
documented remedy is a **session-keyed host bind-mount**:

```
-v "${K8SRCA_WORKSPACES}/${ANTHROPIC_SESSION_ID}:/workspace"
```

So: the *container* is per turn; the *workspace* is per session. Every turn of one Slack thread sees
the same `/workspace`; different threads never share one.

| Property | Comes from |
| --- | --- |
| No drift between different investigations | Per-session host directory, never shared |
| Hard resource caps per turn | `--memory`, `--cpus`, `--pids-limit` on each `docker run` |
| Skills downloaded once, not per turn | Session-scoped `/workspace/skills/` survives |
| Investigation state across turns | Session-scoped `/workspace/investigation/` (Design 002 §7) |
| v2 memory stores work | The worker refuses a work item if a store's `/mnt/memory/<name>/` path already exists, so no two sessions may mount one store on a host concurrently. Container-per-turn satisfies this. |

Cost: container start and a fresh k8stools MCP handshake once per *active period* — paid on the first
turn after any pause longer than `max_idle`, and not at all on a rapid follow-up. Cheaper than the
per-turn cost earlier revisions assumed, and it means `max_idle` is a real tuning knob: raising it
trades idle container residency for fewer cold starts in a conversation with think-time between
questions.

Two operational consequences: host workspace directories need reaping when a session ends or its TTL
expires (§7.4), and the `${K8SRCA_WORKSPACES}` root is a persistent, agent-writable surface that must
be on a volume with a quota.

### 3.3 Agent topology: coordinator + specialists

v1 runs **two agents in one session** (F4): a coordinator that reasons, and a specialist that reads.

| | `rca-coordinator` | `k8s-investigator` |
| --- | --- | --- |
| Default model | `claude-sonnet-5` | `claude-haiku-4-5` |
| Skills | `k8s-rca`, `cluster-architecture` | none (task brief carries what it needs) |
| Cluster tools | **Triage subset** (~6): pod summaries, container statuses, pod events, deployment summaries, node summaries, cluster events | **Full k8stools surface** (~20), including all log retrieval |
| Built-in tools | `read`, `grep`, `glob`, `bash` | `read`, `grep`, `glob`, `bash` |
| Job | Hypothesis generation and ranking, KB and causal-chain reasoning, architecture correlation, the final RCA, all Slack-facing output | Bulk evidence gathering: log retrieval and scanning, cross-namespace sweeps, per-pod detail |
| Roster | `[k8s-investigator, {"type": "self"}]` | — (one level of delegation only) |

**The split is by data volume, not by data source.** This is a deliberate departure from the more
obvious "coordinator has no tools; all data access is delegated" design, for a reason specific to
RCA: diagnosis is a tight iterative loop — *check pod events → now the node's conditions → now the
previous container's logs*. Because subagents carry no shared context (F4), every hop through the
specialist costs a round-trip plus a full re-briefing. A coordinator that cannot cheaply answer "how
many restarts?" for itself reasons well and moves slowly.

So the coordinator keeps the cheap, low-volume, high-signal lookups. What goes to the specialist is
what would otherwise blow out the coordinator's context: **logs, primarily**, plus anything that
sweeps many objects. That is precisely where the multiagent docs say delegation pays — *"reading
large amounts of material without filling the coordinator's context."*

**Why this converges on a pure split later.** With one data source, the specialist earns its place
only on volume. Once metrics, logs, traces, and git are in play (§11), a single agent carrying 50+
tools degrades, and the roster becomes one specialist per source — at which point the coordinator's
triage subset shrinks toward zero on its own. v1 is the concession to there being one source today,
not a rejection of the destination.

**Two constraints this imposes:**

- **`claude-haiku-4-5` has a 200 K context window, not 1 M.** It is the model most suited to log
  scanning and the one most threatened by it. The platform's >100 K-character auto-offload (§9) helps:
  the specialist receives a preview plus a file path rather than the content. Its system prompt must
  mandate **grep-then-read**, never read-then-scan. If that proves insufficient, the per-agent model
  setting (§6) moves it to `claude-sonnet-5` with no other change.
- **One level of delegation, enforced.** A rostered agent must not itself carry a `multiagent` block;
  the create/update fails validation rather than silently flattening. Specialists cannot fan out.

---

## 4. Kubernetes access

### 4.1 Deployment of k8stools

`k8stools` (BenedatLLC) exposes ~20 read-only tools — pod summaries, container statuses, pod/job/cronjob
logs, events, node summaries, deployments, statefulsets, services, configmaps, PVCs — and supports
`--transport=streamable-http`. Secret redaction (AWS keys, JWTs, PEM blocks, `key`/`secret`/`token`/
`password`/`credential` keywords) is on by default via `K8STOOLS_REDACT`; we never pass `--no-redact`.

It runs in **its own container** on a dedicated user-defined bridge network `k8srca-net`, with the
kubeconfig bind-mounted read-only into that container alone. Session sandboxes join `k8srca-net` and
reach it at `http://k8stools:8000/mcp`.

Running it in a container rather than on the host is a deliberate hardening choice: it means the
session sandbox has **no route to the host's network namespace**, and the kubeconfig never exists on
any filesystem the agent can read.

### 4.2 Worker-as-MCP-client

Per F1, the agent declares k8stools' tools as `custom` tools; the worker inside the sandbox executes
them. The flow, per Anthropic's docs:

1. The model calls a wrapped tool → session emits `agent.custom_tool_use`.
2. The worker, inside the sandbox, forwards the call over its open MCP session to k8stools.
3. The worker posts the response back as `user.custom_tool_result`.

The orchestrator is **not involved** — the worker handles it autonomously. This is the critical
difference from the ordinary custom-tool pattern (where the session idles and the client responds),
and it keeps the orchestrator thin.

**Declaration is per agent; execution is per session.** This is the asymmetry that the coordinator /
specialist split (§3.3) introduces, and it is the least obvious part of the design:

- Each **agent** declares only the tools its model should see. `k8s-investigator` declares all ~20
  k8stools tools; `rca-coordinator` declares the ~6-tool triage subset. The declaration is what gates
  *visibility*.
- The **worker** serves every thread in the session (F4), so it must register the **union** of every
  wrapped tool across every agent — and, once §11 lands, across every MCP server. The registration is
  what provides *execution*.

**Agent side** (`sync` time, §6): enumerate `list_tools()` per configured MCP server and emit one
`custom` declaration per tool, filtered by each agent's configured tool set.

```python
def to_custom_tool(tool: types.Tool, name: str) -> BetaManagedAgentsCustomToolParams:
    return {
        "type": "custom",
        "name": name,                       # namespaced — see below
        "description": tool.description or tool.name,
        "input_schema": cast(Any, tool.inputSchema),
    }
```

**Worker side** (sandbox entrypoint) — connects to every configured server and registers the union:

```python
async with AsyncAnthropic(auth_token=os.environ["ANTHROPIC_ENVIRONMENT_KEY"]) as client:
    async with AsyncExitStack() as stack:
        mcp_tools = []
        for server in load_config().mcp:              # k8stools in v1; + prom/loki later
            streams = await stack.enter_async_context(streamable_http_client(server.url))
            read, write = streams[0], streams[1]
            mcp = await stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=float(server.timeout_s)))
            await mcp.initialize()
            listed = await mcp.list_tools()
            mcp_tools += [wrap_mcp_tool(t, mcp, prefix=server.prefix) for t in listed.tools]

        worker = EnvironmentWorker(
            client,
            workdir="/workspace",
            tools=lambda env: [*beta_agent_toolset_20260401(env), *mcp_tools],
        )
        await worker.handle_item()        # IDs read from forwarded ANTHROPIC_* env vars
```

> **Verified against `mcp` 2.2.0 and `anthropic` 1.4.0.** Three shapes differ from
> Anthropic's published example, each failing at runtime rather than at import:
> `streamable_http_client` yields **two** values (not three); the tool field is
> `input_schema` (not `inputSchema` — the Anthropic SDK carries its own v1/v2 shim,
> mirrored in `k8srca.tools.mcp_input_schema`); and `read_timeout_seconds` takes a
> **float** (not a `timedelta`). `EnvironmentWorker` also spells the option
> `memory_sync_deletions`, not `memory_sync_deletes`.

Signals must be wired to *cancellation*, not a kill, so the worker completes teardown (§7.3).

**Namespacing is load-bearing, not cosmetic.** The worker registers into one flat tool namespace. Two
MCP servers both exposing `get_events` — plausible for k8stools and a future Loki server — cannot both
register under that name. Hence the `prefix` field in §6. The prefixed name must be identical on both
sides, so `sync` and the worker apply the *same* renaming function.

**`async_mcp_tool` cannot do this — a wrapper is required.** *(Resolved in implementation.)* The SDK
helper closes over `tool.name` for **both** the declared name and the outbound
`client.call_tool(name=...)`, so renaming the `Tool` before passing it in would break the remote call
silently — the model would see `k8s_get_events` and the server would be asked for `k8s_get_events`,
which it does not have. `k8srca.tools.wrap_mcp_tool()` rebuilds the tool with the two names decoupled,
reusing the SDK's own `_convert_tool_result` so content handling stays identical. A live test asserts
the round trip: exposed `k8s_get_namespaces` → remote `get_namespaces`.

**Schema normalization.** 12 of k8stools' 18 tools encode `Optional[str]` as
`{"anyOf": [{"type": "string"}, {"type": "null"}]}` *nested inside a property*. Only **top-level**
`oneOf`/`anyOf` is forbidden, so these are legal as they stand — but two thirds of the tool surface
resting on that reading is an avoidable risk, so `normalize_schema()` collapses them to the plain type
(optionality is already carried by `required`) and drops the now-contradictory `default: null`.
Generated `title` keys are stripped as pure token cost.

**Schema constraint.** Custom-tool schemas must not use `$ref` or top-level `oneOf`/`anyOf`. `sync`
validates every generated schema against this and fails loudly rather than at session runtime.

### 4.3 Declaration drift

An agent's tool declarations and the worker's live `list_tools()` are two copies of the same truth.
If k8stools is upgraded and a tool is renamed, the model calls a tool the worker cannot resolve.

Mitigation: `sync` records a **tool-manifest hash** (sorted namespaced names + schema digest) in
**each agent's** `metadata`, covering only the tools that agent declares. The sandbox entrypoint
recomputes the manifest from its live connections and, on mismatch with any agent participating in
the session, fails the work item with a clear log line rather than serving a half-broken toolset.

Per-agent rather than one global hash, for two reasons: it localizes the error message to the agent
that is actually stale, and it means adding a metrics specialist (§11) does not invalidate the
Kubernetes agent's manifest. Upgrading a data source is then a `sync` step enforced by a check rather
than by memory.

---

## 5. Knowledge delivery

Per F2, everything the agent should *know* arrives as **Skills**, downloaded by the worker into
`/workspace/skills/<name>/`. Two skills in v1 (limit is 20 per agent).

### 5.1 `k8s-rca` — methodology + knowledge base

> The skill's *contents* — SKILL.md structure, playbook schema, and the investigation scripts
> that sit alongside `kb_query.py` — are specified in [Design 002](002-investigation-model.md)
> §§4–8. This section covers only how the bundle is built and delivered.

```
skills/k8s-rca/
  SKILL.md              # RCA methodology, escalation hierarchy, output contract
  knowledge_base.json   # normalized from kubernetes_rca_knowledge_base_v2.json
  kb_query.py           # CLI lookup — the agent shells out rather than reading 85 KB
```

The source KB is 81 flat records keyed by `alert_name`, with `;`-separated multi-values across
`root_causes`, `promql_signals`, `correlated_alerts`, `causal_parent`, etc. Loading it wholesale costs
roughly 25 K tokens of context on every session for information that is 95 % irrelevant to any one
incident. `kb_query.py` is the fix:

```
kb_query.py lookup CrashLoopBackOff          # full record
kb_query.py search --symptom "pod restart"   # fuzzy match to candidate alerts
kb_query.py chain OOMKilled                  # walk causal_parent upward
kb_query.py promql PodPending                # just the evidence queries
```

A build step (`k8srca kb build`) normalizes the source JSON: splits the `;`-separated fields into
arrays, inverts `causal_parent` into an explicit child index so `chain` is a graph walk rather than a
scan, and validates that every `causal_parent` and `correlated_alerts` entry resolves to a known rule.
The background analysis in `k8s-rca.md` is emphatic that RCA should follow **causal chains**, not
single-alert lookups; the inverted index is what makes that cheap.

`SKILL.md` encodes the diagnostic loop from that analysis:

> **alert/symptom → affected resource → evidence (events, logs, state) → ranked hypotheses →
> confidence → recommended remediation**

with the escalation hierarchy `Application → Pod → Node → Cluster → Control Plane → External`, and the
standing instruction to treat an alert as a *starting hypothesis*, never as the root cause.

### 5.2 `cluster-architecture` — what is actually deployed

Generated, not hand-written, so it cannot drift silently:

```
skills/cluster-architecture/
  SKILL.md              # how to navigate the below
  services/<name>.md    # per service: image, replicas, probes, limits, deps, owning team
  topology.md           # service dependency graph, namespaces, ingress
  runbooks/<name>.md    # verbatim existing runbooks
```

`k8srca arch build` renders Helm charts (`helm template`) and extracts the structured facts an RCA
needs — resource requests/limits, probe configuration and thresholds, replica counts,
PDBs, HPA targets, `ConfigMap`/`Secret` references, and declared dependencies. Runbooks are copied
verbatim. This is a local build step over a checkout; it does **not** require the agent to have git
access (which is deferred), and it is the input that later makes git access an optimization rather
than a prerequisite.

> **Trust boundary.** Skills are agent instructions. Anything that lands in `skills/` is executed with
> the agent's full authority. Generated architecture docs come from your own charts; runbooks come
> from your own repo. Do not template arbitrary third-party content into either.

---

## 6. Configuration and provisioning

The requirement was *"some way to configure the agent workspaces with the MCP server(s), model,
documentation, etc."* — a single declarative file, version-controlled, applied by one command. With
the coordinator / specialist split, the file's central job is **per-agent** model and tool routing, so
cost and context window can be traded off per role without touching code.

`k8srca.yaml`:

```yaml
mcp:
  - name: k8stools
    url: http://k8stools:8000/mcp
    wrap: worker_custom_tools            # per F1 — worker is the MCP client
    prefix: k8s_                         # namespaces the worker's flat tool registry
    timeout_s: 60
    # Named tool groups, referenced by agents below. A group is a routing label,
    # not a capability grant — the worker registers the union regardless (§4.2).
    groups:
      triage:
        - get_pod_summaries
        - get_pod_container_statuses
        - get_pod_events
        - get_deployment_summaries
        - get_node_summaries
        - get_events
      full: "*"

agents:
  # ---- coordinator: reasons, ranks, writes the RCA -------------------------
  rca-coordinator:
    role: coordinator
    model:
      id: claude-sonnet-5
      effort: high
    system_prompt: agents/rca-coordinator.system.md
    skills: [skills/k8s-rca, skills/cluster-architecture]
    builtin_tools: [read, grep, glob, bash]
    mcp_tools:
      k8stools: triage
    roster: [k8s-investigator, self]

  # ---- specialist: reads a lot, reports a little ---------------------------
  k8s-investigator:
    role: specialist
    model:
      id: claude-haiku-4-5              # → claude-sonnet-5 if 200K context binds
    system_prompt: agents/k8s-investigator.system.md
    skills: []
    builtin_tools: [read, grep, glob, bash]
    mcp_tools:
      k8stools: full

environment:
  type: self_hosted

sandbox:
  image: k8srca/sandbox:0.1.0
  network: k8srca-net
  memory: 2g
  cpus: "2"

session:
  budget_usd: 5.00
  idle_ttl_minutes: 120
```

Every agent's model is set independently, which is the intended tuning knob. Moving
`k8s-investigator` from Haiku 4.5 (200 K context, cheapest) to Sonnet 5 (1 M context) is a one-line
change plus a `sync`; so is raising the coordinator's `effort`. Note that `effort` is **agent
configuration only** — an `effort` inside a session-level model override is silently ignored, so it
must live here rather than being adjusted per session.

`web_search` and `web_fetch` are omitted from every `builtin_tools` list: cluster state is the source
of truth, and these tools run on Anthropic's servers regardless of environment type (§8.3). `write`
and `edit` are likewise omitted — the agents read and reason, they do not author files.

`k8srca sync` is the single control-plane command. It:

1. Builds the KB and architecture skills (§5).
2. Uploads/versions each skill via the Skills API.
3. Connects to every configured MCP server, enumerates `list_tools()`, resolves each agent's tool
   groups, applies the `prefix` namespacing, validates schemas (no `$ref`, no top-level
   `oneOf`/`anyOf`), detects cross-server name collisions, and computes a per-agent manifest hash.
4. **Creates specialists first, then the coordinator** — the roster references specialists by ID, so
   ordering is a hard dependency. On subsequent runs, **updates in place** (`POST /v1/agents/{id}`)
   with optimistic concurrency on `version`.
5. Creates the self-hosted environment (`config: {type: "self_hosted"}`) if absent.
6. Writes resolved IDs and versions to `.k8srca/state.json` (gitignored).

Two validations `sync` must perform that only exist because of the roster:

- **One level of delegation.** A rostered agent must not itself have a `roster`. The API rejects this,
  but catching it in `sync` gives a better message than a 400 at apply time.
- **Roster pinning.** Roster entries pin the specialist's version at coordinator-save time. Updating a
  specialist therefore requires re-saving the coordinator to pick it up; `sync` does this
  automatically and reports which agents were re-versioned as a result.

Change-management semantics — which edits need a drain, how versions are pinned per session,
and the GitOps watcher that drives `sync` — are [003 §3](003-operations.md#3-change-management-gitops).

**Agents are created once and updated, never recreated.** Every update produces a new immutable
version; sessions pin to a version at creation. This gives rollback (pin new sessions back to the
last-good prompt) and safe iteration (in-flight sessions keep their version). Recreating agents per
run would forfeit all of it and accumulate orphaned objects.

The `ant` CLI can drive steps 4–5 directly from YAML (`ant beta:agents create < agent.yaml`), which is
the documented recommendation. `k8srca sync` exists because steps 1–3 — generating tool declarations
from a live MCP handshake and building skill bundles — have no CLI equivalent. It emits the same YAML
the CLI would consume, so the CLI remains usable for inspection and manual rollback.

---

## 7. Runtime flows

### 7.1 Slack ⇄ session mapping

One Slack thread = one CMA session. State lives in SQLite (`.k8srca/sessions.db`):

| column | note |
| --- | --- |
| `channel_id`, `thread_ts` | composite key |
| `session_id` | CMA session |
| `agent_version` | version pinned at creation |
| `status`, `last_activity_at` | for TTL and reaping |

**Plain messaging, not Slack's assistant surface.** *(Revised: Rev 4.)* The agent is reached by
`@k8srca` in a channel or by DM; a thread is a session either way. It does **not** use Slack's
Agents / AI-apps feature.

Two reasons. First, that feature is a UI surface we don't need — suggested prompts, thread-title
management, Slack's own session lifecycle — and enabling it means adopting all of it. Second, it is
mid-migration: the `assistant_thread_started` generation is deprecated in favour of
`agent_session_*` / `app_context_changed`, and building on either half now buys a migration.

What is lost is `set_status()`, the native "thinking…" indicator, which §7.5 replaces.

The channel path is the same code the v2 alert responder will reuse, which is why it is in v1 rather
than deferred.

**Slack app configuration** (setup guide: [`docs/slack-app-setup.md`](../docs/slack-app-setup.md)):

| Item | Value |
| --- | --- |
| Connection | Socket Mode — no public endpoint, matching the rest of the deployment |
| App-level token | `connections:write` (`xapp-…`) |
| Bot scopes, required | `app_mentions:read`, `chat:write`, `channels:history`, `im:history` |
| Bot scopes, optional | `reactions:write`, `files:write`, `users:read`, `groups:history` |
| Events | `app_mention`, `message.channels`, `message.im` |

`message.channels` delivers **every** message in channels the bot has joined, not only mentions.
That is what makes in-thread follow-ups work without re-mentioning on each turn, and the
orchestrator discards anything whose `thread_ts` is not in its session map. Where a workspace
policy forbids an app ingesting channel traffic, dropping `message.channels` and `channels:history`
degrades cleanly: the user must `@`-mention every turn.

### 7.2 A turn

```
Slack message
   ├─ resolve or create session (stream-first: open SSE before sending)
   ├─ send user.message
   └─ consume SSE:
        span.model_request_start        → set_status("analyzing…")
        agent.custom_tool_use           → set_status("querying pods in prod…")
                                           (worker executes; orchestrator only observes)
        session.thread_created          → set_status("delegating log analysis…")
        agent.thread_message_sent/_recvd→ status only; never posted to Slack
        session.thread_status_*         → status only
        agent.message                   → post to thread  (coordinator thread only)
        session.status_idle:
            stop_reason == requires_action → keep consuming (see §7.4)
            stop_reason == end_turn        → close stream, mark idle
            stop_reason == budget_reached  → post a cost notice, close
        session.status_terminated       → close, mark terminated
```

**Only the coordinator thread's output reaches Slack.** With a roster in play the session-level stream
carries thread lifecycle events and cross-thread messages as well as the coordinator's own
`agent.message` events. Posting a specialist's raw findings would defeat the point of delegating —
the user should see the synthesized RCA, not the log dump that produced it. The orchestrator renders
specialist activity as **status text only** (`set_status("scanning logs for payment-api…")`), which
also gives the user useful progress signal during long delegated reads.

Note that `agent.message` events from subagent threads are distinguishable by thread ID; the relay
filters on the session's primary thread rather than assuming a single thread exists. Live previews
(`event_deltas[]`) are thread-scoped and never cross-posted, but we do not use them (§7.5).

Four client-side rules from the CMA documentation that are easy to get wrong:

- **Stream first, then send.** The stream only delivers events emitted after it opens.
- **Do not break on `session.status_idle` alone.** The session idles transiently. Break only on a
  non-`requires_action` `stop_reason`, or on `session.status_terminated`.
- **SSE has no replay.** On reconnect, fetch `events.list()` first and dedupe by event ID before
  tailing the live stream. Dedupe must gate *rendering only* — terminal checks still run on
  already-seen events, or a terminal event present in the history is skipped and the loop hangs.
- **Post-idle status-write race.** The stream emits idle slightly before the session's queryable
  status reflects it; poll `sessions.retrieve()` before any archive/delete.

Streams are opened per turn and closed when the turn settles, rather than held open for the life of a
Slack thread. Threads are long-lived and mostly quiet; holding one SSE connection per thread would
scale with idle threads instead of with active work.

### 7.3 Sandbox lifecycle

`spawn.sh`, invoked once per claimed work item with the work-item JSON on stdin:

```bash
#!/bin/bash
set -euo pipefail
ANTHROPIC_WORK_SECRET="$(jq -r '.secret // empty')"
export ANTHROPIC_WORK_SECRET

# Per-session workspace on the host: the container is per turn, this is not (§3.2)
WS="${K8SRCA_WORKSPACES:?}/${ANTHROPIC_SESSION_ID:?}"
mkdir -p "$WS"

exec docker run --rm \
  --network k8srca-net \
  --memory 2g --cpus 2 --pids-limit 512 \
  --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp \
  -v "$WS:/workspace" \
  -e ANTHROPIC_SESSION_ID -e ANTHROPIC_WORK_ID -e ANTHROPIC_ENVIRONMENT_ID \
  -e ANTHROPIC_ENVIRONMENT_KEY -e ANTHROPIC_BASE_URL -e ANTHROPIC_WORK_SECRET \
  -e K8SRCA_MCP_URL=http://k8stools:8000/mcp \
  k8srca/sandbox:0.1.0
```

Two things that are silent failures if missed:

- **`ANTHROPIC_WORK_SECRET` is not set by `--on-work`** for the spawned script — read it from the
  work-item JSON on stdin. Omitting it works in v1 and breaks v2 memory stores at claim time.
- **`-v "$WS:/workspace"`, not `--tmpfs /workspace`.** *(Verified.)* A tmpfs workspace dies with the
  container. Because a container exits `max_idle` after the session goes quiet and a later turn gets
  a **new** container (§3.2), everything under `/workspace` would be lost across any pause longer
  than a minute — skills re-downloading and, once 002 L2 lands, the investigation record gone. This
  looks fine in a one-shot test and in a fast follow-up; it fails on the first turn after a coffee
  break.

The rootfs stays read-only; only `/workspace` and `/tmp` are writable.

Sandbox image: Debian slim + `/bin/bash` (required at that exact path), Python 3.12 via `uv`,
`anthropic` + `mcp`, `jq`, `tar`, `unzip`, and the worker entrypoint. Deliberately **no** `kubectl`, no
`helm`, no kubeconfig, no cloud CLI.

### 7.4 Failure handling

> Detection and diagnosis plumbing for these conditions — worker logs, the session-metadata
> index, and the snapshot archive — is [003 §2](003-operations.md#2-observability). The
> `requires_action`-with-nothing-pending row below is invisible in the Console by design;
> 003 §2.3(b) covers how to catch it.

| Condition | Behavior |
| --- | --- |
| `session.error` | Render a short message in-thread; keep the session (usually recoverable). |
| `stop_reason: retries_exhausted` | Terminal. Fetch the session, report, offer a fresh thread. |
| `stop_reason: budget_reached` | Post consumed-vs-cap; the session pauses, it is not terminated. |
| Idle `requires_action` with nothing pending | **Self-hosted-specific.** Means the worker failed the claimed work item (logged only on the host). Do not spin: surface it, and send `user.interrupt` to re-queue after fixing. |
| k8stools unreachable | Worker fails fast at startup with a distinguishable exit code; poller logs it. |
| Session terminated mid-thread | Create a new session, seed it with a summary of the prior thread via `initial_events`, tell the user. The investigation record is re-seeded from the orchestrator's copy — see [002](002-investigation-model.md) §7. |
| Session ended or TTL expired | Reap `${K8SRCA_WORKSPACES}/<session_id>` after archiving the investigation record. Unreaped directories are the main disk-growth risk (§3.2). |

### 7.5 Slack rendering

- Chunk at 3 800 chars; Slack's hard limit is 4 000.
- Do **not** stream token deltas into Slack — rate limits make it hostile. Post buffered
  `agent.message` events. (`event_deltas[]` exists but is a poor fit here.)
- **Progress, without `set_status()`.** Having dropped the assistant surface (§7.1), progress is
  shown with two ordinary primitives: a 👀 reaction on the user's message as an immediate ack
  (`reactions:write`), and a placeholder reply edited in place via `chat.update` as the agent works
  — *"checking pod events…"* → *"scanning logs for payment-api…"* → the answer. `chat.update` needs
  no scope beyond `chat:write`. One edited message beats a stream of new ones: it keeps the thread
  readable and stays clear of rate limits.
- Long evidence dumps go to a thread snippet, not an inline block.
- Recommendations render with an explicit "suggested — not applied" prefix (§8.3).

---

## 8. Read-only guarantee and security

The requirement is that the agent *cannot* change the cluster. Four independent layers, so that no
single failure — including a successful prompt injection via a hostile pod log — produces a write.

**8.1 No mutating tools exist.** k8stools deliberately excludes state-modifying functions. There is no
`apply`, `patch`, `delete`, or `exec` in the tool surface the model can see.

**8.2 No credentials in the sandbox.** The kubeconfig is mounted only into the k8stools container. The
session sandbox has no kubeconfig, no service-account token, no `kubectl`. Even with full `bash`, the
agent has nothing to authenticate with.

> **This layer holds only in the containerised worker.** *(Found in testing, Rev 7.)* The in-process
> `k8srca worker` runs agent-authored `bash` as a subprocess of itself, inheriting `os.environ` —
> which, after `.env` is loaded, contains `KUBECONFIG`, `ANTHROPIC_API_KEY`, and the Slack tokens.
> During a real-cluster test the agent announced *"I have kubectl read access directly"* and used it;
> `kubectl auth can-i delete pods -A` returned **yes** from that environment. It only read, but the
> guarantee was not holding: k8stools' read-only tool surface (8.1) constrains the MCP path and says
> nothing about a `kubectl` binary on the host.
>
> Fixed by `settings.scrub_environment()`, called before the worker starts: it removes the credential
> variables and points `KUBECONFIG` at `/dev/null` rather than merely unsetting it, because kubectl
> falls back to `~/.kube/config` when the variable is absent — usually cluster-admin on a developer
> workstation. The worker also prints a warning that it is a development shape.
>
> Scrubbing is defence in depth, not isolation. The in-process worker still runs arbitrary bash on the
> host with the invoking user's filesystem access. **Use `k8srca poller` against anything real.**

**8.3 Constrained network egress.** The sandbox joins `k8srca-net`, whose only other member is
k8stools; it has no access to the host network namespace and no route to other containers. This is
*not* sufficient on its own: a user-defined bridge network still NATs outbound traffic, so the sandbox
can reach the internet — and, on most setups, the kube-apiserver — unless egress is explicitly
restricted. **Egress filtering is a required implementation task, not a property we get for free.**
The rule set: allow `api.anthropic.com:443` and `k8stools:8000`; deny everything else, the
kube-apiserver address included. Implement with `iptables`/`nftables` rules on the `k8srca-net` bridge
(or an egress-proxy sidecar), and add a startup assertion in the sandbox entrypoint that a probe to
the API server address fails — so a misconfigured firewall is caught at session start rather than
discovered later.

`web_search`/`web_fetch` are disabled on the agent. Note these run on *Anthropic's* servers regardless
of environment type, so no amount of local egress filtering would have constrained them; disabling
them per-tool is the only control that works.

**8.4 Least-privilege RBAC.** The kubeconfig binds a dedicated ServiceAccount to a read-only
ClusterRole (`get`/`list`/`watch` on the resources k8stools uses, plus `pods/log`). This is the
backstop: even if 8.1–8.3 all failed, the API server rejects the write.

The system prompt *also* instructs the agent to recommend rather than act, and Slack output labels
recommendations as suggestions — but that is UX, not enforcement, and is not counted among the layers.

### Data flow (F3)

Tool inputs and results are transmitted to Anthropic's control plane so the model can reason over
them. In practice this means pod logs, ConfigMap values, event streams, and pod specs leave your
network. k8stools' redaction filter strips common secret shapes before results ever reach the worker,
which reduces but does not eliminate this. If any namespace must never have its logs leave the
network, exclude it at the RBAC layer — that is the only reliable control.

### Credential custody

| Secret | Held by | Never on |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` (org-scoped) | Orchestrator only | Poller host env, sandbox |
| `ANTHROPIC_ENVIRONMENT_KEY` | Poller, forwarded to sandbox | Orchestrator |
| `ANTHROPIC_WORK_SECRET` | Per-session, forwarded to that sandbox only | Images, shared volumes, logs |
| Slack bot/app tokens | Orchestrator only | Sandbox |
| kubeconfig | k8stools container only | Sandbox, orchestrator |

Anthropic cannot fast-revoke a leaked environment key — rotation is manual, and the key is worth
protecting accordingly.

---

## 9. The agents' reasoning contracts

> This section fixes the **output shape**. The reasoning *method* — hypotheses, evidence,
> discriminating actions, stopping criteria — is [Design 002](002-investigation-model.md).
> 002 §9 defines the maturity levels; the contract below is what every level must satisfy.

Two agents, two prompts, two contracts. The split only pays if the specialist reports *findings*
rather than dumping evidence back into the coordinator's context.

### 9.1 `k8s-investigator` — the specialist

Because subagents carry no shared context (F4), the coordinator's brief must be self-contained and the
specialist's reply must be small. Its standing rules:

- **Answer exactly the question asked.** Do not speculate about root cause — that is the coordinator's
  job, and a specialist that editorializes wastes the context the delegation was meant to save.
- **Grep before read.** Large tool outputs (>100 000 characters) are automatically offloaded by the
  platform to a file in the sandbox, with the agent receiving a preview plus the path. Pod-log
  retrieval will routinely exceed this. The specialist must `grep` the file for the patterns it was
  given and read only the matching regions — never load the file to scan it. On a 200 K-context model
  this is the difference between working and failing (§3.3).
- **Report findings with citations, bounded.** Each finding cites the tool call that produced it. Cap
  the report; if there is more, say so and summarize the shape of the remainder.
- **Say what you could not find**, so the coordinator can distinguish absence of evidence from absence
  of the signal.

### 9.2 `rca-coordinator` — the diagnosis

The coordinator owns everything the user sees. Its system prompt fixes an output shape, which matters
for Slack readability now and is the substrate the v2 memory system will synthesize from.

Every diagnosis returns:

1. **Finding** — one sentence.
2. **Evidence** — each item citing the tool call that produced it (`get_pod_events` on `payment-api-7d9f`
   → 15 restarts, last termination `OOMKilled`). No claim without a citation.
3. **Ranked causes** — with confidence, and at least one alternative considered and why it ranks lower.
4. **Recommended actions** — explicitly labeled as suggestions, ordered by priority.
5. **Not checked** — what the agent could not verify and why (no metrics backend, namespace not in
   RBAC scope, logs rotated). This is the field that keeps the agent honest and is the highest-value
   input to deciding whether the deferred Prometheus integration is worth building.

Standing behavioral rules:

- An alert is a **hypothesis**, not a conclusion. Corroborate before concluding.
- Prefer the *lowest* layer in the hierarchy consistent with the evidence — `CrashLoopBackOff` across
  every pod on one node is a node problem, not fifteen application problems.
- Correlate temporally. "Did anything change?" precedes "what is broken?"
- Say "I don't know" with the specific missing evidence rather than producing a confident guess.
- Evidence from a specialist is cited as such. A finding the coordinator did not verify itself is
  labeled with its source thread, so a wrong answer is traceable to where it was produced.

**When to delegate** (the coordinator's prompt must say this explicitly, since delegation is a choice
the model makes, not a routing rule the platform enforces):

| Delegate to `k8s-investigator` | Do it yourself |
| --- | --- |
| Any log retrieval or log scanning | Pod/deployment/node summaries, container statuses, events |
| Sweeps across many pods or namespaces | A single object's detail |
| Anything expected to exceed a few thousand tokens of output | Anything answerable in one triage call |

One self-contained task per spawn, several in parallel when the questions are independent. Do not
delegate a single lookup — the round-trip and re-briefing cost more than the call.

---

## 10. Repository layout

```
k8srca/
├── designs/001-architecture.md
├── k8srca.yaml                     # the single config file (§6)
├── agents/
│   ├── rca-coordinator.system.md   # system prompts, version-controlled
│   └── k8s-investigator.system.md
├── src/k8srca/
│   ├── config.py                   # k8srca.yaml schema (pydantic)
│   ├── sync.py                     # control plane: skills, agents, environment
│   ├── tools.py                    # MCP → custom-tool decls, prefixing, manifest hash
│   ├── kb/build.py                 # KB normalization + causal index
│   ├── arch/build.py               # helm template → cluster-architecture skill
│   ├── worker/entrypoint.py        # sandbox: handle_item + union of wrapped MCP tools
│   └── slack/
│       ├── app.py                  # Bolt, Socket Mode, assistant + mention
│       ├── sessions.py             # thread_ts ⇄ session_id (SQLite)
│       └── relay.py                # SSE → Slack rendering
├── skills/
│   ├── k8s-rca/{SKILL.md,knowledge_base.json,kb_query.py}
│   └── cluster-architecture/       # generated
├── docker/
│   ├── Dockerfile.sandbox
│   ├── Dockerfile.k8stools
│   ├── compose.yaml                # k8stools + poller + orchestrator
│   └── spawn.sh
└── rbac/k8srca-readonly.yaml       # ServiceAccount + ClusterRole (§8.4)
```

---

## 11. Extension points for deferred work

Each deferred item has a named seam, so adding it is additive.

**Alert-driven sessions (v2).** The channel-mention path in §7.1 already creates a session from a
channel message. An Alertmanager webhook receiver becomes a second producer into the same
`create_session_for_thread()` entry point, seeding `initial_events` with the alert payload. The
knowledge base is keyed by `alert_name`, so an Alertmanager alert name is already the KB's primary
key — no translation layer.

**Memory (v2).** Memory stores are the *only* resource type self-hosted environments accept, and the
SDK worker (Python/TS/Go — not the `ant` CLI) mounts them under `/mnt/memory/<store-name>/` and syncs
on an interval. Our worker is the SDK worker, and §7.3 already forwards `ANTHROPIC_WORK_SECRET`, so
this reduces to: `mkdir -p /mnt/memory` on the host with correct ownership, attach
`resources: [{type: memory_store, ...}]` at session create, and add a synthesis step. Two host
requirements to note now: POSIX only (the worker opens memory files with `O_NOFOLLOW`), and workers
must be stopped by *cancellation* with ≥30 s before any hard kill, or unsynced edits are lost.

**Git access to Helm charts (v3).** `github_repository` resources are rejected on self-hosted (F2). The
route is a read-only clone maintained by the poller on a shared volume, bind-mounted into the sandbox
`:ro`. The `cluster-architecture` skill already carries the *rendered* state; git adds *history* —
"what changed in the 20 minutes before this alert" — which the causal analysis identifies as one of
the highest-value signals.

**Observability MCP (v3).** This is where the coordinator / specialist split earns most of its keep.
Adding metrics is: a second entry in `k8srca.yaml`'s `mcp:` list (joining `k8srca-net`, `prefix:
prom_`), plus one new `agents:` block for a `metrics-investigator`, plus that agent's name in the
coordinator's `roster`. The worker picks up the new server from the same config and registers its
tools into the union (§4.2) with no code change. The coordinator's own tool list does **not** grow —
which is the property that keeps it from degrading as sources multiply.

The KB's `promql_signals` field becomes directly executable at that point, which is the single change
that most increases diagnostic precision. If an in-cluster observability server is not reachable from
the host, MCP tunnels become worth reconsidering — that is the case they are actually designed for.

**Converging on a pure split.** Each source added this way shifts more evidence-gathering out of the
coordinator. At three or four sources, the coordinator's triage subset is worth re-examining: if the
specialists cover the ground, drop it and let the coordinator become pure orchestration. That is the
architecture originally proposed for v1, arrived at when the tool count justifies it (§3.3).

---

## 12. Open questions

1. **Tool-call timeout.** The background doc claims a 120 s worker backstop on tool execution. I could
   not confirm this in the official documentation. Pod-log retrieval across a large namespace can be
   slow, so this needs an empirical measurement before we tune `read_timeout_seconds` on the MCP
   client session.
2. **Session TTL.** How long should a quiet Slack thread hold a live session? Longer preserves context
   for follow-ups; shorter bounds cost and stale cluster state. Proposed default 120 min, configurable.
3. **Effort level.** `high` is proposed for the coordinator. `xhigh` may pay for itself on multi-hop
   causal reasoning. Worth measuring once there is a scenario set (see 5).
4. **KB coverage vs. the actual cluster.** The 81 rules are generic Kubernetes. Some will never fire
   here; some real failure modes are absent. Recommend an audit after two weeks of real sessions,
   driven by the "Not checked" field from §9.
5. **Evaluation.** No scenario suite is defined yet. A small set of reproducible broken deployments
   (OOMKill, bad probe, image pull failure, PVC pending, node pressure) run against a kind/minikube
   cluster would let us measure regressions when the prompt or KB changes. Worth defining before the
   prompt starts accumulating ad-hoc fixes.
6. ~~**Multiagent on a self-hosted sandbox — verify before relying on it.**~~ **RESOLVED — it works.**
   Verified end to end against the live platform: a coordinator on `claude-sonnet-5` delegated to a
   `k8s-investigator` thread on `claude-haiku-4-5`, and that subagent's `agent.custom_tool_use`
   **was served by the worker**. The proof is tool-level rather than circumstantial — the specialist
   called `k8s_get_logs_for_pod_and_container`, which is not in the coordinator's triage group, so
   the call can only have originated in the subagent thread. `sessions.threads.list()` shows both
   threads on their own models with the correct `parent_thread_id`.
   Still open, and cheaper to answer once skills exist: whether skills attached to the coordinator
   are visible to specialist threads or must be attached per agent.
   *(The related question — whether `async_mcp_tool` can carry the `prefix` — is also **resolved**:
   it cannot, and §4.2 specifies the wrapper that replaces it.)*
7. **Where the triage/specialist line sits.** The six-tool triage subset in §3.3 is a first guess.
   Too small and the coordinator delegates trivia; too large and logs creep back into its context.
   Tune against the scenario suite, not by intuition.

---

## 13. Build order

| Phase | Deliverable | Proves | Status |
| --- | --- | --- | --- |
| 0 | RBAC + k8stools container + `compose.yaml` + egress rules (§8.3) | Read-only cluster access works end-to-end, and the sandbox network is actually closed | RBAC/container/compose done; **egress rules outstanding** |
| 1a | `sync` → agent + self-hosted environment; worker; CLI-driven session | Worker-as-MCP-client (F1) works — the highest-risk assumption | **Proven.** In-process worker; containerisation outstanding |
| 1b | Coordinator + `k8s-investigator`; roster; per-agent manifest hashes | Multiagent on a self-hosted sandbox (F4, §12.6) — the second-highest-risk assumption | **Proven** (§12.6) |
| 2 | `kb build` + `k8s-rca` skill | Skills reach a self-hosted sandbox (F2), and whether specialists inherit them |
| 3 | Slack orchestrator (assistant + mention), session map, SSE relay with thread filtering | The actual product |
| 4 | `arch build` + `cluster-architecture` skill | Cluster-specific reasoning |
| 5 | Scenario suite (open question 5) | Changes can be evaluated rather than guessed at |

**Phase 1 was split deliberately**, and both halves are now proven, so neither fallback is needed:
MCP tunnels are not required (F1), and the coordinator/specialist split stands (F4).

**What 1a/1b deliberately did *not* cover.** They were proven with an **in-process worker on the
host**, not the sandbox-per-turn container of §3.2/§7.3 — the least machinery around the risky
assumption. Containerisation is a separate step, and it is where the session-scoped workspace mount,
`ANTHROPIC_WORK_SECRET` forwarding, and egress filtering get exercised. None of those affect whether
the architecture works; all of them affect whether it is safe.

**Validated against a real cluster (Rev 6).** An OpenTelemetry-demo cluster, 27 pods, two genuinely
broken services (`fraud-detection`, 1481 restarts; `ad`, 1307). Asked why `fraud-detection` was
crash-looping, the agent produced a diagnosis whose every verifiable claim held up against `kubectl`:
exit code 137, container alive for exactly two seconds, `memory` request = limit = 300Mi, no JVM heap
flags set, node not under memory pressure.

Three things it did that the design intended but could not guarantee:

- **It noticed the cross-service pattern.** Two unrelated Java services on the same resource template
  failing identically pointed it at the template rather than at application logic — reasoning nobody
  prompted for.
- **It flagged the evidence that did not fit.** The container's `reason` was `Error`, not the
  `OOMKilled` the hypothesis predicts, and it said so, declining to claim the OOM killer had been
  proven. Independent checking confirmed the discrepancy is real (a Docker-runtime labelling quirk)
  and that the hypothesis is nonetheless correct: neither pod has *any* probe configured, so nothing
  else could be sending SIGKILL.
- **It separated `unavailable` from `absent`** unprompted — "no exception logged" was reported as the
  process dying before it could log, not as an absence of errors (002 §5.1).

Cost: 30 tool calls, one delegation. The remaining gap it named itself — no metrics backend to show
the working-set trajectory — is exactly the case for the deferred Prometheus integration (§11).

**Observed baseline (L0, no system prompt, no skills).** Asked which pods were unhealthy, the
coordinator chained pod summaries → container statuses → events and identified an `OOMKilled` loop
against a 300Mi limit unaided. This is the floor that [002](002-investigation-model.md) §9's ladder
has to beat, and it is higher than expected — worth measuring L1 against before assuming structure
helps. One delegating turn cost $0.06 and 36 s of active time.
