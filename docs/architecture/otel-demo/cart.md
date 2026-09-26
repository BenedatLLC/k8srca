Holds the shopping cart. Persists to `valkey-cart`.

**A hard dependency on its datastore.** Losing `valkey-cart` breaks cart
directly, and cart failure blocks `checkout`. Unlike the flag and telemetry
dependencies, this one carries its failure straight through.
