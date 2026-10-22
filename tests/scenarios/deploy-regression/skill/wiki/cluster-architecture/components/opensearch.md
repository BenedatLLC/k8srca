# opensearch

**Kind:** telemetry [chart: opentelemetry-demo-0.40.7.yaml] [derived: callers]  
**Workload:** StatefulSet  
**Namespace:** default  
**Also reached as:** `opensearch-headless` (headless Service)  
**Called by:** [otel-collector-agent](otel-collector-agent.md)  

```mermaid
flowchart LR
  opensearch["opensearch"]
  otel_collector_agent["otel-collector-agent"] -.->|telemetry| opensearch
```

## Purpose

Receives and stores telemetry exported to it by the OpenTelemetry Collector agent, its only caller. [derived: callers] [derived: edges]

Runs as a single-replica StatefulSet exposing ports 9200, 9300 and 9600, also addressed as opensearch-headless. [chart: opentelemetry-demo-0.40.7.yaml]

## If it fails

The collector agent's export to it has no destination, so that telemetry is not stored; the agent's other exports are unaffected. [derived: callers] [derived: edges]

## Documentation

No documentation page names this component. [derived: docs source]

## Declared configuration

- image: opensearchproject/opensearch:3.4.0 [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests cpu 1000m; memory 100Mi; limits memory 1100Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: readiness_probe, startup_probe [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 9200, 9300, 9600 [chart: opentelemetry-demo-0.40.7.yaml]
