Serves a banner advert for the product page. JVM (Java).

**Not on the critical path.** `frontend` calls it to decorate a page; when it is
unavailable the page renders without an advert. An `ad` outage is a cosmetic
degradation, not a checkout failure, and nothing downstream of a purchase
depends on it.

Memory limit equals its request and no JVM heap flags are set, so the heap can
exceed the cgroup during startup — see `_triage.md`. When it is killed with exit
137 before reaching readiness, it has served no traffic, which rules out
load-driven explanations rather than merely weakening them.
