# What is planned, in order, and what else the designs leave open

Written 2026-11-14 from the design documents, the open issues and the two repositories' state
that day. Dates are when a decision was made. The designs are authoritative; this page is the
list, kept short, and should be updated when a design's status line changes. Designs 014 and
015, and the 011 revision of 2026-11-14, are on branches (`design-014-sregym`,
`jev-decision-models`) until merged.

## 1. The planned sequence

Three threads run at once. The order within each is fixed; the join points are marked.

**Thread A: the decision model (design 011), on its own branch and machine.**

1. **011 step 1**, offline: can a decision model (JEV first) read our container logs? Label a
   corpus from the scenario captures, score JEV against a regex arm and a Haiku arm on
   redacted text, pick thresholds per predicate and model. About $25 and a week. Done when
   JEV states no confident false negative and beats regex on five predicates (011 §8).
2. **011 step 2**, offline then live: the `Judge` contract, the judge service, `by: judge`
   facts, behind `judge.enabled: false`. Offline check: `tests/kb_eval/measure_2a.py` with the
   judge's facts added, scored on the computed leader (011 §9). Expected live effect: small.

**Thread B: SREGym (design 014), waits for thread A.**

3. **014 step 2**, pre-register the comparison once 011 steps 1–2 have ended (no runs).
   §9 question 1 is decided: 011 as written, arm B = arm A plus the judge.
4. **014 step 2b**: fix k8srca#18 (`open`'s printed leader), #15 (narration in the graded
   answer) and #17 (the coordinator's `sleep`) on `main`, with tests, once. Tag `holdout-v3`.
   Arm A re-runs on it (14 faults, n=5, ~$41, ~10 h).
5. **014 step 3**: both arms, `holdout-v3` and `holdout-v3` plus judge, interleaved fault by
   fault, 14 faults, n=5 each (~$80, ~20 h). Decision rule: five faults better and none worse,
   else "can't tell". Then the 14 faults are burned.
6. **014 steps 4–6**, each planned only when the one before ends: the successor hold-out
   (Lite-IA, and a fresh-fault suite if the owner wants one); SREGym as a tuning target,
   offline first; the leaderboard, gated on SREGym accepting a diagnosis-only entry (§9 q2,
   q4, unasked).

**Thread C: the knowledge base (design 015), waits for 014 step 3.**

7. **015 step 2c**: capture the 14 burned SREGym faults as scenarios through k8stools, with
   truths and checklists written first. Check first whether capture works through SREGym's
   view proxy. This supplies the unauthored-symptom cases that Checkpoint 1 needs.
8. **015 Checkpoint 1**: the false-leader rate on symptoms with no authored discriminators,
   measured on 2c's cases, ≥ 25% gates step 3 (owner, 2026-11-13: wait for 2c).
9. **015 step 3**, if gated through: the live ablation, arm A against A without KB candidates,
   8 faults, n=10 per arm.
10. **015 step 4**, improvements, each settled offline first: 4.1 search aliases and a
    fallback (built on the build half of the query corpus, checked on the report half);
    4.2 coverage honesty; 4.3–4.4 (see the design); 4.5 mine the missing families (`networking`,
    `rollouts-and-jobs`, `service-traffic`, credentials, timeouts; ~$5 each), which 007 left
    waiting for a scenario per family and 2c would supply.
11. **015 step 5**: the live guard.

**Then:** design 013 (what changed near the alert), parked 2026-11-07 until 014 and 011 are
complete; its step 1 waits on k8stools#38.

## 2. Housekeeping on the current work

- Merge `design-014-sregym` into `main` when the owner wants: 41 commits, including
  `kubewiki build --hide-label`, the `kb_eval` labels and scripts, designs 014 and 015, and
  CLAUDE.md's 014/015 lines. `docs/process.md` §7's table is stale on the branch (014's row says
  step 1 not begun) and should be rewritten at the merge.
- 014 §10.1 item 8: write the final astronomy-shop scores (edge 5/5, kafka 5/5, internal 0/5,
  valkey 0/5, secret_rotation 0/5, unschedulable 2/5) where it still says "running".
- The polling shim (fork `d118a717`) has not been exercised on a session over 600 s. The first
  such session confirms it; watch for it.
- Suite repo: `baseline-summary.md`'s per-fault sections describe the first pass; the six
  astronomy-shop sections have not been rewritten for the re-run. The README's guard wording
  ("Opened, with a declared exposure") was asked for and should be checked.
