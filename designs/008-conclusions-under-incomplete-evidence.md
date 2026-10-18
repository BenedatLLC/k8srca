# Design 008 — Conclusions under incomplete evidence

**Status:** proposed, 2026-10-18. Step 2, the grader, is built (§8).

Changes [002](002-investigation-model.md) (how the agent reasons),
[004](004-scenario-testing.md) (how answers are graded) and
[007](007-knowledge-representation.md) (the knowledge format). Each of those
documents points here from the sections it changes. Where they disagree with
this one, this one describes the intended direction and they describe what is
built.

---

## 1. Why

The agent never sees the whole history of the system it is diagnosing, and it
never will. Kubernetes keeps each container's last termination and a restart
count, not the terminations before. Events are kept for a window of hours to
days. Logs rotate, and a killed container's log stops mid-line. Adding metrics
and centralized logs (007 §4.1) widens the window. It does not close it:
retention ends, scrapes sample, and the change that mattered may have been made
by a person in a tool the agent cannot read.

The system has so far treated this as an exception. The answer was to
*confirm* a cause; a conclusion short of that was a "partial conclusion"
(002 §5.4), and "could not check" was a verdict for the cases where evidence
was missing. The last two gates (007 §11.2) show what that costs.

**"Confirmed" meant two things.** To the grader it meant the explanation that
survives. The jvm truth file says so in its own words, of OOM: "This is the
hypothesis that survives". To the knowledge base and the reviewer it meant
proven. `memory-limit-too-low` was confirmed by `killed_on_every_life`,
"killed on every restart including the first". No record holds every restart.
A careful agent would not assert it, so `open` could not confirm the cause,
and the agent reported it as could-not-check. The grader then failed the
answer, because it wanted the surviving explanation called confirmed. In the
ninth gate, five of six rivals misses were this:
- the first answer named OOM from a too-low limit as most likely;
- the revision turn, asked by the knowledge and by the reviewer for a standard
  of proof the record cannot meet, demoted it.

Neither the agent nor the reviewer was wrong. They applied the stricter of two
meanings, and the grader graded the looser one.

**The fix is not to pick one meaning of "confirmed".** It is to stop asking
for confirmation where the evidence can only rank:
- the answer says what is most likely, and how strongly the evidence supports it;
- the knowledge says how much each fact counts, over what it was observed;
- the grader asks whether the answer drew the conclusions the evidence allows,
  no stronger and no weaker.

## 2. Principles

1. **Ruling out can be decisive. Confirming is comparative.** "No liveness
   probe is configured" rules out a failing probe outright. Nothing in a crash
   loop proves OOM; it can only be the best-supported explanation left. The
   answer, the knowledge and the grader all keep this asymmetry.
2. **Every fact is scoped to what was observed.** A fact says what the record
   showed and over what span: the retained terminations, the event window, the
   log tail. A fact that quantifies over the whole history ("every life",
   "never", "the first failure") is one no record can state, unless an action
   can actually see the whole history.
3. **The answer is a hand-off.** The person asking can see more than the agent
   and can change the system. The answer's last job is to tell them the
   cheapest thing that would settle what the agent could not.
4. **Under-committing is an error, as over-committing is.** An answer that
   hedges every candidate is no more use than one that is certain of the wrong
   one. When the evidence separates the candidates, the answer must say which
   one leads.
5. **Ordinal strength, not invented numbers** (002 §10.1, unchanged). §5.3 says
   what would change that.

## 3. The answer

### 3.1 Shape

Four sections, in this order:

| Section | Holds | Each entry carries |
| --- | --- | --- |
| **Most likely** | One cause per level of refinement, or "no leading cause" when the evidence does not separate the top candidates | its strength (§3.2), the facts behind it, the facts against it, and what would change the conclusion |
| **Not ruled out** | Candidates that could still be the cause, most likely first | why it is less likely, and what would decide it. If nothing the agent can reach would decide it, the gap that stops it (the old could-not-check) |
| **Ruled out** | Candidates the evidence excludes | the fact that excludes it |
| **Next steps** | What the person asking should do or check, most useful first | which entry above it would settle or move. Things that need access the agent lacks are the point of this section, not an apology for it |

"Not checked" candidates, the rare ones nothing pointed at, share one line at
the end of *Not ruled out*. In the eighth gate most of the added length came
from listing them one by one.

**What would change it** is the field that matters most. "Most likely" says
that the evidence ranks this cause first. It does not say that nothing could
move it. The answer names the observation that would. For jvm that is
"`kubectl describe` on the node shows no OOM kill for the container" or
"raising the limit to 600Mi does not stop the kills". That gives the human
their first check, and it gives the reviewer something to hold the strength
against.

