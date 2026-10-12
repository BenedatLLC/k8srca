# System index

What this deployment is made of and how its parts connect. This wiki holds
no live state: for what is running now, ask the cluster.

## Overview

This is a microservice web-shop deployment whose components reach one another over addresses supplied as environment variables (for example CART_ADDR, PRODUCT_CATALOG_ADDR, KAFKA_ADDR). [derived: edges] [env: CART_ADDR] [env: PRODUCT_CATALOG_ADDR] [env: KAFKA_ADDR]

All externally reachable traffic enters through frontend-proxy, which forwards to the frontend web application, the image-provider, the flagd feature-flag host and UI, and the load-generator web interface. [derived: edges] [env: FRONTEND_HOST] [env: IMAGE_PROVIDER_HOST] [env: FLAGD_HOST] [env: FLAGD_UI_HOST] [env: LOCUST_WEB_HOST]

The only declared traffic source inside the deployment is load-generator, which drives requests at frontend-proxy via LOCUST_HOST. [derived: edges] [derived: callers] [env: LOCUST_HOST]

Order flow fans out from frontend to ad, cart, checkout, currency, product-catalog, recommendation and shipping; checkout in turn calls cart, currency, email, payment, product-catalog and shipping and publishes to kafka, which accounting and fraud-detection consume. [derived: edges] [derived: callers]

Persistent and keyed state lives in postgresql (used by accounting) and valkey-cart (used by cart); most services read feature flags from flagd. [derived: edges] [derived: callers] [env: DB_CONNECTION_STRING] [env: VALKEY_ADDR] [env: FLAGD_HOST]

## Structure

```mermaid
flowchart LR
  subgraph group_cache["cache"]
    valkey_cart["valkey-cart"]
  end
  subgraph group_datastore["datastore"]
    postgresql["postgresql"]
  end
  subgraph group_feature_flags["feature-flags"]
    flagd["flagd"]
  end
  subgraph group_gateway["gateway"]
    frontend_proxy["frontend-proxy"]
  end
  subgraph group_load_generator["load-generator"]
    load_generator["load-generator"]
  end
  subgraph group_queue["queue"]
    kafka["kafka"]
  end
  subgraph group_service["service"]
    accounting["accounting"]
    ad["ad"]
    cart["cart"]
    checkout["checkout"]
    currency["currency"]
    email["email"]
    fraud_detection["fraud-detection"]
    frontend["frontend"]
    image_provider["image-provider"]
    payment["payment"]
    product_catalog["product-catalog"]
    quote["quote"]
    recommendation["recommendation"]
    shipping["shipping"]
  end
  subgraph group_telemetry["telemetry"]
    otel_collector["otel-collector"]
  end
  accounting -->|async-event| kafka
  accounting -->|datastore| postgresql
  ad -.->|feature-flags| flagd
  cart -.->|feature-flags| flagd
  cart -->|cache| valkey_cart
  checkout -->|sync-call| cart
  checkout -->|sync-call| currency
  checkout -->|sync-call| email
  checkout -.->|feature-flags| flagd
  checkout -->|async-event| kafka
  checkout -->|sync-call| payment
  checkout -->|sync-call| product_catalog
  checkout -->|sync-call| shipping
  email -.->|feature-flags| flagd
  fraud_detection -.->|feature-flags| flagd
  fraud_detection -->|async-event| kafka
  frontend -->|sync-call| ad
  frontend -->|sync-call| cart
  frontend -->|sync-call| checkout
  frontend -->|sync-call| currency
  frontend -.->|feature-flags| flagd
  frontend -->|sync-call| product_catalog
  frontend -->|sync-call| recommendation
  frontend -->|sync-call| shipping
  frontend_proxy -->|route| flagd
  frontend_proxy -->|route| frontend
  frontend_proxy -->|route| image_provider
  frontend_proxy -->|route| load_generator
  load_generator -.->|feature-flags| flagd
  load_generator -->|load| frontend_proxy
  payment -.->|feature-flags| flagd
  product_catalog -.->|feature-flags| flagd
  recommendation -.->|feature-flags| flagd
  recommendation -->|sync-call| product_catalog
  shipping -->|sync-call| quote
```

## Components

**cache:** [valkey-cart](components/valkey-cart.md)

**datastore:** [postgresql](components/postgresql.md)

**feature-flags:** [flagd](components/flagd.md)

**gateway:** [frontend-proxy](components/frontend-proxy.md)

**load-generator:** [load-generator](components/load-generator.md)

**queue:** [kafka](components/kafka.md)

**service:** [accounting](components/accounting.md), [ad](components/ad.md), [cart](components/cart.md), [checkout](components/checkout.md), [currency](components/currency.md), [email](components/email.md), [fraud-detection](components/fraud-detection.md), [frontend](components/frontend.md), [image-provider](components/image-provider.md), [payment](components/payment.md), [product-catalog](components/product-catalog.md), [quote](components/quote.md), [recommendation](components/recommendation.md), [shipping](components/shipping.md)

**telemetry:** [otel-collector](components/otel-collector.md)

Built from the sources in [sources.md](sources.md); what changed between
builds is in [log.md](log.md).
