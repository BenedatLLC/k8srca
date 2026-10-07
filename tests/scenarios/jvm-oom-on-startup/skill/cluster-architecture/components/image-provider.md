# image-provider

**Kind:** service [docs: image-provider.md] [chart: opentelemetry-demo-0.40.7.yaml]  
**Workload:** Deployment  
**Namespace:** default  
**Called by:** [frontend-proxy](frontend-proxy.md)  

```mermaid
flowchart LR
  image_provider["image-provider"]
  frontend_proxy["frontend-proxy"] -->|route| image_provider
```

## Purpose

Provides the images which are used in the frontend; the images are statically hosted on an NGINX instance instrumented with the nginx-otel module. [docs: image-provider.md]

Serves on port 8081 and is reached through the frontend proxy. [chart: opentelemetry-demo-0.40.7.yaml] [derived: callers]

## If it fails

Users of the frontend proxy's image path no longer receive the store images used in the frontend; the proxy continues serving its other routes. [derived: callers] [docs: image-provider.md]

## Documentation

> # Image Provider Service
>
>
> This service provides the images which are used in the frontend. The images are
> statically hosted on a NGINX instance. The NGINX server is instrumented with the
> [nginx-otel module](https://github.com/nginxinc/nginx-otel/tree/main).
>
> For details, see the
> [image provider service source](https://github.com/open-telemetry/opentelemetry-demo/blob/main/src/image-provider/).

[docs: image-provider.md]

## Declared configuration

- image: ghcr.io/open-telemetry/demo:2.2.0-image-provider [chart: opentelemetry-demo-0.40.7.yaml]
- replicas: 1 [chart: opentelemetry-demo-0.40.7.yaml]
- resources: requests memory 50Mi; limits memory 50Mi [chart: opentelemetry-demo-0.40.7.yaml]
- probes: none configured [chart: opentelemetry-demo-0.40.7.yaml]
- ports: 8081 [chart: opentelemetry-demo-0.40.7.yaml]
