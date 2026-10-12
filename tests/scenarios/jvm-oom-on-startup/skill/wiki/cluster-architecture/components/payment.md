# payment

**Kind:** service [docs: payment.md] [chart: opentelemetry-demo-0.40.7.yaml]  
**Workload:** Deployment  
**Namespace:** default  
**Connects:** [flagd](flagd.md) via feature-flags (soft)  
**Called by:** [checkout](checkout.md)  

```mermaid
flowchart LR
  payment["payment"]
  checkout["checkout"] -->|sync-call| payment
  payment -.->|feature-flags| flagd["flagd"]
```

## Purpose

Processes credit card payments for orders and returns an error if the credit card is invalid or the payment cannot be processed. [docs: payment.md]

Charges the given (mocked) credit card with the given amount and returns a transaction ID. [docs: _index.md]

## If it fails

Checkout, its only caller, cannot carry out the payment step of an order. [derived: callers] [docs: payment.md]

## Connections

- **flagd** (feature-flags, soft): named by `FLAGD_HOST` (declared, opentelemetry-demo-0.40.7.yaml), `FLAGD_HOST` (observed, k8stools)

## Documentation

> # Payment Service
>
>
> This service is responsible to process credit card payments for orders. It will
> return an error if the credit card is invalid or the payment cannot be
> processed.
>
> [Payment service source](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/payment/)

[docs: payment.md]

## Declared configuration

- image: ghcr.io/open-telemetry/demo:2.2.0-payment [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests memory 140Mi; limits memory 140Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: none configured [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 8080 [chart: opentelemetry-demo-0.40.7.yaml]
