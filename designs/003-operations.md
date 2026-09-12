# k8srca — Operations

**Design document 003 — Observability, change management, and Kubernetes deployment**
Status: Draft for review · Date: 2026-09-07
Companions: [001 — Architecture](001-architecture.md) · [002 — Investigation model](002-investigation-model.md)

---

## 1. Purpose

001 describes what to build; 002 describes how it reasons. This document covers running it: seeing
what it did, changing it without disrupting live conversations, and eventually moving the server
components into Kubernetes.

| Question | Section |
| --- | --- |
| How do I see a record of past conversations? | §2 |
| What does CMA give me vs. what must I build? | §2.1 / §2.3 |
| How do I ship a skill change without breaking a live investigation? | §3 |
| What needs a drain, and what doesn't? | §3.1 |
| Can this run in Kubernetes? | §4 |
| What has to happen first? | §4.4 |

---

## 2. Observability

### 2.1 What CMA already provides — do not rebuild it

The Anthropic Console session viewer (sidebar → **Managed Agents** → **Sessions**; Developer and
Admin roles) covers most of what a developer needs:

- Session list — ID, title, status, agent, tokens in/out, cost; filter by status and created-date,
  search by ID
- Full transcript grouped by model request: thinking, tool calls **with inputs and results**,
  streaming text, with a filter box that steps between matches
- **Tools** tab — every configured tool with call count, failure count, median duration, jump-to-call
- **Threads** tab — per-thread status, context size, and cost. This is how you answer *"is the Haiku
  specialist actually saving anything?"* (001 §3.3)
- **Resources** tab — mounted memory stores, session outputs, skills under `/workspace/skills`
- Timeline minimap with one lane per thread — built for exactly the coordinator/specialist shape
- Copy / download-as-JSON, and deep links via `?event={event_id}`

Programmatic equivalents: `GET /v1/sessions` (paginated; the only endpoint with backward pagination),
`GET /v1/sessions/{id}` for `usage`, `list_cost` and `active_seconds`, `GET /v1/sessions/{id}/events`,
and per-thread history at `/v1/sessions/{id}/threads/{tid}/events`. From a shell,
`ant beta:sessions list --transform '{id,title,status,created_at}' --format jsonl`.

**Conclusion: build no transcript UI.** Point developers at the Console.

### 2.2 Session metadata — the index that makes it findable

The Console is excellent once you know *which* session. Getting from *"that conversation in
#incidents on Tuesday"* to a session ID is the part CMA cannot do for you, because it knows nothing
about Slack.

Sessions accept `metadata` — **maximum 8 keys**, which is a tight budget. Allocate it deliberately:

| Key | Value | Why |
| --- | --- | --- |
| `slack_channel` | `C0123ABCD` | Join key from Slack |
| `slack_thread_ts` | `1757246400.123456` | Identifies the thread |
| `investigation_id` | `inv-20260907-payment-api` | Join key to the 002 record |
| `subject` | `prod/payment-api` | Group all investigations of one workload |
| `config_rev` | git SHA at `sync` time | Which prompts/skills produced this (§3.2) |
| `sandbox_image` | image tag/SHA | Which worker code ran (§3.2) |
| `trigger` | `user_question` \| `alert` | Separates v1 chat from v2 alert sessions |
| *(reserved)* | — | Keep one free; v2 will want `alert_name` |

Set these at `sessions.create()`. They cost nothing and are impossible to backfill.

**Also post the Console link into the Slack thread** when a session is created. Use the real workspace
ID — the session response does not carry it, so make it a config value; `default` only works if the
API key belongs to the org's Default workspace, and a wrong one lands on a "Session not found" page
rather than redirecting.

Agent objects carry their own `metadata` (max 16 keys) — that is where the per-agent tool-manifest
hash lives (001 §4.3).

### 2.3 What we must build

**(a) Worker-side structured logs — not optional.**

The Console shows tool inputs and results because those flow to the control plane (001 F3). It shows
**nothing** about your sandbox: container exit codes, MCP handshake failures, manifest mismatches,
skill download problems, memory-mount errors. The documentation is explicit that mount and
background-sync failures are *"logged, not reported to the session."*

