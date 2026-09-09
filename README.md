# k8srca

Kubernetes root-cause analysis agent built on Claude Managed Agents.
Ask in Slack why a workload is failing; get a ranked, evidence-cited analysis.

**Read-only with respect to the cluster by construction** — it observes and
recommends, never applies. See [designs/001-architecture.md](designs/001-architecture.md) §8.

## Design

| Doc | Covers |
| --- | --- |
| [001 — Architecture](designs/001-architecture.md) | Platform: Managed Agents, self-hosted sandboxes, MCP wiring, Slack, security |
| [002 — Investigation model](designs/002-investigation-model.md) | How the agent reasons: hypotheses, evidence, stopping criteria |
| [003 — Operations](designs/003-operations.md) | Observability, GitOps, Kubernetes deployment |

## Guides

| Guide | Covers |
| --- | --- |
| [Installation](docs/installation.md) | **Start here.** Zero to running, plus the full config reference |
| [Slack app setup](docs/slack-app-setup.md) | Creating the Slack app, scopes, events, verification |
| [Cluster setup](docs/cluster-setup.md) | Read-only ServiceAccount, minting a credential, troubleshooting |

## Status

v1 scope is complete (001 §13, phases 0–3) and running against a real
OpenTelemetry-demo cluster through the containerised path. Both high-risk
architectural assumptions are proven against the live platform: the worker
serves a private MCP server's tools as custom tools (F1), and a subagent
thread's tool calls reach that same worker (F4). All four layers of the
read-only guarantee (001 §8) are verified by measurement, including egress
confinement of the sandbox.

- [x] RBAC manifest, k8stools container, compose
- [x] Config schema (`k8srca.yaml`), MCP tool declaration generation, manifest hashing
- [x] Worker-side tool wrapper (prefixed name → unprefixed remote call)
- [x] Slack app configuration + `k8srca slack check`
- [x] `k8srca sync` — agent + environment provisioning
- [x] Worker (in-process) + `k8srca session` — F1 and F4 proven
- [x] Sandbox image, `spawn.sh`, `k8srca poller` — containerised, verified multi-turn
- [x] Egress rules + verification (applied and verified on a real cluster)
- [x] KB + skills + system prompts (Phase 2 / 002 L0)
- [x] Slack orchestrator (`k8srca slack run`)

## Development

Requires `uv` and Docker. No cluster needed — k8stools has a `--mock` mode
returning realistic static data.

```bash
uv sync --extra dev

# Start a mock MCP server (no cluster, no kubeconfig)
docker compose -f docker/compose.yaml --profile mock up -d k8stools-mock

# Point the config at it for local work
sed -i 's|http://k8stools:8000/mcp|http://127.0.0.1:8009/mcp|' k8srca.yaml

uv run k8srca kb build            # normalize the KB into the skill bundle
uv run k8srca tools list          # the surface as the model will see it
uv run k8srca tools validate      # schema legality, collisions, per-agent routing
uv run pytest                     # unit tests

K8SRCA_TEST_MCP_URL=http://127.0.0.1:8009/mcp uv run pytest tests/test_live_mcp.py
```

`tools validate` runs the same checks `sync` does without touching the
Anthropic API — schema legality, cross-server name collisions, and whether each
agent's tool groups resolve.

Slack:

```bash
cp .env.example .env       # fill in the two tokens; see docs/slack-app-setup.md
uv run k8srca slack check  # verifies token, scopes, channel, and event delivery
```

Provision the agents and environment (needs `ANTHROPIC_API_KEY`):

```bash
uv run k8srca sync --dry-run   # resolve tool routing, make no writes
uv run k8srca sync             # create/update environment + agents
```

`sync` is idempotent: it creates on first run and updates in place after,
producing a new agent version each time something changes. Sessions pin their
version at creation, so in-flight investigations are unaffected. Resolved IDs
land in `.k8srca/state.json` (gitignored).

Run a session (needs a worker running in another terminal):

```bash
uv run k8srca worker                      # polls the work queue, serves MCP tools
uv run k8srca session "why is payment-api crash-looping in prod?"
```

`k8srca worker` runs the worker in-process on the host — the development
shape. It runs agent-authored bash directly on your machine with your
filesystem access; credentials are scrubbed from its environment, but that is
defence in depth, not isolation. **Use `k8srca poller` against a real
cluster.** The production shape runs a container per work item:

```bash
docker network create k8srca-net
docker compose -f docker/compose.yaml --profile mock up -d k8stools-mock
docker build -f docker/Dockerfile.sandbox -t k8srca/sandbox:0.1.0 .
sudo ./docker/egress-rules.sh apply    # restrict sandbox egress (001 §8.3)
./docker/verify-egress.sh              # confirm

uv run k8srca poller                   # spawns one sandbox per work item
uv run k8srca status                   # is a mention going to be answered?
uv run k8srca session "..."
uv run k8srca session --resume <id> "follow-up"
```

Run the whole thing:

```bash
# terminal 1 — tool execution
uv run k8srca worker            # or: uv run k8srca poller (containerised)
# terminal 2 — Slack
uv run k8srca slack run
```

Then `/invite @k8srca` into a channel and mention it. A Slack thread is one
investigation; follow-ups in that thread continue it. Set
`SLACK_ALLOWED_CHANNELS` while developing so it only answers where you expect.

## Against a real cluster

```bash
uv run k8srca up          # network, tunnel, kubeconfig, k8stools -- idempotent
./systemd/install.sh      # and again after every reboot, automatically
```


```bash
# 1. Least-privilege credential (design 001 §8.4). Do this FIRST.
kubectl apply -f rbac/k8srca-readonly.yaml
#    ...then build a kubeconfig for the k8srca-reader ServiceAccount and point
#    K8SRCA_KUBECONFIG at it.

# 2. Run k8stools against it
docker compose -f docker/compose.yaml up -d k8stools
```

> **The kubeconfig you mount is the whole ballgame.** k8stools exposes no
> mutating tools and the sandbox never sees the credential, so an
> over-privileged kubeconfig is not immediately exploitable — but it removes
> the RBAC backstop that makes the read-only guarantee hold under failure of
> the other layers. Check with
> `kubectl auth can-i create pods -A --kubeconfig <file>`; it should say `no`.

Validated end to end against a real OpenTelemetry-demo cluster: 27 pods, two
genuinely broken services. See `designs/001-architecture.md` §13.
