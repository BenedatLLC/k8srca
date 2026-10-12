# ad

**Kind:** service [derived: callers]  
**Workload:** Deployment  
**Namespace:** otel-demo  
**Connects:** [flagd](flagd.md) via feature-flags (soft)  
**Called by:** [frontend](frontend.md)  

```mermaid
flowchart LR
  ad["ad"]
  ad -.->|feature-flags| flagd["flagd"]
  frontend["frontend"] -->|sync-call| ad
```

## Purpose

Serves advertisement requests for the frontend, its only caller. [derived: callers]

Reads feature flag values from flagd through FLAGD_HOST. [derived: edges] [env: FLAGD_HOST]

## If it fails

Frontend calls to it fail, so pages the frontend builds lose their ad content for users arriving through frontend-proxy. [derived: callers]

## Connections

- **flagd** (feature-flags, soft): named by `FLAGD_HOST` (observed, k8stools)

## Documentation

No documentation page names this component. [derived: docs source]