JSON lines from `spawn.sh` and the worker entrypoint, correlated on `session_id` (the join key to
everything in the Console):

```jsonc
{"ts":"2026-09-07T11:03:02.114Z","level":"info","event":"container_start",
 "session_id":"sess_01ab...","work_id":"work_01cd...","image":"k8srca/sandbox:9f3c1a2",
 "workspace":"/srv/k8srca/workspaces/sess_01ab..."}
```

Minimum event set: `work_claimed`, `container_start`, `mcp_connect` / `mcp_connect_failed`,
`manifest_mismatch`, `skills_ready`, `handle_item_start` / `handle_item_end`, `tool_call`
(name + duration + ok, **not** arguments or results), `memory_mount_failed`, `container_exit`
(code + duration).

> **Do not log tool arguments or results.** They contain cluster state — ConfigMap values, log lines,
> pod specs. k8stools redacts common secret shapes, but the sandbox log is a second copy on your disk
> with different retention than everything else. Log the tool *name*, duration, and outcome; the
> Console already has the content.

**(b) The invisible failure mode.** One symptom cannot be diagnosed from the Console at all:

> Session `idle` with `stop_reason: requires_action`, no pending tool call to answer, no error event.

This means the worker **failed the claimed work item** — typically a memory-mount error in v2, or a
crashed entrypoint. The session emits nothing. The only evidence is on your host. The orchestrator
should detect this shape (idle + `requires_action` + nothing pending), correlate against the worker
log by session ID, surface it in Slack, and offer `user.interrupt` to re-queue the work. 001 §7.4
lists the condition; this is where the plumbing to diagnose it lives.

**(c) Investigation snapshot archive.** The 002 record is your artifact and the Console will not
render it. Snapshot to the orchestrator's store each time a turn settles (002 §7.1). This is the
record of *reasoning*, as opposed to the Console's record of *activity* — and it is the only durable
copy if a session terminates or a host workspace is reaped.

**(d) Cross-session analytics.** The Console's Tools tab is per-session. Everything worth measuring is
cross-session, and all of it comes from the snapshot archive:

| Question | Drives |
| --- | --- |
| Which alerts recur on this cluster? | 002 §8 on-demand playbook authoring |
| What appears most in *Not checked*? | 001 §12.4 KB audit; whether to build the Prometheus integration (001 §11) |
| Tool calls per conclusion, over time | Whether 002 L3's planner helped or added ceremony |
| Coordinator vs. specialist token split | Whether the model split (001 §3.3) pays |
| Partial vs. full conclusions | Whether the stopping criteria (002 §5.4) are too strict |

A weekly digest over the archive is enough; this does not need a dashboard.

### 2.4 Retention

Session-event retention on Anthropic's side is **not documented** in the material I have. Confirm it
before treating the Console as a system of record. Regardless: the snapshot archive (c) is
authoritative for anything you need to keep, and it is entirely under your control.

---

## 3. Change management (GitOps)

### 3.1 Fast loop and slow loop

The single most useful fact: **CMA agent versioning already gives the semantics you want.** Each
`POST /v1/agents/{id}` creates a new immutable version; **sessions pin their version at creation**.
In-flight sessions keep running on the version they started with; new sessions get the latest.

So for the things you will iterate on constantly, `k8srca sync` *is* a zero-disruption hot reload.
There is nothing to drain and nothing to restart.

| Change | Mechanism | Disruption | Loop |
| --- | --- | --- | --- |
| System prompts, skills, playbooks, KB | `sync` → new agent version | **None** (platform pinning) | **Fast** |
| Coordinator roster / tool routing | `sync` → new agent version | **None** | **Fast** |
| Sandbox image (worker, scripts) | New image tag | **None**, given §3.2 pinning | **Fast** |
| Orchestrator code | Restart | Low — Socket Mode reconnects; streams are per-turn with a documented reconnect path (001 §7.2) | **Fast** |
| Host poller | SIGTERM | **None** — documented to drain in-flight work before exiting | **Fast** |
| k8stools version / tool surface | New image + `sync` | **Requires a drain** — §3.5 | **Slow** |
| Adding an MCP server or specialist | New image + `sync` | Requires a drain (tool manifest changes) | **Slow** |

