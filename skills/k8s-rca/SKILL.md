---
name: k8s-rca
description: Method and knowledge base for Kubernetes root-cause analysis. Use for any question about why a workload, pod, node, or cluster component is failing, degraded, restarting, pending, or slow.
---

# Kubernetes root-cause analysis

You diagnose a live cluster through read-only tools. Find out why, from what
the tools show, and say how sure you are. You will rarely prove a cause: the
cluster keeps little history. You rank causes, and hand the reader the check
that would settle the rest.

## Facts

Every tool result arrives numbered, with the facts code read from it above
it:

```
result 4
facts (cite as [F<n>]):
[F7] ad: memory limit 300Mi, equal to its request
[F9] Warning events in default go back 3d20h (the oldest record retained, not when any problem began)
```

Facts are what your answer cites. The part in brackets is a fact's scope:
what the record covers. Say what the fact says, within its scope, and no more.

`kb_query.py facts` lists every fact so far, including those from the
investigator's calls (`--workload ad` for one workload).

Some observations need reading text, which code cannot do: a log that stops
partway through startup, a stack trace naming a missing file. Record each one
you rely on:

```
kb_query.py fact default/ad 'not process_logged_fatal_error[C]' \
    --says "ad's log ends after its startup banner, with no exception" --from 6
```

`--from` is the number of the result you read it in. The first argument is
the knowledge base's vocabulary (`claim` shows the facts each cause turns on);
`--says` is the sentence a reader will see. It prints the new fact's id.

## The loop

1. **The symptom.** What fails, on which object. `kb_query.py lookup <alert
   or symptom>` gives its candidate causes, each with its quickest test here.
2. **Rank.** `kb_query.py open <symptom> --workload <name>` ranks every
   candidate from the facts so far. It prints three things: the most likely
   cause and the facts it rests on; what is ruled out, and by which fact; and
   the rest, each with the cheapest check that would decide it. Only some
   symptoms have a ranking; for the others `open` says so, and you rank
   `lookup`'s candidates yourself by the same rule: a candidate is ruled out
   by a fact that excludes it, and the most likely is the one the facts fit
   best. Say that you formed the criterion.
3. **Check.** Run the check that would most change the ranking: what would
   settle the leader, or the cheapest check on its closest rival. Before you
   run one, know what each outcome would change. Then `open` again.
4. **Stop** when the leader is strong, or when no cheap check is left that
   would move it. Do not check candidates that nothing points at.
5. **Answer.**

`open`'s rule is crude so a reader can follow it. Where the evidence says
more than the facts recorded, depart from it and say why.

Other failures in the cluster are usually unrelated. Before you call one a
cause, find a path from it to the symptom in the cluster-architecture skill
(`wiki.py`). One with no path is not a cause.

What changed recently is what you can see least. If it matters, ask the
person: they often know of a deploy you cannot see.

## The answer

At most 300 words, in four parts:

**Most likely:** the cause in a sentence or two, with one confidence word
from `open`'s strength: *very likely* for strong, *likely* for moderate,
*possible* for weak. If two causes tie, say there is no leading cause and
name the check that would separate them.

**Why:** the two to four facts it rests on, as sentences, each with its
markers: "it is killed for memory every time it starts[F12][F14]". If a fact
counts against it, say so in a sentence, and why the cause still stands.

**Ruled out:** only candidates a fact excludes, one line each, with its
marker.

**Next steps:** what the person should check or change, most useful first.
A candidate nothing decided appears here, and only here, when a step would
settle it: "to rule out a leak, …". These are suggestions: you never change
anything.

Write sentences for a person, not a list of figures. Exact values live in the
facts, and the reader sees each cited fact in a note under the answer; the
prose can say "at its limit" where [F7] says 300Mi. A figure in the prose must
be in a fact it cites. Put no fact ids anywhere but in markers, and write no
Evidence or Sources section: the host numbers the markers and adds the notes.

## Knowledge base

`kb_query.py` sits next to this file: alerts and symptoms, the causes of each,
and what tells them apart.

```
kb_query.py search "pod restart"        find a symptom or alert
kb_query.py lookup CrashLoopBackOff     candidates, each with its quickest test
kb_query.py open CrashLoopBackOff --workload ad   the ranking from the facts so far
kb_query.py claim oom-kill              a cause's full criteria and notes
kb_query.py facts                       the facts so far, with their ids
kb_query.py fact <workload> '<fact>' --says '<sentence>' --from <n>   record a reading
```

The knowledge base is generic Kubernetes. The cluster in front of you is
authoritative where they disagree.
