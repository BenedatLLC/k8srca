# Design 006 — Hold-out scenarios

**Status:** done in part, on hold, 2026-10-23 (owner). Proposed 2026-10-14. The kit (§5) is built, and the
`restraint` truth field landed with scenario 7 (2026-10-15). The three documents
handed to the hold-out author are written:
[`docs/holdout/targeted-scenarios.md`](../docs/holdout/targeted-scenarios.md)
(what we build, so they avoid it),
[`docs/holdout/requirements.md`](../docs/holdout/requirements.md) (what the
hold-out suite should cover) and
[`docs/holdout/authoring.md`](../docs/holdout/authoring.md) (how).

Companion to [004](004-scenario-testing.md), whose scenarios, grader and report
this reuses unchanged. 004 says how a scenario measures the agent; this says how
to keep one set of scenarios that we never tune against.

**After [010](010-facts-and-short-answers.md) (2026-10-23), the hold-out suite
stays on the rubric.** Targeted scenarios are graded on checklists, and the
rubric is opt-in there (010 §7, §9.10). A hold-out scenario has no
`checklist.yaml`, so `scenario run` grades it on the 004/008 rubric, and the
score report (§6) carries the rubric's dimensions. A hold-out scenario given a
checklist would be graded on it instead, and its report's dimension cells
would be empty. So the authoring guide does not ask for one. Hold-out scores
are therefore not comparable with the targeted suite's checklist scores.

---

## 1. Why

Every scenario in `tests/scenarios/` is also where we look for the next fix.
That is what it is for, and it means a better score on it cannot distinguish
a better agent from an agent fitted to those scenarios.

This is not hypothetical. On 2026-10-14 three changes were designed by reading
`jvm-oom-on-startup` answers: a skill rule, then a coordinator rule, each naming
the exact claims those answers kept making. Neither moved `evidence`, which made
the question moot that time. Had one worked, the jvm score would have risen and
we would have had no way to tell whether the agent got better at evidence or
learned not to say "the only JVM services". `itbench-33`'s rivals 0/6 was
deliberately left alone for the same reason (004 §12.1) — but that was a
judgement made once, not a property of the suite.

A hold-out suite answers the question the targeted suite cannot: **does a
change that helped where we looked also help where we did not?**

## 2. The boundary: who knows what

The suite is authored, run and kept by a colleague who does not work on the
agent. We see scores and nothing else. Blindness is a property of where the
suite lives, not of anyone's discipline.

| | Us (agent developers) | Hold-out author |
| --- | --- | --- |
| Machine | ours | separate |
| Anthropic workspace | production + our scenario environment | their own workspace |
| Knows | the application's name; the scores | everything in the suite |
| Holds | this repo, the kit, the release tags | the suite (a separate, private repo), its runs |

What crosses, and only this:

- **To them:** a release — a git tag of this repo. They build the sandbox
  image from it on their machine (`k8srca sandbox build`), so agent, skills,
  prompts and grader are exactly that commit.
- **To us:** a score report (§6) per release. Per-scenario rows under opaque ids
  `H1…Hn`, per-dimension pass counts, tool calls, cost, unsupported-claim
  counts, check pass counts, and the grader digest. No answers, no grader notes,
  no claims, no scenario names, no questions, no check findings — every one of
  those names what the scenario is about.

The sessions run in their workspace, so the transcripts are not in our Console
either. That is why the separate workspace matters: a hold-out run in ours is
readable by anyone who opens it.

## 3. A different application

Every targeted scenario runs on the OpenTelemetry demo. The prompts, the wiki
pipeline and our sense of which traps matter are all shaped by it. A hold-out
on the same application tests whether we fitted the *faults*; one on a
different application also tests whether we fitted the *application*. It is
also the first real test of kubewiki's claim to describe a deployment it was
not developed against.

Two rules follow:

- **Not the OTel demo, and not Online Boutique.** The OTel demo is descended
  from Online Boutique; the service names (`ad`, `cart`, `checkout`,
  `currency`, `email`…) and call graph largely carry over, so it would leak
  everything we learned.
- **We never build a targeted scenario on the hold-out application.** The
  author tells us its name so that we can keep this rule, and for nothing else.

## 4. What we hand over

