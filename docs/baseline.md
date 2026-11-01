# The current baseline

What the agent scores on the targeted scenario suite (`tests/scenarios/`), as
recorded in `tests/scenarios/baseline.json`. Update this page when a new
baseline is taken.

**Taken:** 2026-11-01, commit `601e70d`. The agent code is `c817681`: design
012 closed, plus the fixes for #12 (tool results up to 8 MiB) and #13
(thousands separators in the answer check). This is the code tagged
`holdout-v1`.

**How:** sandbox image `c8176813064c`, k8stools 4.6.1, the working tree's
`k8s-rca` skill. n=5 for itbench-31 and itbench-80, n=3 for the other 13.
Each answer was graded on its `checklist.yaml` (010 §7) during the run; the
figures below are those gradings. A separate three-way grading of the same
answers agreed 99.2% over 2,028 gradings, with no item split on more than one
answer, and its majority verdicts differ from these by at most about 0.3
items per scenario.

| scenario | n | diagnosis | checklist (mean) | cause | ruled out | traps | gaps | next | tool calls | $/run |
|---|---|---|---|---|---|---|---|---|---|---|
| pvc-pending-no-storageclass | 3 | 3/3 | 10.0/10 | 9/9 | 3/3 | 9/9 | 6/6 | 3/3 | 6–8 | 0.08 |
| liveness-probe-wrong-port | 3 | 3/3 | 8.7/9 | 11/12 | 3/3 | 6/6 | 3/3 | 3/3 | 10–19 | 0.19 |
| nothing-is-wrong | 3 | 3/3 | 7.7/8 | 6/6 | 3/3 | 8/9 | 3/3 | 3/3 | 17–31 | 0.37 |
| deploy-regression | 3 | 3/3 | 13.0/14 | 12/12 | 6/6 | 13/15 | 5/6 | 3/3 | 11–37 | 0.26 |
| jvm-oom-on-startup | 3 | 3/3 | 13.0/14 | 7/9 | 9/9 | 17/18 | 3/3 | 3/3 | 13–24 | 0.27 |
| jvm-oom-with-metrics | 3 | 3/3 | 13.0/14 | 12/12 | 9/9 | 12/15 | 3/3 | 3/3 | 11–18 | 0.23 |
| image-pull-typo | 3 | 3/3 | 8.3/9 | 9/9 | 1/3 | 9/9 | 3/3 | 3/3 | 6–9 | 0.09 |
| itbench-33-invalid-node-selector | 3 | 3/3 | 11.0/12 | 9/9 | 8/9 | 9/9 | 4/6 | 3/3 | 11–23 | 0.20 |
| downstream-latency | 3 | 3/3 | 10.0/11 | 9/9 | 0/3 | 12/12 | 6/6 | 3/3 | 48–57 | 0.66 |
| itbench-105-invalid-command | 3 | 3/3 | 18.0/20 | 12/18 | 9/9 | 24/24 | 6/6 | 3/3 | 9–10 | 0.10 |
| itbench-24-kafka-port-change | 3 | 3/3 | 14.3/16 | 12/15 | 7/9 | 15/15 | 6/6 | 3/3 | 29–48 | 0.59 |
| checkout-gc-pressure | 3 | 3/3 | 11.0/13 | 11/12 | 0/3 | 15/15 | 4/6 | 3/3 | 40–49 | 0.58 |
| itbench-31-network-policy-decoy | 5 | 3/5 | 11.6/14 | 8/15 | 6/10 | 25/25 | 14/15 | 5/5 | 17–32 | 0.40 |
| itbench-102-memory-quota | 3 | 3/3 | 12.7/17 | 8/12 | 3/9 | 15/18 | 9/9 | 3/3 | 15–24 | 0.37 |
| itbench-80-kafka-two-brokers | 5 | 1/5 | 12.0/21 | 17/35 | 8/20 | 23/25 | 11/15 | 1/10 | 21–66 | 0.66 |
| **total** | **49** | **43/49** | **84% of items** | | | | | | | **$17.25** |

**Reading it:**
- **diagnosis** is the scenario's gate: runs that met every checklist item
  marked `diagnosis: true`.
- **checklist** is the mean number of items met per run, out of the
  checklist's size.
- **The group columns** sum the items met over all runs. For example, 3 runs
  × 3 ruled-out items = 9.
- **At n=3, one item missed in one run** moves a scenario's mean by 0.3. A
  difference of that size from the previous baseline is noise.

**Where it is weakest:**
- **Ruling things out:** checkout-gc-pressure and downstream-latency 0/3,
  image-pull-typo 1/3, itbench-102 3/9, itbench-80 8/20, itbench-31 6/10.
  Design 013 targets 31's policy rule-out, 80's checkout config and 102's ad
  fault.
- **itbench-80** meets 57% of its items and its next steps in 1 of 10 runs:
  the two-brokers miss (#14).
- **Repeat trap and gap misses:** deploy-regression's "clean exit", and
  jvm-oom-with-metrics dating the onset to an old rollout.

To compare a new run against this baseline: `uv run k8srca scenario compare F`.
