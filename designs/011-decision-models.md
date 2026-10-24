# Design 011 — Decision models: JEV for the small judgments

**Status:** draft, 2026-10-24, on branch `jev-decision-models`. Step 1 is
planned and offline only. 007 is the design in progress (process.md §4), so
nothing here runs live, or changes the agent on `main`, until the owner
decides it does.

Builds on [010](010-facts-and-short-answers.md) (the facts layer) and
[007](007-knowledge-representation.md) (the knowledge base's fact
vocabulary). Touches [001](001-architecture.md) §8 (egress and credential
custody) when step 2 is built.

---

## 1. Why

The goal is the project's: a more accurate agent that costs less per
answer.

010 moved most fact-making into code, because the agent was worst at
deriving low-level facts by hand. One kind of fact is still left to the
agent: anything that needs **reading text**. Code cannot tell whether a log
"ends with an exception before the cut" or "shows initialisation still
progressing". So the coordinator reads the log itself, or delegates to the
investigator, and records what it read with `kb_query.py fact` (010 §3.4).
That is the last place where facts depend on the model's diligence, and it
costs turns.

A decision model fits that gap. JEV (TypeSafe) takes a *state* (text or
JSON) and typed *questions*, and returns typed answers in about 100 ms:

| Question type | Returns |
| --- | --- |
| `noul` | the probability a statement is true of the state |
| `choice` | one option from a list, with per-option probabilities and a confidence |
| `score` | a level on an ordered scale, with probabilities and a confidence |

Each question is answered separately and in parallel against the same
state. JEV writes no text and gives no reasons. Its docs say it works best on
atomic questions, with code combining the answers.

**The knowledge base already speaks in atomic questions.** Its facts have a
`says` sentence written as a statement about a container:

```
process_logged_fatal_error[Container]
  the process's own logs end with an exception, stack trace or fatal error before the cut
permission_denied_error_in_logs[C]
  the logs of the container report permission denied on a file, directory or socket
```

Each is a `noul` question as it stands, with the container's log as the
state.

**What JEV is not for.** Choosing the next check and deciding when to stop
are multi-step judgments over the whole evidence record. They stay with the
coordinator. `open`'s ranking stays deterministic, so a reader can follow
it (010 §2).

## 2. Principles

1. **JEV judges; code decides.** A JEV answer becomes a fact only through
   thresholds in code. The thresholds and question texts live in one
   reviewed file. This follows the AWS alert-triage write-up and TypeSafe's
   own guidance.
2. **Abstain rather than guess.** Between the thresholds, no fact is stated,
   and the agent reads the text as it does today. A wrong fact that rules a
   cause out is worse than no fact. It is the "under-triage" of this system.
3. **Probabilities are not calibration.** TypeSafe's confidence page makes
   no calibration claim. Thresholds are set on our labelled data, per
   question, and reported with the counts behind them.
4. **Optional, and off by default** (owner, 2026-10-24). `jev.enabled: false`
   in `k8srca.yaml`. With it off, nothing is sent anywhere and the system is
   exactly what `main` runs.
5. **Redact before sending** (owner, 2026-10-24). Text leaves for an outside
   API only after a redaction pass (§4).
6. **Behind a contract.** The facts layer calls a `Judge` (005 style), not
   JEV. A second implementation, such as a small Claude model asked the same
   atomic question, is a configuration change, and it is step 1's comparison
   arm.

## 3. Where it could help, ranked

| # | Use | Kind of gain | Measurable offline? | Step |
| --- | --- | --- | --- | --- |
| 1 | **Text-read facts.** The knowledge base's log predicates, judged on each log result the facts layer sees | accuracy; fewer turns | yes: labelled logs from the captures | 1–2 |
| 2 | **The light reviewer's gate.** "Does this sentence say only what its cited facts say?" per sentence; a revision turn only when one falls under the threshold | cost (the revision turn adds 35–70%, 010 §1) | yes: saved answers | later |
| 3 | **A screen for the checklist grader.** Each checklist item as a `noul` on the answer | cost of measuring | yes: saved verdicts | later, between designs (process.md §5) |
| 4 | **Symptom matching.** A free-text question or unmapped alert to the knowledge base's symptoms, as a `choice` | accuracy at the first step | partly | later |
| 5 | **Model tier by difficulty.** Triage, facts and `open` before any model call; a cheaper coordinator when the case is plain | cost | no: needs live runs | later, if ever |

Rows 2–5 each get their own plan, in process.md's form, when their turn
comes. Row 5's gate can mostly be code reading `open`'s strength, so JEV's
part in it is the weakest.

## 4. Architecture

### 4.1 Where the call runs

The facts layer runs in the worker (`tools.py`, where each MCP result is
turned into facts). In production the worker runs in the sandbox, whose
egress is limited to `api.anthropic.com` and `k8stools` (001 §8.3). Two
things follow:
- the sandbox cannot reach TypeSafe;
- the sandbox should not hold a TypeSafe key. Agent-authored bash runs
  there, and the scrubbing in `settings.py` is a second line, not the first
  (CLAUDE.md).

