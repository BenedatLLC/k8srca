# k8srca — Investigation Model

**Design document 002 — How the agent reasons**
Status: Draft for review · Date: 2026-09-07
Companions: [001 — Architecture](001-architecture.md) · [003 — Operations](003-operations.md)

---

## 1. Purpose and relationship to 001

001 specifies the **platform**: Claude Managed Agents, self-hosted sandboxes, the worker-as-MCP-client
bridge to k8stools, Slack, skills delivery, and the read-only guarantee. It says almost nothing about
*how the agent should think*, beyond an output contract in its §9.

This document specifies the **method**: how hypotheses are formed and discriminated, what counts as
evidence, when an investigation may conclude, and what persists between turns.

| Question | Document |
| --- | --- |
| Where does a tool call execute? | 001 §4 |
| Which model runs which thread? | 001 §3.3, §6 |
| Can the agent change the cluster? | 001 §8 |
| What shape does the answer take? | 001 §9 |
| **How does the agent decide what to look at next?** | **002 §6** |
| **What state survives a turn?** | **002 §7** |
| **When is it allowed to stop?** | **002 §5.4** |
| **How does the 81-rule KB become usable?** | **002 §8** |

### Provenance

The core ideas here — investigation-over-answer, competing hypotheses, evidence ledger,
discriminating actions, stopping policy, causal chains, user input as evidence — come from
`background/kubernetes_rca_design.md`. Two of its structural proposals are deliberately **not**
adopted, for reasons in §10: numeric hypothesis probabilities, and a code-level data abstraction
layer over the backends.

---

## 2. Design principles

1. **Investigation, not answer.** RCA is a process spanning turns, not one model invocation.
2. **Competing hypotheses.** Multiple explanations stay live until evidence separates them. An alert
   is a *starting hypothesis*, never a conclusion.
3. **Discrimination over exhaustion.** The next action should be the one that best separates the
   leading hypotheses — not the next generic health check.
4. **Every claim carries provenance.** No assertion without the tool call, thread, or human statement
   that produced it.
5. **Absence and unavailability are different.** "Looked, found nothing" and "could not look" lead to
   different conclusions and must never collapse into silence.
6. **Human statements are evidence.** Typed, attributed, and weighted differently from observations.
7. **Structure is explicit and inspectable.** The reasoning state is a file on disk, not an
   implication of the conversation.
8. **Ordinal confidence, not invented numbers.** See §10.1.
9. **Diagnosis before remediation.** Establish a causal explanation before recommending action; never
   take one (001 §8).

### What is *not* being built

The alternative design proposes an investigation *engine* — planner, hypothesis manager, evidence
interpreter, stopping policy — as components outside the model. We are not building that. **CMA
already supplies the agent loop, tool orchestration, context compaction, and session state**; an
external engine would reduce CMA to an expensive Messages API endpoint and discard most of 001.

Instead: **the model reasons, and scripts enforce the structure.** The model decides what is evidence
and what it implies. The scripts make it *record* those decisions in a fixed shape, and refuse to let
it conclude without meeting explicit criteria. The structure is a guardrail on the reasoning, not a
replacement for it.

---

## 3. The investigation record

One JSON document per investigation, at `/workspace/investigation/<id>.json` — persistent across
turns via the session-scoped workspace bind-mount (001 §3.2).

```jsonc
{
  "id": "inv-20260907-payment-api",
  "state": "gathering",
  "trigger": { "kind": "user_question", "text": "why is payment-api crash-looping in prod?" },
  "subject": { "kind": "pod", "namespace": "prod", "name": "payment-api-7d9f8c" },
  "opened_at": "2026-09-07T11:02:00Z",

  "hypotheses": [ /* §4 */ ],
  "evidence":   [ /* §5 */ ],
  "questions":  [ /* open questions for the user */ ],
  "actions":    [ /* what was run, and why — §6 */ ],
  "causal_chain": [ /* §5.5, populated at conclusion */ ],
  "conclusion": null
}
```

The record is written **only by the coordinator** (§7.3). It is the substrate for three things: the
answer rendered to Slack, the audit trail (§9), and — in v2 — the content synthesized into a memory
store.

### 3.1 Lifecycle

