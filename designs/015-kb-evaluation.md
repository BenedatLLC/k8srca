# Design 015 — Is the knowledge base helping? Evaluating it, then improving it

**Status:** draft, 2026-11-12. Not started. Steps 0 and 1 change nothing and may start at
once, with the owner's go-ahead. Nothing here changes the knowledge base, the search utility
or the agent before 014 step 3's comparison has run (014 §3, "Exposure").

## 1. The question

The agent's `k8s-rca` skill carries a knowledge base (KB, design 007): alerts and symptoms,
the candidate causes of each, and a `kb_query.py` utility (`search`, `lookup`, `open`,
`claim`, `fact`). In the `holdout-v2` tag the KB has 81 alerts; 13 symptoms have authored
rankings (239 claims behind them) and 32 alerts map to them. Design 007 records the rest as
not mined: `rollouts-and-jobs`, `service-traffic` beyond latency, `nodes` and `networking`
(56 vendored alerts, served by a legacy path with no discriminators), because "no scenario
reaches any of them", so a mining step "would be unmeasurable".

Design 014's baseline found failures that look related (014 §10.1 item 8, and
`baseline-summary.md` in the suite repo, "Why the reasoning faults failed"):

- For five faults the evidence was available and the agent mostly did not read the object
  that held it. The KB has no candidate for DNS misconfiguration, credential staleness,
  retry amplification or Jobs that never complete, and `search "password"` and
  `search "authentication failed"` return "no match".
- KB coverage tracks scores (Spearman 0.69 over the eight comparable faults, p 0.04), but the
  covered families are also the loud ones, so coverage and ease cannot be told apart. Two
  uncovered faults were solved anyway. One fault (`search_rate`) used the KB heavily, was
  offered the wrong candidates, and scored 0 of 5.

**The questions this design answers:**
1. Does the KB add value on faults like these, do nothing, or steer the agent wrong?
2. Which changes to the KB's content and to `kb_query.py` would help, and by how much?

**What it is not:** tuning to SREGym's hold-out faults. See §2.

## 2. What binds

- **014's exposure rule.** What the baseline reading showed is not used to design arm B
  before step 3's comparison. The KB is part of arm A's tag and arm B uses it unchanged. So no
  KB or search change before that comparison. Steps 0 and 1 only read saved sessions.
- **process.md tripwire 3.** No KB text is written from one failing run. Entries for a
  family come from public sources through `k8srca kb mine`, are reviewed, and are measured
  on scenarios whose truth was written before any run. A fault's root-cause text may be a
  scenario's truth, and may never be the source of a KB entry.
- **process.md §1 and §6.** A change whose effect is smaller than the noise at an affordable
  n gets an offline eval, not a live run. Known noise: two n=5 arms of an identical agent
  differ by 2 or more on a 30%-pass dimension about 30% of the time. At n=70 an arm's pass
  rate has a standard error near 5.6 points, so a difference between two arms has one near 8:
  **a live ablation sees swings of about 15 points or more, not subtle effects.**
- **process.md §5.** Each step freezes its instrument (the labelled corpus, the query labels,
  the arm definitions, the decision thresholds) before it measures.

## 3. Instruments

| Instrument | State | What it gives |
| --- | --- | --- |
| SREGym's per-attempt results (`results/<stamp>/k8srca/<fault>/*_results.csv`) | exists; not joined to the run records | each attempt's `Diagnosis.success` and composite score. The ledger holds only counts. |
| Saved sessions (Anthropic session events) | exists | every `kb_query.py` call and its output, every tool call and result, the answer |
| The scenario suite (design 004), 21 captures in `k8srca-scenario-data` | exists | replayable clusters with truth and a checklist; offline `open` on recorded facts (`scenario record-results`) |
| `k8srca kb mine <family>` | exists, about $5 a family | a draft knowledge layer from public guides, to review |
| KB candidate recall, false-leader rate, search hit rate | **to build** (step 2) | the offline measures of whether the KB lists the cause, whether `open` names a wrong leader, and whether the agents' queries find an entry |
| SREGym faults as captures | **to check** (step 2c) | scenarios for the unmined families. Needs checking that the capture tooling can record from the SREGym cluster through k8stools. |