### 3.2 Strength

The most likely cause, and each candidate not ruled out, gets one of three
grades:

| Strength | Means | Example (jvm) |
| --- | --- | --- |
| **strong** | Observations the cause predicts and its rivals do not, and nothing observed against it that the answer cannot explain | the limit is too low: limit equals request at 300Mi with no heap sizing, killed seconds into each life the record retains, never completes startup, and `fraud-detection`, the other container at 300Mi, dies the same way |
| **moderate** | Observations that fit it better than its rivals, but one rival or an unexplained observation remains | OOM itself, read alone: exit 137, no fatal error logged, but the reason reads `Error` and no OOM event was recorded |
| **weak** | Fits, and nothing better fits; little observed points at it specifically | a configuration cause, if the logs named no missing setting but showed nothing else either |

There is no grade above strong. A result that states the cause outright, such
as the reason `OOMKilled` beside an `OOMKilling` event, makes the cause strong
with nothing against it. That is as far as the evidence goes. "Confirmed" and
"certain" in an answer read as strong, and the grader holds them to it (§6).

Slack sees the grade in words ("most likely, strongly supported"), never as a
number (002 §10.1).

### 3.3 Example

The jvm scenario, as an answer in this shape would give it:

> **Most likely: `ad`'s memory limit is too low for its startup** (strong).
> Limit equals request at 300Mi, with no JVM heap flags. Every termination the
> record keeps is a SIGKILL (exit 137) 8–9 seconds into the life, before the
> service finishes starting. `fraud-detection`, the other container whose limit
> equals its 300Mi request, dies the same way.
> *Against it:* the termination reason reads `Error`, not `OOMKilled`, and no
> OOM event was recorded. Exit 137 with nothing logged fits a kernel kill; I
> cannot see why the runtime did not label it.
> *Would change it:* the node's kernel log showing no OOM kill for the
> container, or the kills continuing with the limit raised.
>
> **Not ruled out:** a bad configuration (less likely: nothing in the log names
> a setting, and `ad` uses no ConfigMap or Secret; a flagd flag toggle would
> not show here). *Not checked, nothing pointed at them:* …
>
> **Ruled out:** an application crash (killed from outside, nothing logged
> before the kill); a failing liveness probe (none configured); a memory leak
> and a traffic spike (no life gets far enough to serve, so neither has time).
>
> **Next steps:**
> 1. Raise `ad`'s memory limit to 600Mi, or set `-Xmx` below 300Mi. Either
>    settles the leading cause in one restart.
> 2. Check the node's kernel log for an OOM kill of the `ad` container. This
>    explains the `Error` reason.
> 3. Check whether a flagd flag was changed recently. This would revive a
>    configuration cause.

The answer is no shorter than today's. But nothing in it asks for history the
record cannot hold, so no check of it has grounds to demote the cause.

## 4. The knowledge

### 4.1 Scoped facts

A fact's `says` states what was observed and over what. The build gets a check
for it: a fact whose text quantifies over all time ("every", "always",
"never", "first", "since") is refused unless one of its producing actions
declares `scope: history`. No k8stools action does. A restart series from
Prometheus over the pod's whole life would.

`killed_on_every_life` becomes:

```yaml
killed_in_every_retained_life:
  params: [Container]
  says: >-
    every termination the record retains was a kill -- the last state, the
    previous instance's when it is shown, and the terminations the event
    series implies over its span
```

An agent can assert that from the capture, and the claim's `note` says what it
does not cover. `healthy_period_before_kills` and
`completes_startup_each_life` get the same treatment. A sweep of the authored
and mined layers, run by the build check above, finds the rest.

### 4.2 Weighted evidence in place of confirm

A claim's `confirm` and `supports` merge into `for`, a list of alternatives
each with a weight. `refute` splits:
- **`rules_out`:** decisive, as `refute` is today;
- **`against`:** weighted, the counterpart of `for`.

```yaml
memory-limit-too-low:
  title: memory limit too low
  for:
    - when: ["killed_in_every_retained_life[C]", "not completes_startup_in_retained_lives[C]"]
      weight: strong
    - when: ["limit_equals_request[C, memory]", "not runtime_sizing_flags_set[C]"]
      weight: moderate
  against:
    - when: "healthy_period_before_kills[C]"
      weight: moderate        # it once ran on this limit: look for what grew
  rules_out:
    - ["healthy_period_before_kills[C]", "onset_observed[W]",
       "not changed_before_onset[W, resources]", "not changed_before_onset[W, image]"]

oom-kill:
  for:
    - when: ["last_exit_code[C, 137]", "not process_logged_fatal_error[C]"]
      weight: moderate
    - when: ["last_exit_code[C, 137]", "not process_logged_fatal_error[C]",
             "memory_limit_plausibly_reached[C]"]
      weight: strong
  against:
    - when: "not last_reason[C, OOMKilled]"
      weight: weak            # stated in the answer; does not move the grade
  rules_out:
    - "last_exit_code[C, Code], Code != 137"
    - "process_logged_fatal_error[C]"
```

