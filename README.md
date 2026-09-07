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

## Status

Phase 0 and the local half of Phase 1a (001 §13). No Anthropic API calls yet.

- [x] RBAC manifest, k8stools container, compose
- [x] Config schema (`k8srca.yaml`), MCP tool declaration generation, manifest hashing
- [x] Worker-side tool wrapper (prefixed name → unprefixed remote call)
- [ ] `k8srca sync` — agent + environment provisioning
- [ ] Sandbox image, `spawn.sh`, host poller
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

Against a real cluster:

```bash
kubectl apply -f rbac/k8srca-readonly.yaml
docker compose -f docker/compose.yaml up -d k8stools   # mounts $K8SRCA_KUBECONFIG
```
