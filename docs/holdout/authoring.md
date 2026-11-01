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
  max_tool_calls: 40             # set from calibration (§2.6)
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
    What happened, as the capture shows it: the objects, values, times and
    counts that show it, and what the capture cannot show.
  must_identify: [orders, <the object or value an answer must name>]
```

The truth is your account of the scenario, written once and read by you. Runs
are scored on the checklist (§2.5), which you write from it. `must_identify`
is the one part a run checks directly: an answer that never names these terms
fails the scenario's checks. (Older truth files also list `rivals`, `traps`,
`gaps` and `restraint` for a rubric grader the kit no longer scores on. They
are harmless; the checklist now carries all of it.)

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
shows. For a declining scenario, confirm from the capture -- the `metrics`
sample included -- that nothing near the asked-about service is actually
wrong. Our own first declining scenario failed exactly there: the truth said
nothing was wrong, and the usage sample showed the asked-about service burning
CPU above its Go memory target. Three of six runs found it, correctly.

### 2.5 Write `checklist.yaml` from the truth

The checklist is what a run is scored on: statements a good answer makes, and
statements it must not make, each graded yes or no against the answer alone.
The grader sees the question, the answer and the checklist, never the capture,
so every statement must be checkable from the answer's text.

```yaml
id: orders-stuck                 # the scenario's id
items:
  # --- cause: what a right answer says happened ---
  - id: cause-consumer-down
    expect: present
    group: cause
    diagnosis: true              # a run passes only if it meets every diagnosis item
    statement: orders-worker is not consuming the queue; its pod is crash-looping.
    accept: ["orders-worker is down", "nothing is reading the orders queue"]
  - id: cause-recent-revision
    expect: present
    group: cause
    statement: orders-worker's Deployment rolled to a new revision about 20 minutes before.

  # --- ruled out: what a good answer considers and dismisses ---
  - id: rules-out-db
    expect: present
    group: ruled_out
    statement: the orders database is ruled out; it is running and accepting connections.

  # --- traps: tempting mistakes ---
  - id: no-noisy-neighbour
    expect: absent
    group: traps
    statement: the crash loop in recommendations is a cause of stuck orders.
    tempting: ["recommendations' restarts are slowing orders"]
    allowed: ["recommendations is also failing, unrelated"]

  # --- gaps: what the capture cannot show, so an answer must not claim it ---
  - id: no-onset-date
    expect: absent
    group: gaps
    statement: a time when orders started getting stuck, stated as observed.
    allowed: ["the rollout was 20 minutes ago", "the record does not show when it began"]

  # --- next steps ---
  - id: next-rollback
    expect: present
    group: next
    statement: a next step is to roll orders-worker back to its previous revision.
```

- **`expect: present`** items are things to say; **`expect: absent`** items
  are mistakes not to make. `group` is one of `cause`, `ruled_out`, `traps`,
  `gaps` and `next`, and only counts per group leave your suite.
- **`accept`** lists phrasings that make a present statement. **`tempting`**
  lists the wrong version of an absent one, as an answer would put it.
  **`allowed`** lists nearby things an answer may say that do *not* make the
  statement: the hedge that should not count as the mistake, or the half-truth
  that should not count as the finding.
- **One claim per statement.** An item that bundles two claims is graded on
  whichever the grader reads first, and it splits (§2.6).
- **Mark at least one `diagnosis: true` item**, the statement of the cause. A
  declining scenario's diagnosis item says that nothing found is the cause.
- Write it **from the truth only**. A statement the truth does not support is
  an error in one of them.

The checklists in `tests/scenarios/*/checklist.yaml` are worked examples;
`nothing-is-wrong` is a declining one. Then:

```bash
uv run k8srca holdout lint orders-stuck --root ~/holdout
```

The lint is free. It checks the pins, that a checklist exists with a diagnosis
item, and that every generated name, image and restart count your truth and
checklist state is really in the capture (an absent item's statement and
`tempting` are mistakes on purpose, so they are not checked). It cannot check
your *reading* of the capture; calibration is for that.

### 2.6 Calibrate

```bash
uv run k8srca holdout calibrate orders-stuck --root ~/holdout --n 3
```

About $0.20–0.70 a run in sessions, plus a few cents for each checklist
grading. Every answer is graded three times (`--grades`), and the kit reports
each item's **agreement**: how often a grading matches its answer's majority
verdict. The bar is 97% overall with no item split on more than one answer; an
item that misses it reads two ways. This prints everything: answers, each
item's verdict and the quote it rests on. **Read it for mistakes in your truth
and checklist only:**

- An item met in some runs and missed in others. Suspect its wording before
  the agent: read the quotes and see whether the grader read it as you meant.
- An item split on an answer, printed as `split on N answer(s)`. Sharpen its
  statement, or add the phrasing it hesitated over to `accept` or `allowed`.
- A trap met by an answer that said something true. The statement is too
  broad; narrow it or add an `allowed`.
- A cause item no answer meets, where the answers say it in other words. Add
  those words to `accept`.

Fix, re-lint, re-run until the checklist says what you mean and meets the bar.
Set `budget.max_tool_calls` above the highest you saw, with room to spare.

### 2.7 Seal

```bash
uv run k8srca holdout seal orders-stuck --root ~/holdout
```

The scenario gets its opaque id (`H1`, `H2`, …) and its truth and checklist are
frozen. If you later find an error in either, fix it and re-seal with
`--reason "…"`. The report counts revisions, so a score that moved because the
checklist changed is not read as the agent changing. Commit the suite,
`holdout.yaml` included. A scenario sealed by an earlier release, before the
kit scored on checklists, needs its checklist written and a re-seal.

## 3. Each release

```bash
uv run k8srca holdout score --root ~/holdout
```

This runs every sealed scenario at n=6 (about $1–5 each), grades each answer
once on its checklist, and writes `reports/<tag>-<time>.yaml`. One grading is
enough because calibration showed each item reads one way. **Send that file,
and nothing else.** It holds:
- the release and the checklist grader's digest;
- per opaque id: runs that met every diagnosis item, mean items met out of
  the checklist's size, items met per group (`cause`, `ruled_out`, `traps`,
  `gaps`, `next`), check pass count, mean tool calls and cost, and the
  revision count of its truth and checklist.

It is built field by field so nothing else crosses: no answers, notes,
questions, scenario names or checklist item ids.

It refuses an agent with uncommitted changes, because that is not the release
you were handed.

## 4. What never leaves your suite

Answers, grader notes, claims, check findings, questions, scenario names,
descriptions of what broke, and `.runs/`. That includes the sandbox
workspaces your runs leave in the k8srca checkout, under
`.k8srca/workspaces/` (`sandbox.workspaces` in `k8srca.yaml`), and the run
detail under `.k8srca/`. Each workspace holds an evidence record, a copy of
every tool result the agent read, and those name the scenario's objects.
`.k8srca/` is gitignored, so it stays on your machine unless you copy it.

**Never run `k8srca scenario publish` on a hold-out scenario.** It commits the
scenario's capture and wiki to `BenedatLLC/k8srca-scenario-data`, which is
public. Your scenarios need no publishing: a capture with no digest pinned in
`scenario.yaml` is read where it is. If we ever jointly decide to look at
a scenario because its score dropped, it stops being a hold-out. It moves to
our targeted suite, and you write a replacement.
