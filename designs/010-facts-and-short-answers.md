# Design 010 — Facts in code, short answers, checklist grading

**Status:** in progress: steps 1 and 2 done, 2026-10-21.

Changes [002](002-investigation-model.md) (how the agent reasons),
[004](004-scenario-testing.md) (how answers are graded and what is run),
[007](007-knowledge-representation.md) §6.1 (the completeness check and
revision turn) and [008](008-conclusions-under-incomplete-evidence.md) §3
(the answer), §6 (grading) and §7 (the reviewer). Where they disagree with
this one, this one is the intended direction and they describe what is built.

---

## 1. Why

Four and a half weeks of measurement, 2026-09-20 to 2026-10-21:
- **Spend:** 538 agent sessions cost $217. Grading is not logged; it is
  estimated at about $200 more.
- **Scenarios:** 82% of runs were on the two jvm scenarios.
- **Two different outcomes.** The cause was right in 97% of runs, and at
  ceiling on jvm since late September. Evidence passed in 2–24% of jvm runs in
  every week, and six changes aimed at it did not move it.

**What moved results durably** made the agent's job smaller:
- the k8stools composite tools: jvm traps 6/12 → 10/12, half the calls, 36%
  cheaper;
- shorter tool descriptions: traps 3/12 → 6/12;
- taking diagnostic guidance out of the architecture skill: traps 2/6 → 6/6;
- having the knowledge base at all;
- the completeness check.

**What did not stick** was nearly every instruction-level change measured on
jvm since 2026-10-14. Most were recorded as "no large effect", reverted, or
traded one dimension for another.

Four findings drive this design.

**The agent derives low-level facts by hand, and that is where it fails.**
Of 1,277 unsupported claims since 2026-09-30:
- 55% are about time, horizons, counts or generalizing over every instance:
  "crash-looping for the full 163 days", "SIGKILLed 2–8s into every startup",
  "metrics retain 5–6 days";
- 28% generalize about other workloads ("every other workload is healthy");
- 6% are about the diagnosis.

The agent writes facts such as `killed_in_every_retained_life[C]` from raw
counts and windows: no code makes that mapping. The latest example: a
promtools note gave the span missing from a window beside the word
"retention", and answers read it as the retention (009 §7.5).

**The agent is told too much, and some of it is tuned to the tests.** Before
any evidence it reads about 21k tokens of method and knowledge-base text:
- the coordinator prompt;
- SKILL.md, 15.7k characters with about 40 directives;
- the architecture index;
- `lookup`, `claim` and `open` output, about 12k characters each.

"Every candidate needs a place" is said about six times. Several rules are
scenario traps restated as instructions:
- jvm's back-off count wording, SKILL.md:80–87;
- itbench-33's FailedScheduling message, SKILL.md:229–239;
- jvm's back-off cadence, `review.py:176`.

Some rules contradict others: "keep it tight" against a four-section format,
and "leave out what you did not observe" against "owe every open candidate a
place".

**The answer asks for too much.** 008's shape needs about 25 elements for jvm:
seven candidates over two levels, each with strength, facts for and against,
and what would change it. Answers run 1,000–1,600 words, against 265 for
008's own example. The revision turn adds 35–70% and doubled the cost per
run, from about $0.30 to $0.60.

**Answers vary too much between runs, and the grader too much between
gradings, to show small changes.**
- Two n=5 arms of an identical agent differ by 2 or more on evidence about
  30% of the time.
- The grader disagrees with itself on evidence for an identical answer 11–16%
  of the time. One borderline claim fails the dimension, and the count of
  unsupported claims differed in 44% of re-gradings.
- The reference for each grading call is 90–120k tokens, mostly the raw
  capture.

The merge rule, a move of 2 at n=5, cannot separate a real effect from this
noise. About 4 of 25 arms since 2026-10-16 cleared it.

## 2. Principles

1. **Code states facts, the model decides what to do with them.** Every
   observation the answer can rest on — a count, a duration, a scope, a
   horizon — is produced by deterministic code from a tool result, as a short
   statement. The model chooses which tool to call next, when to stop, which
   cause the facts point to, and how to say it.