Keeping these separate is the whole design. Prompt and playbook iteration — which is most of what you
will do — never disrupts anyone.

### 3.2 Version pinning at three layers

Zero-disruption depends on every layer being pinned per session, not resolved per turn.

| Layer | Pinned by | Notes |
| --- | --- | --- |
| **Agent config** | Platform. `sessions.create(agent={type:"agent", id, version})` | Pin **explicitly** — the string shorthand resolves to latest at create time, which is fine, but recording the version in metadata makes sessions reproducible |
| **Skills** | `skills: [{type:"custom", skill_id, version: N}]` | **Pin the number, not `"latest"`.** `version` defaults to `"latest"`; `sync` should write the concrete version it just published, so a session's skill content is determined at create time |
| **Sandbox image** | **You must build this** | Containers are per *turn* (001 §3.2), so `:latest` would swap code mid-conversation. Record the tag in session `metadata` on the first turn; `spawn.sh` reads it back for subsequent turns |

The image pinning is the piece with no platform support, and it needs two halves. Recording the
reference per session is not enough on its own:

```bash
# spawn.sh — resolve the image this session started on, not the newest one
IMAGE_FILE="$WS/.image"
if [[ -f "$IMAGE_FILE" ]]; then IMAGE="$(<"$IMAGE_FILE")"
else IMAGE="${K8SRCA_SANDBOX_IMAGE:?}"; printf '%s' "$IMAGE" > "$IMAGE_FILE"; fi
exec docker run --rm ... "$IMAGE"
```

**A fixed tag makes that recording meaningless.** `k8srca/sandbox:0.1.0` recorded at session start is
rebuilt to different content under the same name, and the session picks up the new code on its next
container having faithfully "pinned" the tag. The reference therefore carries the commit —
`k8srca/sandbox:305ceaf4be11` — built by `k8srca sandbox build`.

Two refinements, both from the principle that the tag must change exactly when the image content does:

- **Only the files the Dockerfile `COPY`s count.** A documentation commit does not change the sandbox
  and must not invalidate its tag, or every docs change forces a rebuild. A test asserts `IMAGE_INPUTS`
  covers every `COPY`, so the two cannot drift apart silently.
- **A dirty tree gets a content hash, not a bare `-dirty` suffix.** Two different uncommitted states
  sharing one tag is the original bug with extra steps.

The poller resolves the reference for the current tree and **refuses to start** if that image is not
built, naming the command — so a code change cannot reach a session without an explicit rebuild.

Same semantics as agent versions: in-flight sessions finish on the code they started with, new ones
get the new build. (When the workspace is externalized for Kubernetes — §4.4 — this moves to session
metadata, which is the better home for it anyway.)

### 3.3 The watcher

Watch a `deploy` branch; **classify the diff and take the cheapest path that covers it**:

| Changed paths | Path | Actions |
| --- | --- | --- |
| `skills/**`, `agents/*.md`, `playbooks/**`, KB source | **Fast** | `k8srca sync` only. No image build, no restart, nobody disturbed |
| `src/**`, `k8srca.yaml`, `docker/Dockerfile.sandbox` | **Image** | `k8srca sandbox build` — the tag follows the commit, so this is also what the poller will demand. New sessions pick it up; in-flight pinned |
| `src/k8srca/slack/**` | **Restart** | Build, roll orchestrator |
| `k8srca.yaml` `mcp:` block, k8stools version, agent `mcp_tools` routing | **Slow** | **Gated on approval** — §3.5 |

Ordering for a combined change: build and tag images → `k8srca sync` → roll poller (SIGTERM, drains)
→ roll orchestrator. No step interrupts a live conversation.

`config_rev` in session metadata (§2.2) is the git SHA at `sync` time, which is what lets you answer
*"which prompt produced this bad answer?"* three weeks later.

> For your own inner loop, none of this is needed. `k8srca sync` from a laptop takes seconds and new
> sessions pick it up immediately. The watcher earns its place for a shared or staging environment.

