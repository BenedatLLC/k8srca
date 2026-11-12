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

- **Checkpoint 1, after steps 0 to 2.** The owner decides whether step 3 is justified. A
  proposal, to be confirmed before step 1 runs: step 3 goes ahead if the steering trace shows
  at least a third of wrong consulted answers adopting a KB-listed candidate, **and** the
  false-leader rate on symptoms with no authored discriminators is 25% or more. If neither
  holds, the KB is not the main problem, step 4 is decided on judgment where it is cheap, and
  the design closes as done in part.
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
| 0 | §4 | not started | |
| 1 | §4 | not started | |