2. **Numbers come from code.** The answer's figures are the facts' figures,
   printed by the host in footnotes. The prose may repeat a figure only where
   a cited fact carries it.
3. **Say less, and only what a fact supports.** The answer is a few hundred
   words. A candidate appears only if a fact rules it in or out. What nothing
   decided appears only as a next step.
4. **Instructions describe the job, not the test.** No rule is written from a
   scenario's trap. A trap that keeps failing is a missing fact, and goes into
   the facts layer (§3).
5. **Grade against a checklist of statements.** Each scenario lists the
   statements an answer should and should not make. The grader answers
   present or absent per item. It reads the checklist and the cited facts,
   not the whole capture.
6. **Breadth over depth in testing.** A change is measured across many
   scenarios at small n, never by tuning one scenario at larger n.

## 3. The facts layer

### 3.1 Where it lives

In k8srca, in the worker, as `src/k8srca/facts/`. It reads MCP results, never
the cluster, so the rule against direct cluster access holds unchanged. It may
move into k8stools and promtools later, once its statements prove general
(§11).

The worker already sees every tool result. `tools.py`'s `call_mcp` writes each
one to the evidence record before returning it to the agent. The facts layer
runs at that point:
1. Derive facts from the result.
2. Append them to the evidence record, in `evidence/facts.jsonl`.
3. Append them to the result the agent sees, as a `facts` block of numbered
   statements. The raw result stays below it.

### 3.2 A fact

```json
{"id": "F7", "subject": "default/Deployment/ad/ad",
 "key": "last_termination_reason", "value": "OOMKilled",
 "statement": "ad's last 3 terminations were OOMKilled, exit 137",
 "scope": "the instances Kubernetes retains",
 "source": {"tool": "k8s_get_workload_report", "n": 3}}
```

- **`id`.** Numbered in order within the session, so they are stable across
  turns of a thread. Readers never see it (§5.2).
- **`key` and `value`.** In the knowledge base's fact vocabulary (007 §4), so
  `kb_query.py open` can read the facts file directly.
- **`statement`.** Written by a template per key. It is the sentence the
  footnote prints, so it is readable on its own: subject, value and scope,
  with no reasoning.
- **`scope`.** What the record covers, in plain words. Scope stays inside the
  fact: the agent never works it out.

**Horizons are facts too, one per source per session:**
- "events cover the last 10 days";
- "metrics cover the last 31 hours of the 7 days asked for";
- "the previous instance's log is retained; earlier ones are not".

They are stated once, by code, and the answer cites them where they matter,
for example for "no onset is visible". The agent never computes or restates a
window.

### 3.3 First derivations, from the composite tools

The tools agents call most already return structured results (k8stools 3.0.0
composites, the promtools reports). Each derivation is a small function over
one result shape, unit-tested against recorded results from the scenario
captures.

| Tool | Facts |
| --- | --- |
| `k8s_get_namespace_health` | which workloads are unhealthy, and how (crash-looping, pending, degraded); "every other workload in the namespace is ready", stated only when true |
| `k8s_get_workload_report` | last exit code, reason and exit meaning per container; restart count; last instance's lifetime and restart gap; limits and requests, `limit_equals_request`; probes configured or none; last template change, its age and what it changed; usage against limit; whether every retained termination was a kill; a pod that cannot be scheduled |
| `k8s_get_pod_spec` | runtime sizing flags in env and args (`-Xmx`, `MaxRAMPercentage`, `GOMEMLIMIT`) present or absent; a node selector |
| `k8s_get_events` | per reason: count, first and last seen, and the events horizon |
| `k8s_get_node_summaries` | node conditions (pressure, not ready), stated per node only when abnormal, plus one "all nodes ready, no pressure" fact |
| `prom_memory_report` | per container: peak against limit; how many sampled lives started at the limit, rose to it, or stayed clear (`lives`); the metrics horizon |
| `prom_restart_report` | restarts per hour (range), hours without a restart, reasons and their share of the window, whether a healthy period precedes the kills, the metrics horizon |

The KB facts that needed scope reasoning are derived where a result settles
them:
- `killed_in_every_retained_life`: from the last termination and the
  back-off series' span, or from the reason series in the metrics;
