Receives traces and metrics from the instrumented services and forwards
them to the storage backends.

Nothing in the application request path depends on it. When it is unavailable the
application behaves normally and its telemetry is not recorded — including the
telemetry covering that period.