```
   open ──▶ gathering ──▶ concluded
              │  ▲
              │  └── awaiting_user  (question posed; user's reply re-enters as evidence)
              │
              └────── abandoned     (superseded, or the user moved on)
```

Deliberately flatter than the alternative's seven-state machine. `assessing` and `reassessing` are not
distinct states here — the coordinator re-ranks after *every* evidence addition, so a state meaning
"currently re-ranking" would never be observed between turns. `awaiting_user` matters because it
changes what the orchestrator does with the next Slack message.

---

## 4. Hypotheses

```jsonc
{
  "id": "memory_regression_from_release",
  "statement": "Release 2.4.1 introduced a memory regression that exceeds the container limit",
  "status": "supported",
  "rank": 1,
  "supported_by": ["ev-003", "ev-007"],
  "contradicted_by": [],
  "would_confirm": "Memory growth began at the 2.4.1 rollout and request rate was flat across it",
  "would_refute": "Memory growth predates the rollout, or scales with request rate",
  "source": "playbook:CrashLoopBackOff"
}
```

`would_confirm` / `would_refute` are the load-bearing fields — they are what makes §6 possible. A
hypothesis that cannot say what would refute it is not yet a hypothesis, and the script rejects it.

**Status is ordinal, not numeric:**

| Status | Meaning |
| --- | --- |
| `candidate` | Proposed; no discriminating evidence either way |
| `supported` | Confirming evidence, no contradictions |
| `weakened` | Contradicting evidence, but not decisive |
| `refuted` | Decisive contradicting evidence — `would_refute` was observed |
| `confirmed` | `would_confirm` observed **and** every rival `weakened` or `refuted` |

`rank` is a total order the coordinator maintains over non-refuted hypotheses. Ranking is a
comparative judgement the model is good at; assigning `0.72` is one it is not (§10.1).

---

## 5. Evidence

```jsonc
{
  "id": "ev-003",
  "kind": "observation",
  "subject": { "kind": "pod", "namespace": "prod", "name": "payment-api-7d9f8c" },
  "statement": "Last termination reason OOMKilled; 15 restarts in 10 minutes",
  "observed_at": "2026-09-07T10:47:00Z",
  "collected_at": "2026-09-07T11:03:12Z",
  "provenance": { "via": "tool", "tool": "k8s_get_pod_container_statuses", "thread": "coordinator" },
  "bears_on": [
    { "hypothesis": "memory_regression_from_release", "direction": "supports", "decisive": false },
    { "hypothesis": "liveness_probe_misconfigured",   "direction": "contradicts", "decisive": true }
  ]
}
```

### 5.1 Evidence kinds

| Kind | Meaning | Weight |
| --- | --- | --- |
| `observation` | A tool returned a positive finding | Strongest |
| `absence` | Looked and found nothing — *"no OOMKilled events in the window"* | Strong for refutation |
| `unavailable` | Could not look — backend down, RBAC denied, logs rotated | **Never** supports or contradicts; blocks `confirmed` |
| `user_statement` | A human asserted it | Provisional — §5.3 |
| `inference` | Derived from other evidence, not directly observed | Must cite its parents |

**`absence` vs `unavailable` is the distinction that most often goes wrong**, and conflating them
produces confident wrong answers. `unavailable` may appear in `bears_on` with direction `neither`; the
script rejects any other direction. Every `unavailable` also lands in the conclusion's *Not checked*
field (001 §9), which is what makes coverage gaps visible instead of silent.

### 5.2 Provenance

`via` is one of `tool`, `subagent`, `user`, `skill`. Findings returned by `k8s-investigator` are
recorded with `via: "subagent"` and the thread id — so a wrong answer is traceable to where it was
produced (001 §9), and the coordinator's own verification is distinguishable from a delegate's report.

### 5.3 User statements

> *"We deployed 2.4.1 right before the restarts started."*

This is evidence and must be recorded — telemetry frequently cannot supply it, and in v1 (no git
access) change history often comes only from humans. But it is **provisional**: humans misremember
timing, conflate releases, and answer the question they expected. Rules:

- A `user_statement` may move a hypothesis to `supported` or `weakened`.
- It may **never** be `decisive`, and so can never by itself produce `confirmed` or `refuted`.
- Where a tool *could* corroborate it, the coordinator should try, and record the result separately.
- The conclusion states which findings rest on unverified human recollection.

### 5.4 Stopping criteria

