# payment

**Kind:** service [derived: callers]  
**Workload:** Deployment  
**Namespace:** otel-demo  
**Connects:** [flagd](flagd.md) via feature-flags (soft)  
**Called by:** [checkout](checkout.md)  

```mermaid
flowchart LR
  payment["payment"]
  checkout["checkout"] -->|sync-call| payment
  payment -.->|feature-flags| flagd["flagd"]
```

## Purpose

Processes payments for checkout, its only caller. [derived: callers]

Reads feature flags from flagd through FLAGD_HOST. [derived: edges] [env: FLAGD_HOST]

## If it fails

Checkout's payment step fails, so orders placed from the frontend through frontend-proxy do not complete. [derived: callers]

## Connections

- **flagd** (feature-flags, soft): named by `FLAGD_HOST` (observed, k8stools)

## Documentation

No documentation page names this component. [derived: docs source]
