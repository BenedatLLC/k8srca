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

## Setup guides

| Guide | Covers |
| --- | --- |
| [Slack app setup](docs/slack-app-setup.md) | Creating the Slack app, scopes, events, verification |

## Status

Phases 0, 1a and 1b (001 §13). Both high-risk architectural assumptions are
proven end to end against the live platform: the worker serves a private MCP
server's tools as custom tools (F1), and a subagent thread's tool calls reach
that same worker (F4).

- [x] RBAC manifest, k8stools container, compose
- [x] Config schema (`k8srca.yaml`), MCP tool declaration generation, manifest hashing
- [x] Worker-side tool wrapper (prefixed name → unprefixed remote call)
- [x] Slack app configuration + `k8srca slack check`
- [x] `k8srca sync` — agent + environment provisioning
- [x] Worker (in-process) + `k8srca session` — F1 and F4 proven
- [ ] Sandbox image, `spawn.sh`, containerised per-turn worker
- [ ] Egress filtering (001 §8.3)
- [ ] KB + skills (Phase 2 / 002 L0)
- [ ] Slack orchestrator

## Development

Requires `uv` and Docker. No cluster needed — k8stools has a `--mock` mode
returning realistic static data.

```bash
uv sync --extra dev

# Start a mock MCP server (no cluster, no kubeconfig)
docker compose -f docker/compose.yaml --profile mock up -d k8stools-mock

# Point the config at it for local work
sed -i 's|http://k8stools:8000/mcp|http://127.0.0.1:8009/mcp|' k8srca.yaml

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

`k8srca worker` runs the worker in-process on the host, which is the
development shape. Production runs one container per turn — see 001 §3.2.

Against a real cluster:

```bash
kubectl apply -f rbac/k8srca-readonly.yaml
docker compose -f docker/compose.yaml up -d k8stools   # mounts $K8SRCA_KUBECONFIG
```