An investigation may move to `concluded` when **all** of:

1. The top hypothesis is `confirmed` — its `would_confirm` was observed, not merely something
   compatible with it.
2. Every other hypothesis is `weakened` or `refuted`, each citing the evidence that did it.
3. No open question remains whose answer could reorder the top two.
4. No `unavailable` evidence bears on a check that criterion 1 depends on.

If those cannot be met, the investigation concludes anyway — as a **partial conclusion**, stating the
leading hypothesis, what would settle it, and why that could not be obtained. A partial conclusion is
a legitimate, useful outcome; a fabricated confident one is not.

The guard against premature closure is criterion 1's wording. `OOMKilled` is compatible with memory
exhaustion but does not establish *why* memory was exhausted — leak, workload growth, bad limit, or
release regression all survive it. Requiring the pre-declared `would_confirm` forces the extra step.

### 5.5 Causal chain

At conclusion, the coordinator emits an ordered chain from root cause to observed symptom:

```
release 2.4.1 → memory regression → working set exceeds limit → OOMKilled
              → container restart → CrashLoopBackOff
```

This is what distinguishes root cause from contributing factor from symptom, and it is the part users
find most legible. The KB's `causal_parent` graph (§8) supplies candidate links; evidence has to
justify each one that appears in the final chain.

---

## 6. Choosing the next action

The rule, stated for the coordinator's prompt:

> **Prefer the action whose plausible outcomes would most change the ranking of your top two
> hypotheses. If no outcome of an action would change anything, do not run it.**

Concretely, before running anything the coordinator asks: *if this returns X, what changes? if it
returns Y?* Two answers of "nothing" means the action is not worth its cost.

`plan.py` (§7.2) makes this mechanical without making it numeric. Given the current record it:

- collects candidate actions from the playbooks of all non-refuted hypotheses;
- drops any already in `actions`;
- flags those bearing on the `would_confirm` / `would_refute` of the **top two ranked** hypotheses as
  *discriminating*;
- flags those bearing on only one as *confirmatory* (weaker — risks confirmation bias);
- surfaces open questions whose answers would discriminate, as candidates for `ask_user`.

It **suggests and ranks; it does not decide.** The coordinator may override with a reason, which is
recorded. This keeps the model's judgement in the loop while making the default behavior
discrimination-seeking rather than confirmation-seeking.

### 6.1 Investigation actions

Semantic actions are **documented procedures in `SKILL.md`**, not code — recipes the model executes
using the low-level k8stools tools. This gets the model to the right altitude without an abstraction
layer to build and maintain (§10.2).

| Action | v1 implementation | Notes |
| --- | --- | --- |
| `inspect_resource_health` | pod/deployment summaries, container statuses | Coordinator triage set (001 §3.3) |
| `inspect_recent_failures` | pod events, previous-container termination reasons | Coordinator |
| `inspect_resource_pressure` | node summaries, node conditions, evictions | Coordinator |
| `inspect_logs_for_pattern` | pod/job logs, grep-then-read | **Delegated** — volume |
| `inspect_dependencies` | services, endpoints, configmaps | Coordinator; thin without traces |
| `inspect_recent_changes` | **degraded in v1** — replica/generation state only | Real answer needs git (001 §11) |
| `ask_user` | Pose a question, set `awaiting_user` | Often the only route to change history in v1 |

`inspect_recent_changes` is the honest weak spot. "What changed just before this started?" is among
the highest-value RCA questions and v1 can barely answer it from cluster state alone. The v1 fallback
is `ask_user`; §8's playbooks should mark change-related questions as high-value so the coordinator
reaches for them early rather than exhausting telemetry first.

---

## 7. Persistence and the scripts

### 7.1 Where state lives

| Horizon | Location | Notes |
| --- | --- | --- |
| Within a turn | Coordinator's context | Ordinary reasoning |
| Across turns | `/workspace/investigation/<id>.json` | Session-scoped host mount (001 §3.2) |
| Across sessions (v1) | Orchestrator SQLite copy | Snapshot each time a turn settles |
| Across sessions (v2) | Memory store at `/mnt/memory/investigations/` | The natural payload for 001 §11 |

