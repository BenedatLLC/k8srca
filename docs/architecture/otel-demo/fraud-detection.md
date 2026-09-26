Consumes order events from `kafka` and scores them for fraud. JVM (Kotlin).

**Asynchronous and off the request path.** Nothing waits on it to complete a
purchase; it reads a topic after the fact. An outage means fraud scoring falls
behind, and the only visible symptom is consumer lag — orders still complete.

Shares the JVM-with-a-tight-memory-limit shape with `ad` and `accounting`
(`_triage.md`). Sharing a cause with another service is not the same as being
caused by it: neither calls the other.
