# quote

**Kind:** service [derived: callers]  
**Workload:** Deployment  
**Namespace:** otel-demo  
**Called by:** [shipping](shipping.md)  

```mermaid
flowchart LR
  quote["quote"]
  shipping["shipping"] -->|sync-call| quote
```

## Purpose

Computes shipping quotes for shipping, its only caller. [derived: callers]

## If it fails

Shipping's quote calls fail, which in turn affects the shipping cost shown by frontend and used by checkout. [derived: callers]

## Documentation

No documentation page names this component. [derived: docs source]