The orchestrator snapshot is not redundant. A session can terminate mid-thread (001 §7.4), and the
workspace is reaped with it; the snapshot lets a replacement session be re-seeded with the
investigation rather than a lossy prose summary. It also gives the *only* durable record if the host
workspace is lost.

**This is the concrete answer to "what should a memory store hold?"** In v2, concluded investigations
sync to a memory store and become available to later sessions — *"this cluster has had three OOMKills
in `payment-api` this month, all after releases."* That is knowledge synthesis with a real substrate
rather than an aspiration.

### 7.2 The scripts

Shipped inside the `k8s-rca` skill (001 §5.1), alongside `kb_query.py`:

```
skills/k8s-rca/
  SKILL.md              # method, action recipes, output contract
  playbooks/*.yaml      # §8
  kb_query.py           # playbook lookup, causal-chain walk
  investigation.py      # open | hypothesize | evidence | rank | question | show | conclude
  plan.py               # rank candidate next actions by discrimination (§6)
```

`investigation.py` is a validator as much as a store. It rejects:

- a hypothesis without `would_confirm` and `would_refute`;
- evidence without provenance;
- an `unavailable` item claiming to support or contradict anything;
- a `user_statement` marked `decisive`;
- `conclude` when §5.4 is unmet — unless `--partial` with a stated reason.

Those five refusals are where most of the method's value sits. They are cheap, deterministic, and
catch exactly the failure modes an LLM is prone to under pressure to produce an answer.

### 7.3 Single-writer rule

Only the **coordinator** writes the record. Specialists (001 §3.3) return findings; the coordinator
decides what they mean and records the evidence. This follows from F4 — subagents share the
filesystem but not the context, so a specialist writing `bears_on` would be guessing at a hypothesis
set it cannot see. Specialists may *read* the record if a brief points them at it.

---

## 8. From knowledge base to playbooks

The 81-rule KB is keyed by `alert_name` with `;`-separated fields. Usable as lookup; not usable as an
investigation guide, because it names root causes without saying how to tell them apart.

**Current:** `Alert → root causes → evidence → PromQL → solutions`
**Target:** `Alert → candidate hypotheses → {evidence for, evidence against, what would settle it, actions, questions} → causal links → remediation`

```yaml
alert: CrashLoopBackOff
resource_type: Pod
symptom: container repeatedly restarting
hypotheses:
  - id: memory_exhaustion
    statement: The container exceeded its memory limit
    evidence_for:    [ "termination reason OOMKilled", "working set at limit before restart" ]
    evidence_against: [ "exit code non-zero with no OOM event", "memory flat across restarts" ]
    would_confirm: "OOMKilled with working set at the limit immediately prior"
    would_refute:  "No OOM event and a non-zero application exit code"
    actions:   [ inspect_recent_failures, inspect_resource_pressure ]
    questions: [ "Did memory use grow steadily or spike?" ]
    causal_parents: [ memory_regression_from_release, workload_growth, limit_too_low, node_memory_pressure ]
    remediation: [ "investigate memory growth", "right-size the limit", "roll back if release-correlated" ]
    automation_safety: manual
```

The `causal_parents` field is what stops the investigation at `OOMKilled` from being mistaken for a
conclusion: confirming memory exhaustion *opens* a second round over its parents.

Two rules for the transformation:

- **Derive, don't invent.** `evidence_for`, `causal_parents`, `remediation`, and `automation_safety`
  come from existing columns. `would_confirm` / `would_refute` / `evidence_against` / `questions` are
  genuinely new and must be authored.
- **Author on demand.** Do not convert all 81 up front (§9). Convert the ones real sessions hit,
  driven by the *Not checked* field. Most of the 81 will never fire on one cluster; authoring
  discriminators for alerts nobody sees is the largest avoidable cost in this design.

Unconverted rules stay available through `kb_query.py` in their current form, so coverage never
regresses — a converted playbook is an *upgrade* for that alert, not a gate on it.

---

## 9. Staging: from prompt to method

**The method is a maturity ladder, not a prerequisite.** Every level is independently useful and
shippable; each is a superset of the last. The whole point is to prove the pipeline of 001 with the
cheapest possible reasoning, then thicken it where evidence says it is thin.

