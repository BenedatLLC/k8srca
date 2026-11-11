# Design 014 — SREGym: a live exam, a one-use hold-out, a tuning target, a leaderboard entry

**Status:** in progress (draft), 2026-11-07. Step 1 done 2026-11-08 (§10). Step 2a (arm A's baseline reading, with per-application wikis) planned 2026-11-08 and next; step 2 waits on 011's steps 1–2 and on §9 question 1, step 3 on arm B. Steps 1–3 are planned in full
(§7); steps 4–6 are outlines, planned when the step before them ends.

**Process note.** `docs/process.md` §4 allows one design in progress. The owner
parked 013 on 2026-11-07, until this design and 011's decision-model work are
complete, so 014 is in progress (`CLAUDE.md` and process.md §7 say so), with 011 beside it.
011 stays a draft whose steps 1–2 this design needs before step 2 (§7). Both run
in parallel (owner, 2026-11-07; §9 question 8). The private
suite repo exists (`~/code/k8srca-sregym-suite`, 2026-11-07).

Companion to [004](004-scenario-testing.md) §12.2 (SREGym as a periodic live
exam, item X3), [005](005-modular-architecture.md) §8.4 (a live exam is an
ordinary deployment pointed at the exam cluster), [006](006-holdout-suite.md)
(what a hold-out is) and [011](011-decision-models.md) (the decision-model
version this design compares against baseline).

---

## 1. Goal

[SREGym](https://github.com/SREGym/SREGym) injects faults into live Kubernetes
applications and grades an agent's diagnosis. The owner's plan, in order:

1. **Proof of concept** on SREGym-Lite (17 faults): a k8srca agent diagnoses a
   live SREGym problem and SREGym grades it.
2. **A hold-out, once.** SREGym-Lite is the hold-out for one comparison:
   baseline against the decision-model version (011).
3. **A tuning target.** After that comparison, SREGym joins the evaluation
   scenarios and we do the best we can on it. It is no longer a hold-out, and a
   **new hold-out suite** is created.
4. **The SREGym leaderboard.**

What the owner has fixed: the baseline is the agent as tagged `holdout-v1`
(`c817681`; the tag itself is on `b1fff83`, the hold-out kit). Decided
2026-11-08: the seam of §4.4 goes onto `main`, a new tag **`holdout-v2`** marks it
(`e7fa33c`), and both open branches (this one and `jev-decision-models`) are
rebased onto it. Arm A is `holdout-v2`, whose agent behaves as `holdout-v1`'s.
The decision-model code has not diverged yet, so
011 can change if this design needs it (§9 question 1). **Mitigation is out of
scope for now** (owner, 2026-11-07): every run is diagnosis only, and the
leaderboard's mitigation half is deferred (step 6).

**Which suite:** SREGym-Lite for steps 1–3 and 5–6, and **SREGym-Lite-IA kept
untouched as the later hold-out** (§3.1).

## 2. What we know about SREGym

Read directly in the local fork (`~/code/SREGym`): `README.md`,
`docs/SREGym-Lite.md`, `agents.yaml`, `clients/claudecode/driver.py`,
`clients/claudecode_radar/README.md`. Read second-hand, through a subagent that
fetched the pages: the leaderboard and blog pages (marked **[web]**). Recheck
those figures at the source before this design cites them anywhere else.

- **The harness.** `uv run main.py --suite sregym-lite --agent <name> --model <m>`.
  Per problem it deploys the application, injects the fault, and starts the
  agent through `agents.yaml`'s `kickoff_command`. The agent talks to a
  conductor API: `GET /get_app`, `GET /status`, `POST /submit`. Stages are
  `diagnosis` then `mitigation`. `--stages diagnosis` runs the first alone, and
  `--n-attempts` repeats.
- **Agents run in Docker containers** (README, "Container Isolation"), except
  those registered with `container_isolation: false` (demo, tierzero,
  cloudthinker). Registration `cluster_access: false` withholds the kubeconfig
  and the egress routes to the API proxy and SREGym's MCP server.
  `claudecode_radar` is the precedent for an agent that reaches the cluster only
  through its own tool server.
- **The view proxy.** What an SREGym agent can see of the cluster is filtered:
  hidden namespaces and labels, Chaos Mesh objects, write restrictions.
  `claudecode_radar` keeps that filter by reading through SREGym's
  `radar_view_proxy` (`--kubeconfig-out`). k8stools must read through the same
  proxy, or k8srca sees the fault injector (§4.2).
- **The Lite cohort.** `SREGym-Lite-1004`, 17 faults **[web]**, listed in
  `docs/SREGym-Lite.md`. Three are on astronomy-shop, which is the OTel demo our
  targeted scenarios run on (006 §3 says a hold-out should not be the OTel demo;
  §6 below). The rest are on hotel-reservation and social-network
  (DeathStarBench) and on infrastructure faults (network policy, namespace
  quota, Valkey, and so on).
- **Grading.** A free-text diagnosis is judged against SREGym's nine-question
  rubric; a pass is a composite score of at least 0.70, with GPT-6 Astra at high
  reasoning effort as judge **[web]**. Per-agent results are in
  `*_results.csv`.
- **The leaderboard** ranks on **E2E success: correct diagnosis and successful
  mitigation on the same run** **[web]**. It also shows Diag. (%), Mit. (%), time
  and tokens. Third-party rows are marked "verified by the SREGym team". **No
  submission process, harness requirement or tuning rule was found** on the site
  or in the local README. The repository's README says the `full` profile is the
  one "for results you intend to compare against the leaderboard"; `svelte`
  scores are not comparable.
- **Prior art on our exact question [web].** SREGym's own blog reports two
  decision-model pipelines on the older 21-fault cohort: Jev alone, 76.2% of
  105 diagnoses; and a decision model choosing the root cause with an LLM
  reviewing it, 89.5% (Jev) and 95.2% (GPT-6 Luna Decisions), against 78–79% with
  no review. They tuned on those same faults, in the open. This is evidence that
  a decision model can score well on this suite. Its role there is different
  from 011's (§9 question 1).

## 3. The suite's life, and what each phase may do

| Phase | The suite is | We may | We may not |
| --- | --- | --- | --- |
| A. Mechanics (step 1) | the **burned set**, 3 faults | read everything: problems, answers, grader notes | claim any score means anything |
| B. One-use hold-out (steps 2–3) | the **hold-out set**, the other 14 faults | read scores, after the run | read the hold-out problems' definitions, answers or grader notes before the step 3 reading is written; change any frozen item (§5) |
| C. Tuning target (step 5) | burned, all 17 | read everything, build offline-first | call anything on it a hold-out |
| D. Leaderboard (step 6) | the public cohort | submit what the rules allow | tune on the leaderboard run |

Two rules make phase B real:

- **Nobody reads the 14 faults' definitions.** That includes Claude. The fault
  names are public and fair; the source under SREGym's `conductor/problems`,
  the oracles and the mitigation scripts are not. Reading them to design the
  decision model would be fitting to the exam.
- **Scores only until the reading.** Run records hold full answers. They are
  written but not opened until step 3's reading is in this file. This is the
  weaker form of 006's hold-out, whose author reports scores only and whose
  sessions are in another workspace; the owner runs this one. It is accepted
  and said here. Unsealing is automatic: after step 3 the 14 are burned (§3
  phase C).

### 3.1 Lite and Lite-IA

`docs/SREGym-Lite-IA.md` (in the fork) is the same 17 Lite faults, each ported to
a real component of three other applications (Frappe/ERPNext, Saleor, Slack
Spine) in place of hotel-reservation, social-network and astronomy-shop. It is
`--suite sregym-lite-ia`. The leaderboard page shows Lite and the full suite, and
no Lite-IA toggle was seen **[web]**.

| | Lite | Lite-IA |
| --- | --- | --- |
| Faults | 17, new to us | the same 17 families, ported |
| Applications | includes the OTel demo (3 faults) | none we have built on |
| On the leaderboard | yes (`SREGym-Lite-1004`) | not seen |
| Tests whether we fitted | nothing yet | the applications, after tuning on Lite; **not** the faults |

**Decision (proposed): Lite now, Lite-IA later.**
- The suite for the one-use comparison is the one we will tune on and submit
  with anyway. That is Lite, and it burns at step 3 by plan.
- Lite-IA stays untouched through steps 1–3 and 5. It then serves as the
  **application hold-out** for step 4: it asks whether what we tuned on Lite
  holds on the same faults in new applications. It fixes the OTel-demo problem
  of 006 §3 and costs nothing to author.
- It is **not a fault hold-out**. After tuning on Lite, its faults are known
  families, and the successor hold-out (step 4) still wants fresh faults if the
  owner wants that property. The two are different tests.
- **Spent as the first hold-out instead,** it would be burned and the successor
  would have to be authored from nothing. Used the other way round, nothing is
  wasted.

**Exposure so far.** The fork's Lite-IA doc names each port's target component.
This session read it in full. It is public, and a hold-out against *applications*
survives it, but it is not blind. The design does not copy the table, and a
session that designs Lite tuning should not load Lite-IA's `environment_ports.py`
or its `apps/` packages (§3's reading rule applies to Lite-IA from step 1, until
step 4 reads it).

## 4. Architecture

005 §8.4's description holds: an ordinary deployment pointed at the exam
cluster. k8stools has the cluster credential and nothing else does. The
mitigation stage is skipped (k8srca does not mitigate, 001 §8.1).

### 4.0 Where each thing lives

The agent never sees any repository on disk. What reaches it is what
`docker/Dockerfile.sandbox` copies (`src/`, `packages/kubewiki`, `k8srca.yaml`),
the synced skills and the prompts. So the leak that matters is into *those*, and
the people who can fit to the exam are us. On one machine a separate repository
is hygiene (no stray `git add`, no hold-out content in history, no test that
reads it), not blindness; 006 gets blindness from another machine and workspace,
which this design does not have (§3). Three homes:

| Where | Holds | Why |
| --- | --- | --- |
| **The fork** (`BenedatLLC/SREGym`) | `clients/k8srca/` shim and its `agents.yaml` entry, on a branch | `kickoff_command` runs from SREGym's own tree, so the client has to be there. The fork is public (checked 2026-11-07), as is this repository, so **no records, lists or scores** go to it. The shim has nothing in it worth hiding |
| **A private suite repo**, outside `~/code/k8srca` (`K8SRCA_SREGYM_SUITE`) | the burned/hold-out split, run records, saved answers, the reading file, the driver's logic (`k8srca-sregym`, a package that imports `k8srca` as a library) | 006's pattern: `--root` outside this repository, refused if inside it. Nothing SREGym-specific enters `src/` |
| **This repo** | nothing for SREGym until phase C | `src/` ships in the sandbox image. At step 5 the package and the records are merged back in, which is the "move it back" the owner described |

**This design stays in this repo, and the repo is public.** The design holds
nothing that fitting could use: fault names are public, the burned/hold-out split
and the decision rule are choices not results, and no answer, grader note or
score is in it before step 3. Committing step 2's pre-registration here, and
tagging it before the first run, is what makes it checkable afterwards. The
sandbox image does not copy `designs/`. What stays in the private suite repo is
every run record, saved answer and grader note, and the reading file until the
reading is written (§3). Step 3's score table then goes into this file, and so
becomes public; the owner can hold it back until after a leaderboard decision.

A test in this repo scans `src/`, `skills/` and `docs/rca/` for SREGym fault
names, so a rule or a mined note written from a hold-out answer cannot ship
unnoticed. A Claude Code `deny` rule on reading the hold-out problems' source
(`SREGym/sregym/conductor/problems/**`, Lite-IA's `apps/`) is a cheap addition to
§3's reading rule, and is the owner's call.

**Consequence for the arms.** If the driver's logic lived in `src/`, it would be
inside both arms' commits, and `holdout-v1` does not contain it. Living outside,
it is identical for both arms by construction. It needs one thing from each arm:
a way to run a session on a given question. **`holdout-v1` has none** (checked
2026-11-07; §4.4), so a seam is added to both arms.

### 4.4 The seam: what `holdout-v1` lacks (checked 2026-11-07)

`holdout-v1`'s `src/`, `skills/`, `docker/` and `k8srca.yaml` are identical to
`main`'s (the tag differs from `main` in `README.md` and `docs/baseline.md`
only), so arm A's source is today's source.

The only path from a question to an answer is `scenario/runner.py`'s `run_once`,
and it is a scenario run all the way down. It needs a `ScenarioDir` (truth,
capture, checklist and a pinned cluster-architecture skill), starts the poller
and a replay k8stools, rebuilds the scenario's agents against that replay
(`sync_scenario_agent`, which uploads `sd.skill_dir`), runs the review and
revision turns, renders the facts footnotes, reads the spend and only then
grades. Public pieces that could be reused as they are: `session.create`,
`session.turn_events`/`consume`, `review.check`, `facts.render.render_workspace`,
`scenario_state`, `start_poller`. The rest, including the watchdog, the turn loop
and the revision turn, are private to `run_once`.

| Option | Cost |
| --- | --- |
| **(i) Factor a seam out of `run_once`** (**chosen 2026-11-08**): `sync_agent(skill_dir, cfg, state, …)` and `run_session(cfg, state, question, …) -> Answer`, with `run_once` calling them. Behaviour-identical, checked by a line-by-line diff of what moved, 13 new tests on `run_session`, and one replay smoke run. The commit goes onto both arms | Arm A is `holdout-v2`, `holdout-v1` plus the seam commit, not the bare tag. A refactor of `runner.py`, moderate rather than small: two extractions |
| (ii) The driver imports `session`, `review` and `render` and reimplements the loop | No change to the arms. A copy of the review, revision and rendering logic that can drift from what the baseline measured, and imports of underscore names |
| (iii) The driver builds a synthetic `ScenarioDir` per problem and calls `run_once` with live k8stools | No refactor. It abuses the type (it demands a truth and a checklist), and `run_once` grades, which the live problem cannot be |

**Done (2026-11-08), on `main`:** `run_session` and `sync_agent` (`d8ee043`), and
a fix found by the smoke run (`e7fa33c`). The scenario suite polls its own
environment with the test key, but `docker/spawn.sh` reads
`ANTHROPIC_ENVIRONMENT_KEY`, so every sandbox died at once and the session waited
out its 45-minute deadline; the poller now sets that variable from `--env-key-var`,
and the watchdog treats a non-zero `container_exit` as a failed sandbox. Smoke:
`pvc-pending-no-storageclass`, n=3, 3/3 diagnosis, 10.0/10, 6–7 calls, $0.09–0.11
a run (baseline: 3/3, 10.0/10, 6–8 calls, $0.08). Tagged `holdout-v2`; `sregym-arm-b`
is tagged when step 2 freezes it. Two more things SREGym
needs that do not exist: a second k8stools instance on the view proxy
(the production one holds port 8000 and a different cluster's kubeconfig), and a
per-problem architecture skill upload that does not go through a scenario.

### 4.1 The driver is a shim

A client registered in the fork's `agents.yaml`, under `clients/k8srca/`. It
holds no agent logic.

```
SREGym conductor ── /get_app, /status ──►  clients/k8srca/driver.py   (in the fork)
                                                 │  shell out
                                                 ▼
                                           k8srca sregym diagnose     (this repo, tested hermetically)
                                                 │  Managed Agents session, SREGym environment
                                                 ▼
                                    poller sandbox ── k8stools (view proxy) ── kind cluster
                                                 │
                         answer ◄────────────────┘
driver ── POST /submit {solution: <answer>} ──► conductor
```

`k8srca-sregym diagnose` (the suite repo's package, §4.0) takes the application
info the conductor returns, builds the question, runs one session and prints the
answer. All of the logic is there, behind hermetic tests; the fork carries a few
dozen lines and an `agents.yaml` entry. The fork stays otherwise unmodified, so that a leaderboard
run is the upstream harness plus one client.

**Isolation.** Superseded by §4.5: the proposal there is a thin containerised
shim that holds no credential, and a host-side service that does. That answers
§9 question 2's isolation half, if step 1 shows it working.

### 4.2 The cluster view

k8stools is pointed at SREGym's view proxy, not at the kind cluster's
kubeconfig. Without it, k8srca could list Chaos Mesh objects and hidden labels
that a `kubectl` agent in SREGym cannot, so a hold-out score would be partly
the injector's fingerprints. Step 1's done criterion includes a direct test:
through k8stools, the agent cannot list what SREGym hides. This also keeps
CLAUDE.md's rule: the proxy's kubeconfig is the one credential, held by
k8stools.

### 4.3 Environment, arms and the architecture skill

- **A separate Managed Agents environment for SREGym.** The scenario runner
  starts its own poller with k8stools in replay mode; a live SREGym session
  queued in the same environment could be claimed by it, and the reverse. The
  same reasoning split production from scenarios (004 §3). New settings,
  following the existing names: `K8SRCA_SREGYM_ENVIRONMENT_ID`,
  `ANTHROPIC_SREGYM_ENVIRONMENT_KEY`, and `K8SRCA_SREGYM_DIR` (the fork's path).
  SREGym's own judge credentials belong in SREGym's environment, not in
  k8srca's `.env`, which `scrub_environment` empties for the worker.
- **An arm is a commit.** Arm A is `holdout-v2`. Arm B is the decision-model
  commit. Each runs from a git worktree with its own `.k8srca/state.json`,
  synced into the SREGym environment, with its sandbox image built for that
  commit. Both go through the same driver. Whether two arms can share one
  environment without crossing needs checking in step 1 (§9 question 3).
- **The architecture skill.** 005 builds it before the fault is injected; the
  harness injects first and kicks off after. Step 1 ran with none. From step 2a on,
  each application gets a wiki built offline from its chart and values (not from
  the faulted cluster), carried by that application's own agent pair (§7 step 2a).

### 4.5 What the first live run showed (2026-11-08)

Setting up a machine: `docs/setup.md` in the suite repo (`~/code/k8srca-sregym-suite`).

One run of SREGym's own `debug` agent (it does nothing) on `network_policy_block`,
a burned fault, `--stages diagnosis`, `API_PORT=8001`. No k8srca session ran, so
nothing was spent on the agent; SREGym's judge preflight made one cheap call.

**Verified**
- A fresh clone of the fork needs `git submodule update --init --recursive`
  first. Without it the first run died at deploy ("Helm chart_path does not
  exist") after three retries and left nothing behind.
- The conductor's API port defaults to 8000, which k8stools holds, so SREGym runs
  with `API_PORT=8001`.
- The problem deployed, the fault was injected, and `/status` reported
  `diagnosis` with `agent_remaining_seconds: 1500`. `GET /get_app` returned
  `app_name`, `namespace`, `namespaces`, `descriptions` (the application's
  description, not the fault) and `stages`. Stopping the harness undeployed the
  app and removed its egress policy.
- In filtered mode (the default) the conductor's proxy listens on
  `172.17.0.1:16443`, the docker0 gateway, and writes a **per-problem** agent
  kubeconfig to `/tmp/sregym-agent-kubeconfig-*.yaml`: server
  `https://host.docker.internal:16443`, a CA generated for that run, a token.
  The agent container gets it mounted and nothing else of the cluster.
- A throwaway k8stools container on `k8srca-net`, with
  `--add-host host.docker.internal:host-gateway` and that kubeconfig mounted,
  bound to the proxy and served 33 tools. `get_pod_summaries` for the problem's
  namespace returned its pods. So k8stools works through SREGym's view proxy.
- Through that proxy the cluster view equals the raw cluster's, in names: it hid
  no namespace here. The injected fault, a NetworkPolicy, is visible, as it is
  to any agent with `kubectl`.

**Found while wiring the client (2026-11-08).** Each stopped a run, and each has a
fix in the suite repo or the fork. `docs/setup.md` in the suite repo carries the
symptoms and the steps; this is what they mean for the design.

| Finding | Consequence |
| --- | --- |
| SREGym hides the problem from agents: the artifact id is `anon_<hash>`. | The host names the problem (`/expect`), which is also what makes the hold-out guard enforceable. A suite runs one problem per SREGym process |
| Filtered mode has a hardcoded list of agent names (`provider_endpoints.py`). | A new client needs a one-line change in SREGym's own code, not only `clients/`. For the leaderboard that is an upstream pull request (§9 question 2) |
| Without `SREGYM_AGENT_IMAGE`, SREGym runs its published agent image. | A third-party client needs a locally built image, or to be merged upstream and shipped in the release |
| An empty `KUBECONFIG` (from k8srca's `.env`) breaks SREGym's own kubectl. | The runner drops it. Any process started from k8srca's environment should be checked for this |
| `SREGym-applications` is a submodule, not initialised by a plain clone. | Setup must say so; a run without it fails at deploy |
| k8stools holds port 8000, SREGym's API default. | SREGym runs with `API_PORT=8001` |
| `k8srca up`'s egress probe passes when nothing listens on `gateway:6443`. | "Confined" there means nothing. Apply and check `egress-rules.sh` by hand until the probe is fixed |

**First end-to-end result.** `network_policy_block`, a burned fault, one attempt: the
agent's session took 65 s and $0.14 with 5 tool calls; SREGym's whole run took about
five minutes (deploy 127 s, diagnosis 69 s, evaluation 6 s, cleanup 98 s, undeploy 5 s);
a cheap judge scored it 1.0. The score means nothing, as the fault is burned and the
judge is not the leaderboard's. The timings do: a fault costs about five minutes of
wall clock, so step 2's 140 runs are about twelve hours serial, plus one judge call each.

**The view proxy hides what SREGym hides.** With a `khaos` namespace and a pod labelled
`app=load-generator` created as stand-ins, the raw cluster showed both and k8stools
showed neither, and an ordinary pod stayed visible. This is step 1's done criterion
(b); the stand-ins were deleted.

**A security finding.** The proxy's token answers `can-i create pods` and
`can-i get secrets` with *yes*. The read-only RBAC backstop of 001 §8.4 (layer
four) does not apply behind it. k8stools exposes no mutating tool and no secrets
tool, so the guarantee holds at the layers above, but it is now a property of
k8stools alone. The owner decides whether that is acceptable for SREGym runs, or
whether the SREGym proxy should sit behind an RBAC-restricted credential of ours.

**Proposed (not yet built or shown)** — the `claudecode_radar` precedent:
- **A persistent view proxy of our own,** the same `KubernetesAPIProxy` class,
  bound to the `k8srca-net` gateway on a fixed port with a fixed kubeconfig path.
  One long-lived k8stools container then mounts it. The conductor's per-problem
  kubeconfig changes its CA every run, which would need a k8stools restart per
  problem. To check: that the persistent instance applies the same filtering as
  the conductor's.
- **A host-side service** (this package) holds `ANTHROPIC_API_KEY`, the poller
  and k8stools, and runs `run_session` when asked.
- **A thin shim in the fork** (`clients/k8srca/`), registered `cluster_access:
  false` and run in the container, polls `/status`, `GET /get_app`s, asks the
  host service for a diagnosis and `POST /submit`s it. Filtered mode lets it reach
  the host service with `--allow-agent-endpoint http://host.docker.internal:<port>`.
  The container holds no cluster credential and no API key. This stays
  container-isolated and filtered, as the leaderboard presumably wants.

## 5. The instrument and what is frozen

Under process.md §5, fixed when step 3's reading begins and unchanged until it is
written:

- the SREGym fork's commit, the cohort id (`SREGym-Lite-1004`), the profile
  (`full`), the SREGym grader model (`anthropic/claude-sonnet-5-5`, 2026-11-08) and
  `--judge-backend api`, `--stages diagnosis`;
- the three wikis' digests and their source commits (step 2a.1);
- the 14-fault list, the number of attempts, and the interleaving order;
- arm A's commit (`holdout-v2`) and arm B's commit, tagged before the first run;
- the driver, the question it builds, and the k8stools and promtools versions;
- the decision rule (§7, step 3).

Every submitted answer is saved verbatim with the run, so that a change in
SREGym's grader (an external model, which can be retired or revised) can be
scored again on the same answers. A grader that moves mid-design is the
"instrument moved" tripwire (process.md §2 item 4).

All of it is recorded in `docs/journal/` per run: SREGym commit, cohort, profile,
grader, both arms' commits and image digests.

## 6. Where SREGym is not a clean hold-out

Said now, so nobody discovers it later:

- **Three of the 17 faults are on the OTel demo.** 006 §3 wanted a different
  application. Our wiki pipeline and prompts are shaped by it, so these three
  test the faults but not the application. The hold-out set keeps two of them
  (the burned set takes one, §7 step 1), and the reading reports them
  separately. Lite-IA (§3.1) is the answer to this for the later hold-out.
- **SREGym is public.** Its problems, and other teams' tuning on them, are on
  the web. Our agent's skills must contain no SREGym problem knowledge. A test
  scans the shipped skills for the fault names.
- **We chose nothing blind.** The authors of the suite are not us, which is the
  property that matters. They are also not a colleague who reports scores only,
  so §3's two rules stand in for that.

## 7. Steps

### Step 1 — the driver on the burned set (mechanics only)

| | |
| --- | --- |
| **Hypothesis** | A read-only k8srca session, through k8stools on SREGym's view proxy, can take a live SREGym problem, produce a diagnosis, and have SREGym grade it, with the harness upstream and unmodified apart from one client |
| **Expected effect** | A run completes end to end. No claim about a score |
| **Can we see it?** | Yes: mechanics are binary. Nothing here is measured against noise |
| **Burned set** | Confirmed by the owner, 2026-11-07: `network_policy_block`, `env_variable_shadowing_astronomy_shop`, `service_wrong_pod_selection_hotel_reservation` — one per application family, so the wiring is exercised on each. Chosen before any run, for mechanics and not for difficulty |
| **Budget** | Seven calendar days. $50 for sessions, and SREGym's judge at whatever SREGym's grader costs per diagnosis |
| **Done** | (a) each burned fault run twice, diagnosis only, graded by SREGym, the answer saved; (b) the view-proxy test passes (§4.2); (c) the isolation and arms questions (§9 questions 2, 3) are answered; (d) wall clock and dollars per run are measured, which sets step 3's budget; (e) a hermetic test covers the driver's logic, and the shim carries none |
| **Stop** | The isolation question cannot be answered both ways; a run needs more than two hours of wall clock; the budget is spent |

Also in this step: add the hold-out discipline to the code, not to anyone's
memory. `k8srca-sregym` refuses to print a run's answers or grader notes for a
hold-out fault until a reading file exists, and prints scores only.

### Step 2a — the baseline reading: arm A alone, with a wiki for each application

Added 2026-11-08 at the owner's direction: build the baseline now, do the
decision-model work independently (on another machine, on the existing scenarios),
and decide where to run arm B when that work is done. This splits step 3's
"both arms in one sitting"; §7 step 3 says what that costs and what replaces it.
It also answers §9 question 5 as "build the wikis": the agent gets the architecture
skill for **the application under test and no other**, as an SRE's deployment would.

| | |
| --- | --- |
| **Hypothesis** | There is none against a number. This step produces arm A's reading on the 14 hold-out faults, for step 3 to compare arm B with. The agent is `holdout-v2` plus a per-application wiki, so it is **not** the agent `docs/baseline.md` measured (that one had a pinned skill for a different cluster) |
| **Expected effect** | None claimed. A guess, flagged as one: the wiki helps most on faults that need the application's dependency graph (a datastore, a queue) and not on ones visible in a single object. Nothing here tests it, because there is no arm without the wiki |
| **Can we see it?** | Not a comparison yet. The unit of evidence for step 3 is the fault (see below), so this step records, per fault, passes of 5. With n=5 a fault reads 0–5; the spread across attempts is the within-arm noise that step 3's "moved" needs |
| **Judge** | `anthropic/claude-sonnet-5-5` through `--judge-backend api` (owner, 2026-11-08). SREGym fixes none: with no `--judge-model` it reuses the agent's own model. Leaderboard rules on the judge were not found (§9 question 2), so these scores are a proxy until step 6. Answers are saved verbatim, so a different judge can score them again |
| **Budget** | Wikis: about $3 to build (three at about $0.90) plus review. Runs: 14 faults × 5 attempts = 70 runs. Step 1 measured $0.37 and 7.1 minutes a run (tail $1.11, 469 s), so about $26 and 8.3 hours serial, **but with an agent that had no system prompt and no skills (§10), so treat it as a floor**: the real agent reads the knowledge base and a wiki and may review and revise its answer, and the tail can triple it. Ceiling: $100 in sessions and four calendar days, reset after 2a.3 measures a run with a wiki. The judge call is small beside that |

**2a.1 — Build the wikis.** One per application: hotel reservation, social
network, astronomy shop.

- Sources: what SREGym deploys, read from the checkout and not from a cluster.
  Astronomy shop and social network install from local Helm charts in
  `SREGym-applications`, with SREGym's values files (and, for astronomy shop, its
  `--set prometheus.enabled=false`). **Hotel reservation does not use Helm**: SREGym
  applies the manifests in `hotelReservation/kubernetes` (its tree also holds a chart that
  is not used). Its per-fault environment overrides are applied at deploy time and
  are not in those manifests, which is why they are the right source.
  **Not** `live_cluster` (that describes the faulted state, §4.3) and **not**
  `change_history`. Nothing from SREGym's fault generators or problem files.
- Needed a local-chart source in kubewiki (`chart_repo` downloaded from a
  repo): added 2026-11-08 (`HelmChartRef.local`, `values` as a list, `sets`), cached by
  the chart's contents. It runs `helm template` under CLAUDE.md's existing exception: offline,
  `KUBECONFIG=/dev/null`. The change goes on this branch, not `main`, so arm A's
  code stays `holdout-v2`.
- Built wikis are **inputs, not code**. They are kept in the suite repo with their
  source commits and digests, so arm A syncs them as local skills and arm B can use
  the same bytes.
- Done: `kubewiki check` passes on each; a hermetic test scans every built skill
  for the 17 Lite fault names and fails on a hit (§6); digests recorded.
- **Built 2026-11-08.** All three render offline and pass `kubewiki check`,
  synthesised with `claude-opus-5` (about $0.90 each; five builds in all, because
  the first three had every component in namespace `default`, which kubewiki now
  fixes for unqualified manifests). The scan found no fault name. Digests and source
  commits are in the suite repo's `wikis/provenance.json`. Components and edges, as
  built: astronomy shop 27 with 47 edges; **hotel reservation 20 and social network
  27, with none.** Their call graphs live in config files baked into the images
  (hotel's `config.json`, social network's `service-config.json`), not in the
  manifests, so a wiki from manifests alone has components, kinds and what each
  is for, but no dependency edges. Owner, 2026-11-08: accept it, and the reading
  says which wikis have edges. Also noted: the astronomy-shop wiki describes
  `load-generator`, which SREGym hides from the agent's view.
- **Future enhancement, general:** build-time edges *observed* on the healthy
  deployment from its tracing backend (all three applications deploy Jaeger),
  as a new source type with provenance `observed`. It needs a healthy deployment
  before injection, which the harness does not give us today. Smaller options: read
  any mounted config that names a Service (today only the OpenTelemetry collector's
  shape is read; DeathStarBench's shared config would over-connect), or a `docs`
  source describing the call graph. Not in this design.
- Stop: a chart cannot be rendered offline after a day's work on it. Then the
  owner decides between no wiki for that application, flagged in the reading, and
  a different source.

**2a.2 — One agent pair per application.** A skill belongs to an agent, not a
session, so each application gets its own coordinator and investigator, synced
with that application's wiki.

- The service picks the pair from the application named in `app_info`. The
  question the driver builds is unchanged (frozen in §5). An application with no
  wiki gets a pair with none, and the run record says `wiki: none`, so the reading
  can show it.
- State is one directory per arm and application, which the arms question (§9
  question 3) needed anyway. **One environment and one poller still serve all of
  arm A**: the pairs differ in agents, not in sandbox image.
- Done: the service test covers the routing, including an unknown application; the
  suite's tests still pass; no hold-out content is read.
- **Built 2026-11-08.** `pairs.py` in the suite repo; state under
  `.state/arms/<arm>/<pair>/`; `up` syncs the `none` pair and one per application
  with a wiki; `diagnose` picks the pair from the namespace in `app_info` and records
  the pair and the wiki's digest in the run. Checked on the platform: each of the four
  coordinators has the 3,830-character system prompt and `k8s-rca`, and the three
  application pairs also carry their own wiki and no other. The derived config now
  fails on a missing prompt or unbuilt skill, and `sync` fails if it reports skills
  skipped. The agents step 1 used are set aside (`.state/old-bare-agents/`) and not
  deleted. 44 hermetic tests.

**2a.3 — Mechanics check on the burned set.** One run of the burned
astronomy-shop and hotel-reservation faults, and one of the third burned fault,
each with its application's wiki. Done when each session shows its own wiki read
and no other's, and when a run's dollars with a wiki are measured. No claim about
scores. This resets the ceiling above. If the wiki makes a run cost more than
twice step 1's mean, that is a checkpoint, not a reason to continue.

*Done 2026-11-08, on the second attempt.* The first attempt (three runs, $0.90)
read no cluster: the view proxy had been restarted, which regenerated its CA, and
the running k8stools container still held the old one, so every read failed
with `CERTIFICATE_VERIFY_FAILED`. The agent said so and the runs were recorded as
ordinary answers. Restarting k8stools fixed it. The cause was inferred from the
matching CA files and the fix confirmed by a live read; it was not otherwise
traced. Second attempt, real investigations: astronomy-shop $0.18 (92 s),
hotel-reservation `service_wrong_pod_selection` $0.57 (259 s),
hotel-reservation `network_policy_block` $0.31 (166 s); mean $0.35, against
step 1's $0.37 (no agent prompt, no skills, so the two are not like for like).
The largest is 1.5 times step 1's mean, under the 2x tripwire. Each session
read its own application's `cluster-architecture/SKILL.md`, and no session's events
name another application. Three runs is a mechanics check, not a cost estimate:
the 2a.4 budget keeps its floor. **Added from it: a preflight** (suite
`preflight.py`): a real namespace read through k8stools before a session, and an
answer in which the agent says it could not reach the cluster is invalid, not a
result. Phrases are limited to the agent's own access, so a finding that quotes
"connection refused" from application logs is not flagged. Without it, a hold-out
run would have scored the plumbing.

**2a.4 — The run.** Arm A, 14 faults, n=5, in the order of §5's frozen list,
diagnosis only. Frozen first, and unchanged until the score table is written
(§5, with these added): the wiki digests; the judge above; the SREGym fork commit.

- **Sealed.** The score table goes in `baseline-scores.md` in the suite repo.
  **Not `reading.md`**: the suite's guard opens a hold-out fault's answers and
  grader notes once `reading.md` exists, and the hold-out must stay sealed until
  arm B has run too, or whoever reads arm A's failures can tune toward them.
  `reading.md` is written after step 3's comparison.
- The table: per fault, passes of 5, SREGym composite mean, dollars, wall clock,
  the wiki used (or none); the three astronomy-shop faults on their own line.
- The decision-model branch does not see this table until it is complete (it
  runs elsewhere).
- Stop: a frozen item changes; the budget is spent; a run needs more than two
  hours; cluster resets fail on more than two faults; the judge model is retired
  mid-step.

### Step 2 — pre-register the comparison (no runs)

Gate: 011 step 1 has ended, and 011 step 2's flag (the judge service, `by: judge`
facts, the egress rule) is built on a branch. This step writes down, and tags:

- **Arms.** A: `holdout-v2`. B: that commit plus 011 step 2, with `judge.enabled`
  on. The only differences are the decision-model facts.
- **Faults.** The 14 not in the burned set, listed in this file.
- **n.** Five attempts per fault per arm, interleaved fault by fault so that
  cluster drift and API-time effects fall on both arms.
- **Can we see it?** (below, and honestly)
- **Budget.** From step 1's measure of a run. A guess made now: 14 faults × 5 ×
  2 arms = 140 runs; at $0.5–1.5 a run and 10–20 minutes of wall clock each,
  $70–210 and one to three days serial, plus the SREGym grader. Replace this with
  the measured figures.
- **The decision rule**, below.

**Can we see it?** The unit of evidence is the fault, not the run. SREGym's own
Jev pipeline passed 16 of 21 faults in all five attempts and failed 5 of 21 in
all five **[web]**, so attempts within a fault are strongly correlated and 70
attempts per arm behave like fewer than 14 independent ones. With the same faults
in both arms, only faults that move count. A sign test on moved faults needs
five faults moving one way and none the other to reach p ≈ 0.03 (4 and 0 gives
0.06). So the comparison reads as a result only if at least five of 14 faults
(about 35%) improve with none worse. Anything smaller is **"can't tell"** and is
reported as that.

The expected effect of 011's text-read facts on this suite is not established. I
expect few of the 14 faults to hinge on a log reading, since most Lite faults are
visible in objects and events, but that is a guess made without reading the
faults' definitions, and §3 rules that out. That is why §9 question 1 comes
before this step.

**Decision rule, set before the run.** B is *not worse* if no more than one fault
moves down; B is *better* only by the five-and-none test above; otherwise "can't
tell", and the decision-model version is decided on its offline results (011 step
1) and on judgment, said to be.

### Step 3 — the one run, and the reading

Arm B on the 14 faults, n=5, diagnosis only, under §5's frozen list, against
arm A's reading from step 2a. **This is not the interleaved design this step was
first written as** (both arms in one sitting, 004's way of settling a lean): arm
A ran earlier, so cluster and API-time drift no longer fall on both arms. Two
rules replace it:

- Arm B uses arm A's frozen inputs byte for byte: the wiki digests, the judge, the
  SREGym fork commit, the driver and its question. A change to any of them is the
  "instrument moved" tripwire, and arm A is rerun.
- **A drift canary.** In the same sitting as arm B, arm A runs again on three
  faults fixed now, one per application: `cronjob_sidecar_blocks_completion_hotel_reservation`,
  `mutating_webhook_resource_limits_social_network`,
  `internal_traffic_policy_local_astronomy_shop`, n=5 each (15 runs, about
  $6 and 1.8 hours). If arm A's passes on those differ from step 2a's by two or
  more on any fault, the comparison reads "can't tell" whatever the table shows.
  A smaller difference is reported next to the result.

The reading says plainly that the arms were not interleaved. That weakens a
"better" and leaves "can't tell" as it is. Where arm B runs is decided when the
decision-model work is done (owner, 2026-11-08); it needs its own environment
only if it runs beside arm A on this machine (§9 question 3). No answer,
transcript or grader note is opened until the score table is written here. Then:

- the table: per fault, A and B passes of 5, SREGym composite mean, dollars,
  wall clock;
- the three OTel-demo faults reported on their own line;
- the decision under the step 2 rule, the number of faults that moved each way,
  and a checkpoint (process.md §3) if two arms in a row are "can't tell";
- **then** the 14 faults are burned. That is recorded on the status line.

**Stop:** a frozen item changes; the budget is spent; an arm cannot be run
cleanly (cluster resets failing on more than two faults).

### Steps 4–6 — outlines

- **Step 4: the successor hold-out.** Two parts, both untouched until step 5 has
  tuned on Lite. **Lite-IA** is the application hold-out (§3.1), run by the same
  driver, one use, with step 2's pre-registration and step 3's rules. A suite of
  *fresh faults* is the other part, wanted if the owner wants a fault hold-out,
  authored and run by someone who is not on the agent (006's design, revised: a
  record of how its "scores only" worked in practice). Its own plan, started
  during step 3's wait.
- **Step 5: SREGym as a tuning target.** Planned when step 3 ends. Offline first
  (process.md §6): live runs record captures (`k8s-capture-state`), which become
  scenarios, and most changes are settled on those. Live n=2–3 at milestones only.
  Live exam results never become the grader or truth of a scenario (004 §12: we
  import captures, never scoring).
- **Step 6: the leaderboard.** Gated on two answers (§9 questions 2 and 4). The
  board ranks on E2E, which needs mitigation. **Mitigation is deferred (owner,
  2026-11-07)**, so step 6 is planned only if SREGym accepts and shows a
  diagnosis-only entry (the board has a Diag. (%) column). Otherwise it waits for
  a separate design with its own security decision (001 §8.1). Also needed: the
  `full` profile at the recommended 16 vCPU and 32 GB, the leaderboard's attempt
  count, and a fresh run on the final agent, not a tuning run's result.

## 8. Configuration to add (when step 1 starts)

```bash
# SREGym (design 014). A separate environment from the scenario one (§4.3).
K8SRCA_SREGYM_ENVIRONMENT_ID=
ANTHROPIC_SREGYM_ENVIRONMENT_KEY=
K8SRCA_SREGYM_DIR=/home/jfischer/code/SREGym     # the fork; its commit is recorded per run
K8SRCA_SREGYM_SUITE=                             # the private suite repo (§4.0), outside this repo
```

Also: the view proxy's kubeconfig as the one that `k8srca up` gives k8stools in
SREGym mode (CLAUDE.md: the credential lives in `k8srca.yaml`'s
`cluster_access.kubeconfig`, not in `.env`). `SCRUB` in `settings.py` gains the
new key. SREGym's grader credentials are set where SREGym runs, not here.

## 9. Open questions

1. **What is "the decision-model version"?** (owner, before step 2.) 011 as
   written adds *text-read facts* only, and rejects a decision model choosing or
   ranking the cause (011 §11). SREGym's own results [web] come from a decision
   model that chooses the cause with an LLM reviewing it. Options: (a) 011 as
   written, which is what is planned here, with the comparison a regression
   guard and a lean, most likely "can't tell"; (b) change 011 to the
   chooser-plus-reviewer role, for which a larger effect is plausible, which
   reverses a rejected alternative and needs its own design; (c) both as arms.
   Recommended: (a), because (b) is a new design and this one is about SREGym.
   Steps 1 and 4 do not depend on the answer.
2. **Does the leaderboard accept a diagnosis-only entry?** Unknown. The site and
   README do not say how third parties submit. Ask the SREGym team (their table
   says third-party rows are verified by them) before step 6's plan. The
   isolation half is answered by §4.5's proposal, pending step 1: a
   container-isolated shim with `cluster_access: false` and a host-side service.
3. **Can two arms share one environment?** Answered from the code and from what step 1
   showed, not by running a second arm: **not as built.** `sync` updates an agent in
   place (`v1 -> v2` on the same agent id, seen in step 1) and a session takes the
   version from the state file, so two arms sharing `.state/state.json` would run
   whichever arm synced last. And a poller claims any work item in its environment and
   spawns *its own* sandbox image, so two pollers on one environment would serve one
   arm's session with the other's image. Step 2 needs a state directory per arm and
   either **one environment per arm** (recommended: the owner generates one more key) or
   one poller at a time, started and stopped around each run. Per-arm state is not built.
   Revised 2026-11-08 (owner): arm B will be developed independently and may run on
   another machine, so a second environment is **deferred** until that is decided.
   Step 2a runs arm A alone, in one environment, with per-application state under it.
4. **Tuning rules.** None found. SREGym's own blog tunes on the benchmark openly,
   so the norm looks permissive, but ask in the same message as question 2.
5. **What architecture skill does the agent get?** Decided 2026-11-08 (owner): build
   per-application wikis, and give the agent the wiki for the application under test
   only (step 2a). Step 1 ran with **none**: the agents were synced without a wiki,
   so the agent was not the one `docs/baseline.md` measured, which has a pinned
   cluster-architecture skill. The faulted-state question (§4.3) is settled by
   building from chart and values, not from the live cluster. Both arms use the same
   wiki bytes, but neither is `docs/baseline.md`'s agent.
6. **Who runs the successor hold-out,** and does the colleague of 006 still want
   to? The owner's call; step 4.
7. **The baseline reading.** Decided 2026-11-08: arm A is `holdout-v2`. Its agent
   behaves as `holdout-v1`'s (the changes are host-side, in `runner.py` and the
   poller command), so `docs/baseline.md` still describes it. The driver is outside
   both arms and identical for them.
8. **011 under 014?** Decided 2026-11-07 (owner): both run in parallel. 011
   moves to in progress beside 014, an exception to process.md §4's one design at
   a time. Each keeps its own steps and results table. 014's step 2 gate (011
   steps 1–2 done) is the only coupling, and 014's step 1 does not depend on
   011. Each design's tripwires and checkpoints are its own; the shared budget
   and calendar are the owner's to watch.

---

## 10. Results

| Step | Plan | Outcome | Decision |
| --- | --- | --- | --- |
| 1 | §7: the driver on the burned set, mechanics only, 7 days, $50 | Done 2026-11-08, in one day; $2.21 in agent sessions for the six runs, plus a cheap judge call each. (a) Each burned fault run twice, diagnosis only, graded by SREGym, answers saved: yes, six runs ([journal](../docs/journal/2026-11-08-sregym-step1.md)). (b) The view proxy hides what SREGym hides: yes, checked with stand-ins. (c) Isolation answered: a container-isolated shim with `cluster_access: false` and a host-side service works in filtered mode. Arms answered, from the code (§9 question 3). (d) $0.37 and 7.1 minutes per run on average, with a long tail (§7 step 2's estimate: 140 runs about 16.5 h, $52 plus judge). (e) The driver's logic has 27 hermetic tests in the suite repo; the shim, in the fork, has none of its own and holds only glue | Step 1 closed as done. Open before step 2: §9 question 1 (questions 3 and 5 settled 2026-11-08, §9). **Corrected 2026-11-08, found in step 2a.2:** the six runs used agents with **no system prompt and no skills**, not k8srca's agent. The derived config sat in `.state/`, k8srca resolves an agent's prompt and skill paths against the config file's directory, and they pointed at nothing; sync said nothing. The mechanics claims (a–c, e) stand. Everything measured about the agent does not: the scores, the $0.37 and 7.1 minutes a run, and "the agent ran with no architecture skill" (it also lacked `k8s-rca` and its own prompt). Fixed in `pairs.derive`, which makes the paths absolute and fails when one is missing, with a test that loads the derived config from outside the checkout |
| 2a | §7: wikis, per-application agents, a burned-set check, then arm A's 70 runs; $100 ceiling, four days | 2a.1–2a.3 done 2026-11-08; 2a.4 (the 70 runs) not started | 2a.3: mean $0.35 a run with a wiki, each session read only its own wiki; the first attempt found a stale-CA fault and led to the preflight. Ceiling reset to the 2a.4 budget |

### 10.1 Findings from arm A's first baseline runs (2026-11-09)

Updated 2026-11-10, all fourteen faults done: 22 passes in 70 attempts (31%), $40.12 in
agent sessions plus the judge; the table is `baseline-scores.md` in the suite repo.
Scores: `cronjob_sidecar` 0/5, `mutating_webhook` 0/5, `finalizer_deadlock` 3/5, edge 2/5,
`kafka_poison_pill` 3/5, `internal_traffic_policy_local` 0/5, `service_dns_resolution` 1/5,
`namespace_memory_limit` 5/5, `valkey_auth_disruption` 0/5,
`secret_rotation_stale_env_credentials` 2/5, `unschedulable_incorrect_port` 2/5,
`duplicate_pvc_mounts` 3/5, `rolling_update_misconfigured` 1/5,
`search_rate_retry_collapse` 0/5. One fault passes 5 of 5, five pass 0 of 5. These are read-outs from saved
answers, not rules: process.md's tripwire on a rule written from one run applies, and the
baseline is frozen. Each item below is a thing to decide at step 2a's checkpoint.

1. **`edge_request_filter_cpu_saturation` could not be set up here.** SREGym's own
   `_verify_fault_effect` needs a normal request to time out within 3 s. The crafted
   payload (5000 characters) costs about 1.5 s of regex time at the pod's 100m CPU limit on
   this host (fast, 16 vCPU), and the single serial driver puts only one crafted request
   ahead of the probe, so it never fires: 3 deploy retries, twice, 0 attempts. Measured
   with the image's own Perl (5.34), `docker --cpus 0.1`: 5000 characters about 1.5 s,
   10000 about 6 s, 20000 about 24 s (quadratic). **The fork's payload is now 10000
   characters** (SREGym fork commit `05b2f534`, on `80898c1b`). Consequences: the frozen
   cohort's `sregym` commit no longer matches the fork's head; edge's score is for a harder
   fault than the leaderboard's, so it is reported beside the cohort, not inside a
   comparison with other people's numbers; the mitigation oracle also sends this payload
   (diagnosis only here, so it does not matter yet). **Run live 2026-11-09:** the fault's own
   check passed ("the normal request times out"), then 2/5, composite 0.4, $2.49, 37 min.
   The 10000-character fault is the one scored. The suite's frozen check (`baseline.py`
   `verify`) now accepts that one fork commit through an `allowed_sregym_patches` entry in
   `baseline-frozen.json` (owner's choice, 2026-11-09) and still stops on any other change.
2. **`failure-admin-{geo,rate}` are deliberate decoys in hotel-reservation.** SREGym creates
   the two ConfigMaps (scripts that drop or revoke the Mongo admin user) and patches
   `mongodb-geo` and `mongodb-rate` to mount them at `/scripts`
   (`hotel_reservation.py`: `populate_failure_configmaps`, `_patch_mongo_failure_script_mounts`),
   documented there as "noise/red herrings for unrelated faults". It is not the view proxy
   leaking benchmark internals. It also gives those two deployments a pod-template
   revision 2 for every hotel-reservation fault. In `cronjob_sidecar` (0/5, composite 0.0),
   four of five answers made it the main finding ("staged, dormant") and the fifth blamed a
   one-off DNS failure; all five read the real fault, `audit-log-archiver`'s sidecar that
   never exits so the Job cannot complete, as "completed Jobs that exited 0, normal". What
   to weigh: the agent's reading of the decoy was accurate (nothing runs the scripts) and
   still wrong as a conclusion, since the namespace showed nothing broken and the answer
   put the most alarming object first. The signal it missed was Jobs that never reach
   Complete while a sidecar runs.
3. **k8stools 4.6.1 has no tool that lists webhook configurations.** Nothing in the
   package mentions `MutatingWebhookConfiguration`, `ValidatingWebhookConfiguration` or
   `admissionregistration`; it has `get_custom_resource_definitions` and
   `get_custom_resource_status` but not these, and the sandbox has no `kubectl` (001 §8.2).
   In `mutating_webhook` (0/5 then 0.4 on the rerun), the cause is a cluster-scoped
   mutating webhook rewriting memory to 16Mi (with four inert decoy webhooks beside it).
   Every run found the OOM-killed `nginx-thrift`; four noticed the Deployment's revision
   says 128Mi/256Mi while the pod has 16Mi, which is the admission-time-rewrite signature;
   one named "LimitRange… possibly a mutating webhook". None could list the webhooks. So
   this fault is out of reach by tool surface, not by reasoning. **Per CLAUDE.md's rule, the
   fix is a k8stools tool** (read-only list of mutating and validating webhook
   configurations, with rules and selectors; filed as BenedatLLC/k8stools#39,
   2026-11-09), not a shortcut in k8srca. It changes the
   agent after the baseline is frozen, so it is a step 2a checkpoint decision: add it and
   re-baseline the affected faults, or leave it out and report the gap beside the score.
4. **`internal_traffic_policy_local` (0/5, composite mean 0.4): k8stools does not show
   a Service's traffic policy.** The cause is `spec.internalTrafficPolicy: Local` on the
   single-replica `recommendation` Service, so a `frontend` pod on another node has its
   connections silently dropped by kube-proxy. Four of the five answers found the
   symptom path (`frontend` timing out to `recommendation`, one Ready pod behind a Ready
   endpoint, nothing wrong with the pod); three noticed the nodeSelector pins that put the
   caller and callee on different nodes; one blamed a stale conntrack entry on a node,
   which is a kube-proxy-level guess and the nearest miss. None looked at the Service's
   traffic policy, and could not: k8stools 4.6.1's `ServiceSummary` has `type`, IPs, ports,
   selector, labels, annotations and endpoint counts, but no `internalTrafficPolicy`,
   `externalTrafficPolicy` or `sessionAffinity`, and no tool returns the raw Service. Two
   more answers called a hung `recommendation` process the cause (no probes, "listening"
   then silence), which fits the data and is wrong. One answer said the `load-generator`
   Deployment was missing, with every other workload healthy; it is unexplained here, and
   worth a look at whether that namespace really has no `load-generator` or the view hides it.
   Same shape as item 3: a gap in the tool surface, so the fix is in k8stools (add the three
   fields to the Service summary, and let the workload report note a Service whose policy is
   not the default; filed as BenedatLLC/k8stools#40, 2026-11-09), and the same checkpoint
   decision applies.
5. **`valkey_auth_disruption` (0/5, composite mean 0.4): the fault is in a process's
   runtime state, which no read-only Kubernetes tool can show.** Valkey was reconfigured
   with `CONFIG SET requirepass`, so `cart`'s unauthenticated connections get NOAUTH. All
   five answers found `cart` crash-looping on "Wasn't able to connect to redis" (exit 139)
   and `valkey-cart` healthy, Ready, with an endpoint, the init container's TCP check passing
   and no NetworkPolicy in the way, and all five concluded that no auth is needed, because
   the specs set no password. Four called it a client or protocol mismatch inside `cart`;
   one guessed Valkey's protected mode. The password is in the running server, not in any
   object: no spec change, no event. Seeing it takes the server's own config (an exec),
   which the read-only tools do not allow, so no k8stools ticket: it is out of reach by
   design (001 §8.1). Not checked: whether Valkey's log has a line for the CONFIG SET, which
   would be the nearest evidence. Faults of this kind should be counted as unreachable,
   not as the agent's reasoning failing.
6. **Narration leaks into the saved answer** (BenedatLLC/k8srca#15, to be fixed after this
   work, not during it). The scenario runner takes every message of a turn, joined, as the
   answer (`scenario/runner.py:517`, `:485`, `:551`); the Slack path takes the last one
   (`slack/relay.py`). Of 60 baseline records, 43 `first_answer`s begin with narration, and
   3 final answers begin with preamble: `valkey_auth_disruption`'s last attempt ("Good —
   now at 299 words… Here is the revised answer:", then `---`) and one `finalizer_deadlock`
   attempt. All 60 had a revision turn (the 300-word limit fired on every first answer, a
   real overshoot that does not depend on the narration), so the final answer is the
   revision turn's messages, and a revision turn that runs a tool and narrates puts the
   narration before the answer. The judge reads it. The effect on a score is not measured;
   the saved answers can be stripped to their last message and re-graded to find out, with no
   new runs. Until then the baseline's answers carry this, and the comparison's must too.
7. **The rest, read 2026-11-10 against SREGym's root-cause text.**
   - `unschedulable_incorrect_port_assignment` (2/5, mean 0.7) has two faults: a wrong port
     in `PRODUCT_CATALOG_ADDR`, and a nodeSelector pinning `checkout` to a node that does
     not exist. All five found the nodeSelector. All five also saw that `PRODUCT_CATALOG_ADDR`
     changed in the latest revision; one called it "a second issue" (the nearest), three
     called it unrelated or something to verify later, and none compared the value with
     product-catalog's port. The facts were in hand: this is reasoning (follow the second
     thread), not tool surface.
   - `duplicate_pvc_mounts` (3/5): four of five found jaeger's second replica Unschedulable
     on anti-affinity plus a PVC node-affinity conflict, which is the cause; one blamed a
     DNS flood from an OpenTelemetry exporter (wrong).
   - `rolling_update_misconfigured` (1/5, mean 0.7): all five found the hung `hang-init`
     init container. None mentioned the rollout strategy (`maxUnavailable=100%`,
     `maxSurge=0`), which the truth names as the reason all old replicas drained. k8stools
     4.6.1's `DeploymentSummary` has no strategy fields and nothing in `k8s_tools.py`
     reads them, so this half is out of reach by tool surface: a third k8stools gap, with
     items 3 and 4 (filed as BenedatLLC/k8stools#41, 2026-11-11).
   - `search_rate_retry_collapse` (0/5, mean 0.3): four of five blamed the `rate` service
     as hung; one blamed a startup race. Only one answer mentioned retries at all. The
     truth is a timeout, retry and queue feedback loop in `search`'s RPC policy. Whether the
     retry policy is readable (it may be in the application's configuration) was not checked.
   - `secret_rotation_stale_env_credentials` (2/5, mean 0.5): four of five found Postgres
     rejecting `otelu` (authentication failures) and a credential mismatch, which is the
     symptom and half the cause; the missing half is that product-catalog keeps the
     connection string captured at startup. One answer instead said the `load-generator`
     Deployment is missing.
   - **"`load-generator` is missing" is the view and the wiki disagreeing (found
     2026-11-11).** SREGym hides every object labelled `app=load-generator` (and
     `locust-fetcher`, `job=workload`, `network-access=restricted`,
     `opentelemetry.io/name=load-generator`) from the agent, on purpose
     (`sregym/service/agent_visibility_policy.py`, `HIDDEN_LABELS`, applied by the view
     proxy). The astronomy-shop wiki was built from the chart and declares `load-generator`
     as the sole traffic source (`index.md`, `graph.json`, `frontend-proxy.md`). So an
     agent reads that the Deployment exists, looks, finds nothing, and concludes it was
     never created. Four of 31 astronomy-shop answers (13%) made that the most likely cause,
     each a wrong answer: edge 1 of 5, `kafka_poison_pill` 1, `internal_traffic_policy` 1,
     `secret_rotation` 1. The edge fault's driver uses `load-generator` through SREGym's own
     client, which the policy does not touch, which is why SREGym finds it there. (My
     2026-11-10 note that edge "finds it present" mixed the two views.) It is common to both
     arms (the same wiki bytes, §9 question 5), so it does not bias the comparison, but it
     lowers the baseline's absolute scores on this app and is a flaw in the instrument.
     **Checkpoint decision:** (a) leave it, and report it beside the score; (b) make the
     wiki builder honor the visibility policy, so components the agent cannot see are not
     in the wiki. (b) changes the frozen `wiki_digests` for astronomy-shop and means
     re-running that app's six faults (30 runs, about $18). It also belongs in kubewiki as a
     general input (a list of hidden label selectors), not as a SREGym special case;
     filed as BenedatLLC/k8srca#16 for after the hold-out work. The score table now carries
     this as a note (`baseline-notes.md` in the suite repo).
   - **Pattern across the fourteen:** the zeros are mostly not reasoning failures. Of the
     five at 0/5, three are out of reach (webhooks, runtime state, traffic policy); the
     others (`cronjob_sidecar`, `search_rate_retry_collapse`) are where the agent read the
     most alarming object, not the cause. That bears on §9 question 1 and on what a
     decision model can be expected to move.
