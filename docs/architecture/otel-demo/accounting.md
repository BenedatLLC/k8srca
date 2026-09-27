Records order events for accounting. JVM.

Consumes from `kafka` rather than serving requests. When it is unavailable, order
events accumulate on the topic and bookkeeping falls behind.

Runs with a 120Mi memory limit equal to its request, and the chart sets no JVM
heap flags for it. In this deployment it reaches readiness, serves for roughly
ten to twenty minutes, is terminated for memory, and restarts — which is where
its large restart count comes from. Its behaviour therefore differs from `ad` and
`fraud-detection`, which terminate during startup and never reach readiness at
all.