The weak `against` on `oom-kill` is the jvm trap, written as knowledge. 007 §3.2
found that pre-deciding the `reason` question (telling the agent `reason` is
unreliable) cost `traps` twice. This does not pre-decide it. It says the
observation counts against OOM, a little, and so belongs in the answer. That is
what the trap asks for.

**The standing of a claim**, as `open` computes it:
1. **Ruled out** if a `rules_out` alternative holds.
2. Otherwise, **its support** is the highest weight among the `for`
   alternatives that hold, or none.
3. **Each `against` that holds lowers it:** strong by two grades, moderate by
   one. A weak `against` does not lower it but is listed, and the answer owes
   it a sentence.
4. **A sub-cause's support lifts its parent to the same grade.** This follows
   007 §6.1's rule that a confirmed sub-cause confirms its parent.
5. **Among siblings, the highest support leads.** A tie at the top is "no
   leading cause". `open` then names the action that would separate them,
   cheapest first, as it does for open claims today.

The rule is deliberately crude, so that a reader can follow it and the build
can test it. The model may depart from it, saying why. Prose conditions and
facts outside the vocabulary are the usual reason. The reviewer holds the
answer to the reason it gives.

**Owed dispositions** (007 §6.1) carry over: every candidate, and every
sub-cause `open` ranked or ruled out, needs a place in one of the four
sections. The completeness check is unchanged except for the names.

### 4.3 What happens to the old statuses

| 007 today | here |
| --- | --- |
| confirmed | most likely, at the strength `open` computes |
| weakened | not ruled out, with an `against` that holds |
| refuted | ruled out |
| could-not-check | not ruled out, with the gap that stops it named |
| open | not ruled out, with the facts that would decide it |
| conflicting | unchanged: both a `rules_out` and a strong `for` hold, so a fact is wrong |

Could-not-check stops being a verdict and becomes a reason. In the ninth gate
it was where a correct cause went to die. Here a cause the agent cannot
confirm, but whose evidence still ranks it first, stays most likely.

## 5. Probabilities

InferRCA's roadmap puts probabilities in its predicates. They would go where
the weights go, so the question is when.

### 5.1 Not yet

002 §10.1's argument still holds:
- a Bayesian update needs priors and likelihoods nobody can defend;
- numbers the model writes are tokens, not measurements;
- `0.94` in Slack invites acting on it.

Nothing since has changed it. The scenarios give one labelled outcome each,
and three scenarios cannot calibrate anything.

### 5.2 Built so they can be added

The three weights are meant as bands of a likelihood ratio, how much more
likely the observation is if the claim is true than if it is not:

| weight | band | read as |
| --- | --- | --- |
| strong | 10 or more | rarely seen unless the claim holds |
| moderate | 3 to 10 | clearly favours it |
| weak | under 3 | leans toward it |

The bands are not used for anything yet. Writing them down keeps the weights
meaning the same thing across authors and layers. It is also what a
calibration would replace with measured numbers, per entry, without changing
the format. A solver (007 §10.2) could then take the same knowledge as
InferRCA's probabilistic predicates, if that becomes the route.

### 5.3 What would change it

Labelled outcomes. That means enough of them that a weight's band can be checked
against how often its claim turned out to be the cause:
- the hold-out suite (006), once it runs;
- scenarios recorded from real incidents;
- production threads where the human reported what fixed it.

The trigger is 002 §10.1's own: "Revisit if the scenario suite grows enough
labeled incidents to *measure* calibration."

## 6. Grading

The grader stops asking whether the answer reached the truth's verdicts. It
asks whether it drew the conclusions the capture allows. For that, the truth
file has to say what the capture allows, not what really happened.

### 6.1 `truth.yaml`

```yaml
cause:
  summary: ...
  must_identify: [ad, memory, 300Mi]
  most_likely: memory-limit-too-low      # the claim the capture ranks first
  strength: [moderate, strong]           # the grades it supports; outside is wrong

candidates:
  - id: oom
    standing: most_likely                # parent of the leading cause
    strength: [moderate, strong]
  - id: memory-leak
    standing: ruled_out
    also_accept: [not_ruled_out]         # ...weak, on "no memory series"
    note: ...
  - id: bad-config
    standing: not_ruled_out
    strength: [weak]
    note: ...                            # flag state is invisible, so not ruled out
```