- `memory_limit_plausibly_reached`: from usage or the metrics' peak;
- `memory_grows_within_life`: from the metrics' lives;
- `healthy_period_before_kills` and the reserved `onset_observed`: from where
  the failures sit in each record.

**Not derived, as built (step 1):**
- `completes_startup_in_retained_lives`. Whether a life finished starting is
  read from its log, which no result field states. It stays with the agent
  (§3.4).
- `changed_before_onset`. With no onset visible, there is nothing to put a
  change before.

**Two scopes the results forced:**
- **Sizing flags.** The pod spec does not show variables the image sets: jvm's
  `JAVA_TOOL_OPTIONS` came from the image. The sizing fact says it covers the
  spec only.
- **Readiness.** Before k8stools 4.1.0, `k8s_get_namespace_health` listed
  itbench-33's `ad` and `cart` as ready while their new pods could not
  schedule (k8stools #27). 4.1.0 counts a workload with an unscheduled pod or
  a failing Deployment condition as unhealthy, and the facts layer states both
  (`pods_unscheduled`, `rollout_condition`), with the ready count scoped to the
  pods serving.

### 3.4 What the agent still writes

Facts that need judgment over text stay with the agent, for example "the log
ends mid-startup, with no exception". The agent adds them through
`kb_query.py fact <workload> '<fact>' --says '<sentence>' --from <result n>`,
which appends them to
`facts.jsonl` marked `"by": "agent"` and cites the result they came from. They
are footnoted like any other fact, so a reader can see which facts are the
agent's reading.

`open --workload <name>` reads `facts.jsonl`. The hand-written facts file and
`--facts` go away in step 3, when the skill stops asking for them.

## 4. The agent

**Its job is the loop:**
1. Read the facts so far.
2. Pick the next tool, from `open`'s cheapest open check.
3. Stop when the leading cause is decided, or when no cheap check is left.
4. Write the answer (§5).

**SKILL.md shrinks to that loop**, the answer shape and the stopping rule: a
target of about 3k tokens, from 15.7k characters. It loses:
- every rule written from a trap;
- the scope rules ("a fact covers what the record retains…"), which are now
  in the facts layer;
- the per-candidate placement rules;
- the repeats.

**`kb_query.py` output shrinks with it:**
- `lookup` and `open` drop the 900-character `timing` paragraph and the
  per-check `PARTIAL` caveats. What they warned about is now a fact's scope,
  or a horizon fact.
- `open` prints three things: the leader with the facts behind it; what is
  ruled out, with the fact that rules each out; and the open candidates, each
  with its cheapest next check.
- The "owes a place" footer goes.

**The coordinator prompt** keeps routing, delegation and Slack form. It loses
what SKILL.md says.

## 5. The answer

### 5.1 Shape

At most about 300 words, in four parts:
1. **Most likely cause**, in a sentence or two, with one confidence word:
   *likely* or *very likely*, from `open`'s strength.
2. **Why:** the two to four facts it rests on, as sentences with footnote
   markers.
3. **Ruled out:** only candidates a fact excludes, one line each, with its
   marker.
4. **Next steps:** what the person should do or check. A candidate nothing
   decided appears here, and only here, when a step would settle it: "to rule
   out a leak, …".

No candidate list, no strengths per candidate, no "what would change it"
section. The uncertainty is carried by the confidence word and by the next
steps.

### 5.2 Sentences and footnotes

The agent writes plain sentences with markers that name facts, such as
`[F7]`. Before the answer is posted, the host:
- replaces each marker with a number in order of first use, `[1]`, `[2]`;
- appends an **Evidence** block that prints each cited fact's `statement` and
  source tool, from `facts.jsonl`.

The model does not write the Evidence block. In Slack it goes under the
answer, or in a collapsed attachment if it is long.

Readers see sentences and numbered notes, never IDs. Exact figures sit in the
notes, so the prose can stay qualitative: "killed for memory at every start".

**Deterministic checks before posting:**
- every marker resolves;
- the answer is within the length limit;
- every number in the prose appears in a fact it cites, so "300Mi" passes
  where [F9] says 300Mi.

A failure goes into the reviewer's revision prompt (§6).

### 5.3 Example (jvm-oom-with-metrics, illustrative)

> **Most likely:** the `ad` container's 300Mi memory limit is too low for the
> JVM it runs, so it is killed for memory as it starts (very likely).
>
> **Why:** every retained termination is OOMKilled[1], and in every sampled
> life memory is at the limit from the first sample or rises to it within
> seconds[2]. The limit equals the request, and the JVM has no heap
> sizing[3][4].
>
> **Ruled out:** an application crash — no exception is logged before the
> kills[5]. A liveness probe — none is configured[6].
>
> **Next steps:** raise `ad`'s memory limit, or cap the JVM's heap with
> `-XX:MaxRAMPercentage`. To rule out a leak, check whether
> memory keeps climbing after startup once it runs with more headroom. No
> change is visible in the 10 days events cover[7].
>
> *Evidence*
> [1] ad: last 3 terminations OOMKilled, exit 137 (k8s_get_workload_report)
> [2] ad: memory at the 300Mi limit from the first sample in 182 of 214
> sampled lives; the other 32 reach it within 31s (prom_memory_report)
> …

The prose is about 150 words; the Evidence block is extra.

## 6. The knowledge base and the light reviewer

**The knowledge base keeps the ranking and drops "owed".** `open` still ranks
candidates from facts and finds the leader, and it still drives which tool is
next. The completeness check of 007 §6.1 is replaced by two deterministic
checks:
- the leader appears as the most likely cause;
- every candidate `open` rules out with a fact, and that the truth's
  checklist expects (§7), is named under ruled out.

**The light reviewer** replaces the reviewer of 008 §7. It runs once, on
every turn of a thread, and reads only:
- the answer;
- the statements of the facts it cites;
- the deterministic check failures (§5.2).

It answers one question: does each sentence say only what its cited facts
say? It flags:
- a sentence that claims more than its facts, such as "every" from "the last
  3";
- a factual sentence with no marker;
- a cause stated more strongly than `open`'s strength.

Flags go back as one revision turn. The reviewer never sees the whole
evidence record, and never asks for more content.

## 7. Grading

**Each scenario gains a checklist** (`checklist.yaml`, beside `truth.yaml`, so the rubric grader that reads the whole truth is unmoved) of statements an answer should
make (`expect: present`) or should not (`expect: absent`). Each item has:
- an id;
- the statement, in one sentence;
- two or three acceptable phrasings;
- for an absent item, the tempting wrong version.

```yaml
checklist:
  - id: cause-limit-too-low
    expect: present
    statement: ad's memory limit is too low for its JVM
    accept: ["300Mi limit too low", "limit below what the JVM needs at startup"]
  - id: rules-out-application-crash
    expect: present
    statement: an application crash is ruled out
  - id: no-onset-claim
    expect: absent
    statement: the failure began at the 183-day-old rollout
    tempting: "crash-looping since the rollout 183 days ago"
  - id: no-fleet-claim
    expect: absent
    statement: every other workload is healthy
```

The grader returns `present`, `absent` or `unclear` per item, with the
sentence it relied on. Its reference is the checklist plus the answer's cited
facts: a few thousand tokens, not the capture.

**Deterministic checks stay deterministic:** the cause terms
(`must_identify`), the length, and the footnote checks of §5.2.

**The score** is items met out of items listed. The 008 dimensions become
groups of items:
- cause;
- ruled out: the old standing;
- traps: absent items;
- gaps: present items about what cannot be seen;
- next steps.

This keeps the history readable.

**Before the checklist replaces the current rubric, measure its noise:** grade
saved answers three times each. The target is per-item agreement of 97% or
more; the current evidence dimension's disagreement is 11–16%. An item that
splits is rewritten until it does not.

## 8. Testing

**Freeze jvm.** It is at ceiling on cause, and its remaining failures are
what §3 addresses in code. No change is tuned on it alone.

**Widen the suite.** The four recorded scenarios are:
- jvm-oom-on-startup;
- jvm-oom-with-metrics;
- itbench-33-invalid-node-selector;
- checkout-gc-pressure.

`nothing-is-wrong` is not one of them. Its capture was re-cast as
checkout-gc-pressure (004 §5), and it needs a recording on a clean checkout
path.

Add the unrecorded ones from 004 §5:
- probe-too-aggressive;
- image-pull-typo;
- pvc-pending-no-storageclass;
- deploy-regression;
- innocent-bystander;
- evidence-unavailable.

Each new truth is a checklist from the start.

**Run more scenarios at fewer runs each.** A change is measured at n=2–3 on
every scenario it reaches, and read across the suite. A real effect shows as
movement in the same direction on most scenarios, not as a 2-point move on
one.

**Two component evals need no agent session:**
- **Facts layer:** recorded tool results in, expected facts out. Unit tests,
  no model.
- **Answer writer:** a scenario's facts in, the answer out, graded against its
  checklist. Each case is one model call on fixed input, not a replay of an
  investigation. The frozen-evidence screen replayed whole sessions and did
  not reproduce live answers (004 §6.4). This fixes the input at the facts
  instead.

**Every change gets a dollar budget** up front. A live suite run at n=2 over
eleven scenarios is about $15–20 in sessions plus grading.

## 9. Staging

Each step carries a plan as [docs/process.md](../docs/process.md) §1 asks:
- what it should show;
- its budget;
- what finishes it (**done**);
- what ends it early for a checkpoint (**stop**).

Each step allows at most two revision rounds before a checkpoint. The grader
and checklists are frozen once step 2 is done (process §5).

**1. The facts layer** (§3), for the composite tools and promtools reports,
with `open` reading `facts.jsonl`.
- **Shows:** whether the facts layer can state what answers keep getting
  wrong.
- **Budget:** no model spend; about 3 days.
- **Done:**
  - Every derivation in §3.3 is unit-tested on recorded results from the five
    scenarios.
  - `open` reaches the same leader from derived facts as from the
    hand-written jvm facts.
  - Every horizon is stated as a fact.
- **Stop:** a §3.3 fact that the composite results cannot support. Decide
  whether to add it to k8stools, have the agent write it, or drop it, rather
  than approximating it.

**2. Checklists** for the four recorded scenarios, and the grader that reads
them.
- **Shows:** that the instrument is steady enough to measure step 3.
- **Budget:** about $10 of grading; about 2 days.
- **Done:** on saved answers graded three times each, each item gets the same
  verdict in 97% or more of gradings.
- **Stop:** two rounds of rewording leave agreement below 97%. Rethink the
  checklist format, not the items.

**3. The answer and the agent:** short SKILL.md (§4), the answer shape and
footnotes (§5), host rendering in the runner and in Slack.
- **Shows:** whether shorter instructions and a short footnoted answer hold
  the cause and cut unsupported claims.
- **Budget:**
  - the answer-writer eval: about $5;
  - live at n=3 on the four scenarios against main: about $25;
  - about 4 days.
- **Done:** across the four scenarios,
  - cause is no worse than main on any scenario;
  - the checklist score is at least main's on every scenario;
  - answers are 300 words or fewer, without the Evidence block;
  - cost per run is lower than main's.
- **Stop:** the checklist score is worse on three or more scenarios, or cause
  drops on any. That calls a checkpoint, not a round of tuning.

**4. The light reviewer** (§6), on and off.
- **Shows:** whether a reviewer earns its turn once numbers come from facts.
- **Budget:** about $15, at n=3 on the four scenarios.
- **Done:** a decision.
  - Keep it if it removes claims beyond their facts without lowering the
    checklist score.
  - Otherwise turn it off.
- **Stop:** none beyond the budget. This step is one measurement and one
  decision.

**5. New scenarios** from 004 §5, recorded and given checklists, two or three
at a time.
- **Shows:** whether the step 3 result holds on failures it was not built
  from.
- **Budget:** set per batch.
- **Done:** at least six scenarios beyond the first four, each with a
  checklist that passes step 2's agreement bar.
- **Stop:** a new scenario fails in a way step 3's design does not address.
  Record it, then decide whether 010 covers it or a new design does.

Steps 1 and 2 are independent and can go in parallel. Step 3 needs both.

**Results** are a table per step: plan, outcome, decision. Run logs go to the
measurement journal (process §4).

### 9.1 Step 1 result (2026-10-21)

| | |
| --- | --- |
| **Plan** | Derivations for the composite tools and promtools reports, unit-tested on recorded results; `open` reading them |
| **Built** | `src/k8srca/facts/` (derivations for 7 tools, the log, the block the agent reads); `scenario record-results`, which saved 42 real results from the four scenarios' replays; `kb_query.py open --workload` and `kb_query.py fact`; the worker hook, behind `facts.enabled`, off. 29 tests. No model spend |
| **Done criteria** | Met. Every derivation is tested on recorded results. On both jvm scenarios `open` leads with OOM and then memory limit too low, at strong, as from the hand-written facts. With metrics this needs derived facts only. Without metrics it also needs the agent's two log readings (§3.4). Events, metrics and logs each have a horizon fact |
| **Stop criterion** | Met by one fact: `completes_startup_in_retained_lives` has no result field behind it. Proposed: the agent writes it (§3.4) |
| **Found** | `k8s_get_namespace_health` counts a stuck rollout's workload as ready. The pod spec does not show the image's environment. `nothing-is-wrong` is not a recorded scenario |
| **Decision** | Close the step, done (owner, 2026-10-21). The agent writes `completes_startup_in_retained_lives` from the log (§3.4). The readiness gap is k8stools issue #27 |

### 9.2 Step 2 checkpoint (2026-10-21)

**Tripwire:** budget (process §2.5). The first batch's results were lost to an
output bug after it was paid for, and the owner raised the budget to $20.
About $14 is spent; a second rewording round would pass $20.

| | |
| --- | --- |
| **Plan** | Checklists for the four recorded scenarios (`checklist.yaml` beside `truth.yaml`, so the rubric grader is unmoved), a grader that reads them, 32 saved answers graded 3 times |
| **Built** | `scenario/checklist.py`, `k8srca scenario checklist`; 54 items, written from each truth's cause, candidates, traps and gaps |
| **Round 0** | 14 of 1,296 verdicts differ from their answer's majority: 98.9% overall. 10 items below 97% |
| **Round 1** | One prompt rule (a hedged statement counts as made) and 9 items sharpened. 12 of 1,296: 99.1%. 8 items below 97% |
| **Validity** | Unanimous misses were spot-checked against the answers and hold: none of the jvm-oom-on-startup answers mentions heap sizing |

**Was the hypothesis wrong?** No. The checklist is far steadier than the
rubric: about 0.9% of verdicts dissent, against 11–16% for the rubric's
evidence verdict.

**Could the measurement see the effect?** Not per item. At 24 gradings an item
passes 97% only with no dissent at all. A uniform 0.9% flip rate predicts about
11 of 54 items with one dissent, and 10 and then 8 were seen. The splits also
moved: 7 items fixed by round 1 were replaced by 4 that had been unanimous and
were not reworded. The per-item bar at this n measures chance.

**Is the implementation incomplete?** In one place. `no-onset-date` on
jvm-oom-with-metrics split on 3 answers in both rounds. Those answers call the
183-day-old image bump "plausible as the trigger" and then say the onset cannot
be shown. That is a real ambiguity in the item, not noise. The other splits are
mostly long answers' "weakened" candidates, which 010's answer shape (§5.1) no
longer writes.

**Decision (owner):** change the plan. One more round, on the onset item only,
then close under a revised criterion: 97% or more overall, and no item split on
more than one answer.

**Round 2** reworded jvm-oom-with-metrics `no-onset-date` to say a change
offered as the possible trigger counts, even when hedged, and regraded only
that scenario (24 gradings, about $2; about $16 spent in all). It now splits on
one answer, not three, and its gradings agree that 7 of the 8 answers make the
claim, as the truth's trap describes. Against the revised criterion, one item
still fails, on round 1's data: itbench-33 `no-named-author` split on two
answers. Both are hedged ("shared Helm values/overlay is the likely vector";
"whatever deploy/CI step produced revision 2"), the pattern the onset item had.

**Closed (owner, 2026-10-21): done, under the revised criterion.**
`no-named-author` was given the onset item's fix on judgment and not regraded,
so its agreement is unmeasured. The instrument is frozen from here to the end
of 010 (process §5):
- the checklists, at these digests:
  - checkout-gc-pressure: c6ae958218776459
  - itbench-33-invalid-node-selector: 85bd0bc2073834a9
  - jvm-oom-on-startup: c5d6422d0189d3d0
  - jvm-oom-with-metrics: 96a462868e00dd1f
- the checklist grader, at apparatus digest da606073821551c6.

Agreement is checked again on step 3's short answers, which are what it will
grade.

### 9.3 Step 3 plan (2026-10-21)

Written before building, as process §1 asks. k8stools 4.1.0 is adopted first,
so both arms read the same tools.

**What changes, on a branch measured as one arm:**
- SKILL.md shrinks to the loop, the answer shape and the stopping rule (§4).
- `kb_query.py open` prints the leader with the facts behind it, what is
  ruled out and by which fact, and each open candidate's cheapest check. The
  timing paragraph and the owed footer go. `kb_query.py facts` lists the log,
  so the coordinator can cite facts from the investigator's calls.
- The coordinator prompt drops the answer shape; the investigator cites
  `[F<n>]` for what it reports.
- The facts layer is on (`facts.enabled`).
- The host renders `[F<n>]` markers into numbered notes and an Evidence block
  (§5.2), in the runner and in Slack.
- The host checks of §5.2 replace the completeness check: markers resolve,
  300 words or fewer of prose, every number in the prose in a cited fact.
  Failures go back as one revision turn. The reviewer stays off (step 4).

**Measurements, in order:**
1. **Answer-writer eval** (§8; inner loop). Each scenario's recorded facts and
   `open` output in, one answer out, graded on its checklist. n=3 per scenario.
   About $2. It settles the answer shape before any live run.
2. **Live, both arms** (outer loop, once). main and the branch at n=3 on the
   four scenarios, graded on the checklists. About $15 in sessions, $2 in
   grading.

**Can we see it?** Length and cost per run: yes, at any n. Cause: it sits at
ceiling, so a drop of one run in three shows. Checklist score per scenario: an
arm-to-arm noise figure does not exist yet, so at n=3 only a difference of
about two items per answer reads as real. Smaller differences are reported as
inside the noise, not as wins.

**Budget:** $25, about 4 days. **Done** and **stop** are §9 step 3's.

## 10. What this replaces

- **007 §6.1, the completeness check and "owed":** replaced by the
  ruled-out check (§6).
- **008 §3, the answer's four-section shape:** replaced by §5. 008's
  principle that answers rank rather than confirm stays.
- **008 §6, the dimensions:** replaced by groups of checklist items (§7).
- **008 §7, the reviewer:** replaced by the light reviewer (§6).
- **009's promtools notes:** keep their warnings, but the agent now meets
  them as fact scopes and horizon facts.
- **004 §6.4, the merge rule:** a change merges when it moves the suite and
  regresses no scenario's must-have items.

## 11. Rejected alternatives

- **Dropping the candidate list entirely.** The shortest answer, but it loses
  what was ruled out, which saves the reader from dead ends and is the main
  sign that alternatives were checked.
- **Fact IDs visible to readers.** Readers would have to look IDs up; numbered
  footnotes printed by the host give the same traceability.
- **Fully deterministic grading.** It cannot tell a sentence that claims more
  than its fact from one that does not. Keeping the LLM to present-or-absent
  per item keeps its judgment narrow.
- **002 §10.2's adapter layer.** 002 rejected backend-neutral adapters over
  MCP. The facts layer is not one: it sits behind MCP rather than replacing
  it, and it states what a result shows rather than abstracting the backend.
- **The facts layer in k8stools and promtools now.** That needs a release per
  iteration, and the statements are not yet proven general. Pushed down once
  they are.

## 12. Open questions

1. **How facts reach the agent.** Appended to every tool result (§3.1), or
   from a `kb_query.py facts` call on demand. Appending costs tokens on every
   call; on demand risks the agent never asking.
2. **Several failing workloads.** Facts carry their subject, and `open` runs
   per subject. Whether the answer handles two causes in 300 words is
   untested.
3. **Slack rendering.** Footnotes in mrkdwn, and when the Evidence block moves
   to an attachment.
4. **Follow-up turns.** A follow-up such as "has anything changed recently?"
   is answered from the change and horizon facts. Whether it gets the same
   shape, or a shorter one, is open.
