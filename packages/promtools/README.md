# promtools

Read-only Prometheus for LLM agents: an MCP server whose results say what they
cannot show, and a capture of raw samples for replaying a cluster's metrics.
Built for k8srca (its design 009), usable on its own; it never imports k8srca.

```bash
promtools serve   --url http://prometheus:9090 --port 8000       # MCP, streamable HTTP, /mcp
promtools capture --url http://prometheus:9090 --out cap/ -n default --end 2026-10-18T19:42:36Z
promtools report  --url http://prometheus:9090 restarts default 'ad-.*'
```

**Read-only by construction.** `client.py` sends GET requests only, and only to
a fixed list of query endpoints. The admin API, remote write, OTLP ingest and
the lifecycle endpoints are not on it, whatever the server enables. A test
parses the package to keep it that way.

**Results carry notes.** A raw result reads wrong in predictable ways:
- a pod-level cAdvisor series has no `container` label;
- a pod cgroup's OOM counter does not count container kills;
- a 1-minute scrape misses 8-second lives;
- a range that starts before retention shows retention's edge, not an onset;
- kube-state-metrics scraped by two jobs double-counts.

Each result says which of these applies (`notes.py`).

**Tools:** `query`, `query_range`, `series`, `label_values`, `metadata`,
`targets`, `runtime_info`, and two reports. `memory_report` gives working-set
peaks against the limit. `restart_report` gives restart cadence, gaps,
whether the onset is in the window, and termination reasons.

**Capture** (`capture.py`) exports an allowlist by range-selector queries in
six-hour pieces, with node labels dropped, to gzipped OpenMetrics. That
builds into blocks with:

```bash
promtool tsdb create-blocks-from openmetrics metrics.om /path/to/data
```
