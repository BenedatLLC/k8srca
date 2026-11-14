# 014 step 1: the k8srca client on the burned set (2026-11-08)

Mechanics only. The three faults are burned (014 §3), the judge was a cheap model
(`anthropic/claude-haiku-4-5`, not the leaderboard's), and the agent ran **without a
cluster-architecture skill**, unlike every scenario in `docs/baseline.md`. A score
here says the wiring works and nothing about the agent.

**Setup.** Arm A = `holdout-v2` (`e7fa33c`), sandbox image `k8srca/sandbox:e7fa33c5449c`;
fork `80898c1b` on branch `k8srca-client`; SREGym profile `full`, `--stages diagnosis`;
Lite faults on the kind cluster (1 control plane, 3 workers); k8stools 4.6.1 behind the
view proxy. Agents synced into the test environment, no skills but `k8s-rca`.

| fault | attempt | session | $ | calls | answer | SREGym composite | pass |
|---|---|---|---|---|---|---|---|
| network_policy_block | 1 | 65 s | 0.14 | 5 | 3,211 chars | 1.00 | yes |
| network_policy_block | 2 | 95 s | 0.18 | 5 | 3,672 | 1.00 | yes |
| env_variable_shadowing_astronomy_shop | 1 | 58 s | 0.10 | 2 | 3,084 | 0.56 | no |
| env_variable_shadowing_astronomy_shop | 2 | 68 s | 0.10 | 5 | 2,917 | 0.45 | no |
| service_wrong_pod_selection_hotel_reservation | 1 | 219 s | 0.58 | 21 | 5,289 | 0.11 | no |
| service_wrong_pod_selection_hotel_reservation | 2 | 469 s | 1.11 | 37 | 3,050 | 0.11 | no |

Six runs, $2.21 in sessions (mean $0.37, median $0.16), plus one judge call each. Every
run took the host's review check and a revision turn.

**Wall clock per run, SREGym's whole cycle** (deploy, diagnosis, evaluation, cleanup,
undeploy): 302, 329, 371, 374, 464 and 708 s; mean 425 s (7.1 min). Deploy is 127 to
238 s and cleanup 70 to 106 s whatever the agent does. 140 runs (014 step 2) is about
16.5 hours serial.

**Cost is a long tail.** The two `service_wrong_pod_selection` runs cost 3 to 10 times
the others and used 21 and 37 tool calls. A budget from the mean ($0.37 × 140 = $52)
will be low if the hold-out set is weighted to faults like it.

**Not read into.** Two of three faults failed under the cheap judge, with the agent
unlike the baseline's (no architecture skill, no wiki). That is one reading on burned
faults, and a rule written from it would trip process.md §2 item 3. It is recorded so
step 2's pre-registration decides on the wiki (014 §9 question 5) with this in view.

**Also found** (014 §4.5, suite `docs/setup.md`): the first four attempts at this run
each hit a different integration gap; none touched the agent.


## Correction, found the same day in step 2a.2

The agents these runs used had **no system prompt and no skills**: the derived config was
written to `.state/`, k8srca resolves an agent's prompt and skill paths against the config
file's own directory, and both resolved to files that do not exist. `sync` printed a note and
carried on. So every score, cost and duration above is a bare model with k8stools, not the
k8srca agent, and "ran with no architecture skill" understates it: no `k8s-rca` skill and no
prompt either. What the table shows about the wiring still holds. What it shows about the agent
does not, and step 2a's budget treats the cost figures as a floor.