- 014 §9 q2 and q4 (leaderboard entry, tuning rules): ask the SREGym team before step 6.
- 011's `main` copy is at the 2026-10-25 revision; the 2026-11-14 revision is on
  `jev-decision-models`.

## 3. Open issues

| Issue | What | When |
| --- | --- | --- |
| k8srca#18 | `open` prints a leader other than the one `ranking` computed | 014 step 2b |
| k8srca#17 | coordinator `sleep` blocks the specialist; a deterministic bash guard | 014 step 2b |
| k8srca#15 | narration joined into the graded answer (scenario runner vs Slack path) | 014 step 2b |
| k8srca#16 | kubewiki names components the agent's view hides | option 1 built (`--hide-label`); the rest is scope to discuss |
| k8srca#14 | itbench-80: two Kafka replicas not read as two brokers | design input, unscheduled |
| k8srca#10 | why the agent misses evidence (004 §12.3 step 7) | design input; 013 and 015 draw on it |
| k8srca#6 | the answer reviewer: actionable feedback, support as an evaluation category | 011 §6 waits on it; unscheduled |
| k8srca#1 | workload change history for "what changed?" | design 013 |
| k8stools#39 | a read-only tool for webhook configurations | needed for `mutating_webhook` (0/5, out of reach) |
| k8stools#40 | `internalTrafficPolicy` etc. on ServiceSummary | needed for `internal_traffic_policy` (0/5) |
| k8stools#41 | rollout strategy on DeploymentSummary | needed for `rolling_update`'s second half |
| k8stools#38 | write times in captures, ResourceQuotas, `get_recent_changes` | design 013 step 1 |
| k8stools#37 | NetworkPolicy age on policy lines | folded into #38 |

Any k8stools change moves the agent's tool surface, so it lands in a tag after a comparison,
not during one. The three 014 tool gaps (#39–41) explain three of the baseline's five zeros.

## 4. Left open by the closed designs

Each is its own piece of work and has no ticket unless one is named.

- **004 scenario testing** (done in part): grader against a human read (~10 answers);
  frozen-clock A/B; a re-recording policy for new k8stools releases; scenario 8
  `evidence-unavailable` from ITBench 34; X2 (feature-flag and Chaos Mesh imports, need
  metrics and traces); S5 (generic MCP proxy, KWOK); a kubewiki gap (a workload with no pods
  loses its outbound edges); two k8stools gaps (one pod's log for a multi-pod Deployment;
  events not filterable by owner).
- **005 modular architecture** (draft): the `k8s-core` plugin and steps (b) to (e). Not
  scheduled.
- **006 hold-out suite** (on hold): the kit is built and the author's three documents are
  written; the tag and the hand-off wait on the owner. 014 step 4 asks the same question.
- **007 knowledge representation**: the four unmined families (015 step 4.5); runbooks, which
  need a delivery decision (§12 q4), a real runbook and a scenario where it changes the answer;
  removing the legacy alert path once all families are mined; the known limits of steps 5–7
  (liveness probe facts and the others listed there).
- **008 conclusions**: probabilities in place of the three strengths (§5), "not yet".
- **009 metrics**: latency and throttling reports for the agent (§8 q2); span metrics are not
  captured. "As a new design."
- **010**: closed, done. Its ideas 2 and 3 (record more observations as facts; code checks for
  overreach by form) are picked up by 011 §6 and have no schedule of their own.
- **011**: the reviewer rethink waits on issue #6; symptom matching (row 4) on 015 step 4.1;
  the abstain-band second tier (§12) as an extra arm of step 1 if wanted; provider terms and
  JEV pricing to confirm before the first call.
- **012 log evidence**: 31's rate-across-the-policy and policy rule-out, left to 013.
- **014**: besides the steps above, the "not established" items of §10.1 item 8 (why the
  coordinator re-sweeps the cluster after the specialist's reply; what lower effort would do;
  production Slack timing).
- **015**: the step 2a gate reading, settled only when 2c exists; the stale-rule question of
  whether `holdout-v3`'s #18 fix changes the 2a floor (it should make printed = computed in
  12 of 12, and nothing else).

## 5. Rules that bind the order

From `docs/process.md` and the designs: one design in progress at a time (011 beside 014 is
the owner's exception); the instrument is frozen for the length of a design; an arm is a tag
and does not move; what was read about the 14 SREGym faults is not used to design arm B or
the judge before step 3 (014 §3); a checkpoint ends in a decision, not another small change.
