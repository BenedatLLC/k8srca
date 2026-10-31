# 012 Log evidence the agent can't see

**Status:** in progress, approved 2026-10-31 (owner) with §6's
recommendations: the coordinator gets the pattern tool in step 2, and
production moves to 4.6.x after step 2. Step 1 ran 2026-10-31 on 4.6.1
and is closed; step 2 was rewritten at its checkpoint (§4).
Issue #11; the k8stools side is k8stools#34 and #35, released in 4.6.0.

## 1. Problem

The agent misses evidence that is in a log it can read, because the tools
show only part of the log and say nothing about the rest. Issue #11 has the
full account; in short:

- **Back in time (#10 pattern 1).**
  - Tails are short: the workload report's 20 lines, the log tool's 1000,
    and `grep` searched only that tail.
  - No result said where the log started, so a tail that begins after a
    change reads as the whole story, and the last line (capture time) reads
    as an error burst ending.
  - Cost: itbench-80, diagnosis 0/3 (checkout's write failures predate
    every tail read); itbench-31, diagnosis 0/3 (the 503 trickle runs from
    the log's first minute, and every run called the last three a one-off);
    102's evidence was earlier than any tail.
- **Across pods and line width (#10 pattern 6).** The workload report showed
  one pod's log and cut lines at 300 characters. In 80 the evidence was on
  the other Kafka pod, and cut off on the one shown.

## 2. What changes

**k8stools 4.6.0 (released):**
- `get_log_pattern_summary`: match counts per time bucket over the whole
  retained log, gaps collapsed, first and last match, samples, the log's
  span. In `investigate` and `all`.
- The workload report's `grep` searches every pod's whole log (up to 10
  pods), with a match count per pod; a cut line keeps its match.
- Without `grep`, other pods get a 5-line tail.
- A cut tail notes where the log starts and names the pattern tool;
  `LogTail` has `lines_from` and `log_starts`.
- Captures record `log_starts`.

**k8srca:**
1. Upgrade to 4.6.0. In step 1 the replay uses a locally built
   `k8srca/k8stools:4.6.0` (`K8SRCA_SCENARIO_K8STOOLS_IMAGE`) and imports run
   4.6.0 through `uvx`, so the pins stay at 4.5.0 and `k8srca up` keeps
   production where it is (§6). The pins (`pyproject.toml`,
   `tests/test_packaging.py`, the Dockerfile, compose,
   `docs/holdout/requirements.md`) move with step 2's baseline.
2. Re-import the five ITBench captures made with `--from-itbench` (24, 31,
   80, 102, 105) with 4.6.0 so they record `log_starts`. Re-publish, re-pin,
   rebuild a wiki only if its digest changes, and re-record `results/`. 33
   came from an earlier one-off converter (its README), so re-importing it
   would rebuild the capture its truth was written against; it keeps "start
   not recorded", as do the minikube captures.
3. A log-span fact in the facts layer, beside `events_horizon`: "these lines
   cover T1–T2 of a log that starts at T0", and that the log's last line is
   capture time. `log_horizon` is taken (instance retention), so it needs a
   new name. The existing `log_horizon` reads the focus pod's entries only,
   now that the report carries other pods' logs too.
4. Knowledge base and answer rules, stated generally:
   - a claim that errors began at, or rose after, a change needs a count from
     before it;
   - a log's last line is capture time, not the end of anything;
   - grep for the exact token (`" 503 `, not `503`);
   - a multi-pod workload's pods are read, not one of them;
   - added at step 1's checkpoint (§4): an error rate that has held steady
     since the log's start is reported as a background that predates the
     incident, not as a new fault to fix, unless something shows it changed.
6. Added at step 1's checkpoint: when a request path is alerting, the
   NetworkPolicies selecting its upstream are read, and are stated as
   allowing or blocking that traffic. This is a knowledge-base entry (the
   path to `get_networkpolicy_summaries`), not a new tool.
5. The coordinator's `triage` group in `k8srca.yaml` gains
   `get_log_pattern_summary`. Today only the investigator (`full: "*"`) has
   it.

## 3. Steps

Two steps, so the tools' effect is measured apart from ours. Each ends in an
arm across the whole suite, since the tool changes reach every scenario that
reads a log.

### Step 1: the tools alone (§2 items 1–2)

| | |
|---|---|
| **Hypothesis** | The tools' own changes (the cut-tail note naming the pattern tool, whole-log `grep`, other pods' tails) get the agent to the earlier evidence without any change of ours. |
| **Expected effect** | 31: diagnosis at least 3/5 (from 0/3). 80: `cause-two-brokers` or `cause-writes-fail-from-start` met in at least 2/5. Other scenarios: checklist within noise. Cost per run up by at most 30%. |
| **Can we see it?** | The target items are cause items, which don't flip between gradings. From a true rate of 0.2, 3/5 or better happens 6% of the time; from 0.3, 16%. So 3/5 on 31 is a signal, 2/5 on 80 a weak one. Regressions at n=3 are flagged for the checkpoint, not decided. |
| **Inner loop first** | Re-import diff: each k8s.json changes only by `log_starts`, else stop and look. `record-results` before and after: which scenarios' tool results change, and how. Unit tests. |
| **Arm** | n=5 for 31 and 80, n=3 for the other 13. |
| **Budget** | $35 (sessions about $27 with a 30% allowance, grading about $6), 2 days. |
| **Done** | The arm is run, graded with `--grades 3` at the agreement criterion, and the result is in §4 with a decision. |
| **Stop** | Any tripwire; cost per run more than 1.5× baseline suite-wide; a re-import that changes anything but `log_starts`. |

### Step 2: ours (§2 items 3–6)

Rewritten at step 1's checkpoint (§4). Step 1 showed the agent now reaches
the earlier log, so the misses step 2 must close are reading ones: a steady
rate called a live fault, a policy never inspected, broad patterns misread.

| | |
|---|---|
| **Hypothesis** | Stating the log's span as a fact, the background rule, the precise-pattern rule and the policy check get the agent from "the trickle recurs" to "the trickle is old and unchanged, and the policy allows everything", without new unsupported claims. The coordinator's pattern tool and the other rules hold step 1's reach. |
| **Expected effect** | From step 1's levels: 31 `cause-rate-unchanged` at least 3/5 (0/5 now), `rules-out-network-policy` at least 3/5 (0/5), diagnosis at least 2/5 (0/5); `no-errors-began-with-policy` stays met. 80: `cause-writes-fail-from-start` stays at least 2/5; nothing expected of `cause-two-brokers`, which no item of step 2 aims at. Elsewhere checklist within noise, cost per run within 1.3× step 1, and no rise in unsupported claims. |
| **Tripwire 3** | The background rule and the policy check come from 31's runs (and #10's `nothing-is-wrong` reading). The owner reviews their text, with §2 item 4's others, before the arm. |
| **Can we see it?** | As step 1. If step 1 already meets these, step 2's live arm checks for regressions only, and the change is decided on the offline evals and judgment, which §4 says. |
| **Inner loop first** | The facts layer on step 1's fixtures: the new fact appears with the right times on 31, 80 and 102, and on no log it doesn't apply to. `eval answer` on fixed facts for the rules (about $0.10 an answer). |
| **Arm** | As step 1. It becomes the new baseline (`scenario baseline --agreement`). |
| **Budget** | $40, 2 days. |
| **Done** | The arm is run and graded, the baseline taken, §4 written, status set. |
| **Stop** | As step 1. |

**Whole design:** $75, about 4 working days.

## 4. Results

### Step 1: stopped at the re-import check (2026-10-31)

The re-import of 24, 31, 80, 102 and 105 with 4.6.0 changed more than
`log_starts`, which is step 1's stop criterion. Nothing was run live; $0 spent.

- **Logs are shorter.** 31's frontend-proxy log went from 10,000 lines
  (17:42:19 on, 15 `503 UC`) to 2,675 (17:53:10 on, 6), so the window before
  the policy, which 31's truth counts, is gone. 80's Kafka and proxy logs
  shrank the same way; checkout's 6,486 lines, under the cap, did not.
  - Cause: the capture reads each log through `_read_pod_log`, which since
    4.6.0's tail fix keeps a read within 1 MiB and asks again for fewer
    lines. `--max-log-lines` is now bounded by about 1 MiB a log.
  - A k8stools bug: right for a tool result, wrong for a capture.
- **Requests and limits** now in the container statuses of 24, 80 and 105
  (imported on 4.4.0); 4.4.1's replay already filled them from the spec.
- **About 900 empty fields** per capture from the newer `kubernetes` client's
  models (`bind_mount_options`, `eviction_responders`, ...).

**Decision (owner, 2026-10-31): change the plan.** The capture bug is filed as
k8stools#36. Step 1 resumes on the release that fixes it: re-import, the same
check, then the arm. Meanwhile the replay's effect is looked at offline on the
current captures.

**Offline, 4.6.0's replay on the current captures** (`record-results` on
the 15 scenarios against 4.4.1's, times masked):
- 43 workload reports gain the cut-tail note ("These 20 lines start at T;
  the recorded log starts at T0. get_log_pattern_summary searches all of
  it.").
- Two gain another pod's log (image-pull-typo's email, 33's cart), and four
  multi-pod notes are reworded.
- Every log gains `lines_from`.
- The fixtures hold no `grep` calls, so whole-log grep isn't exercised here.
- The facts layer derives the same facts but one: 33's cart gains a
  `log_horizon` fact from the old pod's log, its focus pod being Pending.
  That is §2 item 3's focus-pod fix.

### Step 1: the re-import check on 4.6.1 (2026-10-31)

k8stools 4.6.1 fixed #36. Re-imported with `--max-log-lines 10000` and the
`kubernetes` 36 client (37's models add about 900 empty fields that reach
`get_pod_spec` as nulls; the old imports used 36):
- **Logs:** every log's lines are identical to the old capture's.
- **Added:** `log_starts` and `log_limits`, and, in 24, 80, 102 and 105
  (first imported on 4.4.0), requests and limits in the container statuses.
- **What the agent sees** (`record-results` on 4.6.1, old captures against
  new): only the log notes of 11 workload reports, which now say where the
  log began and that the capture kept its last 10,000 lines. The requests and
  limits show in no result: 4.4.1's replay already took them from the spec.
- The criterion said "only `log_starts`"; it was read as "nothing the agent
  sees changes but the log's start and cut", which holds.
- **Wikis:** rebuilt from the new captures, all five match their pins.
- Published to the data repo; `scenario.yaml` re-pinned. `results/` for all
  15 re-recorded on 4.6.1, which also brings the older scenarios' fixtures
  up to today's recorder (claim summaries and pod events).
  `test_eval_answer` no longer depends on a result's position.

### Step 1: the arm (2026-10-31)

54 runs: n=5 for 31 and 80, n=3 for the other 13. The replay was 4.6.1's
image, with the skill local (`k8s-rca` 69fe31d5dfc9d3c1). Each answer was
graded three times. Checklist is the median of the three gradings; diagnosis
is the majority. The baseline is `baseline.json`.

| scenario | checklist base → step 1 | diagnosis base → step 1 | $ per run | calls |
|---|---|---|---|---|
| itbench-31-network-policy-decoy | 10.0 → 10.2 /14 | 0/3 → **0/5** | 1.22 → 0.66 | 43 → 39 |
| itbench-80-kafka-two-brokers | 9.7 → 12.0 /21 | 0/3 → **0/5** | 0.72 → 0.77 | 31 → 40 |
| checkout-gc-pressure | 10.7 → 12.3 /13 | 2/3 → 3/3 | 0.76 → 0.54 | 48 → 41 |
| deploy-regression | 14.0 → 13.3 /14 | 3/3 → 3/3 | 0.17 → 0.33 | 12 → 26 |
| downstream-latency | 10.0 → 10.0 /11 | 3/3 → 3/3 | 0.66 → 0.43 | 43 → 44 |
| image-pull-typo | 9.0 → 8.7 /9 | 3/3 → 3/3 | 0.08 → 0.08 | 7 → 7 |
| itbench-102-memory-quota | 13.7 → 13.0 /17 | 3/3 → 3/3 | 0.25 → 0.62 | 17 → 36 |
| itbench-105-invalid-command | 18.0 → 17.7 /20 | 3/3 → 3/3 | 0.10 → 0.11 | 10 → 11 |
| itbench-24-kafka-port-change | 14.7 → 13.7 /16 | 3/3 → 3/3 | 0.35 → 0.36 | 20 → 26 |
| itbench-33-invalid-node-selector | 11.3 → 10.7 /12 | 3/3 → 3/3 | 0.18 → 0.20 | 12 → 21 |
| jvm-oom-on-startup | 13.0 → 12.3 /14 | 3/3 → 3/3 | 0.34 → 0.24 | 18 → 13 |
| jvm-oom-with-metrics | 12.5 → 12.7 /14 | 10/10 → 3/3 | 0.20 → 0.20 | 12 → 12 |
| liveness-probe-wrong-port | 8.3 → 8.3 /9 | 3/3 → 3/3 | 0.19 → 0.30 | 12 → 24 |
| nothing-is-wrong | 6.7 → 6.7 /8 | 3/3 → 3/3 | 0.48 → 0.48 | 31 → 32 |
| pvc-pending-no-storageclass | 10.0 → 10.0 /10 | 3/3 → 3/3 | 0.08 → 0.08 | 6 → 6 |

- **Against the expected effect:**
  - **31:** diagnosis 0/5 against at least 3/5. Not met.
  - **80:** `cause-writes-fail-from-start` was met in 2/5 (baseline 0/3), and
    `cause-two-brokers` in 0/5. The expected effect was one of the two in
    2/5, so this is met, but only as the weak signal §3 said 2/5 would be.
  - **Elsewhere:** the runner flagged four drops at n=3 (deploy-regression,
    image-pull-typo, 105, jvm-oom-on-startup). Each is under one item a run,
    and none is a diagnosis. Where each comes from:
    - deploy-regression: `no-clean-exit`, which split between gradings on 2
      of 3 answers.
    - image-pull-typo and 105: one answer each, missing one ruled-out item.
    - jvm-oom-on-startup: `cause-no-heap-sizing`, missed in 2 of 3 (1 of 3 at
      baseline).

    They are flagged for the checkpoint, not decided.
- **Cost:** the suite's cost per run came to 0.93× baseline. Calls rose where
  a log was read more widely: 102 (17 → 36 calls, 2.5× cost),
  deploy-regression (12 → 26 calls, 1.9×), liveness-probe-wrong-port and 33.
  No scenario failed for cost.
- **Agreement:**
  - 99.1% of item gradings matched their answer's majority.
  - Only one item split on more than one answer: 31's
    `no-trickle-cause-as-observed`, a gap item, which split on 2 of 5.
  - So the criterion is missed on that one item, which isn't a target.
  - One grading came back as malformed JSON and stopped `scenario checklist`.
    It was redone with the same `checklist.grade` call. The other 116 had
    already been saved.
- **Spend:** $19.02 in sessions. Grading was about 147 calls, which I put at
  about $6 (an estimate). The total of about $25 is within the $35 budget.
- **What the traces show (31 and 80):**
  - **The agent now reads the earlier log.** 8 of the 10 runs called
    `get_log_pattern_summary`, and several grepped all 10,000 lines.
  - **31, the trickle:**
    - Two runs with a precise pattern (`503 UC upstream_reset`) got exactly
      the trickle: 15 matches from the log's start, with the gap across the
      policy.
    - Both described it as recurring, so no run called it a one-off any
      more. Instead they framed it as a live intermittent fault in frontend
      (keep-alive resets, stalls), with fixes to try. Neither called it a
      background that was there already.
    - Three runs used broad patterns. ` [4-5][0-9][0-9] ` matched 2,904 lines,
      mostly byte counts.
  - **31, the policy:** `rules-out-network-policy` was missed in all 5 runs,
    as it was in all 3 baseline runs. No run in either set called
    `get_networkpolicy_summaries`. With no change found, there was nothing to
    compare before and after, and so no onset claim for step 2's onset rule to
    correct.
  - **80:**
    - One run read "the two brokers disagree … endless
      `TOPIC_ALREADY_EXISTS`" but did not conclude that there were two
      replicas.
    - The others blamed broker epochs, Chaos Mesh or a stuck broker.
  - **One answer leaked the revision turn's preamble** ("Now the revised
    answer, trimmed to fit…"). That belongs to `review.py`, not to this
    design.

**Reading.** Step 1's hypothesis holds for reach: the agent now gets to the
earlier evidence. It does not hold for diagnosis. 31's miss has moved from
evidence never seen to two things:
- what the agent makes of a steady error rate;
- a policy it never inspects.

80's misses have moved to an inference the agent doesn't make (two replicas).
On 31, step 2 as planned targets claims the runs no longer make. Its onset
rule and log-span fact are aimed at "errors began at the change", and no run
said that.

**Decision (owner, 2026-10-31): change the plan,** as recommended below.
Step 1 is closed. Step 2 is rewritten in §3 with the background rule (§2
item 4) and the policy check (§2 item 6); the owner reviews their text before
step 2's arm.

**Checkpoint (2026-10-31).** No tripwire fired: this is
the first arm, the budget isn't spent, and no rule was written. Step 1 is done
on its own criteria, and a decision is needed before step 2:
- **Recommended: change the plan.** Rewrite step 2's expected effects from
  these levels. Drop "31 diagnosis at least 4/5" as something step 2's
  current items could reach. Decide whether step 2 should also carry:
  - a general rule that an error rate steady since the log's start is a
    background to report as such, not a fault to fix. This comes from 31's
    runs and from #10's `nothing-is-wrong` reading, so the owner reviews it
    under tripwire 3;
  - a check of the policies that select an alerting path's upstream.

  Keep the log-span fact and the precise-pattern rule. The broad-pattern
  misreads are this step's evidence for the latter.
- **Continue unchanged:** run step 2 as written, and expect 31 to stay near
  0/5.
- **Close:** take 4.6.1 as step 1's result, move production and the baseline
  to it, and end the design done in part.

## 5. Instrument and tripwires

- **Frozen from step 1's start:** grader, truth files and checklists. The
  re-import changes captures only; a truth file that depends on what changes
  (it shouldn't: only `log_starts` is added) is tripwire 4. The READMEs that
  describe tail lengths (31, 80) are documentation, updated with the
  re-import.
- **Tripwire 3 (a rule from a run).** The rules in §2 item 4 come from #10's
  reading of 12 runs across four scenarios, and from 31, and they state how
  Kubernetes logs work rather than what one scenario needs. The owner reviews
  their text before step 2's arm.
- **Production.** The upgrade changes what the live agent sees as soon as the
  owner runs `k8srca up`. Until step 2's baseline, the baseline in
  `baseline.json` describes the agent on 4.5.0.

## 6. Open for the owner

1. **Approve the plan,** budget included.
2. **The coordinator and the pattern tool.** Recommended: give it in step 2,
   not step 1, so step 1 measures the tools alone. The investigator has it
   from step 1, through `full: "*"`.
3. **When production moves to 4.6.0.** Recommended: after step 2, with the
   new baseline. Step 1's arm needs only the replay image, which is built
   locally.