### 3.4 Restart semantics per process

| Process | Signal | Behavior |
| --- | --- | --- |
| **Host poller** | SIGTERM | Drains in-flight work, then exits cleanly. Documented. Safe to roll at any time |
| **Orchestrator** | SIGTERM | Stop accepting Slack events, let in-flight turns settle, exit. On restart, Socket Mode reconnects and streams re-open per turn with the `events.list()` + dedupe consolidation (001 §7.2). State is in SQLite + CMA, so nothing is lost |
| **Sandbox** | SIGTERM → **cancellation** | Must be wired to *cancellation*, not a kill, or v2 memory uploads are lost. Allow ≥30 s before any hard kill |
| **k8stools** | SIGTERM | Drops live MCP connections. Since connections are per turn, a restart *between* turns is invisible; *during* a turn it fails that turn's tool calls |

### 3.5 The one procedure that needs a drain

Upgrading k8stools changes the tool manifest, and the per-agent manifest hashes (001 §4.3) exist
precisely to refuse a mismatch rather than serve a half-broken toolset. Agent declarations and the
running server must move together, so:

1. Announce; stop creating new sessions (orchestrator replies "maintenance" in Slack).
2. Wait for in-flight sessions to reach `idle` with a non-`requires_action` stop reason.
3. Roll k8stools, run `k8srca sync` (regenerates declarations + hashes), roll the sandbox image.
4. Resume.

Alternatively blue/green: new k8stools under a second network alias, a new sandbox image pointing at
it, and a new agent version — old sessions keep the old stack throughout. Correct, but a lot of
machinery for an operation you will run rarely. Start with the drain.

### 3.6 Rollback

Agent versioning gives rollback for free: pin new sessions to the last-good version
(`{type:"agent", id, version: N}`) while you debug, without touching the agent object. Running
sessions are unaffected either way. `k8srca rollback --agent rca-coordinator --version N` should be a
first-class command, since it is the fastest remedy for a bad prompt change.

Do **not** archive agents as cleanup — archiving is permanent, has no undo, and blocks new sessions
from referencing them.

---

## 4. Kubernetes deployment (follow-on)

### 4.1 Topology

| Option | Verdict |
| --- | --- |
| **Separate ops cluster** | **Recommended.** No circular dependency; natural seam for multi-cluster |
| Same cluster, dedicated namespace | Acceptable for dev/small only, with caveats below |
| Outside Kubernetes (v1 as designed) | Simplest; no circularity at all. Do not rush past this |

The same-cluster risk is not mainly blast radius — it is a **circular dependency at the worst
possible moment**. Node memory pressure evicts the pod that diagnoses node memory pressure. An
overloaded API server breaks both the workload and k8stools. The agent becomes least available
exactly when it is most needed, and it adds its own load to the pressure it is diagnosing.

A dedicated namespace mitigates blast radius but *not* the circularity. If you go that way it needs
its own node pool, Guaranteed QoS, and a high `priorityClass`, or it is among the first things
evicted. Treat it as a development convenience, not a production posture.

A separate ops cluster is also the seam for multi-cluster (deferred in 001): k8stools connects
outward to each monitored cluster's API server with a read-only ServiceAccount token, one deployment
per monitored cluster.

### 4.2 Component mapping

| Component | Kubernetes shape | Notes |
| --- | --- | --- |
| Orchestrator | Deployment, **1 replica** | Socket Mode + SQLite. HA needs Postgres and care with duplicate delivery — not worth it initially |
| Host poller | Deployment, 1+ replicas | Creates Jobs instead of containers — §4.3 |
| Session sandbox | **Job per work item** | gVisor/Kata or GKE Agent Sandbox; §4.5 |
| k8stools | Deployment + Service | In its own namespace with the read-only credential |
| Secrets | k8s Secrets / External Secrets | API key, environment key, Slack tokens, monitored-cluster kubeconfigs |
| Egress control | NetworkPolicy | **Easier than 001 §8.3's iptables approach** — default-deny with explicit allows for `api.anthropic.com` and the k8stools Service |

### 4.3 The two things that do not translate

