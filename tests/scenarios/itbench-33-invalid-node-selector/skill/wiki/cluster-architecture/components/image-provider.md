# image-provider

**Kind:** service [derived: callers]  
**Workload:** Deployment  
**Namespace:** otel-demo  
**Called by:** [frontend-proxy](frontend-proxy.md)  

```mermaid
flowchart LR
  image_provider["image-provider"]
  frontend_proxy["frontend-proxy"] -->|route| image_provider
```

## Purpose

Serves product images to users through the frontend-proxy route, its only caller. [derived: callers]

## If it fails

The image route on frontend-proxy stops serving, so users of that proxy get no product images while the proxy's other routes keep working. [derived: callers] [derived: edges]

## Documentation

No documentation page names this component. [derived: docs source]
