Generates synthetic traffic against `frontend-proxy`.

It is the only source of requests in this cluster. Volume is roughly constant
and there are no external callers, so when it stops the application receives no
traffic at all.
