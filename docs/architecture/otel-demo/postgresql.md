Relational datastore for the services that persist state.

**A hard dependency.** Its dependants fail directly when it is unavailable. Note
that the deployed image is a stock `postgres` image rather than a demo image, so
a version comparison against the chart is a substitution rather than drift.