1. **[`targeted-scenarios.md`](../docs/holdout/targeted-scenarios.md)** —
   every scenario we have built or plan to, by vehicle and by what it
   discriminates, so the hold-outs do not repeat them. We update it when the
   plan changes and tell the author; a hold-out that later collides with a
   new targeted scenario stays — they were first.
2. **[`requirements.md`](../docs/holdout/requirements.md)** — what the suite
   should cover: reasoning modes, mandatory kinds (a restraint scenario among
   them), size, application criteria, how to author truth. Requirements, not
   scenarios.
3. **The kit** (§5): commands in this repo that make (1)–(2) practical on
   another machine, plus an authoring guide.

The suggestion pool in `requirements.md` is deliberately broader than any suite
will use, so knowing it tells us little about which were chosen.

## 5. The kit

The 004 pipeline already does the work — record, build the wiki, run, grade —
but it assumes our machine. The survey that motivated this section found:

- **The wiki inherits `k8srca.yaml`'s sources** when a scenario names none:
  the OTel chart (`opentelemetry-demo` 0.40.7, release `my-otel-demo`), the
  opentelemetry.io docs and `namespaces: [default]`. On another application the
  agent would be handed a wiki of the wrong system and nothing would fail.
  `scenario record --arch-build` (the default) rebuilds the production skill
  the same way.
- **The runner reads production's `.k8srca/state.json`** for the inherited
  k8s-rca skill, so `k8srca sync` has to have run in the author's workspace,
  and the scenario environment (`K8SRCA_SCENARIO_ENVIRONMENT_ID`) has to be
  created by hand in the Console.
- **`restraint` has no truth field.** It is null unless the grader model
  decides the scenario is about declining. A hold-out restraint scenario
  should not rest on that.
- **Run records reveal contents.** `.k8srca/scenario/runs/*.jsonl` holds full
  answers and grader notes; only the aggregate (`last-run.json`) is scores.
  Its scenario ids are directory names, which are descriptive.

The kit, as `k8srca holdout …`:

| Command | Does | Built on |
| --- | --- | --- |
| `doctor` | Checks the author's setup: `.env` keys present, scenario environment set and not production's, `k8srca sync` run, sandbox image built for this commit, k8stools up | `bringup.status` |
| `record <id> -n <ns>` | Captures the cluster. Refuses unless `scenario.yaml` has an `architecture` block; never rebuilds the production skill | `scenario record --no-arch-build` |
| `build-wiki <id>` | Builds the scenario's wiki. Refuses to inherit sources from `k8srca.yaml` | `scenario build-wiki` |
| `lint <id>` | Mechanical checks on `truth.yaml` (below) | `checks.closed_world`, `checks.numeric_claims`, model validation |
| `calibrate <id>` | A full-detail run for the author's own reading, to find truth errors before sealing | `scenario run --full` |
| `seal <id>` | Freezes a calibrated truth and assigns its opaque id; a re-seal needs `--reason` and counts as a truth revision | `holdout.yaml` in the suite |
| `score` | Runs every sealed scenario at n=6 and writes the score report (§6). Refuses a truth edited since its seal, and an agent with uncommitted changes | `scenario run` |

Every command refuses a `--root` inside this repository: one `git add .` would
put the suite in our history.

All of it takes `--root <suite>` and writes run records under the suite, not
under `.k8srca/`, so the author's suite is self-contained in their own repo.

**`lint`** runs the closed-world and numeric checks over the truth file's own
text — every pod, image and count a truth note names must be in the capture —
and checks the pins. A `must_identify` term found nowhere in the capture or
the wiki is advice, not a failure: terms are matched against answers, and a
phrase ("memory limit") can be right without appearing verbatim in any object. It catches typos and invented names. It does not catch a wrong
*reading* of a correct fact, which is how all three of our own truth errors
happened (004 §5); `calibrate` is for those.

**A `restraint` truth field** (`restraint: {note: …}`), read by the grader so
the dimension applies because the truth says so rather than because the grader
inferred it. This changes the grader's system prompt, so it moves the grader
digest and our own baseline resets with it (004 §7). Scenario 7
(`nothing-is-wrong`) needs the same field, so it lands with that scenario.

