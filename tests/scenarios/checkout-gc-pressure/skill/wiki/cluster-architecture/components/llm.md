# llm

**Kind:** service [chart: opentelemetry-demo-0.40.7.yaml] [derived: callers]  
**Workload:** Deployment  
**Namespace:** default  
**Connects:** [flagd](flagd.md) via feature-flags (soft)  
**Called by:** [product-reviews](product-reviews.md)  

```mermaid
flowchart LR
  llm["llm"]
  llm -.->|feature-flags| flagd["flagd"]
  product_reviews["product-reviews"] -->|sync-call| llm
```

## Purpose

Serves requests on port 8000 as a single-replica Deployment with no declared resource limits. [chart: opentelemetry-demo-0.40.7.yaml]

Its only caller is product-reviews. [derived: callers] [derived: edges]

## If it fails

Product-reviews, its only caller, loses the dependency it calls synchronously. [derived: callers] [derived: edges]

## Connections

- **flagd** (feature-flags, soft): named by `FLAGD_HOST` (declared, opentelemetry-demo-0.40.7.yaml), `FLAGD_HOST` (observed, k8stools)

## Documentation

No documentation page names this component. [derived: docs source]

## Declared configuration

- image: ghcr.io/open-telemetry/demo:2.2.0-llm [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests None; limits None [chart: opentelemetry-demo-0.40.7.yaml]
- probes: none configured [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 8000 [chart: opentelemetry-demo-0.40.7.yaml]
