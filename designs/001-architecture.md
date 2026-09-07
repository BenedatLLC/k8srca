# k8srca — Kubernetes Root Cause Analysis Agent

**Design document 001 — Architecture**
Status: Draft for review · Date: 2026-09-07

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
| RCA knowledge base (81 rules) as an agent skill | Git access to Helm charts (v3 — extension point defined) |
| Deployment-architecture docs + runbooks as a skill | Prometheus / Loki / Tempo MCP (v3 — extension point defined) |
| Declarative config for agent, environment, skills | Multi-cluster |

The optional integrations were explicitly deferred, but §11 fixes the seams so adding them is
additive rather than a rewrite.

---

## 2. Key architectural findings

Three facts about Claude Managed Agents (CMA) drive the whole design. All three contradict the
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

---

## 3. System architecture

```
  ┌─────────────────────────── Anthropic control plane ────────────────────────────┐
  │  Agent config (versioned)   Session orchestration   Claude Sonnet 5 loop       │
  │  Skills store               Event log / SSE         Work queue per environment │
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
  └───────▲────────────┘    ┌──────────────────────────────────────────────┐
          │                 │ Session sandbox  (ephemeral, one per session)│
          │                 │  EnvironmentWorker.handle_item()             │
          │                 │   ├── built-in toolset: bash/read/grep/glob  │
          │                 │   ├── wrapped MCP tools (k8s_*)  ───────┐    │
          │                 │   └── /workspace/skills/{k8s-rca,        │    │
          │                 │        cluster-architecture}            │    │
          │                 └─────────────────────────────────────────┼────┘
          │                                    docker net: k8srca-net │
  ┌───────┴────────┐                          ┌────────────────────────▼───┐
  │  Slack          │                          │ k8stools MCP server        │
  │  (workspace)    │                          │ streamable-http :8000      │
  └─────────────────┘                          │ KUBECONFIG (read-only SA)  │
                                               └────────────┬───────────────┘
                                                            │ HTTPS, read-only RBAC
                                                            ▼
                                                     Kubernetes cluster
```

### 3.1 Processes

| Process | Runs as | Responsibility |
| --- | --- | --- |
| **Orchestrator** | host, long-lived | Slack Bolt app in Socket Mode. Owns the Slack↔session mapping, creates sessions, streams events, renders to Slack. Holds the **API key**. |
| **Host poller** | host, long-lived | `ant beta:worker poll --on-work spawn.sh`. Claims work items, spawns one container per session. Holds the **environment key**. |
| **Session sandbox** | container, ephemeral | `EnvironmentWorker.handle_item()` with built-in tools + wrapped k8stools MCP tools. Exits when the session run completes. |
| **k8stools MCP** | container, long-lived | `k8s-mcp-server --transport=streamable-http`. The **only** process holding a kubeconfig. |

Splitting the orchestrator from the poller matters: the orchestrator's org-scoped API key must never
be present on a host where agent-authored `bash` can read it. The self-hosted-sandbox docs call this
out explicitly for control-plane calls.

### 3.2 Why ephemeral containers

`ant beta:worker poll --on-work spawn.sh` claims a work item and hands it to a script that does
`docker run --rm`, so each session gets a fresh filesystem. Three reasons this beats a single
persistent `EnvironmentWorker.run()`:

- No filesystem drift between diagnostic sessions — a session cannot see files another wrote.
- Hard resource caps per session (`--memory`, `--cpus`, `--pids-limit`).
- **Required for v2 memory stores.** The worker creates each store's directory under `/mnt/memory/`
  and *refuses the work item if something already exists at that path*, so two concurrent sessions
  cannot mount the same store on one host. Sandbox-per-session satisfies this by construction.

The cost is per-session container start (~1–2 s) plus a fresh MCP handshake to k8stools per session.
Acceptable for an interactive diagnostic tool.

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

**Agent side** (`sync` time, §6): enumerate `list_tools()` and emit one `custom` declaration per tool.

```python
def to_custom_tool(tool: types.Tool) -> BetaManagedAgentsCustomToolParams:
    return {
        "type": "custom",
        "name": tool.name,
        "description": tool.description or tool.name,
        "input_schema": cast(Any, tool.inputSchema),
    }
```

**Worker side** (sandbox entrypoint):

```python
async with (
    streamable_http_client(MCP_URL) as (read, write, _),
    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60)) as mcp,
    AsyncAnthropic(auth_token=os.environ["ANTHROPIC_ENVIRONMENT_KEY"]) as client,
):
    await mcp.initialize()
    listed = await mcp.list_tools()
    mcp_tools = [async_mcp_tool(t, mcp) for t in listed.tools]
    worker = EnvironmentWorker(
        client,
        workdir="/workspace",
        tools=lambda env: [*beta_agent_toolset_20260401(env), *mcp_tools],
    )
    await worker.handle_item()          # IDs read from forwarded ANTHROPIC_* env vars
```

