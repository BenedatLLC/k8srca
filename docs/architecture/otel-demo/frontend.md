The web application. Calls most of the product-facing services
synchronously and is reached through `frontend-proxy`.

It depends on many services to render a complete page, and renders partial pages
when optional ones such as `ad` are unavailable.
