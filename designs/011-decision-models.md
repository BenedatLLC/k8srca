# Design 011 — Decision models for the small judgments

**Status:** draft, revised 2026-10-25. Step 1 (text-read facts) is planned and
offline only. Step 2, the first live one, waits on the scenario imports of
004 §12.3, which leave the suite room to show a change. The reviewer part is held until
[issue #6](https://github.com/BenedatLLC/k8srca/issues/6) leaves a baseline
(§6). Nothing here changes the agent on `main` until the owner decides it
does.

**Revision of 2026-10-24** (owner feedback): the design is now provider-neutral,
with JEV the first implementation (§4); the reviewer idea of the first draft is
replaced by a rethink (§6), held until issue #6; sources
are cited (§13); and §1–2 are new, for a reader who does not work on the project.

---

## 1. Background: what a reader needs to know

### 1.1 The system in one paragraph

k8srca is an agent that answers an SRE's question in Slack ("why is `ad`
restarting?"). It investigates a live Kubernetes cluster through read-only
tools and replies with a short answer: the most likely cause and how strong
the evidence is, what is not ruled out, what is ruled out, and next steps
(001 describes the platform, 002 how the agent reasons).

### 1.2 How an answer gets made

```
  tool results ──► facts layer ──► knowledge base ──► answer writer ──► checks ──► Slack
 (pod spec, logs,  (code states    (`open`: ranks     (a few hundred    (code,     
  events, metrics)  each result     candidate causes   words, with       maybe a
                    as facts)       from the facts)    footnoted facts)  reviewer)
```

1. **Tool results.** The agent calls read-only tools (k8stools, promtools) and
   gets back pod specs, events, logs and metrics.
2. **Facts** ([010](010-facts-and-short-answers.md) §3). A *fact* is one
   observation, stated as a sentence with its scope and the tool result it
   came from, for example "ad's last 3 terminations were OOMKilled, exit 137".
   Since 010, **code** writes most facts straight from tool results, because the
   agent was worst at deriving low-level facts by hand.
3. **The knowledge base** ([007](007-knowledge-representation.md),
   [008](008-conclusions-under-incomplete-evidence.md)). It is a set of YAML
   files (`docs/rca/knowledge/`) that say which facts count for or against each
   candidate cause. Its `open` command reads the facts, ranks the candidates
   deterministically, and reports the leader and its strength. The ranking is
   plain rules, so a reader can follow it.
4. **The answer writer** (010 §5) turns the leader, the ranking and the facts
   into the short answer. Every claim carries a footnote that points to a fact.
5. **Checks.** Code checks that every footnote resolves, the length is within
   limit, and every number in the prose appears in a cited fact. 010 §6 also
   built a *light reviewer* (a model that reads the answer) and left it **off**
   (§6 here).

How we know whether it works: each *scenario* ([004](004-scenario-testing.md))
is a broken cluster captured to disk and replayed. An answer is graded against
the scenario's `checklist.yaml` of statements it should and should not make
(010 §7). Process rules, such as "plan the measurement before building", are in
`docs/process.md`.

### 1.3 Terms used below

- **Predicate (a fact key).** A named, parameterised fact in the knowledge base
  with a `says` sentence. `last_exit_code[Container, Code]` says "the
  container's last termination exit code is {Code}".
  `process_logged_fatal_error[Container]` says "the process's own logs end with
  an exception, stack trace or fatal error before the cut". Candidate causes are
  written in terms of predicates. For example, the knowledge base's `oom-kill`
  claim is moderately supported by `last_exit_code[C, 137]` together with
  `not process_logged_fatal_error[C]`: killed with code 137 *and* no
  application error logged before the kills. That second part is what rules out
  "the application crashed".
- **Text-read fact.** A predicate whose truth needs *reading text*, almost always
  a container's log. Code can compute "exit code is 137" but cannot tell whether
  a log "ends with an exception before the cut". These are the facts this
  design is about (§3). The list of them is in §8.
- **Decision model.** A small model that does not write text. It is given a
  *state* (text or JSON) and typed *questions*, and returns typed answers,
  mostly probabilities (§4).
- **Abstain.** State no fact, so the agent reads the text itself as it does
  today.
- **`by`.** A fact's author: `code`, `agent`, and (new here) the judge (§7.4).

### 1.4 How this design relates to the others

| Design | Relation to 011 |
| --- | --- |
| [010](010-facts-and-short-answers.md) facts and short answers | 011 extends the facts layer to the one kind of fact it left to the agent (text-read facts, 010 §3.4). 011's reviewer rethink (§6) takes up 010 §9.8's "ideas for a later reviewer" |
| [007](007-knowledge-representation.md) knowledge representation | Supplies the predicates and their `says` sentences, which 011 reuses as the questions. 007 closed done in part on 2026-10-25 |
| [008](008-conclusions-under-incomplete-evidence.md) conclusions | Owns the ranking and strength that `open` computes. 011 never touches them (§2 principle 1) |
| [004](004-scenario-testing.md) scenarios | The ten captures are step 1's data; checklists are how a live change would be judged. Step 2 waits on 004 §12.3's ITBench imports, for scenarios with room |
| [005](005-modular-architecture.md) modular architecture | The `Judge` contract (§7.1) follows its style: a contract in `core/`, components behind it, component evals |
| [001](001-architecture.md) platform | §8 (egress, credential custody) is touched when the judge service is built (§7.2) |
| `docs/process.md` | Step 1 (§9) is written in its form: hypothesis, effect, can-we-see-it, budget, done, stop |

---

## 2. Why

The goal is the project's: a more accurate agent that costs less per answer.

010 moved most fact-making into code. What is left to the agent is any fact
that needs reading text. Today the coordinator reads the log itself, or delegates
to an investigator, then records the reading with `kb_query.py fact` (010 §3.4).
That is the last place where facts depend on the model's diligence, and it
costs turns.

A decision model fits the gap. A *question* about a log can be as small as the
predicate's own sentence:

```
process_logged_fatal_error[Container]
  the process's own logs end with an exception, stack trace or fatal error before the cut
permission_denied_error_in_logs[C]
  the logs of the container report permission denied on a file, directory or socket
```

Each is already a yes/no question about a state (the container's log), which is
the form decision models answer. TypeSafe's guidance is to ask atomic questions
and compose them in code [1][3], and the knowledge base is already written that
way.

**What it is not for.** Choosing the next check and deciding when to stop are
multi-step judgments over the whole evidence record. They stay with the
coordinator. `open`'s ranking stays deterministic (010 §2).

---

## 3. Principles

1. **The model judges; code decides.** A decision model's answer is an *input*.
   It becomes a fact only through thresholds written in code, and those
   thresholds and the question texts live in one reviewed file. In words: the
   model may say "0.93 that this log ends in an exception", but only our code
   decides that 0.93 is enough to *state* the fact, and only `open`'s rules
   decide what that fact does to the ranking. The model never ranks causes and
   never writes the answer. This follows TypeSafe's guidance to set cutoffs in
   code by the cost of a wrong decision [1], and the AWS Builder Center
   alert-triage example, where "Jev supplies the judgment, code owns the
   consequences" [5].
2. **Abstain rather than guess.** Between the two thresholds, no fact is stated
   and the agent reads the text as today. TypeSafe's own pattern is the same:
   send in-between values to a person [1]. A wrong fact that *rules a cause out*
   is worse than no fact. The AWS example calls the matching error
   "under-triage" and says to tune until it is zero, since over-triage only
   costs money [5]. Here the under-triage error is a confident false negative.
3. **Probabilities are not calibrated until we measure them.** TypeSafe's
   confidence page makes no calibration claim, and calls its confidence "one
   reasonable way to summarize a distribution" [2]. Its suggested 0.7 and 0.3
   are "a starting point" [1]. Thresholds are set on our labelled data, per
   question *and per model*, and reported with the counts behind them (§7.5).
4. **Optional, and off by default** (owner, 2026-10-24). `judge.enabled: false`
   in `k8srca.yaml`. With it off, nothing is sent anywhere and the system is
   exactly what `main` runs.
5. **Redact before text leaves** (owner, 2026-10-24). Text goes to an *outside*
   API only after a redaction pass (§7.3). A model run on our own machine sends
   nothing outside; whether the pass still applies to it is the owner's call
   (§12).
6. **Behind a contract, with no provider assumed.** The facts layer calls a
   `Judge` (005 style), never a vendor. JEV is the first implementation (§4, §7.1).
   A second one, such as a small Claude model asked the same atomic question,
   is a configuration change.
7. **Less variance in the results.** A user asks once, so the agent's
   run-to-run spread is part of its accuracy, and evaluation's repeated runs
   only measure it. Today a log's fact depends on whether the agent read it
   closely. A judge asks the same question of the same text on every run, which
   we expect to vary less, and `open`'s deterministic ranking carries whatever
   is gained through to the answer. We do not assume the judge is deterministic:
   TypeSafe's public docs say nothing on repeatability, so it is measured (§8).
   This targets one source of spread, text reading. It leaves the agent's choice
   of checks (010 §9.8's main cause of misses) and its wording untouched. A
   steadier judge can still be consistently wrong, so repeatability is measured
   beside accuracy and never in place of it. Pinned model versions (§7.5) guard
   against drift over time; results near a threshold abstain, and there the
   agent's own reading still varies.

---

## 4. Decision models, and who offers them

A decision model takes a **state** and a set of **questions**, answers each
separately, and returns typed answers without text or reasons. Three question
types recur across every provider found so far:

| Type | Returns |
| --- | --- |
| yes/no: `noul` (TypeSafe, Kev, Clef), `predicate` (OpenAI) | the probability the statement is true of the state |
| `choice` | one option from a list, with per-option probabilities and a confidence |
| `score` | a level on an ordered scale, with probabilities and a confidence |

| Provider | Interface | Notes (sources in §13) |
| --- | --- | --- |
| **TypeSafe JEV** (first implementation) | `POST /v1/systemone`: `state`, `model` (such as `jev-latest`), `questions` as an object keyed by ids you choose; answers keyed by the same ids [1][4] | Text and JSON only. The answer carries a model version (`jev-1.13.0` in the docs' example) [1]. Hosted API. Pricing and retention not in the public docs read so far |
| **Kev** (open weights, Apache-2.0) | The same `POST /v1/systemone`, so TypeSafe's SDK can point at a local server [7] | 0.8B–27B on Qwen bases; runs on CUDA, ROCm, Apple Silicon or CPU. Runs on our machine, so no text leaves. It can be fine-tuned on labelled JSONL |
| **Cloudflare Clef, Clef-flash** | "Fully Jev-API compatible" on Workers AI; weights on Hugging Face, Apache-2.0 [8] | Vendor-reported: 64k context against JEV's 32k, and a median latency of 209 ms (Clef) and 39 ms (Clef-flash) against JEV's 524 ms. Cloudflare says it does not store requests. The post gives no price |
| **OpenAI Decisions API** | `POST /v1/decisions`: `model` (`gpt-6-luna`), `input`, `questions` as an *array* of `{name, type, instructions}`; `answers` array, with a `refusal` type [6] | Different wire format and names. Up to 400 questions a request. Uncached input $0.10 per 1M tokens, output free. Zero Data Retention for eligible customers |
| **AWS Strands Decider 2B** | Not read | Reported by The New Stack as a local model that scores developer-supplied options [9]. Only the headline was available; check before relying on it |

Two consequences for the design:
- **Three of the five share one wire format,** and one differs in shape. So a
  `Judge` contract (§7.1) with a System One adapter and an OpenAI adapter covers
  all of them. Anything else is one more adapter.
- **The numbers are not interchangeable.** Kev says it calibrates its
  probabilities with a temperature fitted per checkpoint [7]; TypeSafe makes no
  such claim [2]. A threshold is therefore a property of a question *and* a
  model. Changing the model means running the eval again (§7.5).

Speed: Cloudflare reports a median of 524 ms for JEV [8], not the 100 ms of the
first draft. It does not matter for step 1, and matters little for step 2, where
a result's questions go in one request.

---

## 5. Where it could help, ranked

| # | Use | Kind of gain | Measurable offline? | Step |
| --- | --- | --- | --- | --- |
| 1 | **Text-read facts.** The knowledge base's log predicates, judged on each log result the facts layer sees | accuracy; fewer turns | yes: labelled logs from the captures | 1–2 |
| 2 | **Support checking as an evaluation category**, and a per-sentence screen for it. Replaces the first draft's reviewer gate (§6) | measurement we cannot do cheaply today | yes: saved answers | later |
| 3 | **A screen for the checklist grader.** Each checklist item as a yes/no on the answer | cost of measuring | yes: saved verdicts | later, between designs (process.md §5) |
| 4 | **Symptom matching.** A free-text question or unmapped alert to the knowledge base's symptoms, as a `choice` | accuracy at the first step | partly | later |
| 5 | **Next-check selection.** A `choice` over the knowledge base's candidate checks, so the order of checks does not vary run to run. **Unproven** (below) | spread in which checks run | partly: needs a miss caused by ordering | not planned |
| 6 | **Model tier by difficulty.** Triage, facts and `open` before any model call; a cheaper coordinator when the case is plain | cost | no: needs live runs | later, if ever |

Rows 2–6 each get their own plan, in process.md's form, when their turn comes.
Row 6's gate can mostly be code reading `open`'s strength, so a decision
model's part in it is the weakest.

**Row 5, what the one observed case says.** The misses 010 §9.8 traced to tool
choice are itbench-33's two runs that never read the pod spec. Replaying that
scenario's recorded results through the facts layer (2026-10-28), `open` ranks
the right cause strong from the events and node facts alone, with or without the
pod specs, so it lists no pod-spec read as a next check. The runs missed the
selector's value (`invalid-node`), which only the spec holds. That is a missing
detail on a decided leader, not a bad order among listed checks, and a `choice`
over `open`'s checks has nothing to pick there. A code rule (require the value
before the answer names a node selector) fits it better. This is one scenario
and a reconstruction, since the original runs are not in the scenario data. Try a
decision model here only if a miss shows poor ordering among checks that
were listed.

---

## 6. The reviewer, rethought (decided 2026-10-24: improve the generic reviewer first)

**Decision (owner, 2026-10-24):** only the reviewer part of this design waits.
The reviewer is improved in the generic system first, without a decision model,
tracked in [issue #6](https://github.com/BenedatLLC/k8srca/issues/6), and a
decision model is then tried against the baseline it leaves. Text-read facts
(§8, step 1) do not depend on the reviewer and go ahead in parallel. What
follows is the analysis that led there.

The first draft proposed using a decision model as the *gate* of 010's light
reviewer: judge each sentence against its cited facts, and send a revision turn
when one falls under a threshold. That does not survive 010's own results.

### 6.1 What 010 found

The light reviewer asked one question: does each sentence say only what its
cited facts say? (010 §6, §9.8.)

- It flagged **every** live answer, 4.75 flags a run, so every session took a
  revision turn. Cost rose 5–53% a run.
- Offline, where every claim came from the facts it was given, about 90% of
  flags were right. Live, most flags were true statements the reviewer had not
  been shown, because it saw only the facts the answer cited.
- Unsupported claims fell from 8 to 4 in 12 runs, which is inside chance at that
  n. The gains on unsupported claims have all come from fixing things upstream.
- The revision weakened right inferences. In one itbench-33 run it turned "a
  bad node selector was introduced" into "changed something in their pod
  templates". The cause miss was mostly the agent's tool choice (010 §9.8's
  correction), but the mechanism is real.

### 6.2 Why the feedback is not actionable

A flag of the form "this sentence is not supported" is a *complaint*. The
writer has one move that always satisfies it: say less. It cannot tell the
writer *which fact* would support the sentence, whether the sentence is wrong
or merely uncited, or what to do about a true observation that was never
recorded. A decision model that produces the same flag, faster, has the same
problem. It judges well and still gives the writer nothing to act on.

### 6.3 Three jobs the first reviewer mixed together

| Job | Example | Right tool |
| --- | --- | --- |
| **Overreach by form** | "every" where the fact says "the last 3"; a confidence word stronger than `open`'s strength; a claim about another workload with no marker | **Code** (010 §9.8 idea 3). It can name the sentence, the rule and the fix: *"Sentence 2 says 'every'; F3 says 'the last 3'. Say 'the last 3', or cite a fact that covers every life."* That is actionable because it is specific and mechanical, and it needs no judgment |
| **Attribution gaps** | A true sentence with no marker, or a marker on the wrong fact | **Feedback as a patch**: *"this sentence matches F12; add [F12]"*, applied by code or offered to the writer. If no fact matches, the record has a gap: the remedy is to record a fact (010 idea 2), not to cut the sentence. Whether this beats a complaint is untested |
| **"Is everything supported?" as a quality measure** | How often do answers go beyond their facts? | **Evaluation**, not a production step |

### 6.4 Proposal

1. **Make support an evaluation category** (the owner's point). Add it to the
   instruments that judge a run: per-sentence labels (supported by a cited fact;
   supported by an uncited fact; unsupported), reported per scenario beside the
   checklist. `k8srca eval answer --review` already runs the light reviewer as an
   offline diagnostic; this makes it a measure with a definition. It is an
   instrument change, so under process.md §5 it lands between designs, after an
   agreement check on saved verdicts. This is where a decision model earns its
   place, as a cheap per-sentence screen *for the measurement* (row 2), not as a
   gate. Row 3 is the same idea for checklist items.
2. **No judged gate in production.** The light reviewer stays off. Its
   reliable checks move into code (010 idea 3), with specific feedback of the
   kind in §6.3.
3. **Test attribution patches later, as their own plan,** after 010 idea 2 makes
   more observations citable (log readings, pod-spec values), since "no fact"
   cannot mean "unsupported" until then. Matching a sentence to the fact that
   supports it is retrieval, not judgment. A decision model's `choice` over the
   fact ids could do it, but so might a lexical match, and the plan should
   compare them.

**What this costs the design:** the reviewer drops out of 011's scope for now.
The decision-model idea keeps rows 1 and 2 (text-read facts, and measuring
support).

---

## 7. Architecture

### 7.1 The `Judge` contract

In `core/judge.py`, in 005's style: a contract the facts layer depends on, and
implementations behind it.

```python
class Question:   # id, kind ("yes_no" | "choice" | "score"), text, options/levels
class Answer:     # id, then one of:
                  #   Probability(p)
                  #   Choice(option, probabilities, confidence)
                  #   Score(value, probabilities, confidence)
                  #   Unavailable(reason)      # refusal, timeout, provider error

class Judge(Protocol):
    name: str            # "jev", "kev-4b", "claude-haiku"
    model: str           # the pinned version; recorded on every fact it states
    def ask(self, state: str, questions: list[Question]) -> list[Answer]: ...
```

Adapters, each small:
- `systemone`: TypeSafe JEV, Kev and Clef, with a `base_url`, a `model` and an
  optional key. One adapter, because they share the wire format [4][7][8].
- `openai_decisions`: the array-shaped format, mapping `noul` to `predicate` [6].
- `claude`: a small Claude model asked the same question and told to return a
  probability. This is step 1's comparison arm.

`Unavailable` is an answer, so any provider failure becomes an abstention. The
facts layer never raises on a judge, and the agent reads the text as today.

### 7.2 Where the call runs

The facts layer runs in the worker (`tools.py`, where each MCP result becomes
facts). In production the worker runs in the sandbox, whose egress is limited to
`api.anthropic.com` and `k8stools` (001 §8.3). So:
- the sandbox cannot reach a decision-model provider;
- the sandbox should not hold a provider key. Agent-authored bash runs there,
  and the scrubbing in `settings.py` is a second line, not the first.

So the adapters sit behind a **judge service** on `k8srca-net`, as promtools sits
beside k8stools:
- It holds the provider key (from `.env`), and is the only process that does.
  One credential, one holder.
- It accepts `{question ids, subject, text}`. It does not accept question
  *texts*: those live in its own reviewed file, so nothing in the sandbox can
  turn it into a general-purpose model.
- It redacts (§7.3), calls the configured `Judge`, and returns probabilities
  with the question ids.
- The sandbox's egress rules gain `judge:<port>` only when `judge.enabled`.
  The service's own egress is the configured provider's host and nothing else.
  For a local model such as Kev it needs no outside egress at all.
- It logs what it sent: question ids, a hash of the redacted text and the
  redaction counts. It does not log the text.

The in-process development worker calls the same service. Step 1 needs none of
this: it runs on the host, on scenario captures.

### 7.3 Redaction

**What already happens.** k8stools redacts common secret shapes before a result
leaves it (AWS keys, JWTs, PEM blocks, and `key`, `secret`, `token`, `password`
and `credential` values; 001 §4). Captures are recorded through it
(`redacted: true`).

**What that misses.** Personal data and topology. The deploy-regression capture's
accounting log holds order ids, tracking ids and a street address. The service
adds a second pass before anything leaves for an outside provider:
- email addresses, phone numbers, IPv4 and IPv6 addresses, UUIDs, card-number
  shapes (with a Luhn check), long hex and base64 runs;
- hostnames and URLs, reduced to their shape: a scheme and `<host>`, with the
  port kept, because a port can be the signal (`port already in use`);
- every replacement is a typed placeholder, such as `<email>` or `<ip>`, so the
  sentence still parses.

Free-text personal data, such as a street address in a JSON log line, is not
reliably caught by patterns. The design says so rather than claiming otherwise.
Two mitigations:
- **Trim before redacting.** A predicate about how a log *ends* needs the last
  lines, not 200 of them. The facts layer sends the tail (`judge.tail_lines`,
  default 40), plus any lines that match the predicate's cheap pattern screen.
- **Field drop for JSON log lines.** A line that parses as JSON keeps its level,
  message and error fields and drops the rest.

**The measurement watches redaction too.** If redaction removes the signal,
accuracy shows it. Step 1 scores each model on redacted text, and JEV on raw text
for comparison. The raw run is offline only, on the OpenTelemetry demo's
synthetic data that is already in this repository.

### 7.4 Facts from a judge

A judge answer becomes a `Fact` (010 §3.2) with `by: "judge"` and the judge's
`name` and `model` recorded, beside `code` and `agent` (010 §3.4):
- `p ≥ hi` states the predicate; `p ≤ lo` states its negation; anything between
  states nothing.
- **The statement** names the scope: "ad, previous instance, last 40 lines
  retained: no exception or fatal error before the cut (read by a model)".
- **The footnote** marks it as read by a model, as agent facts are marked today
  (`render.py`).
- **The agent's own reading wins.** If the agent records a `fact` that
  contradicts a judge's fact, `open` uses the agent's, and the disagreement is
  logged. It is a measurement, not an error.

`open` needs no change. It already reads `kb` lines from facts, whoever stated
them.

### 7.5 Questions and thresholds

One reviewed file, `judge/questions.yaml`: the predicate id, the question text
(by default the predicate's `says` sentence, edited so that high means yes [1]),
and thresholds **per model**:

```yaml
process_logged_fatal_error:
  text: the process's own logs end with an exception, stack trace or fatal error before the cut
  thresholds:
    jev-1.13.0: {lo: 0.15, hi: 0.85}   # illustrative; set by step 1 on the constructed set
    kev-4b:     {lo: ..., hi: ...}
```

A model with no entry for a question abstains on it. Changing a pinned version
therefore cannot silently reuse another version's thresholds.

### 7.6 Configuration

```yaml
judge:
  enabled: false                # principle 4
  provider: systemone           # systemone | openai_decisions | claude
  base_url: <provider url>      # judge service side; not set in the sandbox
  model: <pinned version>       # decision boundaries move with the model
  tail_lines: 40
  questions: judge/questions.yaml
```

---

## 8. Step 1 plan: can a decision model read our logs? (offline)

**Hypothesis.** On the knowledge base's log predicates, a decision model's
yes/no answers, thresholded, state correct facts on most container logs and
abstain on the rest, with no confident wrong answers. On redacted text, JEV does
this better than a regex screen, and at least as well as a small Claude model
asked the same question, for less. Other decision models are tried as extra arms
of the same experiment, since the contract makes them cheap to add.

**The predicates.** These facts read a container's own log. None is derived in
code today (`facts/derive.py`). This step's first task fixes the list, dropping
any whose state is not a single container's log.

> `process_logged_fatal_error`, `startup_logs_show_error`,
> `startup_logs_show_progress`, `logs_show_different_fatal_error`,
> `logs_show_executable_not_found`, `logs_show_initialization_failure`,
> `logs_show_missing_or_incompatible_library`,
> `logs_show_port_already_in_use`, `logs_show_queue_empty_exit`,
> `logs_show_timeout_waiting_for_dependency`,
> `logs_show_dependency_connection_failure`, `logs_show_dependency_wait`,
> `logs_show_auth_rejection`, `logs_name_invalid_setting`,
> `missing_file_error_in_logs`, `permission_denied_error_in_logs`,
> `operation_not_permitted_in_logs`, `readonly_filesystem_error_in_logs`,
> `probe_endpoint_logs_show_errors`

Event-message predicates (`pull_message_names_*`, `sandbox_message_names_*`) are
left out. Their text is short and formulaic, and code is the right tool for
them. (What a predicate is, and how `open` uses one, is in §1.3.)

**The corpus.** The ten captures hold 307 current and 235 previous container
logs (up to 200 lines each, about 42k lines). Many repeat across scenarios,
because they share one demo. After deduplication, each log is paired with each
predicate.

**Labels.** Hand-labelling every pair is too slow, and most pairs are negative.
The plan:
1. **Screen.** A pair is a candidate positive if the regex screen, JEV (p > 0.2)
   or Haiku flags it.
2. **Label every candidate by hand.** Two labellers: the owner and Claude, blind
   to which arm flagged it. Disagreements are settled by the owner and counted.
3. **Sample the rest.** A random sample of 200 unflagged pairs is labelled by
   hand, to estimate the positives all arms missed.
4. **Constructed positives.** The captures will hold few positives for most
   predicates. Each predicate gets about five constructed logs: real error shapes
   from the mined guides' sources, set inside demo log context. They are marked
   `constructed`, scored and reported separately, and never mixed into the
   capture counts.

The labels are frozen before any arm is scored (process.md §5).

**The arms,** each run on redacted text:

| Arm | What |
| --- | --- |
| regex | one pattern set per predicate, written before looking at the arms' results |
| JEV (primary) | the predicate's `says` sentence as a `noul`; the model version pinned |
| Haiku | `claude-haiku-4-5`, the same sentence and state, asked for a probability; one call per pair |
| JEV, raw | JEV on unredacted text: the redaction's cost |
| Other decision models (optional) | Kev (4B, run locally; text never leaves, so also run on raw), Clef, and OpenAI Decisions, each only if an account or hardware is at hand. Same labels, same measures, same thresholds procedure |

**Measures,** per predicate and overall:
- **confident wrong:** a fact stated (p ≥ hi or ≤ lo) that the label
  contradicts. Split into false positives, and false negatives, the kind that
  would rule a cause out;
- **abstain rate** at the chosen thresholds;
- **repeatability** (principle 7): each arm answers every pair in the labelled
  set five times, on separate calls. Report, per arm, the share of pairs whose
  five outcomes (fact stated true, stated false, abstain) are identical, and
  the share whose outcome changes across a threshold. Regex is the
  deterministic reference. It is also compared with how consistently the live
  agent recorded facts on the same logs, taken from existing saved runs where
  they hold them. Reported for every arm, and not a done criterion: a stable arm that is
  confidently wrong still fails the lines below;
- dollars and milliseconds per log;
- for the raw arm, the change in confident wrong and abstain against JEV.

Thresholds are chosen per predicate and per model on the constructed set, then
applied unchanged to the capture set, so they are not fitted to what they are
scored on.

**Can we see it?**
- **Confident wrong:** yes, as counts. With a handful of positives per
  predicate, the doc reports counts and not rates, and says that it does.
- **Repeatability:** yes, as counts of pairs. We expect fewer changes
  than the live agent shows, and do not assume none: TypeSafe's docs do not say
  whether identical calls return identical answers. Pairs that change across a
  threshold are the finding.
  The comparison with the live agent depends on how many saved runs read the
  same logs; if too few do, the report says so and drops it.
- **A model against regex and Haiku:** only where the difference is large. A tie
  is a finding: it means the simpler arm wins.
- **Any change in the live agent:** not in this step. Nothing runs live.

**Budget.** One week of calendar time. $25 in all: decision-model calls (to be
checked against each provider's pricing before the first call), Haiku calls, and
Claude's labelling. Hand labelling is the owner's time and the step's real cost.

**Done** when, for the primary arm (JEV), on the capture set at thresholds fixed
on the constructed set:
- it states no confident false negative;
- it states at most one confident false positive across all predicates;
- it abstains on at most a third of the pairs;
- it beats the regex arm on confident wrong or abstain, on at least five
  predicates.

The other models are reported against the same four lines. They do not gate the
step, but one that passes where JEV fails changes the primary arm.

**Stop, and say which,** when:
- **regex matches the best decision model.** Close the step, and put the regex
  screen in the facts layer as code (010's own principle);
- **Haiku matches the best decision model** at a cost that is acceptable per
  log. The question becomes whether a Claude judge is worth the turns, and the
  case for a dedicated decision model is gone;
- **decision models make confident false negatives that no threshold removes.**
  The idea does not work on this text;
- **redaction costs more than it keeps.** The raw arm is clearly better. That is
  a decision for the owner about data, not a threshold to tune; a local model
  (Kev) may make it moot;
- **the budget is spent.**

**What it produces,** in every outcome:
- `judge/questions.yaml` (draft), and `core/judge.py` with the first adapters;
- the labelled set, under `tests/judge/`, with its provenance;
- `uv run k8srca eval judge` to rerun the arms;
- a results table here (§10).

---

## 9. Later steps, in outline

Each is planned in full only when the one before it ends.

- **Step 2: the facts layer, behind the flag.** The judge service, the egress
  rule, `by: "judge"` facts, and the recorder keeping log results in fixtures
  (`record-results` keeps none today). Offline: `open` on the scenarios with
  log-shaped discriminators (jvm-oom-on-startup, liveness-probe-wrong-port,
  deploy-regression) reaches the same leader with no agent facts. Live, n=3, with
  the owner's go-ahead: a guard for no REGRESSION under 004 §6.4, and a lean, at
  most, on `fact` commands, investigator delegations and dollars per run.
  - **Before it is planned,** 004 §12.3's imports have landed. Their results
    say which scenarios leave room, and how many misses come from reading
    logs.
  - **Step 1's corpus.** If step 1's labels aren't frozen when the imports
    land, the logs of 24 and 80 join the corpus. They are real positives for
    `logs_show_dependency_connection_failure`, and 80's "topic or partition
    does not exist on this broker" is a message no predicate covers.
- **Step 3: support as an evaluation category** (§6.4 item 1), on saved answers.
  An instrument change, so it lands only between designs. Waits for issue #6.
- **Step 4: the grader screen** (§5, row 3), on the same terms. Waits for issue #6.
- **Step 5: symptom matching** (row 4).
- **Not scheduled:** attribution patches (§6.4 item 3), after 010 idea 2.

---

## 10. Results

None yet.

| Step | Plan | Outcome | Decision |
| --- | --- | --- | --- |
| 1 | §8 | | |

---

## 11. Rejected alternatives

- **A decision model as the reviewer's gate.** Faster flags are still
  complaints (§6.2), and 010's gate cost 5–53% a run for a gain inside chance.
- **A decision model ranking the candidates, or setting the strength.** It would
  put uncalibrated numbers where `open`'s readable rule is, and ten scenarios
  cannot calibrate them.
- **Calling a provider from the sandbox with the key in its environment.** This
  would mean a second credential in the place agent code runs (§7.2).
- **Sending whole logs.** It sends more data than any predicate needs, and more
  personal data with it.
- **Building for JEV alone.** Providers share one wire format already, and the
  numbers differ by model (§4). Tying the facts layer to one vendor would also
  tie the thresholds to it.

---

## 12. Open questions

- **Provider terms.** What each hosted provider retains of a request, for how
  long and where. TypeSafe's public docs read so far do not say. Cloudflare says
  it does not store requests [8]; OpenAI offers Zero Data Retention to eligible
  customers [6]. This goes into the redaction decision as much as the patterns do.
- **Pricing for JEV.** Not in the docs read. Only a secondary report, which was
  not checked, gives a figure. Confirm before the first call.
- **Pinning.** Responses carry a version such as `jev-1.13.0` and `jev-latest`
  is an alias [1], so pinning looks possible. How long a version lives is not
  known.
- **State size.** Vendor-reported contexts are 32k tokens for JEV and 64k for
  Clef [8]. Whether 40 lines is the right tail, or the whole 200 costs no
  accuracy, is for step 1.
- **Local models and redaction.** Whether the redaction pass applies to a model
  we run ourselves. Principle 5's wording says outside APIs only.
- **Reviewer timing.** Steps 3–4 of §9 (support as an evaluation category, the grader screen) wait for [issue #6](https://github.com/BenedatLLC/k8srca/issues/6) to leave a baseline (§6). Steps 1–2 do not.
- **Agent facts, after step 2.** Whether SKILL.md's paragraph on recording
  text-read facts shrinks once a judge states most of them. Not before step 2's
  measurements.

---

## 13. Sources

Read in full unless noted. Vendor claims are marked as such where they are used.

1. TypeSafe, *Noul* (statements, thresholds, request and response shape):
   <https://docs.typesafe.ai/primitives/noul.md>
2. TypeSafe, *Confidence*: <https://docs.typesafe.ai/confidence.md>
3. TypeSafe, *Patterns* (atomic questions, fan-out, confidence-gated routing):
   <https://docs.typesafe.ai/patterns.md>
4. TypeSafe, *System One* (the `POST /v1/systemone` endpoint, text-only state):
   <https://docs.typesafe.ai/concepts/system-one.md>
5. *Jev as an alert triage: typed decisions for OpenSearch on-call with Bedrock
   and AgentCore*, AWS Builder Center. A community-written article on AWS's
   builder site, not an official AWS blog post; the first draft's "AWS blog" was
   imprecise:
   <https://builder.aws.com/content/3JpdH4acDKLD3Mta05sZ1xQOpVt/jev-as-an-alert-triage-typed-decisions-for-opensearch-on-call-with-bedrock-and-agentcore>
6. OpenAI, *Decisions API* guide: <https://developers.openai.com/api/docs/guides/decisions>
7. Jared Palmer, *Kev*: <https://github.com/jaredpalmer/kev>
8. Cloudflare, *Clef decision models* (vendor-reported figures):
   <https://blog.cloudflare.com/clef-decision-models/>
9. The New Stack, *AWS launches a local answer to TypeSafe's Jev decision model*
   (headline only; body not read): <https://thenewstack.io/aws-strands-decider-model/>

Internal: [issue #6](https://github.com/BenedatLLC/k8srca/issues/6) (the reviewer rethink); [010](010-facts-and-short-answers.md) §3 (facts), §6 and §9.7–9.8
(the light reviewer, its measurements and its ideas), §7 (grading);
[007](007-knowledge-representation.md) (predicates);
[008](008-conclusions-under-incomplete-evidence.md) (ranking and strength);
`docs/process.md`.