`standing` is one of `most_likely`, `not_ruled_out` or `ruled_out`.
`strength` is a range, because two sound readings can grade the same evidence
a step apart. `also_accept` keeps its 004 §6.3 rule: it is for genuine
boundaries, and the note must say what argument leads to each.

**Migration** (*done 2026-10-18*). The loader translates a file still written
with `rivals`, and ours were rewritten by hand, both using §4.3's table:
- confirmed becomes most_likely;
- refuted becomes ruled_out;
- weakened and could-not-check become not_ruled_out.

A person then sets the strength ranges by hand, reading the capture, because
the old files never stated them. Each file is re-reviewed under 004 §6.3's
rule, which pins the truth to its capture. The hold-out suite's truth files are
the colleague's (006), so the format change reaches them as a schema and a
note, not as edits.

### 6.2 Dimensions

| Dimension | Passes when |
| --- | --- |
| **cause** | the answer's most likely cause is the truth's `most_likely`, or its parent with the sub-cause among the leading few. "No leading cause" fails when the truth names one |
| **standing** (was rivals) | every candidate has a standing, and it matches the truth's or an `also_accept`. Silence still fails, as it always has |
| **calibration** | the leading cause's strength is inside the truth's range, and no candidate is ruled out without a fact that rules it out |
| **evidence** | unchanged: every claim is tied to a result that supports it |
| **traps**, **gaps** | unchanged |
| **next steps** | at least one step bears on the leading cause's "would change it", or on a candidate not ruled out. Rubric-graded; a lean, not a gate, until it has a measured spread |

**Calibration** is where both kinds of error show:
- *Over-commitment:* "confirmed" or "certain" for a cause the truth grades
  moderate; a candidate ruled out on its absence from the logs.
- *Under-commitment:* "could not check" for a cause the truth grades strong; a
  candidate left open that a retained fact rules out. The ninth gate's
  memory-leak in a container that never finishes starting is one.

Splitting it from **standing** keeps a grade wrong by one step from looking
like a candidate dropped.

### 6.3 What the regrade can show for free

`scenario regrade` grades saved answers again. Once the grader and truth files
change, every gate in 007 §11.2 can be re-scored for a few cents a run, before
any prompt or knowledge changes. Two questions get answered:

- **Which past results were the instrument's.** The ninth gate's demoted runs
  ranked OOM first while calling it could-not-check. Under §6.2 they may pass
  `cause` and fail `calibration` by a step, which is what happened.
- **Which conclusions survive.** The completeness check cut dropped
  candidates; that should still show under `standing`. If a past conclusion
  reverses under the new grader, it was resting on the meaning of "confirmed".

### 6.4 Result: the saved gates, regraded (2026-10-18)

Every jvm gate from the k8stools 4.0.2 sittings (007 §11.2), regraded under
this grader. Nothing was re-run. The ninth gate's first answers are the ones
before its revision turn, graded here for the first time:

| gate | cause | standing | calibration | rivals (old grader) | traps | next | unsupported/run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 10/10 | 10/10 | 9/10 | 10/10 | 8/10 | 10/10 | 4.4 |
| seventh | 10/10 | 7/10 | 10/10 | 7/10 | 9/10 | 10/10 | 3.3 |
| eighth (completeness) | 10/10 | 10/10 | 9/10 | 9/10 | 7/10 | 10/10 | 2.9 |
| ninth, first answers | 10/10 | 7/10 | 10/10 | - | 10/10 | 10/10 | 2.5 |
| ninth (+ reviewer, revised) | 10/10 | 7/10 | 6/10 | 4/10 | 10/10 | 10/10 | 1.4 |

- **The ninth gate's loss was partly the instrument, and partly real.**
  - *The instrument:* the old rivals read 4/10, and the cause now reads
    10/10. Every demoted answer still led with OOM from a too-low limit. The
    old grader read "could not check" as a verdict, and failed them for it.
  - *Real:* calibration fell from 10/10 on the first answers to 6/10 after
    revision. In four runs the revision lowered the cause to weak, which is
    under what the capture gives. That is the revision turn moving certainty
    the wrong way, now measured as what it is.
- **Standing agrees with the old rivals where the misses were dropped
  candidates.** The seventh gate's three misses dropped memory-leak and
  workload-spike, and read 7/10 either way. The eighth gate's completeness
  check fixed them: 10/10.