## 4. Steps

### Step 0 — Join per-attempt results to the run records

| | |
| --- | --- |
| **Hypothesis** | None; instrument preparation |
| **Expected effect** | The "right answers outnumber passes" gaps in the baseline summary become explainable, and every later analysis can use the judge's per-attempt verdict, not a keyword proxy |
| **Can we see it?** | Deterministic: per fault, the joined passes sum to the ledger's count |
| **Budget** | $0; about a day |
| **Done and stop** | Done when all 14 faults join and match the ledger. A fault whose records and attempts differ in number (a lost attempt, an orphan record) is marked and left out of per-attempt analyses |

### Step 1 — The steering trace on saved sessions

| | |
| --- | --- |
| **Hypothesis** | The KB's candidate list is what a wrong answer adopts: among consulted sessions, a larger share of wrong answers than of right ones name a KB-listed candidate as the cause |
| **Expected effect** | If the KB steers, the two shares differ by about 25 points or more; if not, they are close |
| **Can we see it?** | Only coarsely. About 50 sessions consult the KB; the share comparison has a standard error near 15 points, so it sees only a difference of 30 or more. It is descriptive and will not settle the question |
| **Measures** | M1 consulted `open` or `lookup`; M2 the headline cause matches a candidate listed in that session's KB output; M3 it equals the candidate `open` ranked first; M4 the answer is right and its cause was not a KB candidate. Each split by per-attempt pass (step 0) |
| **Matching rule** | Written before reading, applied to a labelled sample of 40 answers by one reader and to 20 of them by a second; agreement of 85% or more, or M2 and M3 are dropped and only M1 and M4 are reported |
| **Budget** | about $2 if a model reads; no cluster, no agent run |
| **Done and stop** | Done when a table for all 14 faults is written to the journal. Stop early if the matching rule cannot reach 85% |

### Step 2 — Offline instruments, before any change

