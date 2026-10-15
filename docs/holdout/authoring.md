# Authoring the hold-out suite

For the hold-out author ([design 006](../../designs/006-holdout-suite.md)). Read
[requirements.md](requirements.md) and
[targeted-scenarios.md](targeted-scenarios.md) first: they say what to build
and what to avoid. This says how.

You will work on your own machine, in your own Anthropic workspace, against
your own cluster, and keep the suite in your own private repository. The only
thing that comes back to us is the score report `holdout score` writes.

---

## 1. Set up (once)

Follow [installation.md](../installation.md) §2–§5 and
[cluster-setup.md](../cluster-setup.md) for the parts that apply. You need
the agent and cluster access, not Slack.

1. **Clone this repo at the release tag** you were given:
   `git clone … k8srca && cd k8srca && git checkout <tag> && uv sync --extra dev`.
2. **`.env`**, from `.env.example`, filled from *your* workspace:
   - `ANTHROPIC_API_KEY`: an org key for your workspace. It syncs agents,
     creates sessions and grades.
   - `ANTHROPIC_ENVIRONMENT_KEY`: for your workspace's production environment.
     `k8srca sync` creates the environment.
   - `ANTHROPIC_TEST_ENVIRONMENT_KEY` and `K8SRCA_SCENARIO_ENVIRONMENT_ID`: a
     **second** environment, for scenario runs, which you create by hand in the
     Console. It must not be the production one.
   - Leave `K8SRCA_SSH_*` empty if your cluster is on this machine.
3. **Cluster access:** the read-only credential from cluster-setup.md, then
   `uv run k8srca up`. Only the k8stools container holds the kubeconfig; nothing
   in this repo reads it directly.
4. `uv run k8srca sync`, then `uv run k8srca sandbox build`.
5. Make your suite directory, **outside this repo**, as its own git repo:
   `mkdir ~/holdout && cd ~/holdout && git init`. Add `.runs/` to its
   `.gitignore` if you do not want run detail committed.
6. `uv run k8srca holdout doctor --root ~/holdout`. Every line should say `ok`.
   A `note` is something only some steps need.

Every later release: `git checkout <new tag>`, `uv sync`, `k8srca sync`,
`k8srca sandbox build`, `holdout doctor`.

## 2. Each scenario

Commands run from the k8srca checkout, with `--root ~/holdout`.

### 2.1 Write `scenario.yaml` first

```yaml
id: orders-stuck                 # any name; it never leaves your suite
question: |
  Orders have been stuck in "pending" for 20 minutes. Why?
sources:
  k8stools:
    state: k8s.json
    clock: frozen
budget:
  max_tool_calls: 40             # set from calibration (§2.5)
  max_usd: 0.70                  # reported, never gates
architecture:                    # required: what the agent's wiki describes
  namespaces: [shop]
  sources:
    - type: live_cluster
    - type: change_history
    # and, if your application has them, its chart and docs:
    # - type: chart_repo
    #   helm: {repo: https://…, chart: …, version: …, release: …}
    # - type: docs
    #   git: {repo: https://…, ref: <commit>, path: docs/services}
```

The `architecture` block is required for a hold-out (`record`, `build-wiki` and
`lint` refuse without it). Left out, the wiki would be built from *our*
application's chart and docs, and nothing would fail. See
`tests/scenarios/itbench-33-invalid-node-selector/scenario.yaml` for a real
example and `k8srca.yaml`'s `architecture.sources` for every source type.

Ask the question an SRE would ask. Name the symptom, never the cause.

### 2.2 Break the cluster, then record it

Break it for real, and let it settle long enough to look like an incident:
restarts accumulate, events repeat, the old ReplicaSet keeps serving or
doesn't. Then:

```bash
uv run k8srca holdout record orders-stuck --root ~/holdout -n shop
```

Then restore the cluster. The capture is now the scenario's world; the live
cluster is never read again for it.

### 2.3 Build the wiki

```bash
uv run k8srca holdout build-wiki orders-stuck --root ~/holdout
```

This builds the agent's architecture wiki from the capture, replayed, plus the
chart and docs you named. Synthesis costs about $1 the first time and is cached
after that. It prints the digest to pin.

### 2.4 Write `truth.yaml` against the capture

