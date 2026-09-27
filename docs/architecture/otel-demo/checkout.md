Completes a purchase. Calls `cart`, `currency`, `email`, `payment` and
`shipping` synchronously, then produces an order event to `kafka`.

It has the most synchronous dependencies of any service here, and cannot complete
a purchase without them. The order event it produces is consumed asynchronously,
so a stalled consumer does not block it.
