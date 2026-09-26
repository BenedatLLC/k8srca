Key-value store backing `cart`.

**A hard dependency of `cart`**, and therefore an indirect one of `checkout`.
Cart data is ephemeral here; losing this loses carts and does not corrupt
anything downstream.