**(a) `docker run` → Kubernetes Jobs.** There is no Docker socket, and mounting containerd's socket is
a non-starter for a sandbox executing model-authored code. The poller uses the mid-level SDK work
poller and creates a **Job per work item**, injecting the same `ANTHROPIC_*` environment.

Three details that matter:

- **The work secret must not be a plain env var in the Job spec.** `kubectl get job -o yaml` would
  expose it to anyone with read access. Create a short-lived Secret with an `ownerReference` to the
  Job so it is garbage-collected with it, and mount it.
- `ttlSecondsAfterFinished` for cleanup; otherwise completed Jobs accumulate one per turn.
- **Latency gets worse.** Pod scheduling plus image pull can be 5–15 s versus ~1–2 s for `docker run`,
  and this is paid *per turn* (001 §3.2), so it lands on every Slack reply. Mitigate with a pre-pulled
  image (warming DaemonSet) and a small image. Measure before committing.

**(b) The per-session workspace bind-mount.** 001 §3.2 depends on a host directory keyed by session
ID, shared across per-turn containers. Per-turn Jobs land on arbitrary nodes, so this would require an
RWX volume (NFS/CephFS) per session — heavy, fragile, and a new storage dependency.

### 4.4 Prerequisite: externalize the workspace first

The right answer to (b) is not to build shared storage. It is to **stop depending on workspace
persistence** — which is what [002 §9's **L2**](002-investigation-model.md#9-staging-from-prompt-to-method)
already does: the investigation record lives in the orchestrator snapshot (and later a memory store),
not on disk. With L2 done, the workspace becomes disposable: skills re-download per turn, costing a
little latency and nothing else.

> **Do 002 L2 before the Kubernetes move, not after.** Otherwise you build an RWX storage layer to
> preserve state you had already decided to externalize.

This is the one hard ordering constraint between the three documents.

### 4.5 Sandbox hardening in Kubernetes

The sandbox runs model-authored `bash`. In-cluster that needs more than `--cap-drop ALL`:

- **gVisor or Kata** runtime class, or GKE Agent Sandbox (an Anthropic-supported target)
- Restricted PodSecurity admission on the sandbox namespace
- `automountServiceAccountToken: false` — **critical.** A mounted SA token would hand the agent
  cluster credentials and break the "no credentials in the sandbox" layer (001 §8.2), which is one of
  the four legs of the read-only guarantee
- Default-deny NetworkPolicy; allow only the k8stools Service and Anthropic egress
- Non-root, read-only root filesystem, resource limits per Job

The read-only guarantee (001 §8) survives the move intact — but layer 8.2 depends entirely on that
`automountServiceAccountToken: false`, which has no Docker equivalent and is easy to omit.

### 4.6 Staging

| Step | Gate |
| --- | --- |
| 0 | 002 L2 shipped — workspace is disposable (§4.4) |
| 1 | k8stools + NetworkPolicy in the ops cluster; verify read-only access to the monitored cluster |
| 2 | Poller creating Jobs; measure per-turn latency against the Docker baseline |
| 3 | Orchestrator with Postgres or SQLite-on-PVC |
| 4 | Sandbox hardening (§4.5), verified by attempting a write from inside a sandbox |
| 5 | Retire the Docker/host path |

---

## 5. Open questions

1. **Session-event retention on Anthropic's side** (§2.4). Unknown; confirm before relying on the
   Console as a record.
2. **Job latency per turn** (§4.3). If pod start plus image pull lands at 10 s+, the Kubernetes move
   trades a noticeably worse Slack experience for better isolation. Measure at step 2 and be willing
   to stop there.
3. **Orchestrator HA.** One replica with SQLite is fine to start. If it must be HA, Socket Mode with
   multiple connections plus Postgres needs verifying against duplicate event delivery.
4. **How often is the slow loop actually hit?** If k8stools iterates as fast as the skills do, the
   drain in §3.5 will chafe and blue/green becomes worth building. Track it before deciding.
5. **Snapshot archive store.** SQLite alongside the session map is enough for one host; the analytics
   in §2.3(d) may eventually want something queryable. Not a v1 decision.
