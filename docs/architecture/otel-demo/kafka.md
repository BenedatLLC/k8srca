Message bus between `checkout` (producer) and `accounting` /
`fraud-detection` (consumers).

**Asynchronous, so its failure is delayed and misdirected.** The browse-and-buy
path keeps working without it; the consumers stall. Symptoms surface in the
consumers minutes later and look like consumer faults. When a consumer is
unhealthy, check kafka before concluding the consumer is at fault.
