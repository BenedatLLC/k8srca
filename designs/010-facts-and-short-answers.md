# Design 010 — Facts in code, short answers, checklist grading

**Status:** in progress: steps 1–4 done, 2026-10-22.

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

**The light reviewer** replaces the reviewer of 008 §7. *Built and measured in
step 4, and off: §9.8.* It runs once, on
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

### 9.4 Step 3 result and checkpoint (2026-10-22)

**Budget:** raised from $25 to $40 by the owner when live sessions on checkout
proved to cost $1.10–1.70, against the $0.40 planned. About $23 is spent:
answer-writer eval $3, sessions $13.40 (main $8.93, branch $4.44), rubric
grading about $5, checklist grading about $2.

**The answer-writer eval** (inner loop, two rounds) found three gaps before
any live run: the recorder kept no result for the workload a question names,
the usage fact dropped CPU, and `open` listed sub-causes ruled out under an
undecided claim. All three were fixed. It also showed that only the restart
family has a ranking: for pending pods and latency `open` has nothing to rank,
and SKILL.md now says to rank `lookup`'s candidates by the same rule.

**Live, n=3 per scenario, checklists graded once:**

| Scenario | Arm | Checklist | Cause | Words | $/run |
| --- | --- | --- | --- | --- | --- |
| checkout-gc-pressure | main | 10.7/13 | 2/3 | 1,019–1,501 | 1.42 |
| | branch | 6.7/13 | 1/3 | 286–322 | 0.83 |
| itbench-33 | main | 10.7/12 | 3/3 | 652–696 | 0.49 |
| | branch | 10.3/12 | 3/3 | 295–311 | 0.19 |
| jvm-oom-on-startup | main | 12.3/14 | 3/3 | 1,026–1,068 | 0.56 |
| | branch | 13.3/14 | 3/3 | 468–566 (two turns) | 0.30 |
| jvm-oom-with-metrics | main | 13.7/15 | 3/3 | 921–1,272 | 0.51 |
| | branch | 13.0/15 | 3/3 | 479–548 (two turns) | 0.16 |

Words exclude the Evidence block. The jvm scenarios ask a follow-up, so their
counts are two answers of about 250 words each.

**Against the done criteria:**
- cause no worse than main on any scenario: not met on checkout (1/3 against
  2/3, one run);
- checklist at least main's on every scenario: not met. checkout is 4 items
  lower; itbench-33 and jvm-oom-with-metrics are under one item lower, inside
  the noise at n=3; jvm-oom-on-startup is one item higher;
- 300 words or fewer without the Evidence block: met within a few words; the
  host's check sends an over-long answer back once;
- cost lower than main's: met, by 40–70% on every scenario.

**Stop criterion:** met as written. Cause dropped on checkout, and the
checklist score is lower on three scenarios, two of them inside the noise.

**Was the hypothesis wrong?** Not on the restart family and the stuck
rollout: there the short answer holds cause and checklist at a third to a
quarter of the length and half the cost. On checkout it is worse by more than
the noise. The groups say where: cause 31/42 against 36/42 and gaps 11/18
against 15/18 across the suite, nearly all of it on checkout. Both arms' wrong
checkout answers take the same trap, `ad`'s crash loop seen through frontend's
errors; the branch took it in two runs of three.

**Could the measurement see it?** The checkout difference, yes: four items an
answer. The other three scenarios' differences are inside the noise.

**Is the implementation incomplete?** In two ways, both on checkout:
- the knowledge base has no ranking for latency, so `open` gives the agent
  nothing to rank with, and the loop of §4 has no step 2 there;
- the long SKILL.md carried a section on other failures in the cluster,
  written from checkout's traps, which the short one drops by design (§4). It
  was a rule from a run (process §2.3), and main still has it.

**Decision (owner, 2026-10-22):** change the plan. Give the knowledge base a
ranking for slow requests, so the loop of §4 has its ranking step on checkout,
and re-measure checkout. Merge step 3 only if checkout recovers.