| Level | Reasoning | Artifacts | Gate to the next level |
| --- | --- | --- | --- |
| **L0 — Prompt only** | Method lives entirely in `SKILL.md` + system prompt. Hypotheses and evidence are prose in the output contract (001 §9). No scripts, no record. | `SKILL.md`, `kb_query.py` over the *unmodified* KB | End-to-end works: Slack → session → k8stools → answer |
| **L1 — Structured output** | Same, but the final answer must be emitted as the §3 JSON shape (hypotheses with `would_refute`, typed evidence). Written once at turn end; not read back. | + output schema in `SKILL.md` | The model can produce the shape without flailing |
| **L2 — Durable record** | `investigation.py` with its five validations. State survives turns; follow-ups extend an investigation rather than restarting. | + `investigation.py`, orchestrator snapshot | Multi-turn threads demonstrably reuse prior evidence |
| **L3 — Discriminating planner** | `plan.py`; the coordinator justifies each action against the top-two ranking. | + `plan.py`, action recipes in `SKILL.md` | Measurable drop in redundant tool calls per conclusion |
| **L4 — Playbooks** | Hypothesis sets seeded from converted playbooks rather than invented per incident. | + `playbooks/*.yaml` for alerts actually seen | Converted alerts outperform prompt-only on the scenario suite |
| **L5 — Cross-session memory** | Concluded investigations sync to a memory store; prior investigations inform new ones. | + memory store (001 §11) | — |

### Mapping onto 001's build order

| 001 phase | 002 level |
| --- | --- |
| 0 — RBAC, k8stools, egress | — |
| 1a — worker-as-MCP-client, single agent | — |
| 1b — coordinator + specialist | — |
| 2 — `kb build` + `k8s-rca` skill | **L0** |
| 3 — Slack orchestrator | **L0**, then **L1** once real questions flow |
| 4 — `arch build` | L1 |
| 5 — scenario suite | **L2**, then L3/L4 measured against it |

**L0 through 001 Phase 3.** Do not build `investigation.py` before Slack works end to end. The riskiest
assumptions in this project are platform assumptions (001 F1, F4, §12.6), not reasoning ones, and a
prompt-only agent exercises the entire pipeline while costing a day of prompt writing. If L0 already
produces useful answers on real incidents, that is worth knowing before building a ledger.

**L2 and beyond only after the scenario suite exists** (001 §12.5, designed as
[004](004-scenario-testing.md)). L3 and L4 are optimizations, and
optimizing without measurement is how prompts accumulate folklore. The suite is what tells you whether
`plan.py` reduced tool calls or just added ceremony.

**Skip levels deliberately, not accidentally.** If L1 shows the model already discriminates well
unprompted, go straight to L2 for durability and defer L3. The ladder is an ordering of *risk*, not a
mandatory sequence.

### L2 is a hard prerequisite for the Kubernetes move

