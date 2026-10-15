# ad

**Kind:** service [docs: ad.md] [chart: opentelemetry-demo-0.40.7.yaml]  
**Workload:** Deployment  
**Namespace:** default  
**Connects:** [flagd](flagd.md) via feature-flags (soft)  
**Called by:** [frontend](frontend.md)  

```mermaid
flowchart LR
  ad["ad"]
  ad -.->|feature-flags| flagd["flagd"]
  frontend["frontend"] -->|sync-call| ad
```

## Purpose

Determines appropriate ads to serve to users based on context keys; the ads are for products available in the store. [docs: ad.md]

Serves requests on port 8080 as a single-replica Deployment. [chart: opentelemetry-demo-0.40.7.yaml]

## If it fails

The frontend, its only caller, loses ad results for the pages it renders and for API clients of those pages. [derived: callers] [derived: edges]

## Connections

- **flagd** (feature-flags, soft): named by `FLAGD_HOST` (declared, opentelemetry-demo-0.40.7.yaml), `FLAGD_HOST` (observed, k8stools)

## Documentation

> # Ad Service
>
>
> This service determines appropriate ads to serve to users based on context keys.
> The ads will be for products available in the store.
>
> [Ad service source](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/ad/)

[docs: ad.md]

## Declared configuration

- image: ghcr.io/open-telemetry/demo:2.2.0-ad [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests memory 300Mi; limits memory 300Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: none configured [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 8080 [chart: opentelemetry-demo-0.40.7.yaml]