So JEV sits behind a **judge service** on `k8srca-net`, as promtools sits
beside k8stools:
- It holds `TYPESAFE_API_KEY` (from `.env`), and is the only process that
  does. One credential, one holder.
- It accepts `{question ids, subject, text}`. It does not accept question
  *texts*: those live in its own reviewed file, so nothing in the sandbox can
  turn it into a general-purpose model.
- It redacts (§4.2), calls JEV, and returns probabilities with the question
  ids.
- The sandbox's egress rules gain `judge:<port>` only when `jev.enabled`.
  The judge's own egress is the TypeSafe API host and nothing else.
- It logs what it sent: question ids, a hash of the redacted text, and the
  redaction counts. It does not log the text itself.

The in-process development worker calls the same service. Step 1 needs none
of this: it runs on the host, on scenario captures.

### 4.2 Redaction

**What already happens.** k8stools redacts common secret shapes before a
result leaves it (AWS keys, JWTs, PEM blocks, and `key`, `secret`, `token`,
`password` and `credential` values; 001 §4). Captures are recorded through it
(`redacted: true`).

**What that misses.** Personal data and topology. The deploy-regression
capture's accounting log holds order ids, tracking ids and a street address.
The judge adds a second pass before anything leaves:
- email addresses, phone numbers, IPv4 and IPv6 addresses, UUIDs,
  card-number shapes (with a Luhn check), long hex and base64 runs;
- hostnames and URLs, reduced to their shape: a scheme and `<host>`, with
  the port kept, because a port can be the signal (`port already in use`);
- every replacement is a typed placeholder, such as `<email>` or `<ip>`, so
  the sentence still parses.

Free-text personal data, such as a street address in a JSON log line, is
not reliably caught by patterns. The doc says so rather than claiming
otherwise. Two mitigations:
- **Trim before redacting.** A predicate about how a log *ends* needs the
  last lines, not 200 of them. The facts layer sends the tail
  (`jev.tail_lines`, default 40), plus any lines that match the predicate's
  cheap pattern screen.
- **Field drop for JSON log lines.** A line that parses as JSON keeps its
  level, message and error fields and drops the rest.

**The measurement watches redaction too.** If redaction removes the signal,
JEV's accuracy shows it. Step 1 scores JEV on redacted text, and on raw text
for comparison. The raw run is offline only, on the OpenTelemetry demo's
synthetic data that is already in this repository.

### 4.3 Facts from JEV

A JEV answer becomes a `Fact` with `by: "jev"`, beside `code` and `agent`
(010 §3.4):
- `p ≥ hi` states the predicate; `p ≤ lo` states its negation; anything
  between states nothing.
- **The statement** names the scope: "ad, previous instance, last 40 lines
  retained: no exception or fatal error before the cut (read by JEV)".
- **The footnote** marks it as read by a model, as agent facts are marked
  today (`render.py`).
- **The agent's own reading wins.** If the agent records a `fact` that
  contradicts a JEV fact, `open` uses the agent's, and the disagreement is
  logged. It is a measurement, not an error.

`open` needs no change. It already reads `kb` lines from facts, whoever
stated them.

### 4.4 Configuration

```yaml
jev:
  enabled: false               # principle 4
  judge_url: http://judge:8000 # as the sandbox sees it
  model: <pinned version>      # decision boundaries move with the model
  tail_lines: 40
  questions: judge/questions.yaml   # ids, texts, thresholds; reviewed
```

## 5. Step 1 plan: can JEV read our logs? (offline)

**Hypothesis.** On the knowledge base's log predicates, JEV's `noul`
answers, thresholded, state correct facts on most container logs and
abstain on the rest, with no confident wrong answers. On redacted text, it
does this better than a regex screen, and at least as well as a small Claude
model asked the same question, for less.

**The predicates.** The facts below read a container's own log. None is
derived in code today (`facts/derive.py`). This step's first task fixes the
list, dropping any whose state is not a single container's log.

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

Event-message predicates (`pull_message_names_*`, `sandbox_message_names_*`)
are left out. Their text is short and formulaic, and code is the right tool
for them.

**The corpus.** The ten captures hold 307 current and 235 previous container
logs (up to 200 lines each, about 42k lines). Many repeat across scenarios,
because they share one demo. After deduplication, each log is paired with
each predicate.

**Labels.** Hand-labelling every pair is too slow, and most pairs are
negative. The plan:
1. **Screen.** A pair is a candidate positive if the regex screen, JEV
   (p > 0.2) or Haiku flags it.
2. **Label every candidate by hand.** Two labellers: the owner and Claude,
   blind to which arm flagged it. Disagreements are settled by the owner and
   counted.