**2a. Candidate recall and false-leader rate.** On a labelled corpus, record for each case:
whether the true cause is among the candidates `lookup` and `open` list (recall), its rank,
the number of candidates (clutter), and whether `open` names a confident leader that is wrong
or when the cause is not listed (**false-leader rate**, the measure of "confusing the
agent"). Corpus: the scenario captures that have a truth and a KB mapping, and later the SREGym
faults (2c). Truth labels are written before the first measurement. No model call, so it costs
nothing and can be repeated at will.

**2b. Search hit rate.** Collect every `kb_query.py search "<q>"` string the agents issued
in the saved sessions (the baseline's and the scenario suite's). A reader labels each with the
KB entry it wanted, or "none exists". Report the hit rate, the no-match rate and the
wrong-entry rate. Split the queries in half at random before any change; one half is for
building improvements, the other only for reporting.

**2c. SREGym faults as scenarios (after step 3 of 014).** After the comparison the 14 faults
are burned (014 §3, phase C). Capture each through k8stools with a truth and a checklist
written before any run, giving 007's missing "scenario per family". **To check first:**
whether the scenario capture tooling can record from the SREGym cluster through the view
proxy, and how many of the 14 can be captured while faulted.

| | |
| --- | --- |
| **Hypothesis** | The false-leader rate is high on symptoms without authored discriminators, and the search miss rate on queries about authentication, DNS and Jobs is high |
| **Expected effect** | A measurable false-leader rate and miss rate that a later change can move |
| **Can we see it?** | Yes. Both are deterministic over a fixed corpus and a fixed query set, with no sampling noise |
| **Budget** | $0 in model calls; about 1 to 2 weeks of engineering |
| **Done and stop** | Done when 2a and 2b report on frozen labels. **Checkpoint 1** (§6) follows. Stop if the corpus has fewer than 10 labelled cases, as the numbers would mean little |

### Step 3 — The ablation (live, the only counterfactual)

| | |
| --- | --- |
| **Hypothesis** | Removing the KB's candidate surfaces changes the pass rate on faults whose evidence is available |
| **Arms** | A, the agent as shipped. A−cand, the same with `search`, `lookup`, `open` and `claim` removed from `kb_query.py` and from the skill text, and the facts layer, the wiki and the method text kept. It answers "the candidate surfaces as a whole", not which part |
| **Faults** | The eight faults not confounded by a tool gap or the wiki artifact, on the post-step-3 burned set, n=10 per arm (80 attempts each) |
| **Expected effect** | Unknown in sign. Planned to see a swing of 15 points or more |
| **Can we see it?** | Only that large. Smaller differences are inside the noise, and the step says so beforehand |
| **Decision rule (fixed now)** | A−cand ahead by 15 points or more: the KB is net harmful here, and step 4 starts with 4.2 and 4.3. A−cand behind by 15 or more: the KB helps, and step 4 targets coverage. Within 15 either way: **cannot tell**, decided on the offline evidence and said so (tripwire 1) |
| **Budget** | about $50 per arm (the baseline cost $0.57 an attempt) and about 12 hours of wall clock per arm |
| **Done and stop** | Done when both arms are scored. Gated by checkpoint 1. Stopped if an arm loses attempts to the harness; a lost attempt is rerun, not scored |

### Step 4 — Improvements, one at a time, each settled offline first

Each is one change for one thing (tripwire 2: a third change aimed at one thing is a
checkpoint). Each is measured on 2a and 2b before any live run.

| # | Change | Offline measure | Live? |
| --- | --- | --- | --- |
| 4.1 | **Search:** aliases and log-line triggers ("password authentication failed" maps to an authentication-failure entry), and a category listing in place of "no match" | hit rate on the held-back query half; no drop in 2a recall | no |
| 4.2 | **Coverage honesty:** for a symptom with no authored discriminators, say so and list classes to consider, and have `open` name no leader | false-leader rate down; recall unchanged | no |
| 4.3 | **Open-world candidates and config checks:** a standing "not listed" candidate, and for each candidate the object to read (spec, env, ConfigMap) beside its log and metric checks | recall of causes that live in configuration | milestone |
| 4.4 | **A survey routine for a vague question** ("something is wrong with X", no symptom): a sweep of every workload kind and the configuration on the path | needs live behaviour | milestone |
| 4.5 | **Mine the missing families** with `k8srca kb mine`, from public sources only: `networking` (DNS), `rollouts-and-jobs`, `service-traffic`, and new ones for credentials and authentication, and timeouts and retries. About $5 a family | recall and false-leader rate on the corpus | milestone |

4.5 is the one place the tripwire matters most. A family is mined once from public guides,
blind to the fault that reaches it, then measured on that fault's capture. A second iteration
on the same family is a checkpoint, not another edit.

**Alternative for 4.1:** design 011's row 4 (symptom matching with a decision model) answers
the same problem. If 4.1's aliases leave a high miss rate, compare the two offline on the same
held-back queries, as part of 011's step 5. Design 011 otherwise targets text-read facts and
does not address the failures this design started from (not reading a configuration object,
stopping at the first explanation).

### Step 5 — Live confirmation at a milestone

| | |
| --- | --- |
| **Hypothesis** | The changes that passed offline do not regress, and 4.3 to 4.5 move the faults they reach |
| **Design** | Arm A against A plus the shipped changes, n=3 per fault reached (process.md §6, the outer loop), on the burned set |
| **Can we see it?** | A regression of 15 points or more across the reached faults, not a small gain. A change is kept on its offline evidence, with the live run as a guard |
| **Budget** | about $40 |
| **Done and stop** | Done when no change regresses. A change that does is backed out |

## 5. Tripwires specific to this design

1. **A KB entry written from an SREGym fault's run or root-cause text** (process.md
   tripwire 3). Entries come from public sources only.
