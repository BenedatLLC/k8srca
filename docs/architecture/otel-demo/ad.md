Serves a banner advert shown on the product page. JVM (Java).

Called by `frontend` to decorate a page; reads feature flags from `flagd`.
Nothing in the purchase path depends on it, and a page renders without an advert
when it is unavailable.

Runs with a 300Mi memory limit equal to its request, and the chart sets no JVM
heap flags for it.
