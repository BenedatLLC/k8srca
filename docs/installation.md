# Installation and configuration

From an empty machine to answering questions in Slack. Budget about an hour,
most of it waiting on Slack's app UI and Anthropic's console.

Two companion guides cover the fiddly external setup, and this document links
to them at the right moments:

- [Slack app setup](slack-app-setup.md) — creating the app, scopes, events
- [Cluster setup](cluster-setup.md) — the read-only credential, reachability, reboots

---

## 1. What you are installing

Four processes. They are separate so that no single one holds everything:

| Process | Holds | Does |
| --- | --- | --- |
| **k8stools** (container) | the kubeconfig — **and nothing else does** | Reads the cluster; exposes ~18 read-only tools over MCP |
| **poller** | the environment key | Claims work items; starts one sandbox container per work item |
| **sandbox** (container, ephemeral) | a per-session secret | Runs the agent's tools, including its `bash` |
| **orchestrator** | the API key and Slack tokens | Drives Slack threads and Managed Agents sessions |

The split is the security model, not tidiness. The sandbox runs code the model
wrote; it must not sit next to a cluster credential or an org-scoped API key.
See [design 001 §8](../designs/001-architecture.md).

Anthropic runs the agent loop; tool execution happens on your machine.

---

## 2. Prerequisites

