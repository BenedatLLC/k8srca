Orchestrates a purchase: cart, currency, email, payment, shipping, then
produces an order event to `kafka`.

**The critical path.** This is the service whose failure actually costs
something in a real deployment, and the one worth prioritising. It has the most
synchronous dependencies of any service here, so a checkout failure is more
often a symptom of one of them than a fault of its own — work down the
dependency graph before suspecting checkout itself.