### 9.5 Step 3b plan: a ranking for slow requests

**The risk, said first.** checkout's cause, garbage-collection pressure in the
service itself, is not one of the four candidates the vendored
HighRequestLatency record gives. A ranking that helps checkout needs a
candidate for a service's own resource pressure. Writing that candidate now,
after checkout failed, is close to process §2.3, knowledge written from one
scenario's run. The guard:
- the content comes from public guides through the mining pipeline (007 §8),
  not from checkout's truth or its answers;
- every fact and criterion is generic and observable (the header rules of
  `docs/rca/knowledge/pod-restarting.yaml`);
- step 5's new scenarios, not tuned on, test whether it holds; one of them
  should be a latency scenario whose cause is not resource pressure.

**Hypothesis:** with a ranking for slow requests, the short answer on checkout
leads with the service's own pressure and rules out what the facts exclude,
as it does on the restart family.

**Expected effect:** checkout's checklist score returns to main's (about 10.7
of 13), and cause to at least 2 of 3.

**Can we see it?** The gap was four items an answer, above the noise at n=3.

**Steps and budget:**
1. A `request-latency` family in `docs/rca/mining/sources.yaml`: public
   guides, licences checked by hand. Mine it (about $5) and review the draft.
2. Convert it to `docs/rca/knowledge/request-latency.yaml`, with `rules_out`
   for every claim.
3. The answer-writer eval on checkout (about $1).
4. Live: the branch on checkout at n=3 (about $3), against main's three
   runs from §9.4, which do not change. Checklist grading (about $0.30).

About $10, which takes the step to about $33 of the $40.

**Done:** checkout's checklist score within one item of main's, and cause
at least 2 of 3. **Stop:** either not met. That closes step 3 as done in
part, with latency carried to a later design.

### 9.6 Step 3b result (2026-10-22)

| | |
| --- | --- |
| **Built** | `request-latency` mined from seven public guides ($5.90) and reviewed (`docs/rca/mined/request-latency.yaml`, changes in its header): out-of-scope reachability claims dropped, the Go guide's GC-thrashing claim restored after the merge dropped it, a request `downstream-latency` claim in place of a probe claim reused by name. Facts: `runtime_memory_limit_configured` from GOMEMLIMIT, `cpu_limit_set` from the usage sample. `open` on checkout's derived facts now leads with GC thrashing, weak, and rules out CPU throttling by the missing CPU limit |
| **Eval** | checkout 10.3/13 at n=3 |
| **Live** | checkout, branch, n=3: checklist 11.7/13 (13, 11, 11), cause 3/3, $0.65 a run, 316–318 words. main's three runs from §9.4: 10.7/13, cause 2/3, $1.42 |
| **Done** | Met: within one item of main's checklist (above it), cause 3/3 |
| **Not re-measured** | The other three scenarios after these changes. The new facts and claims touch only Go memory limits, CPU limits and slow requests, none of which those scenarios' rankings use |
| **Spent** | About $34 of the $40 for step 3 in all |

**Step 3 across the four scenarios**, with checkout from 3b:

| Scenario | Checklist, main / branch | Cause, main / branch | $/run, main / branch |
| --- | --- | --- | --- |
| checkout-gc-pressure | 10.7 / 11.7 | 2/3 / 3/3 | 1.42 / 0.65 |
| itbench-33 | 10.7 / 10.3 | 3/3 / 3/3 | 0.49 / 0.19 |
| jvm-oom-on-startup | 12.3 / 13.3 | 3/3 / 3/3 | 0.56 / 0.30 |
| jvm-oom-with-metrics | 13.7 / 13.0 | 3/3 / 3/3 | 0.51 / 0.16 |

Against step 3's done criteria: cause no worse anywhere, met; checklist at
least main's everywhere, met on two scenarios and within one item (inside the
noise) on two; 300 words, met within about 6%; cost lower, met everywhere, by
45–70%.