| | Why |
| --- | --- |
| Linux with `/bin/bash` at that exact path | Required by the Anthropic SDK worker helpers |
| Docker + Compose v2 | Sandboxes and k8stools |
| [`uv`](https://docs.astral.sh/uv/) | Python toolchain |
| `kubectl` | Creating the read-only credential |
| An Anthropic account with Managed Agents | The platform this is built on |
| A Slack workspace you can install an app into | Only for the Slack surface |

A cluster is **not** required to get started — k8stools has a `--mock` mode
with realistic static data, and everything up to the Slack step works against
it.

---

## 3. Install

```bash
git clone <your-fork> k8srca && cd k8srca
uv sync --extra dev
uv run pytest          # ~100 tests, no credentials or cluster needed
```

---

## 4. Cluster access

Full detail in [cluster-setup.md](cluster-setup.md). The short version:

```bash
kubectl apply -f rbac/k8srca-readonly.yaml     # read-only ServiceAccount
./rbac/make-reader-kubeconfig.sh               # -> ~/.kube/k8srca-reader.yaml
```

The script prints a verification block. **The three `no` answers are the
point** — if `create pods` says `yes`, you are about to mount a credential that
can write, and layer four of the read-only guarantee is gone:

```
  create  pods: no
  delete  pods: no
  patch   pods: no
  list    pods: yes
  get     pods/log: yes
  get     secrets: no
```

Then point `k8srca.yaml` at it:

```yaml
cluster_access:
  mode: auto
  kubeconfig: ~/.kube/k8srca-reader.yaml
```

`mode: auto` inspects that kubeconfig. If its server is on loopback — an SSH
tunnel, minikube, kind — no container can reach it, and you need two variables
in `.env`:

```bash
K8SRCA_SSH_HOST=bastion.example.com
K8SRCA_SSH_REMOTE=192.168.49.2:8443    # the API server, as the ssh host sees it
```

These live in `.env` rather than `k8srca.yaml` because they are infrastructure
topology, not portable configuration. If the kubeconfig's server is directly
routable, leave them unset and nothing more is needed. Detail in
[cluster-setup.md](cluster-setup.md).

```bash
uv run k8srca up          # network, tunnel if needed, kubeconfig, k8stools
uv run k8srca tools validate
```

`tools validate` is the one that matters: it proves the ClusterRole is
*complete*, not merely restrictive. An incomplete role does not fail at
startup — it fails as `forbidden` partway through an investigation.

---

## 5. Anthropic credentials

```bash
cp .env.example .env
```

Set `ANTHROPIC_API_KEY`. Then provision the control plane:

```bash
uv run k8srca sync --dry-run     # resolve tool routing, write nothing
uv run k8srca sync               # create environment + agents + skills
```

`sync` prints the environment id and tells you the remaining step: open that
environment in the Anthropic console, **Generate environment key**, and put the
`sk-ant-oat01-…` value in `.env` as `ANTHROPIC_ENVIRONMENT_KEY`.

That is a second, narrower credential. It can claim work for one environment
and nothing else, which is why the poller may hold it and the orchestrator may
not.

`sync` is idempotent. Run it after any change to prompts, skills, or tool
routing; it updates agents in place, producing a new version. Sessions pin
their version at creation, so **in-flight investigations are unaffected**.

---

## 6. Slack

Full detail in [slack-app-setup.md](slack-app-setup.md) — it is eight steps and
worth following exactly, because a wrong scope costs a reinstall.

Put the two tokens in `.env`, then:

```bash
uv run k8srca slack check
```

It verifies in the order things break: token → scopes → channel membership →
Socket Mode → **inbound events actually arriving**. That last step is the only
one that proves your event subscriptions are right; everything else can pass
while no events are delivered.

Set `SLACK_ALLOWED_CHANNELS` to one channel while you are getting started. An
empty allowlist means the bot answers anywhere it has been invited, which is
exactly when it is most likely to say something wrong in public.

---

## 7. Run it

Two shapes. **Use the containerised one against anything real.**

### Development

```bash
uv run k8srca worker                       # terminal 1
uv run k8srca session "why is X failing?"  # terminal 2
```

`k8srca worker` runs agent-authored `bash` **directly on your machine** with
your filesystem access. Credentials are scrubbed from its environment, but
that is defence in depth, not isolation.

### Production

```bash
docker build -f docker/Dockerfile.sandbox -t k8srca/sandbox:0.1.0 .
sudo ./docker/egress-rules.sh apply        # start k8stools first
./docker/verify-egress.sh                  # expect PASS

uv run k8srca poller                       # terminal 1
uv run k8srca slack run                    # terminal 2
```

Then `/invite @k8srca` into a channel and mention it. A Slack thread is one
investigation; follow-ups in that thread continue it.

### `k8srca up` does not start the agent

It prepares **cluster access only** — network, tunnel, kubeconfig, k8stools.
It starts neither long-running process, so every step of `up` can be green
while mentioning the bot does nothing at all.

Two processes make a mention do something:

| Process | Without it |
| --- | --- |
| `k8srca poller` (or `worker`) | Sessions start and never progress — no tool ever executes |
| `k8srca slack run` | Nothing listens to Slack; a mention goes nowhere |

`k8srca status` answers this directly:

```
ok    docker network         k8srca-net gateway=172.20.0.1
ok    k8stools               running
ok    tool execution         poller (containerised)
ok    slack orchestrator     running -- mentions will be answered
ok    egress rules           sandbox is confined
```

### Surviving a reboot

```bash
./systemd/install.sh
```

Installs user units for `k8srca up` and the supervised SSH tunnel, and offers
to enable linger — without which user units wait for your first login rather
than starting at boot. It prints the one command it cannot run itself: the
system unit for the egress rules, which needs root.

`k8srca status` keeps warning until linger is on, since the consequence only
shows up at the next reboot.

---

## 8. Configuration reference

### `k8srca.yaml` — version-controlled, portable

| Key | Meaning |
| --- | --- |
| `mcp[].url` | MCP address **as the sandbox sees it** (`http://k8stools:8000/mcp`) |
| `mcp[].sync_url` | Same server **as the host sees it**. `sync` and the CLI run on the host; the sandbox does not |
| `mcp[].prefix` | Namespaces tools in the worker's flat registry. Load-bearing once you add a second server |
| `mcp[].groups` | Named tool sets referenced by agents. `"*"` means every tool |
| `agents.<name>.model` | Per-agent `id` and `effort`. **The cost/context tuning knob** |
| `agents.<name>.mcp_tools` | `{server: group}` — what this agent's model can see |
| `agents.<name>.builtin_tools` | From `read`, `write`, `edit`, `glob`, `grep`, `bash`, `web_search`, `web_fetch` |
| `agents.<name>.roster` | Coordinator only. Agent names plus `self` |
| `cluster_access` | §4. Site-specific tunnel details live in `.env`, not here |
| `sandbox` | Image, network, and per-container resource caps |
| `session.budget_usd` | Hard spend cap. The session pauses at it rather than dying |
| `session.idle_ttl_minutes` | How long a quiet Slack thread keeps its session |

`effort` is **agent configuration only** — set inside a per-session override it
is silently ignored, so it must live here.

### `.env` — secrets, never committed

| Variable | Held by | Notes |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | orchestrator | Org-scoped. **Never on the poller host**, where agent-authored bash could read it |
| `ANTHROPIC_ENVIRONMENT_KEY` | poller, sandbox | Scoped to one environment's work queue |
| `SLACK_BOT_USER_OAUTH_TOKEN` | orchestrator | `xoxb-…` |
| `SLACK_SOCKET_MODE_TOKEN` | orchestrator | `xapp-1-…`, scope `connections:write` |
| `SLACK_ALLOWED_CHANNELS` | orchestrator | Comma-separated. Empty = anywhere it is invited |
| `K8SRCA_WORKSPACE_ID` | orchestrator | Console workspace id, for session deep links |
| `K8SRCA_SSH_HOST` / `K8SRCA_SSH_REMOTE` | host | Tunnel to the API server, when it is not routable from a container |
| `SLACK_SIGNING_SECRET` | — | Unused in Socket Mode; the slot exists because Bolt may want it |

Environment variables always beat `.env`, so you can override one for a single
run without editing the file.

---

## 9. Checking the install

Each command answers one question, in dependency order:

```bash
uv run pytest                     # the code is sound
uv run k8srca up                  # cluster access is wired
uv run k8srca tools validate      # the ClusterRole is complete and routing resolves
uv run k8srca sync --dry-run      # agents plan without writing
uv run k8srca slack check         # Slack can send AND receive
./docker/verify-egress.sh         # the sandbox is confined
uv run k8srca status              # everything is running, mentions answered
uv run k8srca session "list unhealthy pods in default"   # end to end
```

Worth running the whole list after a reboot or a Docker restart. The egress
rules in particular are runtime iptables rules and do not persist by
themselves.

---

## 10. When something is wrong

| Symptom | Look at |
| --- | --- |
| `tools validate` reports `forbidden` | ClusterRole is missing a resource — [cluster-setup.md](cluster-setup.md) |
| Every tool fails, k8stools started fine | Container cannot reach the API server. `k8srca up` will say so |
| Slack sends but never receives | Event subscriptions — [slack-app-setup.md](slack-app-setup.md) §4 |
| Bot silent in a channel | Not invited (`/invite`), or not in `SLACK_ALLOWED_CHANNELS` |
| `verify-egress.sh` says the API server is REACHABLE | Rules not applied, or dropped by a reboot/Docker restart |
| Session idle, nothing pending, no error event | The worker failed the work item. **Only the worker's own log will say why** |
| Agent says it cannot reach the cluster, but `kubectl`/`k9s` work | The container uses a *different* SSH forward than you do. `k8srca status` → `k8srca up` |
| Agent answers but knows nothing about your cluster | Expected: the `cluster-architecture` skill is not built yet |

That second-to-last row is worth knowing about in advance: it is invisible in
the Anthropic console by design, so worker logs are the only evidence.
