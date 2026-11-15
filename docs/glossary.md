# Glossary

Terms used across the k8srca documentation, in plain words. Each entry links to the document
that defines the term in full.

## The running system

**k8srca**
: This project. An agent that diagnoses problems in a Kubernetes cluster when someone asks in
  Slack, using read-only tools, and answers with the evidence behind its conclusion.
  [Design 001](../designs/001-architecture.md).

**Claude Managed Agents ("the platform")**
: Anthropic's hosted agent service. It stores agent configurations, runs the agent loop (the
  model calls and the conversation state) and hands tool calls to a runtime we host. Called
  "CMA" in some older notes.

**Session**
: One conversation on Claude Managed Agents. One Slack thread maps to one session, so a
  follow-up question in the thread continues the same investigation.

**Self-hosted environment**
: A Managed Agents environment whose tools run on our infrastructure rather than Anthropic's.
  We need it because the cluster is usually not reachable from the internet.
  [001 §2](../designs/001-architecture.md#2-platform-facts-that-shape-the-design).

**Work item**
: The platform's unit of work for a self-hosted environment. One work item covers one
  continuous active period of a session: it can span several turns, and ends when the session
  has been idle for 60 seconds. [001 §3.2](../designs/001-architecture.md#32-containers-per-work-item-workspace-per-session).

**Coordinator (`rca-coordinator`)**
: The agent that plans the investigation, ranks the candidate causes and writes the answer.
  Runs on Claude Sonnet 5. [001 §3.3](../designs/001-architecture.md#33-agent-topology-a-coordinator-and-a-specialist).

**Specialist (`k8s-investigator`)**
: The agent the coordinator delegates high-volume reading to (logs, sweeps across many pods).
  Runs on the cheaper Claude Haiku 4.5. It runs in the same session as the coordinator but in
  its own thread, with its own context.

**Thread (agent thread)**
: A separate conversation inside one session, used for delegation. Not the same thing as a
  Slack thread.

**Orchestrator**
: `k8srca slack run`, the Slack bot process on our host. It maps Slack threads to sessions,
  relays progress, checks the answer and posts it. Holds the Anthropic API key and the Slack
  tokens. [001 §3.1](../designs/001-architecture.md#31-processes).

**Poller**
: `k8srca poller`, the host process that claims work items from the platform and starts a
  sandbox container for each one. Holds only the environment key, never the API key.

**Sandbox**
: The Docker container that serves one work item. It runs the worker and any shell commands
  the agent writes. It holds no cluster credentials and cannot reach the cluster's API server.
  [001 §7.3](../designs/001-architecture.md#73-sandbox-lifecycle).

**Worker**
: The code inside the sandbox (`src/k8srca/worker/`) that connects to the tool servers and
  serves the agent's tool calls.

**Workspace**
: The directory mounted at `/workspace` in the sandbox. There is one per session, on the host,
  so files survive from one sandbox container to the next.

**MCP (Model Context Protocol)**
: The protocol our tool servers speak. The worker is the MCP client.

**k8stools**
: The read-only Kubernetes MCP server ([BenedatLLC/k8stools](https://github.com/BenedatLLC/k8stools)),
  a separate project. The only component that holds cluster credentials.

**Composite tool**
: A k8stools or promtools tool that assembles one object's whole state in one call, with
  Kubernetes semantics already applied (for example `get_workload_report`), in place of several
  raw lookups.

**Triage tools**
: The small set of k8stools tools the coordinator may call itself (`triage` group in
  `k8srca.yaml`). Everything else goes through the specialist.

**promtools**
: Our read-only Prometheus MCP server (`packages/promtools`). Off by default in production.
  [Design 009](../designs/009-metrics.md).

**Skill**
: A bundle of instructions and files that the platform downloads into the sandbox for an agent.
  k8srca has two: `k8s-rca` and `cluster-architecture`. [001 §5](../designs/001-architecture.md#5-knowledge-delivery-skills).

**`k8s-rca` skill**
: The investigation method (`SKILL.md`), the knowledge base, and `kb_query.py`, the command the
  agent runs to look things up and rank causes.

**`cluster-architecture` skill (the wiki)**
: A description of what is deployed and what calls what, with no live state, built by kubewiki.
  [001 §5.2](../designs/001-architecture.md#52-cluster-architecture-what-is-deployed-and-how-it-fits-together).

**kubewiki**
: The standalone package (`packages/kubewiki`) that builds the cluster-architecture wiki from
  the live cluster, the official Helm chart and the official docs.
  [kubewiki design](../packages/kubewiki/docs/design.md).

**Host checks**
: Checks the orchestrator runs on the agent's answer before posting it: every fact marker
  resolves, the prose is at most 300 words, and every number is in a cited fact
  (`src/k8srca/review.py`). [001 §7.2](../designs/001-architecture.md#72-a-turn).

**Read-only guarantee**
: The four independent controls that keep the agent from changing the cluster.
  [001 §8](../designs/001-architecture.md#8-read-only-guarantee-and-security).

**Evidence record**
: Every tool result, saved in the session's workspace as `evidence/<session>/<n>-<tool>.json`
  before the agent sees it, so scripts and the orchestrator can read it later
  ([src/k8srca/evidence.py](../src/k8srca/evidence.py)). Each result starts with its number,
  `result <n>`.

**Facts log**
: `evidence/facts.jsonl` in the workspace: every fact stated so far in a session.

**Revision turn**
: One extra message the orchestrator sends to the session when the host checks find a problem,
  asking for the whole answer again. The revised answer is the one posted.

**Evidence block**
: The numbered notes the orchestrator appends under an answer, printing each cited fact and where
  it came from.

**Tool-manifest hash**
: A hash of the tools an agent declares, stored at `sync` time. The sandbox refuses a work item if
  the live tools no longer match. [001 §4.3](../designs/001-architecture.md#43-keeping-declarations-and-the-live-tool-list-in-step).

## How the agent reasons

**Facts layer**
: Code (`src/k8srca/facts/`) that reads each tool result and states facts from it, such as
  "the last 3 terminations were OOMKilled", with their scope.
  [Design 010 §3](../designs/010-facts-and-short-answers.md).

**Fact marker (`[F<n>]`)**
: The numbered reference to a fact that the agent writes in its answer. The orchestrator turns
  markers into numbered notes and prints the cited facts in an Evidence block.

**Scope (of a fact)**
: How much of the system's history a fact covers, such as "events cover the last 10 days" or
  "the last termination only". Kubernetes keeps little history, so a fact says what it covers.
  [Design 008](../designs/008-conclusions-under-incomplete-evidence.md).

**Knowledge base (KB)**
: Symptoms, their candidate causes, the facts that support or rule out each cause, and the
  actions that observe those facts. [Design 007](../designs/007-knowledge-representation.md).

**Symptom**
: What is observed to be wrong, such as `pod-restarting` or `pod-pending`. The entry point into
  the knowledge base.

**Family**
: A group of related symptoms that were authored or mined together (for example, storage).

**Candidate cause / claim**
: A possible cause of a symptom. In the knowledge-base format a candidate cause is called a
  *claim*.

**Action**
: In the knowledge base, a way to observe a fact (usually a tool call).

**Capability / availability**
: Which data sources a deployment has (for example Kubernetes, Prometheus), so the knowledge
  base only suggests checks that can be run there. [007 §4](../designs/007-knowledge-representation.md).

**Vendored alerts**
: The 81 alert records of the original, third-party knowledge base that k8srca started from,
  kept in `vendor/kb/`. 25 of them map to a symptom in the newer format; the rest are returned
  as unranked lists of causes.

**Mined layer**
: Knowledge extracted by a model from public debugging guides (`k8srca kb mine`), reviewed by
  a person, and kept in `docs/rca/mined/`. [007 §8](../designs/007-knowledge-representation.md).

**Ranking**
: Ordering the candidate causes by how well the facts fit them (`kb_query.py open`), in place of
  trying to prove one. [Design 008](../designs/008-conclusions-under-incomplete-evidence.md).

**Strength word**
: The answer's confidence in its leading cause: *very likely*, *likely* or *possible*.

**Ruled out**
: A candidate cause that a fact excludes. A candidate that no fact decided is not "ruled out";
  it appears as a next step.

**Standing**
: Where a candidate cause stands after `kb_query.py open`: supported (with a strength of strong,
  moderate or weak), ruled out, conflicting (facts both support and exclude it, so one is wrong),
  open (nothing decided it yet), or could-not-check (nothing available in this deployment can
  decide it). [007 §6.1](../designs/007-knowledge-representation.md),
  [008](../designs/008-conclusions-under-incomplete-evidence.md).

**`for` / `against` / `rules_out`**
: The three kinds of evidence a candidate cause lists in the knowledge base: facts that support it
  (weighted strong, moderate or weak), facts that count against it, and facts that exclude it.

**Sub-cause (refinement)**
: A more specific kind of a candidate cause, such as `memory-limit-too-low` under `oom-kill`.

**Text-read fact**
: A fact whose truth can only be established by reading text, usually a log, so code cannot state
  it. The agent records these itself with `kb_query.py fact`.
  [Design 011](../designs/011-decision-models.md) proposes that a decision model state them.

**Horizon fact**
: A fact stating how far back a source's record reaches (events, metrics, a log).

**Log span**
: What a log result covers: the lines shown, where the retained log starts, and when the
  container started. Stated as the `log_span` fact. [Design 012](../designs/012-log-evidence.md).

**Legacy path**
: How `kb_query.py` answers one of the vendored alerts not yet converted to the newer format: it
  lists the record's candidate causes without criteria to rank them.

**Completeness check**
: An older host check that the answer placed every candidate cause `open` listed. Off since the
  short answer of design 010.

**Light reviewer**
: An optional model call that checks each sentence of an answer against its cited facts. Built
  and off, because it showed no measurable gain ([010 §9.8](../designs/010-facts-and-short-answers.md)).

**Change fact (planned)**
: A fact stating that an object in the alerting namespace was written recently: what, when, and by
  which client. Design 013 proposes it, from k8stools' write records. Not built.
  [Design 013](../designs/013-what-changed.md).

**Write records (managedFields)**
: The record Kubernetes keeps on each object of which client last wrote which fields, and when.
  It is how "what changed recently" can be read without an audit log. Recordings do not keep it
  yet ([k8stools#38](https://github.com/BenedatLLC/k8stools/issues/38)).

**Authored discriminators**
: Hand-written rules in the knowledge base that tell a symptom's candidate causes apart (which
  facts count for or against each). Symptoms without them get only the candidate list, and `open`
  ranks them weakly. [Design 007](../designs/007-knowledge-representation.md).

## How we measure changes

**Scenario**
: A fault injected into a test cluster on purpose, recorded once and played back, so the agent
  can be run against it repeatedly without a live cluster. Scenarios live in
  `tests/scenarios/<id>/`. [Design 004](../designs/004-scenario-testing.md).

**Capture / replay**
: A capture is the recorded state of a broken cluster (k8stools' state file, and optionally
  Prometheus data). Replay serves that state through k8stools' replay mode, so a run sees the
  same cluster every time. Captures are stored in the separate repository
  `BenedatLLC/k8srca-scenario-data`.

**ITBench**
: A public benchmark of injected IT incidents. Several of our scenarios (`itbench-*`) are
  adapted from it.

**Checklist**
: The statements a good answer to a scenario should make, in `checklist.yaml`. A model grader
  marks each item met or not. [010 §7](../designs/010-facts-and-short-answers.md).

**Trap**
: A checklist item that an answer fails by saying something wrong, such as blaming a decoy.

**Baseline**
: The agent's recorded scores on the scenario suite, against which a change is compared
  ([docs/baseline.md](baseline.md)).

**Hold-out suite**
: Scenarios the team never sees or tunes against, written and run by a colleague who reports
  only scores. [Design 006](../designs/006-holdout-suite.md).

**Component eval**
: A test of one component (such as the architecture generator, or the answer writer given fixed
  facts) without running the whole agent (`k8srca eval`). [Design 005](../designs/005-modular-architecture.md).

**Generator**
: A component that builds a skill's content behind a common interface
  (`src/k8srca/core/generator.py`). [Design 005](../designs/005-modular-architecture.md).

**Step, tripwire, checkpoint**
: From [docs/process.md](process.md). A design is built in steps, each with a planned
  measurement. A tripwire is a condition (such as two inconclusive results in a row) that stops
  work for a checkpoint. A checkpoint ends in a decision: continue, change the plan, or close
  the step.

**Arm**
: One configuration in a comparison experiment (for example, the agent with and without a
  decision model). Documents say what each arm is where they use the term.

**n**
: The number of runs per scenario in a measurement.

**Truth file (`truth.yaml`)**
: A scenario's ground truth: the cause, the candidate causes and where each should stand, the
  traps and gaps, and digests pinning the capture, skills and metrics it was written against.

**Gap**
: Something a scenario's capture cannot show, which a good answer reports as unavailable rather
  than guessing.

**Diagnosis item / diagnosed**
: A checklist item marked `diagnosis: true` (it states the cause). A run is *diagnosed* when it
  meets all of them.

**Rubric grader**
: The older model grader that scored answers on dimensions (cause, evidence, standing, traps,
  gaps, restraint) against the truth file and the capture. Replaced by checklist grading; still
  available with `--rubric`.

**Agreement bar**
: The test that checklist grading is consistent enough to trust: when each answer is graded three
  times, at least 97% of gradings agree with the majority, and no item splits on more than one
  answer.

**Regression rule**
: How a change is judged on the suite: a scenario regresses when fewer runs are diagnosed than in
  the baseline, or its mean checklist score drops by more than the baseline's spread.
  [004 §6.4](../designs/004-scenario-testing.md).

**Deterministic checks**
: Free checks in code run on every scenario answer: names that are not in the capture
  (closed world), restart counts, required statements, required tools, and the call budget.

**benben**
: The project's test cluster, which runs the OpenTelemetry demo and where most scenarios were
  recorded. (Its broken `ad` and `fraud-detection` services are broken on purpose.)

**Life (sampled life)**
: One run of a container from start to termination, as Prometheus saw it.

**SREGym, SREGym-Lite, SREGym-Lite-IA**
: SREGym is a public benchmark that deploys an application on Kubernetes, injects a fault, runs an
  agent, and has a model judge grade the diagnosis. SREGym-Lite is a 17-fault subset; Lite-IA
  ports the same fault families to other applications and is kept for a later hold-out.
  SREGym is a source of hold-out tests: a fault stays unread until a hold-out pass has used it,
  then moves into the regular scenario suite. [Design 014](../designs/014-sregym.md).

**`holdout-v1`, `holdout-v2`, `holdout-v3`**
: Git tags of the agent. `holdout-v1` is the version frozen for the hold-out suite (design 006);
  `holdout-v2` adds the entry point design 014's SREGym driver uses; `holdout-v3` (planned) is
  `main` once issues #15, #17 and #18 are fixed, three k8stools tools are adopted, a first pass at
  design 011 is done and design 014's comparison is written down; the tag holds the code and that
  document. Design 014 compares it with the decision model off and on.

**Burned (fault or scenario)**
: Seen or used during development, so no longer usable as a hold-out.

**Decision model, Judge, JEV**
: Design 011's proposal: small, specialised models that answer one narrow question (such as "does
  this log show the process completed startup?") with a probability, behind a replaceable `Judge`
  interface. JEV is the first provider considered. Not built.
  [Design 011](../designs/011-decision-models.md).

**Collector, collect-first pipeline**
: Design 011's proposal: code (the collector) reads a fixed set of objects from the cluster
  before any model runs, the facts layer states facts from them, and only then is it decided
  whether to answer or investigate. Not built.
  [Design 011 §3.1](../designs/011-decision-models.md#31-the-pipeline).

**Fast path, deep path, routing check**
: In the collect-first pipeline, the fast path answers from the collected facts with one model
  call; the deep path is today's agent session, started with those facts; the routing check
  chooses between them, and chooses the deep path whenever it is unsure. Not built.
  [Design 011 §3.4](../designs/011-decision-models.md#34-the-routing-check).

**Decoy**
: Something in a scenario that looks like the cause and is not, such as an unrelated failing
  workload. A checklist trap fails an answer that blames it.

**False-leader rate**
: How often `kb_query.py open` names a top cause that is wrong, over a labelled set of cases.
  Design 015's measure of whether the knowledge base misleads the agent.
  [Plan 015](../plans/015-kb-evaluation.md).

**Consulted session**
: A session in which the agent queried the knowledge base (`lookup`, `open` or `claim`) at least
  once. Design 015 compares consulted and unconsulted sessions.

**A−cand ("A minus candidates")**
: Design 015's live test configuration: the agent as shipped, with the knowledge base's candidate
  commands (`search`, `lookup`, `open`, `claim`) removed. Compared against the agent as shipped.
  [Plan 015](../plans/015-kb-evaluation.md).

**Suite repository**
: The private repository (named by `K8SRCA_SREGYM_SUITE`) that holds design 014's SREGym driver and
  every run record, kept outside this repository so held-out answers stay out of it.
  [Design 014](../designs/014-sregym.md).

**The reading**
: Design 014's write-up of its comparison's results, written only after the score table, before
  any answer is opened.

**Seam**
: The entry points `sync_agent` and `run_session` in `src/k8srca/scenario/runner.py`, which let an
  outside program run one session on a given question. Tagged into `holdout-v2` for design 014.

**View proxy**
: SREGym's filtered Kubernetes API endpoint, which hides the injector's objects and labels from
  the agent. k8stools reads through it in design 014.

**Conductor**
: SREGym's component that deploys an application, injects a fault and runs the agent and the
  judge.

**Drift canary**
: In a comparison whose configurations cannot run in one sitting, a few faults rerun on the first
  configuration during the second's sitting, to show whether the cluster or API drifted between
  them.

## How we work

**Constitution**
: The rules every change keeps, for the architecture and for how we work, each with its reason
  and what enforces it. [docs/constitution.md](constitution.md).

**Reference document, change design**
: A reference document (designs 001–003) describes the system as it is and is kept current. A
  change design (004 onward) proposes one change and ends as done, done in part or abandoned.
  [designs/README.md](../designs/README.md).

**Design statuses**
: draft, approved, in progress, on hold, done, done in part, abandoned; reference for reference
  documents. [designs/README.md](../designs/README.md#statuses).

**Plan**
: The steps that carry out a design, each with its measurement (hypothesis, expected effect,
  whether it can be seen, budget, done and stop), the issues that track them, and the results.
  [plans/README.md](../plans/README.md).

**Measured design**
: The one design at a time allowed to spend the live-evaluation budget or change the agent being
  measured. [Constitution P5](constitution.md#p5-one-measured-design-at-a-time).

**Owner (of a design, plan or task)**
: The developer doing it, who approves its plan and budget and makes its checkpoint decisions.
  [Constitution P8](constitution.md#p8-the-owner-of-a-task-decides-for-it).
