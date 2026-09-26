Serves feature-flag values over gRPC to nearly every other service.

**The widest fan-in in the graph, and a soft dependency.** Callers fall back to
default flag values when it is unreachable, so `arch_query.py blast flagd`
overstates the consequence of losing it: many services read it, few fail
without it. Treat a wide blast radius here as reach, not impact.

Flag *values* are invisible to this skill and to ReplicaSet history. A flag flip
changes behaviour while leaving no trace in any workload revision, so "nothing
was deployed recently" does not rule out a configuration change.
