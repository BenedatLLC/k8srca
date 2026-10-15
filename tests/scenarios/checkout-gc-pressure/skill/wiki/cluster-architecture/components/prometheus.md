# prometheus

**Kind:** telemetry [chart: opentelemetry-demo-0.40.7.yaml] [derived: callers]  
**Workload:** Deployment  
**Namespace:** default  
**Called by:** [jaeger](jaeger.md), [otel-collector-agent](otel-collector-agent.md)  

```mermaid
flowchart LR
  prometheus["prometheus"]
  jaeger["jaeger"] -->|sync-call| prometheus
  otel_collector_agent["otel-collector-agent"] -.->|telemetry| prometheus
```

## Purpose

Receives metrics exported by the OpenTelemetry Collector agent and is queried by Jaeger. [derived: callers] [derived: edges]

Serves on port 9090 as a single-replica Deployment. [chart: opentelemetry-demo-0.40.7.yaml]

## If it fails

The collector agent's metrics export has no destination, and Jaeger's calls to it do not return data. [derived: callers] [derived: edges]

## Documentation

No documentation page names this component. [derived: docs source]

## Declared configuration

- image: quay.io/prometheus/prometheus:v3.9.0 [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests memory 400Mi; limits memory 400Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: liveness_probe, readiness_probe [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 9090 [chart: opentelemetry-demo-0.40.7.yaml]
