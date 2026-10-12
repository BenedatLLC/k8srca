# email

**Kind:** service [derived: callers]  
**Workload:** Deployment  
**Namespace:** otel-demo  
**Connects:** [flagd](flagd.md) via feature-flags (soft)  
**Called by:** [checkout](checkout.md)  

```mermaid
flowchart LR
  email["email"]
  checkout["checkout"] -->|sync-call| email
  email -.->|feature-flags| flagd["flagd"]
```

## Purpose

Sends order confirmation messages on behalf of checkout, its only caller. [derived: callers]

Reads feature flags from flagd through FLAGD_HOST. [derived: edges] [env: FLAGD_HOST]

## If it fails

Checkout's synchronous call to it fails, which affects the order flow checkout serves for the frontend. [derived: callers]

## Connections

- **flagd** (feature-flags, soft): named by `FLAGD_HOST` (observed, k8stools)

## Documentation

No documentation page names this component. [derived: docs source]
