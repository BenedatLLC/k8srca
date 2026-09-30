Records order events for accounting. JVM.

Consumes from `kafka` rather than serving requests. When it is unavailable, order
events accumulate on the topic and bookkeeping falls behind.

Runs with a 120Mi memory limit equal to its request, and the chart sets no JVM
heap flags for it.
