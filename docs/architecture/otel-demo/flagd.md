Serves feature-flag values over gRPC.

Read by nearly every other service, which gives it the widest fan-in in the
dependency graph. Callers fall back to compiled-in default values when it is
unreachable, so the application keeps serving with default flag behaviour.

Flag values themselves are not recorded in this skill, and changing one leaves no
trace in any workload revision.