2. **The instrument moved.** The corpus labels, the query labels, the arm definitions or the
   decision thresholds changed after a measurement began.
3. **Drift.** The work becomes tuning the KB to the 14 faults, not evaluating whether it
   helps.
4. **Two inconclusive arms in a row** (step 3 and step 5).

## 6. Checkpoints and who decides

- **Checkpoint 1, after steps 0 to 2.** The owner decides whether step 3 is justified.
  Decided 2026-11-12 (owner): step 3 is gated on the false-leader rate alone, from step 2a. It
  goes ahead if that rate on symptoms with no authored discriminators is 25% or more. The
  earlier second gate, that a third of wrong consulted answers adopt a KB-listed candidate,
  is dropped: step 1 could not measure it reliably (§10.2). If the rate is below 25%, the KB
  is not the main problem, step 4 is decided on judgment where it is cheap, and the design
  closes as done in part.
- **Checkpoint 2, after step 3.** Continue to step 4 as the decision rule says, change the
  plan, or close.
- **The design ends** as done, done in part or abandoned (process.md §4), with: the answer
  to question 1 (helps, hurts, or cannot tell), the changes that shipped with their offline
  evidence, and what remains.

## 7. Budget and calendar

| Steps | Model spend | Work |
| --- | --- | --- |
| 0 to 2 | about $2 | 1 to 2 weeks |
| 3 | about $100 for both arms | about 24 hours of wall clock |
| 4 | about $25 for mining | days, offline |
| 5 | about $40 | about 12 hours |
| **Total** | **about $170** | |

Steps 0 and 1 can run during 014. Steps 2c to 5 wait for 014's step 3, and the KB is not
touched before then. The figures use the baseline's $0.57 an attempt and about 8 to 9
minutes of wall clock an attempt.

## 8. Risks and unknowns

- **Per-attempt joins.** A fault with a lost attempt or an orphan record has more session
  records than attempts. Step 0 marks and excludes those.
- **Can the SREGym faults be captured?** Unchecked (2c). If not, the corpus is the 21 existing
  scenarios, which reach few of the missing families, and step 4.5 cannot be measured and is
  decided on judgment, as 007 says of any family no scenario reaches.
- **Loudness.** Coverage and ease are confounded in the baseline. The corpus and the step 3
  fault set should include loud and quiet faults in the covered and uncovered families;
  the coverage coding must be done before the outcomes are looked at.
- **A−cand is a blunt arm.** It removes the candidate surfaces together. If it comes out
  inconclusive, a finer ablation is a new plan, not an extension.
- **Reading the baseline.** Steps 0 and 1 read saved sessions of the 14 hold-out faults, which
  014 §3's exposure paragraph allows as analysis; they are not used to design arm B.

## 9. Relation to other designs

- **007** supplies the KB's format, `kb mine`, and the "scenario per family" requirement that
  step 2c is meant to meet.
- **011**: row 4 (symptom matching) is an alternative to step 4.1. Decision models otherwise
  target text-read facts.
- **013** is parked and independent.
- **014** is the source of the findings, and its exposure rule governs the timing.

## 10. Results

| Step | Plan | Outcome | Decision |
| --- | --- | --- | --- |
| 0 | §4 | Done 2026-11-12. All 14 faults join to the ledger; 69 of 70 attempts joined | Done |
| 1 | §4 | M1 and the within-fault comparison reported; M2 and M3 dropped (matching rule agreement 70%, below 85%) | Stopped early as planned; no evidence of steering, none against it |
| 2a | §5 | Done 2026-11-12 on 12 cases (§10.3): recall 11 of 12 at the top level; printed false-leader rate 5 of 12 at any grade, 2 of 12 at strong or moderate; the corpus has no unauthored-symptom case | Gate not decidable as worded; owner to choose a reading (§10.3) |
| 2b | §5 | not started | |

