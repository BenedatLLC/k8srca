# 012 Log evidence the agent can't see

**Status:** in progress, approved 2026-10-31 (owner) with §6's
recommendations: the coordinator gets the pattern tool in step 2, and
production moves to 4.6.0 after step 2. Step 1 started 2026-10-31.
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
   - a multi-pod workload's pods are read, not one of them.
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

### Step 2: ours (§2 items 3–5)

| | |
|---|---|
| **Hypothesis** | Stating the log's span as a fact, giving the coordinator the pattern tool, and the four rules close what step 1 leaves, without new unsupported claims. |
| **Expected effect** | From step 1's levels: 31 diagnosis at least 4/5 and `cause-rate-unchanged` at least 3/5; 80 diagnosis at least 2/5; `no-errors-began-with-policy` stays met. Elsewhere checklist within noise, and no rise in unsupported claims. |
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