**Decision (owner, 2026-10-22):** close step 3, done, and merge. Production
stays on the previous skill until `k8srca sync` is asked for.

### 9.7 Step 4 plan: the light reviewer (2026-10-22)

**Hypothesis:** a reviewer that reads only the answer and the facts it cites
removes sentences that say more than those facts, at the cost of one call and
sometimes one revision turn, without losing checklist items.

**Expected effect:** fewer claims beyond their facts, such as "every retained
life was killed after 8 seconds" where the fact gives 8s for the last life.

**Can we see it?** Not live. Step 3's short answers carry about 0.67
unsupported claims a run by the rubric grader (8 in 12 runs, 5 of them on
jvm-oom-on-startup). Halving it, 8 to 4, is inside chance at n=12, and that
grader disagrees with itself on evidence 11–16% of the time. So, as process
§1 says, the effect is measured offline, and the live run checks only for
harm.

**Measurements:**
1. **Offline** (inner loop): the answer-writer eval at n=3 on the four
   scenarios, with the reviewer run on each answer and one revision when it
   flags. Every flag is read and judged right or wrong (a judgment, and said
   to be). Both versions are graded on the checklist. About $3.
2. **Live** (outer loop, once): the reviewer on, n=3 on the four scenarios
   (about $7 with grading). The "off" arm is step 3's runs (§9.4, §9.6),
   reused: nothing the reviewer touches differs from them.

**Budget:** $15. **Done:** a decision. Keep it if, offline, most of its flags
are right and the revised answers keep their checklist items, and live it
lowers no scenario's checklist score by more than one item. Otherwise turn it
off. **Stop:** none beyond the budget.

### 9.8 Step 4 result (2026-10-22)

**Offline,** the answer-writer eval with the reviewer, 12 answers, two rounds:

| | Round 0 | Round 1: the reviewer reads the knowledge base's ranking |
| --- | --- | --- |
| Flags | 35, every answer | 31, every answer |
| Right, by reading each (a judgment) | 27 (77%) | about 28 (90%) |
| Flagged spans changed by the revision | 34 of 35 | |
| Checklist, before → after revision | 140 → 137 | 142 → 140 |

Round 0's wrong flags disputed what the knowledge base rules out (a leak, when
the container never finishes starting). Round 1 gave the reviewer the ranking,
and most of those went. What remained were flags right by the rule that remove
the answer's best inferences: "the old pods are still serving traffic".

**Live,** the reviewer on, n=3 on the four scenarios, against step 3's runs
(§9.4, §9.6):

| Scenario | Checklist, off / on | Cause, off / on | Unsupported claims, off / on | $/run, off / on |
| --- | --- | --- | --- | --- |
| checkout | 11.7 / 11.7 | 3/3 / 3/3 | 1 / 0 | 0.65 / 0.68 |
| itbench-33 | 10.3 / 10.0 | 3/3 / 1/3 | 0 / 2 | 0.19 / 0.21 |
| jvm-oom-on-startup | 13.3 / 12.7 | 3/3 / 3/3 | 5 / 2 | 0.30 / 0.46 |
| jvm-oom-with-metrics | 13.0 / 13.3 | 3/3 / 3/3 | 2 / 0 | 0.16 / 0.17 |

It flagged every live answer, 4.75 flags a run, so every session took a
revision turn. Unsupported claims fell from 8 to 4 in 12 runs, inside chance
at this n, as §9.7 said.