**The authoring guide** ([`docs/holdout/authoring.md`](../docs/holdout/authoring.md)):
setup on a fresh machine, the workflow below, and 004 §5–§6 condensed to what
an author needs — real breakage, truth from the capture, the three ways our
truth went wrong.

`build_wiki` no longer needs a `truth.yaml` (it named the wiki slot through
`ScenarioDir`, which requires one): an author builds the wiki before writing
the truth that pins it. `holdout record` and `build-wiki` work on a `Draft`,
the scenario without its truth.

### The author's workflow

1. Clone this repo at the release tag; fill `.env` from their workspace; create
   the scenario environment; `k8srca up` (k8stools only — no tunnel, no Slack);
   `k8srca sync`; `k8srca sandbox build`; `k8srca holdout doctor`.
2. Install the application on their minikube. Write `scenario.yaml` with its
   `architecture` block — that application's chart and docs if it has them, or
   `[live_cluster, change_history]` alone.
3. Break the cluster (004 §4.1), let it settle, `holdout record`, restore.
4. `holdout build-wiki`; write `truth.yaml`; `holdout lint`.
5. `holdout calibrate` (n=3–6). Read the answers **for errors in the truth
   only**, fix them, then `holdout seal`: from here the truth changes only by
   a recorded decision (§7).
6. Per release: `holdout score`, send the report.

## 6. The score report

```yaml
release: {commit: <sha>, tag: <git tag>, dirty: false}
grader: <apparatus digest>
taken_at: <time>
scenarios:
  H1: {runs: 6, invalid: 0, cause: 6/6, evidence: 4/6, rivals: 5/6,
       traps: 6/6, gaps: -, restraint: -, unsupported_per_run: '0.7',
       checks_passed: 6/6, tool_calls_mean: 18.0, usd_mean: 0.21,
       truth_revision: 1}
  ...
```

The `H` ids are stable across releases (a manifest in the suite maps them to
directories), so a row can be compared release to release. Dimension columns
and the unsupported-claim count are not content: they say how well the agent
did, not what it was asked. Check *findings* are content (`answer cites pod
'x-7d9'`) and stay with the author; only the pass count crosses.

## 7. Reading the results

- **A hold-out drop is a finding, not a task.** It blocks the release or is
  recorded as a known cost. The fix is designed against targeted scenarios,
  never against the hold-out that dropped — we cannot see it, and should not
  try to infer it.
- **The 004 noise rule applies** (004 §6.4). A single batch against the
  previous release's report resolves a move of four runs in six or more; a
  smaller move is a lean, because the agent alone varies by up to three
  between identical batches. Every answer is graded once: the default panel
  went from three gradings to one on 2026-10-18, and `holdout score` does not
  raise it. So the grader's own variation, 11–16% on evidence (process §6),
  is part of that spread. The panel size is in the grader digest the report
  carries, so the author's scores and ours are graded alike. A lean that matters is settled the 004
  way: the previous release scored again beside the new one in the same
  sitting.
- **Unsealing retires the scenario.** If a drop is serious enough that we
  jointly decide to look at a hold-out, it moves to the targeted set (contents
  shared, truth and all) and the author writes a replacement. A scenario we have
  seen is not a hold-out any more.
- **Truth fixes after sealing are recorded** in the suite with the release they
  apply from, and the report says so, so a score change from a truth fix is not
  read as an agent change.
- **Cadence:** once per shipped change set, not per experiment. Each run is a
  look; a hold-out consulted after every experiment becomes a targeted suite
  with extra steps.

## 8. Open questions

1. **n per release.** n=6 matches the targeted suite as it stood in 2026-10.
   At four to eight scenarios this was estimated at $12–48 a release with
   three-way grading.

   The recount in 010 §9.10 puts a rubric grading at about $0.70 an answer on
   a reference of about 190K tokens, less when the cache hits. At n=6 with
   one grading each, that is up to about $4 of grading per scenario, plus
   $1–4 of sessions. Revisit if releases get frequent.
2. **Who reads a calibration batch.** The author, alone. Having us help would
   end the blindness for that scenario.
3. **Results history.** Score reports could live in this repo
   (`docs/holdout/results/`) — they are scores only — so release-to-release
   movement is visible next to the change that caused it.
