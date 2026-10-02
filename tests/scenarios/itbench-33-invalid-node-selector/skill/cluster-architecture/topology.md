# Service topology

Derived from environment variables that reference other services
(`*_ADDR`, `*_URL`, ...). It reflects what each service is *configured*
to call, which is not the same as what it called during an incident.

Entry points (nothing calls them): accounting, fraud-detection, otel-collector, postgresql, valkey-cart

| service | calls | called by |
| --- | --- | --- |
| `accounting` | `kafka` | — |
| `ad` | `flagd` | `frontend` |
| `cart` | `flagd` | `checkout`, `frontend` |
| `checkout` | `cart`, `currency`, `email`, `flagd`, `kafka`, `payment`, `product-catalog`, `shipping` | `frontend` |
| `currency` | — | `checkout`, `frontend` |
| `email` | `flagd` | `checkout` |
| `flagd` | — | `ad`, `cart`, `checkout`, `email`, `fraud-detection`, `frontend`, `frontend-proxy`, `load-generator`, `payment`, `product-catalog`, `recommendation` |
| `fraud-detection` | `flagd`, `kafka` | — |
| `frontend` | `ad`, `cart`, `checkout`, `currency`, `flagd`, `product-catalog`, `recommendation`, `shipping` | `frontend-proxy` |
| `frontend-proxy` | `flagd`, `frontend`, `image-provider`, `load-generator` | `load-generator` |
| `image-provider` | — | `frontend-proxy` |
| `kafka` | — | `accounting`, `checkout`, `fraud-detection` |
| `load-generator` | `flagd`, `frontend-proxy` | `frontend-proxy` |
| `payment` | `flagd` | `checkout` |
| `product-catalog` | `flagd` | `checkout`, `frontend`, `recommendation` |
| `quote` | — | `shipping` |
| `recommendation` | `flagd`, `product-catalog` | `frontend` |
| `shipping` | `quote` | `checkout`, `frontend` |
