# flagd

**Kind:** feature-flags [docs: flagd-ui.md] [derived: callers] [chart: opentelemetry-demo-0.40.7.yaml]  
**Workload:** Deployment  
**Namespace:** default  
**Called by:** [ad](ad.md), [cart](cart.md), [checkout](checkout.md), [email](email.md), [fraud-detection](fraud-detection.md), [frontend](frontend.md), [frontend-proxy](frontend-proxy.md), [llm](llm.md), [load-generator](load-generator.md), [payment](payment.md), [product-catalog](product-catalog.md), [product-reviews](product-reviews.md), [recommendation](recommendation.md)  

```mermaid
flowchart LR
  flagd["flagd"]
  ad["ad"] -.->|feature-flags| flagd
  cart["cart"] -.->|feature-flags| flagd
  checkout["checkout"] -.->|feature-flags| flagd
  email["email"] -.->|feature-flags| flagd
  fraud_detection["fraud-detection"] -.->|feature-flags| flagd
  frontend["frontend"] -.->|feature-flags| flagd
  frontend_proxy["frontend-proxy"] -->|route| flagd
  llm["llm"] -.->|feature-flags| flagd
  load_generator["load-generator"] -.->|feature-flags| flagd
  payment["payment"] -.->|feature-flags| flagd
  product_catalog["product-catalog"] -.->|feature-flags| flagd
  product_reviews["product-reviews"] -.->|feature-flags| flagd
  recommendation["recommendation"] -.->|feature-flags| flagd
```

## Purpose

Holds the feature flags that alter the behavior of the demo environment, which users can toggle and edit through the flagd UI. [docs: flagd-ui.md]

Is read by ad, cart, checkout, email, fraud-detection, frontend, llm, load-generator, payment, product-catalog, product-reviews and recommendation. [derived: callers]

## If it fails

Its many callers read flag values with defaults on failure, so they continue without flag-driven behavior changes. [derived: callers] [derived: edges]

The feature flag interface reached through the frontend proxy route is unavailable to users of that route, while the proxy keeps serving its other paths. [derived: callers] [derived: edges] [docs: flagd-ui.md]

## Documentation

> # Flagd-UI Service
>
>
> This service acts as a frontend where users can toggle and edit feature flags to
> alter the behavior of the demo environment.
>
> [Flagd-UI service source](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/flagd-ui/)

[docs: flagd-ui.md]

## Declared configuration

- image: ghcr.io/open-feature/flagd:v0.12.9 [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests memory 75Mi; limits memory 75Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: none configured [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 8013, 8016, 4000 [chart: opentelemetry-demo-0.40.7.yaml]
