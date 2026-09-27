Scores order events for fraud. JVM (Kotlin).

Consumes from `kafka` rather than serving requests, so nothing waits on it to
complete a purchase. When it is unavailable, order events accumulate on the topic
and fraud scoring falls behind.

Runs with a 300Mi memory limit equal to its request, and the chart sets no JVM
heap flags for it.
