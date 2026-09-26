Consumes order events from `kafka` and records them for accounting. JVM.

**Asynchronous.** Like `fraud-detection`, it reads a topic rather than serving
requests, so its failure delays bookkeeping and breaks no user-facing flow.

A high restart count here is normal history rather than a current fault: it has
accumulated restarts over the cluster lifetime while staying ready. Judge it by
readiness, not by the counter.
