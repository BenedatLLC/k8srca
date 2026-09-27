Stores metrics scraped from the cluster and the application, and backs the
Grafana dashboards.

Nothing in the application request path depends on it. It is the only record of
resource usage over time in this cluster; when it is unavailable, that history is
not recorded and cannot be recovered afterwards.