- **The revision turn fixes and breaks in equal number.** The ninth gate's
  first answers lost standing in three runs, all leaving out memory-leak and
  workload-spike. After revision, two of those three were fixed. But two new
  misses appeared: memory-limit-too-low bundled as not ruled out, and an
  application crash first refuted and then walked back.
- **Calibration's other failures are single and mild.** In the baseline,
  bad-config was not ruled out but held moderate. In the eighth gate,
  memory-limit-too-low led but was held weak.
- **Next steps passed 50/50.** As graded, it does not separate these answers.
  Every answer here recommends raising the limit, which settles the cause. It
  stays a lean, and needs a stricter reading, or scenarios where the right
  step is less obvious, before it means anything.
- **Unsupported claims read lower than the old grader's,** for example 4.4
  against 5.1 on the baseline. The rubric changed around them, so they
  compare only within this table.
- **The ninth gate's reviewer stays off.** It cut unsupported claims to 1.4,
  the lowest yet. But its revision costs calibration in four runs of ten,
  which is the failure §7's change is for.

This table is the comparison point for step 3.

## 7. The reviewer

The reviewer (007 §6.1) fits once the answer has strength grades.
- **`overstated` moves a strength down a grade.** It never moves a standing.
  The revision prompt says so: keep the claim in its section and change its
  grade. Today's "state its certainty as the results allow" let the revision
  pick could-not-check.
- **`unsupported` and the other kinds are unchanged.**
- **It also gets the scoped-fact rule.** A claim about "every life" is
  supported when the answer says "every retained life".

It is measured again only after §4 is built. Then it, `open` and the grader
will be asking for the same standard.

## 8. Staging

Each step is a gate with an n=10 arm (004 §6.4), except the first, which is a
document.

| Step | What changes | Measured how |
| --- | --- | --- |
| 1. This design | 002, 004 and 007 point here | review |
| 2. The grader (*built*) | truth schema, migration, rubric, `scenario regrade` | regrade every saved gate. Nothing is run; the instrument alone moves |
| 3. The answer shape | SKILL.md's answer section, the coordinator prompt, the revision prompts in `review.py` | n=10 on jvm under the new grader, against the regraded eighth gate |
| 4. The knowledge | scoped facts and the build check; `for`/`against`/`rules_out` and their weights; `open`'s standing | n=10, against step 3 |
| 5. The reviewer | `overstated` moves strength | n=10, against step 4 |

The grader goes first because every later number depends on it. Changing the
answer shape under the old grader would be scored by the instrument whose
meaning of "confirmed" caused the problem.

checkout and itbench-33 are re-run at step 3. checkout's quiet cause, one
metrics sample, is the case strength grades are for: today the truth can only
say "confirmed" or "could not check" about it.

## 9. Rejected alternatives

### 9.1 Loosening the grader alone

Accept "could not check" for OOM in the jvm truth, and the ninth gate passes.
But the knowledge and the reviewer still ask for a standard the record cannot
meet. Every answer then has a reason to be weaker than its evidence, and the
grader rewards hedging. Principle 4 rules that out.

### 9.2 Keeping "confirmed" and defining it carefully

002 §4 defines confirmed precisely: `would_confirm` observed and every rival
weakened or refuted. In practice:
- models read "confirmed" as proven;
- SREs read it as proven;
- the reviewer read it as proven.

A word whose careful definition everyone overrides is the wrong word.

### 9.3 Dropping the history facts

Removing `killed_on_every_life` and its kind leaves memory-limit-too-low with
nothing but `limit_equals_request` behind it. That is a moderate fact at best.
The retained terminations are real evidence, and stated with their scope they
say a lot. The problem was the claim to more than was retained, not the facts
themselves.

### 9.4 Numeric probabilities now

§5.1.

## 10. Open questions

1. **Three grades, or four?** A grade above strong for a cause a result states
   outright may earn its place if answers keep wanting to say "confirmed". Wait
   for the step 3 answers.
2. **Is the standing rule mechanical in `open`, or advice?** §4.2 has `open`
   compute it and the model depart from it with a reason. If departures are
   common and right, the rule is wrong; if common and wrong, `open` should
   hold the line.
3. **Grading next steps.** Whether a step would settle something is a judgement.
   A rubric dimension with no measured spread is a lean. It may stay one.
4. **Slack length.** The four sections and the shared "not checked" line should
   come out shorter than the eighth gate's 9,300 characters. If not, *Not ruled
   out* needs a cut-off: the candidates with a reason to stay, plus a count.
5. **Production feedback as labels.** §5.3 counts threads where the human said
   what fixed it. Capturing that is a Slack-side feature (a reaction, or a
   closing question), and nothing exists for it yet.