### 10.1 Step 0: the join (2026-11-12)

Each row of SREGym's `results/*/k8srca/<fault>/*_results.csv` was matched to a suite run record
by answer text (`Diagnosis.submission` against the record's `answer`, `raw_answer` or
`first_answer`), then the latest five rows per fault were taken as the final clean set. Their
`Diagnosis.success` counts equal the ledger's `passes` for all 14 faults (25 in all). The one
exception to 70 joined attempts is `service_dns_resolution_failure_social_network`, whose lost
(`incomplete`) attempt has no submission; it is left out of per-attempt analyses, as planned,
leaving 69. Session events for the 69 were fetched by `session_id` and saved with the analysis
scripts outside the repo. Which attempt of `service_dns` passed is now known (it is in the CSV);
the joined table is not reproduced here because it holds answers and scores (014 §3).

### 10.2 Step 1: the steering trace (2026-11-12)

Cost: about $1 of Sonnet 5.5 labelling. Consulted means a session whose bash ran
`kb_query.py open` or `lookup` (M1); 46 of 69 did.

**M1, consulted, by the judge's verdict (step 0):** 14 of 25 passing attempts (56%) and 32 of
44 failing ones (73%). This is confounded by fault: `edge_request_filter` and `kafka_poison_pill`
pass 5 of 5 with no consultation at all, while the faults in the KB's covered families are
consulted every time and pass less.

**Within the five faults where some attempts consulted and some did not**, consulted attempts
passed 3 of 11 and unconsulted 1 of 13. That is 27% against 8%, a difference inside the noise
(one fault, `finalizer_deadlock`, supplies 3 of the 4 passes). It does not say consulting
helps, and it gives no sign that consulting hurts.

**M2 and M3 (the answer names a KB-listed candidate; the one `open` ranked first): dropped.**
A model read all 46 consulted sessions under the rule fixed beforehand (design §4, Step 1).
Its labels gave M2 79% of passing and 69% of failing attempts (22 of 32 wrong answers named a
listed candidate) and M3 36% and 34%. I then labelled 20 sessions myself, seeing the answer's
opening and the KB item names but not the model's labels. We agreed on 14 of 20 (70%). Five of
the six disagreements are the same judgment: whether a more specific cause than any listed item
(a hung `rate` handler against "downstream dependency slow"; a pod unschedulable for a
combined affinity and volume reason against "pod-not-scheduled") counts as listed. The rule
does not settle it. The agreement is below the 85% the plan set, so M2 and M3 are not reported
as findings and the rule is not reworded and re-run on the same sessions. Deviations from the
plan: the first reader was a model, not a person, and the second read 20 sessions, not a
labelled 40 and 20.

**M4 (right and the cause not a KB candidate)**, from the model's labels and so of lower
confidence: 3 of 14 passing consulted attempts.

**What this supports.** The planned hypothesis was a 25-point gap between wrong and right
answers in how often they adopt a KB candidate. The model's labels show none (wrong 69%, right
79%), and the labels are too uncertain to lean on. Two things can be said without the
matching rule: the KB's output is long and mostly generic (the lists above run to 20 or more
items, most broad), so almost any answer overlaps it, and wrong answers do not stand out by
consulting it more once the fault is accounted for. Neither shows the KB confuses the agent;
neither shows it helps.

**Checkpoint 1.** Not reached. Its first threshold (a third of wrong consulted answers
adopting a KB candidate) cannot be tested honestly without M2, and the second (the false-leader
rate on unauthored symptoms) comes from step 2a, which has not been run. The model's 69%
meets the first threshold on its face, but the threshold would be met by any broad list, so
it is not evidence of steering. Decided by the owner on 2026-11-12: the false-leader rate of step 2a is the only gate for step 3 (§6).

