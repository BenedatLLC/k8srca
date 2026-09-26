Receives traces and metrics from every instrumented service and
forwards them to the backends.

**Observes the system; is not part of it.** Its failure loses telemetry and
breaks nothing a user would notice. Worth checking early for a different reason:
if it is down, the evidence an investigation would normally rely on may be
missing, and absent telemetry is not evidence of absent activity.