3. **Sample the rest.** A random sample of 200 unflagged pairs is labelled
   by hand, to estimate the positives all three arms missed.
4. **Constructed positives.** The captures will hold few positives for most
   predicates. Each predicate gets about five constructed logs: real error
   shapes from the mined guides' sources, set inside demo log context. They
   are marked `constructed`, scored and reported separately, and never mixed
   into the capture counts.

The labels are frozen before any arm is scored (process.md §5).

**The arms,** each run on redacted text:

| Arm | What |
| --- | --- |
| regex | one pattern set per predicate, written before looking at the arms' results |
| JEV | the predicate's `says` sentence as a `noul`; the model version pinned |
| Haiku | `claude-haiku-4-5`, the same sentence and state, asked for a probability; one call per pair |
| JEV, raw | JEV on unredacted text: the redaction's cost |

**Measures,** per predicate and overall:
- **confident wrong:** a fact stated (p ≥ hi or ≤ lo) that the label
  contradicts. Split into false positives, and false negatives, the kind
  that would rule a cause out;
- **abstain rate** at the chosen thresholds;
- dollars and milliseconds per log;
- for the raw arm, the change in confident wrong and abstain against JEV.

Thresholds are chosen per predicate on the constructed set, then applied
unchanged to the capture set, so they are not fitted to what they are
scored on.

**Can we see it?**
- **Confident wrong:** yes, as counts. With a handful of positives per
  predicate, the doc reports counts and not rates, and says that it does.
- **JEV against regex and Haiku:** only where the difference is large. A
  tie is a finding: it means the simpler arm wins.
- **Any change in the live agent:** not in this step. Nothing runs live.

**Budget.** One week of calendar time. $25 in all: JEV calls (to be checked
against TypeSafe's pricing before the first call), Haiku calls, and Claude's
labelling. Hand labelling is the owner's time and the step's real cost.

**Done** when, on the capture set at thresholds fixed on the constructed set:
- JEV states no confident false negative;
- it states at most one confident false positive across all predicates;
- it abstains on at most a third of the pairs;
- it beats the regex arm on confident wrong or abstain, on at least five
  predicates.

**Stop, and say which,** when:
- **regex matches JEV.** Close the step, and put the regex screen in the
  facts layer as code (010's own principle);
- **Haiku matches JEV** at a cost that is acceptable per log. The question
  becomes whether a Claude judge is worth the turns. JEV's own case is gone;
- **JEV makes confident false negatives that no threshold removes.** The
  idea does not work on this text;
- **redaction costs more than it keeps.** The raw arm is clearly better.
  That is a decision for the owner about data, not a threshold to tune;
- **the budget is spent.**

**What it produces,** in every outcome:
- `judge/questions.yaml` (draft);
- the labelled set, under `tests/judge/`, with its provenance;
- `uv run k8srca eval judge` to rerun the arms;
- a results table here (§7).

## 6. Later steps, in outline

Each is planned in full only when the one before it ends.

- **Step 2: the facts layer, behind the flag.** The judge service, the
  egress rule, `by: "jev"` facts, and the recorder keeping log results in
  fixtures (`record-results` keeps none today). Offline:
  `open` on the scenarios with log-shaped discriminators (jvm-oom-on-startup,
  liveness-probe-wrong-port, deploy-regression) reaches the same leader with
  no agent facts. Live, n=3, with the owner's go-ahead: a guard for no
  REGRESSION under 004 §6.4, and a lean, at most, on `fact` commands,
  investigator delegations and dollars per run.
- **Step 3: the reviewer's gate** (§3, row 2), on saved answers.
- **Step 4: the grader screen** (row 3). An instrument change, so it lands
  only between designs, after an agreement check on saved verdicts.
- **Step 5: symptom matching** (row 4).

## 7. Results

None yet.

| Step | Plan | Outcome | Decision |
| --- | --- | --- | --- |
| 1 | §5 | | |

## 8. Open questions

- **TypeSafe's terms.** What it retains of a request, for how long, and
  where. This goes into the redaction decision as much as the patterns do.
- **Pricing, limits and the API host.** The public docs pages read so far
  do not state them.
- **The pinned model version.** Whether JEV versions can be pinned, and how
  long a version lives.
- **The state size.** Whether 40 lines is the right tail, or JEV takes the
  whole 200 at no cost in accuracy.
- **Agent facts, after step 2.** Whether SKILL.md's paragraph on recording
  text-read facts shrinks once JEV states most of them. Not before step 2's
  measurements.

## 9. Rejected alternatives

- **JEV ranking the candidates, or setting the strength.** It would put
  uncalibrated numbers where `open`'s readable rule is, and ten scenarios
  cannot calibrate them.
- **Calling JEV from the sandbox with the key in its environment.** This
  would mean a second credential in the place agent code runs (§4.1).
- **Sending whole logs.** It sends more data than any predicate needs, and
  more personal data with it.
