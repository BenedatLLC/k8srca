# What is planned, in order, and what else the designs leave open

Written 2026-11-14 from the design documents, the open issues and the two repositories' state
that day. Each step below says what it is, why it is there, what it costs, and what ends it, so
the page can be read without the designs open. The designs stay authoritative, and this page
should be updated when a design's status line changes. Designs 014 and 015, and the
2026-11-14 revision of 011, are on branches (`design-014-sregym`, `jev-decision-models`) until
merged.

## 0. The three things in play

- **The knowledge base (KB).** The agent's `k8s-rca` skill carries a set of YAML files that
  name symptoms (such as "pod restarting"), the candidate causes of each, and which observed
  facts count for or against each cause. The agent queries it with `kb_query.py`: `search`
  finds entries by words, `lookup` lists a symptom's candidates, `open` ranks them from the
  facts in hand and prints a "most likely" line. Design 007 defines it; 015 measures whether it
  helps.
- **The decision model (design 011).** Most facts are computed by code from tool results ("the
  last exit code was 137"). A few need a log to be read ("the log ends in a stack trace").
  Today the agent reads the log itself, inconsistently. 011 hands those yes/no questions to a
  small, cheap model (TypeSafe's JEV first) that returns a probability; code turns a confident
  answer into a fact and `open` uses it. If the model is unsure, nothing is stated and the agent
  reads the log as today. This is the only thing arm B adds.
- **SREGym (design 014).** A public benchmark that deploys an application on a local cluster,
  injects a fault, asks the agent what is wrong, and has a judge score the answer. We use its
  Lite cohort: 17 faults, 3 burned for debugging the harness, 14 held out. **Arm A** is our
  agent as it is (tag `holdout-v2`; baseline 25 passes of 70, 2026-11-12). **Arm B** is the same
  agent plus the decision model. The one comparison of A against B is what the hold-out is for;
  afterwards the 14 faults become ordinary test material.

## 1. The planned sequence

Three threads run at once; the join points are marked.

### Thread A: the decision model (011), on its own branch and machine

**1. 011 step 1: can a decision model read our logs?** Offline, no agent, no cluster. Take the
container logs from the 15 scenario captures (about 540 logs), pair each with each of 19
log-reading predicates from the KB ("the log ends in a fatal error", "the log reports
permission denied", ...), label the pairs by hand with a screen to find the positives, then score
three arms on redacted text: a regex per predicate, JEV, and a small Claude model asked the same
question. Choose thresholds per predicate and model on constructed examples, apply them
unchanged to the captures. Also measure repeatability: the same question five times.
*Why:* before anything is built into the agent, know whether the model's answers are right,
whether it abstains sensibly, and whether plain regex would do as well. *Cost:* about $25 and a
week, most of it hand-labelling. *Done when* JEV states no confident false negative, at most
one confident false positive, abstains on at most a third of pairs, and beats regex on five
predicates. *Stops* if regex or Haiku matches it (use the simpler one), or no threshold removes
confident false negatives (the idea does not work on this text). *Produces* `core/judge.py`
with the adapters, `judge/questions.yaml`, the labelled set under `tests/judge/`, and
`k8srca eval judge`.

**2. 011 step 2: the judge in the facts layer, behind a flag.** Build the `Judge` contract, a
judge service beside k8stools that holds the provider key and redacts text before it leaves,
and `by: judge` facts, all off by default (`judge.enabled: false`). *Offline check:*
`tests/kb_eval/measure_2a.py` runs `open` on 12 labelled scenarios without log facts; today
the computed leader is right in 6, wrong in 3, undecided in 3. Add the judge's facts and report
what moves; the two jvm-oom cases should decide for `oom-kill`, and no new wrong leader should
appear. Score the leader `ranking` computes, not the printed line (issue #18). *Live:* n=3 on the
scenarios as a guard against regression. *Expected effect:* small. A judge improves the reading
of logs the agent fetched; it cannot reach an object the agent never read, which is where most
misses were. "Can't tell" is the predicted outcome, said in advance.

### Thread B: SREGym (014), waits for thread A

**3. 014 step 2: pre-register the comparison.** No runs. Write down and tag: the two arms, the
14 faults, n=5 per fault per arm, the budget, and the decision rule, before any arm B attempt
runs. *Gate:* 011 steps 1–2 have ended and arm B's flag is built on a branch. *Why:* so the
result cannot be read into after the fact. §9 question 1 (what "the decision model" means) is
decided: 011 as written, text-read facts only, not a model that chooses the cause.

**4. 014 step 2b: `holdout-v3`, the known defects fixed once.** Three agent-side bugs were found
during the baseline and deliberately left in, because fixing them would have moved the frozen
arm: #18, `open` prints a "most likely" cause other than the one its ranking computed (seen in
4 of 12 scenario cases, visibly in 3 of the 44 baseline sessions that ran `open`); #15, the
agent's narration is joined into the answer that gets graded; #17, the coordinator waits for the
specialist with `sleep`, which blocks the specialist's tool calls (once, for 561 s). Each is
fixed on `main` with a test, once, when arm B's commit is ready, and tagged `holdout-v3`. Arm B
is `holdout-v3` plus the judge, so the fixes are common to both arms and the comparison measures
only the judge. Arm A is then run again on `holdout-v3`: 14 faults, n=5, about $41 and 10 hours.
*Why once and not now:* fixing them in arm B alone would confound fixes with the judge; fixing
them now risks paying for another re-run if a fourth defect turns up before arm B exists. The
`holdout-v2` baseline stays as the record of the agent as it was. *Stop:* a fourth agent-side
change proposed for the tag is a checkpoint, not a fourth change.

**5. 014 step 3: the one comparison.** Both arms on the 14 faults, 5 attempts each, interleaved
fault by fault in one sitting so cluster and API drift fall on both. About 140 runs, $80 in
agent sessions and 20 hours, plus SREGym's judge. *Decision rule, set in advance:* attempts
within a fault are correlated, so the unit is the fault. B is "better" only if five or more
faults improve and none get worse; "not worse" if at most one fault moves down; otherwise
"can't tell", reported as that. No answer or transcript is opened until the score table is
written. *Then* the 14 faults are burned: they are no longer a hold-out and may be read,
captured and tuned against. *Caveat already recorded:* the hold-out was opened on 2026-11-10 when
the baseline's answers were read against the fault definitions, so this is reported as a
non-blind comparison.

**6. 014 steps 4–6, each planned only when the one before ends.** Step 4, a successor hold-out:
SREGym's Lite-IA cohort (other applications, kept unread so far), and if the owner wants a fault
hold-out too, a suite of fresh faults authored by someone not working on the agent (design 006's
idea, revised). Step 5, SREGym as a tuning target: live runs record captures that become
scenarios, most changes are settled on those, live runs only at milestones. Step 6, the
leaderboard: only if SREGym accepts a diagnosis-only entry (their board ranks on fixing, which we
have deferred); needs two questions asked of the SREGym team first (§9 q2 and q4: how third
parties submit, and what tuning is allowed).

### Thread C: the knowledge base (015), waits for 014 step 3

What 015 has already done (steps 0–2b, 2026-11-12 and 13), so the rest makes sense: joined
SREGym's per-attempt verdicts to our session records; traced whether the agent consulted the KB
and whether that tracked passing (no evidence it helps, none that it hurts); measured on 12
scenarios that the KB lists the true cause 11 times but that `open` names a wrong leader in 3 to
5 of 12 depending on how you read it; and found that 31% of the agent's KB searches return
nothing for a query the KB could answer, because the agent uses words the KB does not contain
(`unschedulable`, `exit 139`, `panic`).

**7. 015 step 2c: the 14 SREGym faults become scenarios.** After step 3 burns them, capture each
faulted cluster through k8stools (the same capture tooling the 15 existing scenarios use), with a
truth and a checklist written before any run. *Check first* whether the capture tooling can
record through SREGym's view proxy, and how many of the 14 can be captured while faulted. *Why:*
design 007 left four KB families unmined because no scenario reached them, and these faults
reach exactly those families (DNS, credentials, Jobs, retries). They are also the only cases
where `open` is consulted on a symptom with no authored ranking, which the next item needs.

**8. 015 Checkpoint 1.** The owner decides whether the live ablation (step 9) is worth running.
The gate, decided 2026-11-12: the rate at which `open` names a wrong leader on symptoms with no
authored discriminators is 25% or more. The 12 existing scenarios all have authored rankings, so
the gate could not be applied to them (owner, 2026-11-13: wait for 2c's cases rather than adopt a
substitute reading). Below 25%, the KB is not the main problem and step 4 is done on judgment
where cheap.

**9. 015 step 3: the live ablation.** Arm A against arm A with the KB's candidate lists removed
(`lookup` and `open` give no candidates), 8 faults mixing covered and uncovered families, n=10
per arm. *Why:* the one direct test of whether the KB's candidates help, do nothing, or steer the
agent wrong. *Can we see it:* only a difference of 15 points or more. About $90.

**10. 015 step 4: improvements, each settled offline first, then one live guard (step 5).**
4.1, search aliases and a fallback: give the search the agent's vocabulary (`unschedulable`,
exit codes, signals, state names) and a looser match when every-word matching fails; built
against the half of the 185 recorded queries set aside for building, scored on the other half,
never the reverse. 4.2, coverage honesty: when a symptom has no authored ranking, say so, list
classes to consider, and have `open` name no leader. 4.3 and 4.4 are in the design (shorter
`open` output; the printed grade matching the computed one). 4.5, mine the missing families with
`k8srca kb mine` from public guides, about $5 a family: `networking` (DNS), `rollouts-and-jobs`,
`service-traffic`, and new ones for credentials and for timeouts and retries; measured on 2c's
scenarios. Step 5 runs the changed KB live once as a guard.

**Then: design 013, what changed near the alert.** Parked 2026-11-07 until 014 and 011 are
complete. Captures keep each object's write times, a `get_recent_changes` tool and change facts
(step 1, waits on k8stools#38), then rules for weighing a change against the symptom (step 2).
Approved 2026-11-01.

## 2. Housekeeping on the current work

- **Merge `design-014-sregym` into `main`** when the owner wants: 41 commits, including
  `kubewiki build --hide-label` (so a chart-built wiki omits components SREGym hides from the
  agent), the `tests/kb_eval/` labels and scripts, designs 014 and 015, and CLAUDE.md's 014/015
  lines. `docs/process.md` §7's design-state table is stale on the branch (014's row still says
  step 1 not begun) and should be rewritten at the merge.
- **014 §10.1 item 8** still says the astronomy-shop re-run is running; write the final scores
  (edge 5/5, kafka 5/5, internal_traffic 0/5, valkey 0/5, secret_rotation 0/5, unschedulable 2/5).
- **The polling shim** (SREGym fork `d118a717`, added because the proxy dropped sessions silent
  for 600 s) has not been exercised on a session over 600 s. The first such session confirms it.
- **Suite repo:** `baseline-summary.md`'s per-fault sections describe the first pass; the six
  astronomy-shop sections were not rewritten for the re-run. The README's hold-out guard
  wording ("Opened, with a declared exposure") was asked for and should be checked.
- **Ask the SREGym team** (014 §9 q2 and q4) before step 6 is planned: whether a
  diagnosis-only entry is accepted, and what tuning is allowed.
- **011 on `main`** is the 2026-10-25 revision; the 2026-11-14 revision, which adds what 015
  found, is on `jev-decision-models`.

## 3. Open issues

| Issue | What | When |
| --- | --- | --- |
| k8srca#18 | `kb_query.py open` prints a "most likely" cause other than the one `ranking` computed; a weak lead from another group takes the slot | 014 step 2b |
| k8srca#17 | the coordinator waits for the specialist with `sleep`, which blocks the specialist's tool calls; fix is a deterministic guard on bash, no prompt change | 014 step 2b |
| k8srca#15 | the scenario runner grades every message of a turn as the answer; the Slack path uses only the last, so narration leaks into graded answers | 014 step 2b |
| k8srca#16 | kubewiki names a component the agent's view hides (SREGym's load generator); `--hide-label` built, wider scope to discuss | option 1 done; rest unscheduled |
| k8srca#14 | itbench-80: the agent does not conclude that two Kafka replicas are two independent brokers | design input, unscheduled |
| k8srca#10 | why the agent misses evidence (004 §12.3 step 7's findings) | design input; 013 and 015 draw on it |
| k8srca#6 | rethink the answer reviewer: make its feedback actionable; measure "is every sentence supported" as an evaluation category rather than a production gate | 011 §6 waits on it; unscheduled |
| k8srca#1 | use workload change history for "what changed?" | design 013 |
| k8stools#39 | a read-only tool for mutating and validating webhook configurations | explains `mutating_webhook` 0/5 (out of reach) |
| k8stools#40 | `internalTrafficPolicy`, `externalTrafficPolicy`, `sessionAffinity` on ServiceSummary | explains `internal_traffic_policy` 0/5 |
| k8stools#41 | rollout strategy (`maxUnavailable`, `maxSurge`) on DeploymentSummary | explains half of `rolling_update` |
| k8stools#38 | write times in captures, ResourceQuotas, `get_recent_changes` | design 013 step 1 |
| k8stools#37 | NetworkPolicy age on policy lines | folded into #38 |

Any k8stools change moves the agent's tool surface (the pinned image is `k8stools:4.6.1`), so it
lands in a new tag between comparisons, never during one.

## 4. Left open by the earlier designs

Each is its own piece of work and has no ticket unless one is named above.

- **004 scenario testing** (done in part). The 15 scenarios with checklists are the offline
  instrument. Open: check the checklist grader against a human read (hand-grade about ten saved
  answers, measure agreement); a frozen-clock A/B (does the agent's sense of "now" matter); a
  policy for re-recording captures when k8stools changes; scenario 8 `evidence-unavailable`
  from ITBench 34; X2, the ITBench feature-flag and Chaos Mesh imports (need metrics and traces);
  S5, a generic MCP proxy and KWOK synthetic clusters; a kubewiki gap (a workload with no pods
  loses its outbound edges); two k8stools gaps (the workload report shows one pod's log for a
  multi-pod Deployment and cuts long lines; events cannot be filtered by owner).
- **005 modular architecture** (draft). Components behind contracts in `core/`; only the
  generator contract and its evals exist. The `k8s-core` plugin and steps (b) to (e) are not
  scheduled.
- **006 hold-out suite** (on hold). A colleague authors scenarios we never see and reports
  scores only. The kit and the author's three documents are written; the tag and the hand-off
  wait on the owner. 014 step 4 asks the same question again.
- **007 knowledge representation** (done in part). The four unmined families (015 step 4.5);
  runbooks, whose format exists but nothing reads one (needs a delivery decision, a real
  runbook from an operator, and a scenario where following it changes the answer); removing the
  legacy alert path once all families are mined; the known limits of its steps 5–7 (liveness
  probe facts among them).
- **008 conclusions under incomplete evidence** (done in part). Ranking with three strengths is
  built; probabilities in their place (§5) are "not yet".
- **009 metrics** (done in part). Prometheus capture and replay and the promtools reports are
  built. Open: latency and throttling reports for the agent (the throttling series are captured
  but no report reads them; span metrics carrying latency are not captured). "As a new design."
- **010 facts and short answers** (done). Its ideas 2 and 3 (record more observations as facts,
  such as log readings and pod-spec values; code checks for overreach by form, such as "every"
  where the fact says "the last 3") are picked up by 011 §6 and have no schedule of their own.
- **011 decision models** (in progress). Beyond steps 1–2: the reviewer rethink waits on issue
  #6; symptom matching (a `choice` over the KB's symptoms for a free-text question) waits for 015
  step 4.1's simpler lexical fix to be scored first; a stronger second-tier model for the
  abstain band as an optional extra arm of step 1; provider terms and JEV pricing to confirm
  before the first call.
- **012 log evidence** (done in part). ITBench 31's rate-across-the-policy and policy rule-out
  items are handed to 013.
- **014 SREGym**. Besides the steps above, the "not established" items of §10.1 item 8: why the
  coordinator re-sweeps the cluster after the specialist replies (half of a delegated session's
  time); what lower coordinator effort would do to duration and scores; timing in production
  Slack sessions.
- **015 KB evaluation**. The 2a gate reading, settled only when 2c's cases exist; after the #18
  fix, re-run `measure_2a.py` and confirm printed = computed in 12 of 12 and nothing else moved.

## 5. Rules that bind the order

From `docs/process.md` and the designs: one design in progress at a time (011 beside 014 is the
owner's exception of 2026-11-07); the grader and truth files are frozen for the length of a
design; an arm is a tag and does not move during a comparison; what was read about the 14
SREGym faults is not used to design arm B or the judge before step 3 (014 §3); a checkpoint ends
in a decision, never in another small change; a rule written from one run is a tripwire.
