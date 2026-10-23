# grafana

**Kind:** ui [chart: opentelemetry-demo-0.40.7.yaml] [derived: callers]  
**Workload:** Deployment  
**Namespace:** default  
**Called by:** [frontend-proxy](frontend-proxy.md)  

```mermaid
flowchart LR
  grafana["grafana"]
  frontend_proxy["frontend-proxy"] -->|route| grafana
```

## Purpose

An interface reached only through the frontend proxy, its single caller. [derived: callers] [derived: edges]

Deployed as a single-replica Deployment serving on port 80. [chart: opentelemetry-demo-0.40.7.yaml]

## If it fails

Users of the Grafana path on the frontend proxy lose that interface; the proxy continues serving its other routes. [derived: callers] [derived: edges]

## Documentation

No documentation page names this component. [derived: docs source]

## Declared configuration

- image: quay.io/kiwigrid/k8s-sidecar:2.2.1 [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests cpu 100m; memory 100Mi; limits cpu 100m; memory 100Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: none configured [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 80 [chart: opentelemetry-demo-0.40.7.yaml]
