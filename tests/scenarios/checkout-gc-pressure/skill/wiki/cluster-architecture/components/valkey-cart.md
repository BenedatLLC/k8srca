# valkey-cart

**Kind:** cache [docs: _index.md] [chart: opentelemetry-demo-0.40.7.yaml] [derived: callers]  
**Workload:** Deployment  
**Namespace:** default  
**Called by:** [cart](cart.md)  

```mermaid
flowchart LR
  valkey_cart["valkey-cart"]
  cart["cart"] -->|cache| valkey_cart
```

## Purpose

The Valkey instance in which the items of the user's shopping cart are stored and retrieved. [docs: _index.md] [derived: callers]

Runs as a single-replica Deployment serving on port 6379. [chart: opentelemetry-demo-0.40.7.yaml]

## If it fails

Cart, its only caller, loses the store it keeps shopping cart items in, which in turn affects the frontend's cart operations and checkout's retrieval of the user cart. [derived: callers] [derived: edges] [docs: _index.md]

## Documentation

No documentation page names this component. [derived: docs source]

## Declared configuration

- image: valkey/valkey:9.0.1-alpine3.23 [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests memory 20Mi; limits memory 20Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: none configured [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 6379 [chart: opentelemetry-demo-0.40.7.yaml]