Signals must be wired to *cancellation*, not a kill, so the worker completes teardown (§7.3).

**Schema constraint.** Custom-tool schemas must not use `$ref` or top-level `oneOf`/`anyOf`. `sync`
validates every generated schema against this and fails loudly rather than at session runtime.

### 4.3 Declaration drift

The agent's tool declarations and the worker's live `list_tools()` are two copies of the same truth.
If k8stools is upgraded and a tool is renamed, the model calls a tool the worker cannot resolve.

Mitigation: `sync` records a **tool-manifest hash** (sorted names + schema digest) in the agent's
`metadata`. The sandbox entrypoint recomputes it at startup and, on mismatch, fails the work item with
a clear log line rather than serving a half-broken toolset. Upgrading k8stools is then a `sync` step,
enforced by a check rather than by memory.

---

## 5. Knowledge delivery

Per F2, everything the agent should *know* arrives as **Skills**, downloaded by the worker into
`/workspace/skills/<name>/`. Two skills in v1 (limit is 20 per agent).

### 5.1 `k8s-rca` — methodology + knowledge base

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
documentation, etc."* — a single declarative file, version-controlled, applied by one command.

`k8srca.yaml`:

```yaml
model:
  id: claude-sonnet-5
  effort: high

agent:
  name: k8s-rca
  system_prompt: agents/rca-agent.system.md
  toolset:
    default: { enabled: true }
    overrides:
      web_search: { enabled: false }     # cluster state is the source of truth
      web_fetch:  { enabled: false }
      write:      { enabled: false }     # no reason for the agent to author files
      edit:       { enabled: false }

mcp:
  - name: k8stools
    url: http://k8stools:8000/mcp
    wrap: worker_custom_tools            # per F1
    prefix: k8s_

skills:
  - path: skills/k8s-rca
  - path: skills/cluster-architecture

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

`k8srca sync` is the single control-plane command. It:

1. Builds the KB and architecture skills (§5).
2. Uploads/versions each skill via the Skills API.
3. Connects to k8stools, enumerates `list_tools()`, generates custom-tool declarations, validates
   schemas (no `$ref`, no top-level `oneOf`/`anyOf`), computes the tool-manifest hash.
4. Creates the agent on first run; on subsequent runs **updates in place** (`POST /v1/agents/{id}`)
   with optimistic concurrency on `version`.
5. Creates the self-hosted environment (`config: {type: "self_hosted"}`) if absent.
6. Writes resolved IDs and versions to `.k8srca/state.json` (gitignored).

**Agents are created once and updated, never recreated.** Every update produces a new immutable
version; sessions pin to a version at creation. This gives rollback (pin new sessions back to the
last-good prompt) and safe iteration (in-flight sessions keep their version). Recreating the agent per
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

Both Slack surfaces are supported. The **assistant container** (`assistant_thread_started` →
suggested prompts; `set_status()` during work) is the primary 1:1 experience. **`@k8srca` in a
channel** creates a session bound to that thread — the same code path the v2 alert responder will
reuse, which is why it is in v1 rather than deferred.

### 7.2 A turn

```
Slack message
   ├─ resolve or create session (stream-first: open SSE before sending)
   ├─ send user.message
   └─ consume SSE:
        span.model_request_start  → set_status("analyzing…")
        agent.custom_tool_use     → set_status("querying pods in prod…")
                                     (worker executes; orchestrator only observes)
        agent.message             → post to thread
        session.status_idle:
            stop_reason == requires_action → keep consuming (see §7.4)
            stop_reason == end_turn        → close stream, mark idle
            stop_reason == budget_reached  → post a cost notice, close
        session.status_terminated → close, mark terminated
```

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
exec docker run --rm \
  --network k8srca-net \
  --memory 2g --cpus 2 --pids-limit 512 \
  --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /workspace:rw,exec --tmpfs /tmp \
  -e ANTHROPIC_SESSION_ID -e ANTHROPIC_WORK_ID -e ANTHROPIC_ENVIRONMENT_ID \
  -e ANTHROPIC_ENVIRONMENT_KEY -e ANTHROPIC_BASE_URL -e ANTHROPIC_WORK_SECRET \
  -e K8SRCA_MCP_URL=http://k8stools:8000/mcp \
  k8srca/sandbox:0.1.0
```

`ANTHROPIC_WORK_SECRET` is **not** set by `--on-work` for the spawned script — it must be read from the
work-item JSON on stdin. Forgetting this is silent in v1 and breaks v2 memory stores at claim time.

Sandbox image: Debian slim + `/bin/bash` (required at that exact path), Python 3.12 via `uv`,
`anthropic` + `mcp`, `jq`, `tar`, `unzip`, and the worker entrypoint. Deliberately **no** `kubectl`, no
`helm`, no kubeconfig, no cloud CLI.

