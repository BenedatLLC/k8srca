Consumes order events from `kafka` and records them for accounting. JVM.

**Asynchronous.** Like `fraud-detection`, it reads a topic rather than serving
requests, so a delay here holds up bookkeeping and breaks no user-facing flow.

**It is memory-pressured, and this is a live fault rather than old history.** It
runs on a 120Mi limit, reaches readiness, serves for roughly ten to twenty
minutes, is then OOMKilled and restarts — which is where its large restart count
comes from. So it belongs with `ad` and `fraud-detection` as a service whose
memory limit is too tight, and it is *not* failing the same way they are: they
die during startup and never become ready at all.

That distinction is the one to keep. "Three services are memory-starved here" is
correct. "Three services are crash-looping" is not: this one completes startup
and does useful work between kills, which is why it shows as ready and why its
symptoms are consumer lag rather than an outage.