```yaml
id: orders-stuck
capture:
  captured_at: '<from record>'
  skill_digests:
    wiki: <from build-wiki>
cause:
  summary: >-
    What happened, as the capture shows it.
  must_identify: [orders, <the object or value an answer must name>]
rivals:                          # explanations a good answer considers and rules on
  - id: db-down
    disposition: refuted         # confirmed | weakened | refuted | could_not_check
    also_accept: [weakened]      # where the capture honestly supports either
    note: Why, citing what in the capture shows it.
traps:                           # tempting mistakes
  - id: blames-the-noisy-neighbour
    expect: avoided              # or: flagged (a thing the answer must point out)
    note: What is wrong, and what the capture shows instead.
gaps:                            # evidence the agent cannot reach
  - id: no-metrics
    note: What would settle it, and why it is not available.
restraint:                       # only for a declining scenario
  note: >-
    The visible breakage that is NOT the answer, and what declining looks
    like here.
```

`restraint` is what makes a scenario a declining one: the grader then checks
that the answer found no cause rather than reaching for the breakage the note
names, and a run it cannot judge counts as a failure. Leave it out everywhere
else. Before writing it, confirm from the capture -- the `metrics` sample
included -- that nothing near the asked-about service is actually wrong. Our
own first declining scenario failed exactly there: the truth said nothing was
wrong, and the usage sample showed the asked-about service burning CPU above
its Go memory target. Three of six runs found it, correctly.

The two existing truth files in `tests/scenarios/` are worked examples. Their
comments explain each judgement.

**Write it from the capture, not from what you did to the cluster.** Every one
of our truth errors was a note written from a mental model of the fault that
the capture then contradicted (design 004 §5):

- A service with a high restart count was called "normal history". The
  capture showed it was being OOM-killed every fifteen minutes.
- Two crash loops were called "independent". They were the only two containers
  with that memory limit, so treating them as one condition was the
  best-supported reading.
- An onset was implied by the age of a ReplicaSet. Age is when it was
  deployed, not when it started failing.

If the capture shows more than you broke, the truth says what the capture
shows. Then:

```bash
uv run k8srca holdout lint orders-stuck --root ~/holdout
```

The lint is free. It checks the pins, and that every generated name, image and
restart count your truth mentions is really in the capture. It cannot check
your *reading* of the capture; calibration is for that.

### 2.5 Calibrate

```bash
uv run k8srca holdout calibrate orders-stuck --root ~/holdout --n 3
```

About $0.20–0.70 a run in sessions, plus about $0.35 to grade it: every
answer is graded three times and each dimension takes the majority, because
one grading alone flips the `evidence` verdict often enough to move a score by
two runs in six. This prints everything: answers, grader notes and claims.
**Read it for mistakes in your truth only:**

- A dimension that passes in some runs and fails in others. Suspect the truth
  before the agent.
- A rival or trap the grader could not apply, or applied in a way you did not
  intend. The note is ambiguous.
- An "unsupported" claim that is actually in the capture. Your truth or the
  capture is missing something.
- A split vote, printed as `traps=NO(1/3)`: three gradings of the same answer
  disagreed. On `evidence` that is ordinary; on a rival, trap or gap it usually
  means a note that can be read two ways.

Fix, re-lint, re-run until the truth says what you mean. Set
`budget.max_tool_calls` above the highest you saw, with room to spare.

### 2.6 Seal

```bash
uv run k8srca holdout seal orders-stuck --root ~/holdout
```

The scenario gets its opaque id (`H1`, `H2`, …) and its truth is frozen. If you
later find a truth error, fix it and re-seal with `--reason "…"`. The report
counts revisions, so a score that moved because the truth changed is not read
as the agent changing. Commit the suite, `holdout.yaml` included.

## 3. Each release

```bash
uv run k8srca holdout score --root ~/holdout
```

This runs every sealed scenario at n=6 (about $3–6 each, sessions and the
three-way grading together) and writes
`reports/<tag>-<time>.yaml`. **Send that file, and nothing else.** It holds:
- the release and grader digest;
- per opaque id, the pass counts per dimension, unsupported claims per run,
  check pass count, mean tool calls and cost, and the truth revision.

It is built field by field so nothing else crosses: no answers, notes,
questions or scenario names.

It refuses an agent with uncommitted changes, because that is not the release
you were handed.

## 4. What never leaves your suite

Answers, grader notes, claims, check findings, questions, scenario names,
descriptions of what broke, and `.runs/`. If we ever jointly decide to look at
a scenario because its score dropped, it stops being a hold-out. It moves to
our targeted suite, and you write a replacement.
