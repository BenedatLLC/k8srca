# Triage notes for this cluster

Operator notes, as stale as the last edit. See `_system.md` for what the system
is and which dependencies are load-bearing.

## The JVM services and their memory limits

`ad`, `fraud-detection` and `accounting` are JVM workloads. The chart sets
modest memory limits and does not set JVM heap flags, so the JVM sizes its heap
from what the cgroup reports and can exceed the limit during startup. When a JVM
service here is killed with exit code 137, the memory limit is the first thing
to check and the limit being equal to the request is the specific shape to look
for.

This is a property of the *deployment*, not of the code. Two services sharing it
share a cause without either causing the other.

## What this cluster is not

- Not multi-tenant, and not multi-namespace: everything is in `default`.
- Not autoscaled. Replica counts are what the chart declares.
- Not highly available. Most workloads are a single replica, so a pod failure is
  a service outage, and there is no partial-degradation story to look for.