### 10.3 Step 2a: recall and false leaders (2026-11-12)

Labels: `tests/kb_eval/labels-2a.yaml`, committed (`7e9999a`) before the first run. Script:
`tests/kb_eval/measure_2a.py` (`uv run python tests/kb_eval/measure_2a.py`), no model, no
cluster. 12 cases from the 15 scenarios; three have no KB claim or no symptom to open
(`itbench-31`, `itbench-80`, `nothing-is-wrong`), and the two `jvm-oom-*` cases are near copies. `open` ran on the facts the facts layer derives from each scenario's recorded results,
without the text-read facts an agent would add, so each standing is a floor.

**Recall (what `lookup` lists).** The true cause is a top-level candidate in 11 of 12 cases, at
rank 1 to 4 of a list of 6 to 13 (one at rank 9 of 9: `gc-thrashing-near-memory-limit`). In the
twelfth (`pvc-pending-no-storageclass`) it is reachable below a candidate. Recall is not the
problem on this corpus; clutter (6 to 13 candidates, most of them not the cause) is the cost.

**Leader (what `open` prints on the `most likely:` line).**

| | correct | false leader | no leader |
| --- | --- | --- | --- |
| as printed | 5 | 5 (2 strong, 3 weak) | 2 |
| as `ranking` computes it among the symptom's candidates | 6 | 3 (all strong) | 3 |

The false leaders: `application-release`, graded strong, for `deploy-regression` and
`liveness-probe-wrong-port`; and a weak "application crash; within it, port already in use" for
`itbench-24-kafka-port-change`, `jvm-oom-on-startup` and `jvm-oom-with-metrics`. The last two
are `oom-kill` in truth, and `oom-kill` had the highest grade (moderate).

**A defect in `open`'s output, found by this run.** `ranking` computes the right leader for
the two `jvm-oom-*` cases (`oom-kill leads (moderate)`), but `fmt_open` prints the first
depth-0 claim found in a flat set of every group's leaders (`kb_query.py`, `top = [cid for d,
cid, _ in rows if d == 0 and cid in leads]`). `application-crash` is in that set because it
leads the sub-causes of `failing-init-container`, so it prints as "most likely" at weak while
`oom-kill` waits in the undecided list. The printed line differs from the computed leader in 4
of 12 cases. The same weak line ("application crash; within it, port already in use") appears
in the saved SREGym sessions' `open` output. This is a measurement finding about the shipped
`kb_query.py` (filed as k8srca#18); nothing is changed (§2).

**The gate (§6), under three readings.** The gate is a false-leader rate of 25% or more on
symptoms with no authored discriminators. All 12 cases have an authored truth claim, so there is
no case for the gate as worded: `open` is not offered on a bare alert, where the question is
whether the first-listed candidate misleads, and that needs a corpus the scenarios do not
supply (SREGym's faults after 014 step 3, step 2c). On the cases that exist:

| reading | rate | against 25% |
| --- | --- | --- |
| printed, any grade | 5 of 12 (42%) | meets |
| printed, confident only (strong or moderate), as §4 describes it | 2 of 12 (17%) | does not meet |
| computed leader, any grade | 3 of 12 (25%) | meets, at the line |

Two cautions on the confident readings. All of them are `application-release`, which is true at a
coarse level in `deploy-regression` and `liveness-probe-wrong-port` (both are rollouts that
introduced the fault) and wrong only in being less specific than the cause. The labels fixed
beforehand call it a false leader and that is what is counted. And n is 12: one case moves a
rate by 8 points.

**Decision for the owner.** Which reading gates step 3, or whether to wait for 2c to supply
unauthored cases. The numbers do not settle it; the defect is a reason to fix the rendering
(a code change that needs its own plan and cannot go in before 014 step 3).