### 7.4 Failure handling

| Condition | Behavior |
| --- | --- |
| `session.error` | Render a short message in-thread; keep the session (usually recoverable). |
| `stop_reason: retries_exhausted` | Terminal. Fetch the session, report, offer a fresh thread. |
| `stop_reason: budget_reached` | Post consumed-vs-cap; the session pauses, it is not terminated. |
| Idle `requires_action` with nothing pending | **Self-hosted-specific.** Means the worker failed the claimed work item (logged only on the host). Do not spin: surface it, and send `user.interrupt` to re-queue after fixing. |
| k8stools unreachable | Worker fails fast at startup with a distinguishable exit code; poller logs it. |
| Session terminated mid-thread | Create a new session, seed it with a summary of the prior thread via `initial_events`, tell the user. |

### 7.5 Slack rendering

- Chunk at 3 800 chars; Slack's hard limit is 4 000.
- Do **not** stream token deltas into Slack — rate limits make it hostile. Use `set_status()` for
  progress and post buffered `agent.message` events. (`event_deltas[]` exists but is a poor fit here.)
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

## 9. The agent's reasoning contract

Beyond tools and knowledge, the system prompt fixes an output shape. This matters for Slack
readability now and is the substrate the v2 memory system will synthesize from.

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

Large tool outputs (>100 000 characters) are automatically offloaded by the platform to a file in the
sandbox, with the agent receiving a preview plus the path. This works in our favor: pod-log retrieval
will routinely exceed it, and the agent can `grep` the file rather than carrying it in context.

---

## 10. Repository layout

```
k8srca/
├── designs/001-architecture.md
├── k8srca.yaml                     # the single config file (§6)
├── agents/rca-agent.system.md      # system prompt, version-controlled
├── src/k8srca/
│   ├── config.py                   # k8srca.yaml schema (pydantic)
│   ├── sync.py                     # control plane: skills, agent, environment
│   ├── kb/build.py                 # KB normalization + causal index
│   ├── arch/build.py               # helm template → cluster-architecture skill
│   ├── worker/entrypoint.py        # sandbox: handle_item + wrapped MCP tools
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

**Observability MCP (v3).** Wrapped exactly like k8stools: a second entry in `k8srca.yaml`'s `mcp:`
list, joining `k8srca-net`, tools declared with a `prom_` prefix. The KB's `promql_signals` field
becomes directly executable at that point, which is the single change that most increases diagnostic
precision. If the in-cluster server is not reachable from the host, MCP tunnels become worth
reconsidering — that is the case they are actually designed for.

---

## 12. Open questions

1. **Tool-call timeout.** The background doc claims a 120 s worker backstop on tool execution. I could
   not confirm this in the official documentation. Pod-log retrieval across a large namespace can be
   slow, so this needs an empirical measurement before we tune `read_timeout_seconds` on the MCP
   client session.
2. **Session TTL.** How long should a quiet Slack thread hold a live session? Longer preserves context
   for follow-ups; shorter bounds cost and stale cluster state. Proposed default 120 min, configurable.
3. **Effort level.** `high` is proposed. `xhigh` may pay for itself on multi-hop causal reasoning.
   Worth measuring once there is a scenario set (see 5).
4. **KB coverage vs. the actual cluster.** The 81 rules are generic Kubernetes. Some will never fire
   here; some real failure modes are absent. Recommend an audit after two weeks of real sessions,
   driven by the "Not checked" field from §9.
5. **Evaluation.** No scenario suite is defined yet. A small set of reproducible broken deployments
   (OOMKill, bad probe, image pull failure, PVC pending, node pressure) run against a kind/minikube
   cluster would let us measure regressions when the prompt or KB changes. Worth defining before the
   prompt starts accumulating ad-hoc fixes.

---

## 13. Build order

| Phase | Deliverable | Proves |
| --- | --- | --- |
| 0 | RBAC + k8stools container + `compose.yaml` + egress rules (§8.3) | Read-only cluster access works end-to-end, and the sandbox network is actually closed |
| 1 | `sync` → agent + self-hosted environment; poller + sandbox; CLI-driven session | Worker-as-MCP-client (F1) works — the highest-risk assumption |
| 2 | `kb build` + `k8s-rca` skill | Skills reach a self-hosted sandbox (F2); KB is queryable |
| 3 | Slack orchestrator (assistant + mention), session map, SSE relay | The actual product |
| 4 | `arch build` + `cluster-architecture` skill | Cluster-specific reasoning |
| 5 | Scenario suite (open question 5) | Changes can be evaluated rather than guessed at |

Phase 1 is the one to build first and to timebox. It carries the only assumption whose failure would
force a different architecture — if worker-hosted MCP tools do not behave as documented, the fallback
is MCP tunnels (§2, F1) and that decision is much cheaper to make in week one than in week four.