L2's value is not only multi-turn continuity. Until it ships, the investigation record lives on the
session-scoped host workspace (001 §3.2) — a directory shared across per-turn containers on one host.
**That arrangement has no clean Kubernetes equivalent:** per-turn Jobs land on arbitrary nodes, so
preserving it would mean an RWX volume per session (see [003 §4.3](003-operations.md#43-the-two-things-that-do-not-translate)).

L2 removes the dependency rather than satisfying it. With the record in the orchestrator snapshot,
the workspace becomes disposable — skills re-download per turn, costing a little latency and nothing
else. So **L2 must precede any Kubernetes deployment** ([003 §4.4](003-operations.md#44-prerequisite-externalize-the-workspace-first)),
or you end up building shared storage to preserve state you had already decided to externalize.

This is the one hard ordering constraint across the three documents.

---

## 10. Rejected alternatives

### 10.1 Numeric hypothesis probabilities

`background/kubernetes_rca_design.md` §6/§9/§15 carries explicit probabilities updated per evidence
(`0.33 → 0.72 → 0.54`), with a stopping policy of `top ≥ 0.90 AND no competitor ≥ 0.20`. Not adopted.

Its §9 concedes that "the exact probability-update algorithm is an implementation detail" — but that
is the whole difficulty, and everything downstream depends on it. Only two implementations exist:

- **Genuine Bayesian update** needs priors per hypothesis and likelihoods per evidence type, elicited
  across 81 alerts × several hypotheses each. That is a very large authoring effort producing numbers
  nobody can defend, on a cluster whose real base rates are unknown.
- **The LLM emits the numbers.** Then `0.72` is a token, not a measurement. LLM confidence is poorly
  calibrated, and a threshold policy on uncalibrated output fires on noise while looking rigorous.

The false precision is the actual hazard: `0.94` in a Slack message reads as a measurement to an SRE
at 3 a.m., and invites acting on it. Ordinal ranking plus a pre-declared `would_confirm` (§5.4)
delivers the behavior the probabilities were meant to produce — competing hypotheses, no premature
closure — without inventing a number.

**Revisit if** the scenario suite (001 §12.5) grows enough labeled incidents to *measure* calibration.
Then the numbers would mean something. Until then they are decoration.

### 10.2 A code-level data abstraction layer

The alternative's §12 proposes backend-neutral adapters (Prometheus / K8s / Loki / Tempo / Git) behind
an `RCADataSource` interface. Not adopted, for two reasons:

- **MCP already is that boundary.** In 001 a new data source is one `mcp:` entry plus one specialist
  agent (001 §11) — no code. A hand-written adapter layer over MCP servers is a second abstraction
  doing the first one's job.
- **Semantic actions don't need to be code.** Its stronger point — that raw tool calls are the wrong
  altitude for the model — is addressed by §6.1: the same `inspect_*` vocabulary, implemented as
  documented recipes. This keeps the model's flexibility to deviate when an incident is unusual, which
  a fixed adapter would foreclose.

What *is* adopted from §12: **PromQL and other backend-specific queries stay in the playbooks**, never
in the reasoning prompt.

### 10.3 An external investigation engine

See §2. CMA supplies the loop; scripts supply the guardrails; the model supplies the reasoning.
Building a planner and hypothesis manager as services outside the model would mean paying for CMA's
orchestration and not using it.

---

## 11. Open questions

0. **The knowledge base was never delivered until now.** *(Resolved, 2026-09-09.)*
   Skills did not reach the sandbox at all — see 001 §5, two delivery bugs — so
   every result attributed to Phase 2 came from the model's own knowledge plus
   the system prompts. The first genuine test of the KB is recorded below.

   **What it showed.** The agent reached for `kb_query.py` unprompted and in
   the designed sequence (SKILL.md → `search` by symptom → `lookup` the alert).
   But having obtained three candidate causes for `OOMKilled`, it discussed one
   and silently dropped the other two — demoting *memory leak* to a follow-up
   action and never mentioning *workload spike*, despite a `load-generator`
   running that made it plausible.

   That is a **method gap, not a knowledge gap**, and the most dangerous shape
   of one: the conclusion reads as well-supported *because* the alternatives
   went unmentioned. §5.4 requires every rival to be weakened or refuted with
   evidence; nothing enforced it at the point where candidates enter.

   Fixed by requiring a disposition — confirmed, weakened, refuted, or could
   not check — for every candidate the base returns, in both the skill and the
   coordinator's prompt, with "worth checking later" explicitly not counting as
   one. On a re-run of the same question all three candidates were
   dispositioned, workload spike was tied to the running load-generator, and
   candidates from correlated entries were dispositioned too.

   **The KB's value here was supplying the hypothesis space, not the answer.**
   The model already had the leading cause. What it lacked, and the base
   provided, were the rivals it then had to rule out.

1. **Does L1 structured output degrade reasoning?** Forcing a JSON shape can make a model optimize for
   filling fields over thinking. Compare L0 and L1 on the same scenarios before assuming L1 is
   strictly better.
2. **Playbook authoring cost.** Unknown until a few are written. If a good playbook takes an hour,
   converting the alerts one cluster actually sees is a week; if it takes a day, the on-demand policy
   in §8 becomes essential rather than merely sensible.
3. **Who writes `would_confirm` for an unplaybooked alert?** Currently the model, at investigation
   time. That is the weakest link in §5.4 — the criterion is only as good as the discriminator the
   model invented. Worth checking whether model-authored discriminators hold up.
4. **Does the coordinator maintain the record faithfully across a long thread?** Ledger discipline may
   decay as context grows. If so, a per-turn reconciliation step ("re-read the record, confirm
   rankings still follow from the evidence") may be needed.
5. **Investigation identity.** One Slack thread currently means one investigation. A thread that
   drifts to a second, unrelated problem should probably fork a new record — detecting that
   automatically is unsolved; an explicit user command may be enough.