**Against the decision rule:** most flags are right offline, and no
scenario's checklist drops by more than one item, so the rule as written says
keep it. **What the rule did not foresee:** itbench-33's cause fell from 3 of
3 to 1 of 3. In both misses the agent never read the pod spec, and the
reviewer then flagged the inference that carried the cause ("a bad node
selector was introduced in the rollout") and the revision weakened it to
"changed something in their pod templates". Step 3's runs made the same
inference and kept it. The same mechanism showed offline. Cost rose 5–53% a
run.

**Spent:** about $5 offline (one run lost to a rejected `tool_choice`) and
about $4.20 live with grading: about $9 of the $15.

**A correction on the cause drop.** itbench-33's cause tracks whether the
agent read the pod spec, which is decided before the reviewer runs. Both runs
that missed it never read the spec and never named `invalid-node`; the one run
that read it got the cause. The reviewer weakened the cause's wording in one
of the two misses, not both. So the drop is mostly the agent's tool choice
and the grader, not the reviewer.

**Why it flags so much live.** It sees only the facts an answer cites, so
anything the agent learned otherwise reads as unsupported to it: a value read
from the pod spec and not cited, a log line, a dependency from the
architecture skill. Offline, where every claim came from the facts it was
given, about 90% of its flags were right. Live, in runs like itbench-33's
third, most were true statements it had not been shown.

**Decision (owner, 2026-10-22):** close step 4, done. The light reviewer is
merged and off (`review.light_reviewer: false`). `k8srca eval answer
--review` keeps it as an offline diagnostic, for finding overreach to fix
upstream, which is where every gain on unsupported claims has come from. The
old reviewer (007 §6.1, 008 §7), never on in production, is deleted.

**Ideas for a later reviewer,** not part of 010:
1. Show it every fact, not only those cited, so a true but uncited claim gets
   "cite F12 here" rather than "remove this".
2. Make more observations citable: log readings, architecture lookups,
   pod-spec values. Then "no fact" means unsupported, and the footnotes are
   better for readers whether or not a reviewer runs. This continues 010's own
   goal, and may be the next design.
3. Move its reliable flags into code: a quantifier ("every", "always",
   "never") on a fact that does not say "every" or "retained"; a confidence
   word above the ranking's strength; a claim about another workload with no
   marker.
4. Revise only on the flags that matter, and log the rest.
5. Measure it with an instrument that can see it: checklist items for known
   overreach, not the rubric's unsupported-claims count.

### 9.9 Step 5 plan, batch 1 (2026-10-22)

**Shows:** whether step 3's agent, now main, holds on failures it was not
built from. One arm, main, at n=3; no comparison.

**Batch 1, four scenarios,** each with a Prometheus window, faults injected by
Claude on benben with the owner's admin kubeconfig (k8srca itself stays
read-only), one at a time:

| Scenario | Fault | What it tests |
| --- | --- | --- |
| `image-slow-load` | the demo's `imageSlowLoad` flag at 5s, set as the flagd UI sets it (no Kubernetes trace) | latency from a slow dependency, not resource pressure: the untuned test of 3b's latency ranking (§9.5) |
| `deploy-regression` (004 #5) | one Deployment rolled to a pod template whose containers fail at start; the prior ReplicaSet intact | change correlation through workload history |
| `pvc-pending-no-storageclass` (004 #4) | a workload given a PVC naming a StorageClass that does not exist | widening to another object, on a symptom with no ranking |
| `image-pull-typo` (004 #3) | one Deployment's image tag misspelled | stopping early |

**Per scenario:** inject; wait for a steady state; `k8srca scenario record`;
revert, and confirm the revert; write `truth.yaml` from the capture before any
run, then the checklist from the truth; run main at n=3; grade each answer
three times on the checklist. The exact commands are in the measurement
journal (`docs/journal/2026-10-22-step5-batch1.md`).

**Can we see it?** Cause per scenario at n=3 shows a scenario the agent cannot
do (0 or 1 of 3). The checklist score has no baseline on a new scenario, so it
is read item by item: which statements the answers miss, and why.

**Budget:** about $20: sessions and grading about $4 a scenario, and the
architecture wiki's synthesis where a fault changes the deployment.

**Done:** four scenarios recorded, each with a truth, a checklist that passes
step 2's agreement bar (97% overall, no item split on more than one answer),
and n=3 results. **Stop:** as §9 step 5: a scenario fails in a way 010's design
does not address. It is recorded, and the owner decides whether 010 covers it.

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
