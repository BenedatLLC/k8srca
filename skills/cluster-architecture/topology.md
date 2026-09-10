# Service topology

Derived from environment variables that reference other services
(`*_ADDR`, `*_URL`, ...). It reflects what each service is *configured*
to call, which is not the same as what it called during an incident.

Entry points (nothing calls them): accounting, fraud-detection, kubernetes, opensearch, opensearch-headless, otel-collector-agent-fxhxp, postgresql, valkey-cart

| service | calls | called by |
| --- | --- | --- |
| `accounting` | `kafka` | — |
| `ad` | `flagd` | `frontend` |
| `cart` | `flagd` | `checkout`, `frontend` |
| `checkout` | `cart`, `currency`, `email`, `flagd`, `kafka`, `payment`, `product-catalog`, `shipping` | `frontend` |
| `currency` | — | `checkout`, `frontend` |
| `email` | `flagd` | `checkout` |
| `flagd` | — | `ad`, `cart`, `checkout`, `email`, `fraud-detection`, `frontend`, `frontend-proxy`, `llm`, `load-generator`, `payment`, `product-catalog`, `product-reviews`, `recommendation` |
| `fraud-detection` | `flagd`, `kafka` | — |
| `frontend` | `ad`, `cart`, `checkout`, `currency`, `flagd`, `product-catalog`, `product-reviews`, `recommendation`, `shipping` | `frontend-proxy` |
| `frontend-proxy` | `flagd`, `frontend`, `grafana`, `image-provider`, `jaeger`, `load-generator` | `load-generator` |
| `grafana` | — | `frontend-proxy` |
| `image-provider` | — | `frontend-proxy` |
| `jaeger` | `otel-collector`, `prometheus` | `frontend-proxy` |
| `kafka` | — | `accounting`, `checkout`, `fraud-detection` |
| `llm` | `flagd` | `product-reviews` |
| `load-generator` | `flagd`, `frontend-proxy` | `frontend-proxy` |
| `otel-collector` | — | `jaeger` |
| `payment` | `flagd` | `checkout` |
| `product-catalog` | `flagd` | `checkout`, `frontend`, `product-reviews`, `recommendation` |
| `product-reviews` | `flagd`, `llm`, `product-catalog` | `frontend` |
| `prometheus` | — | `jaeger` |
| `quote` | — | `shipping` |
| `recommendation` | `flagd`, `product-catalog` | `frontend` |
| `shipping` | `quote` | `checkout`, `frontend` |
