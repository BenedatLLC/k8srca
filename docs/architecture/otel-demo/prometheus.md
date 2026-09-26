Stores metrics scraped from the cluster and the application.

**Telemetry, not application.** Losing it breaks dashboards and alerting, not
the demo. It is also the source an investigation would use to check resource
usage over time — when it is unavailable, memory and CPU history cannot be
recovered, and that gap should be reported rather than filled with inference.
