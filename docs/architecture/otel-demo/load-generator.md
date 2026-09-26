Drives synthetic traffic against `frontend-proxy`. There is no other
traffic source in this cluster.

**All load is synthetic and roughly constant.** A hypothesis that depends on a
traffic increase should be checked here first and will usually not survive. If
this is unhealthy, the whole application goes quiet — which looks like a
widespread outage and is not one.
