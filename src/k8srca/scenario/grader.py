"""Rubric grading (design 004 §6.2).

The grader is given the answer, the ground truth, *and* the capture. That is
what turns it from a judge into a checker: asked for an opinion it shares the
generator's blind spots, but asked "is this claim supported by this file" it
largely does not (004 §6.2, §11.1).

It runs on a different model from the one under test for the same reason. The
coordinator is Sonnet; grading with Opus is not about capability so much as
about not asking one model to notice the exact things it is itself prone to
missing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from .model import ScenarioDir

#: 004 §11.1 says Claude grading Claude needs validating against a human read
#: before the baseline is trusted. A different model is a cheap partial hedge.
GRADER_MODEL = "claude-opus-5"

#: Log lines per container for the pods a scenario is about.
DIGEST_LOG_LINES = 25

#: Log lines kept for every *other* pod. Dropping them entirely was a mistake:
#: answers routinely say "flagd's logs are clean" or "no OOM in any of the
#: three services' logs", and with the logs gone the grader cannot tell a true
#: claim from an invented one -- it reported them as fabrications, and the
#: evidence dimension measured this scoping decision rather than the agent.
#: A short tail is enough to check "clean" and "no error in", which is what
#: such claims assert.
DIGEST_BYSTANDER_LOG_LINES = 8


class RivalVerdict(BaseModel):
    id: str
    dispositioned: bool = Field(description="Was this rival given a disposition at all?")
    disposition_given: str = Field(
        description="What the answer actually did with it: confirmed, weakened, "
                    "refuted, could_not_check, mentioned_only, or absent")
    matches_truth: bool = Field(description="Does that match truth.yaml's disposition?")
    note: str


class TrapVerdict(BaseModel):
    id: str
    handled: bool = Field(description="Was the trap handled as truth.yaml specifies?")
    note: str


class GapVerdict(BaseModel):
    id: str
    reported_unavailable: bool = Field(
        description="Did the answer report this as unavailable, rather than "
                    "asserting it away or inventing a value?")
    note: str


class Grade(BaseModel):
    cause_correct: bool
    cause_note: str
    evidence_supported: bool = Field(
        description="Is each substantive claim tied to a tool result that supports it?")
    evidence_note: str
    rivals: list[RivalVerdict] = Field(default_factory=list)
    traps: list[TrapVerdict] = Field(default_factory=list)
    gaps: list[GapVerdict] = Field(default_factory=list)
    restraint_ok: bool | None = Field(
        default=None,
        description="Null unless the scenario is about declining to invent a cause.")
    restraint_note: str = ""
    unsupported_claims: list[str] = Field(
        default_factory=list,
        description="Concrete claims the reference CONTRADICTS, or is silent on "
                    "where it should not be. Quote the claim, do not paraphrase.")
    unverifiable_claims: list[str] = Field(
        default_factory=list,
        description="Claims of a kind neither source covers, so you cannot check "
                    "them either way. Not failures -- kept separate so an answer "
                    "is never marked as fabricating merely because the reference "
                    "is silent.")

    def dimensions(self) -> dict[str, bool | None]:
        """Per-dimension pass/fail, for the report table (004 §6.4)."""
        return {
            "cause": self.cause_correct,
            "evidence": self.evidence_supported and not self.unsupported_claims,
            "rivals": all(r.dispositioned and r.matches_truth for r in self.rivals)
                      if self.rivals else None,
            "traps": all(t.handled for t in self.traps) if self.traps else None,
            "gaps": all(g.reported_unavailable for g in self.gaps) if self.gaps else None,
            "restraint": self.restraint_ok,
        }


def _is_relevant(pod: dict, mentioned: str) -> bool:
    """Whether this pod's logs need to be in the digest.

    Unhealthy pods always are -- they are what the question is about. So is any
    pod the truth names, because a claim about its logs has to be checkable. A healthy pod nobody mentioned contributes nothing a grader can
    use, and there are usually twenty of them.
    """
    summary = pod.get("summary") or {}
    name = summary.get("name", "")
    if summary.get("ready_containers", 0) < summary.get("total_containers", 1):
        return True
    return bool(name) and name in mentioned


def digest(capture: dict, mentioned: str = "", log_lines: int = DIGEST_LOG_LINES,
           bystander_log_lines: int = DIGEST_BYSTANDER_LOG_LINES) -> dict:
    """The capture, reduced to what a claim can be checked against.

    A capture is a few megabytes, nearly all of it logs, and the grader sees it
    on every call -- at full size that cost more than the run being graded.

    Everything *structural* is kept whole for every pod: summaries, container
    statuses, images, limits, restart counts, events, replica sets. That is what
    entity and numeric claims are checked against, and it is small.

    Logs are the expensive part, so they are kept only for pods that are
    unhealthy or that the answer or truth actually names, and then only their
    tail -- a claim about a log is nearly always about how it *ends*: the last
    line before a crash, or the absence of a stack trace. Pods whose logs are
    dropped say so, so the grader never mistakes an omission for an empty log.

    `previous_logs` identical to `logs` are replaced by a marker rather than
    duplicated. During CrashLoopBackOff the kubelet serves the same instance for
    both, so the bytes are the same -- and saying so is more useful to a grader
    than printing them twice, since treating them as two observations is itself
    a failure worth catching.
    """
    def tail(text: str, keep: int) -> str:
        lines = [l for l in (text or "").splitlines() if l.strip()]
        if len(lines) <= keep:
            return "\n".join(lines)
        return f"... [{len(lines) - keep} earlier lines omitted]\n" + \
               "\n".join(lines[-keep:])

    pods = []
    for pod in capture.get("pods") or []:
        entry: dict = {
            "summary": pod.get("summary"),
            "container_statuses": pod.get("container_statuses"),
        }
        relevant = _is_relevant(pod, mentioned)
        lines = log_lines if relevant else bystander_log_lines
        entry["logs"] = {k: tail(v, lines) for k, v in (pod.get("logs") or {}).items()}
        previous = {}
        for name, prior in (pod.get("previous_logs") or {}).items():
            if prior and prior == (pod.get("logs") or {}).get(name):
                previous[name] = ("[identical to current logs -- the kubelet serves "
                                  "the same terminated instance for both while a "
                                  "container is in CrashLoopBackOff]")
            elif relevant:
                previous[name] = tail(prior, lines)
        entry["previous_logs"] = previous
        pods.append(entry)

    return {
        "captured_at": capture.get("captured_at"),
        "note": ("Ages are stored as *_offset_seconds relative to captured_at. "
                 "Container logs are tailed; omitted lines and omitted pods are marked."),
        "namespaces": capture.get("namespaces"),
        "nodes": capture.get("nodes"),
        "pods": pods,
        "deployments": capture.get("deployments"),
        "replicasets": capture.get("replicasets"),
        "services": capture.get("services"),
        "events": capture.get("events"),
    }


SYSTEM = """You are checking one Kubernetes root-cause answer against ground truth.

You are a checker, not a judge. For each claim, the question is whether the
reference supports it, not whether it sounds plausible.

The reference has two sources, and the agent can legitimately cite either:

- `capture` -- live cluster state as k8stools read it. Every pod, container,
  image, event, restart count and log line the agent could observe. Logs for
  healthy, unmentioned pods are dropped and say so; an omitted log is not an
  empty one.
- `architecture_skill` -- the cluster-architecture skill the agent carries.
  Facts *declared* in charts and manifests and *documented* in runbooks, which
  are not in the capture at all: declared images, drift between declared and
  observed, probe configuration, chart-level settings. The agent reaches these
  through a bundled `arch_query.py`, so a claim attributed to `arch_query` is
  sourced here, not invented. Check it against this source.

Claims go in exactly one of two lists, and the split matters:

- `unsupported_claims` -- the reference CONTRADICTS the claim, or is silent
  where it should not be. A pod that is not in the capture; a restart count
  that disagrees; a limit that is not what the container status says. These are
  failures.
- `unverifiable_claims` -- neither source covers this kind of fact, so you
  cannot check it either way. Kubelet internals, timing quirks, what a JVM
  typically needs, what the runtime did with a rotated log. These are NOT
  failures, and putting them in the first list marks an answer as fabricating
  because the reference is silent.

When in doubt, prefer `unverifiable_claims`. A grader that cries fabrication
gets ignored, and then it catches nothing at all.

Rules that matter:

- A rival is "dispositioned" only if the answer confirmed, weakened, refuted or
  explicitly could-not-check it. "Worth checking later", "we should also
  consider", or listing it without a verdict does NOT count. An answer that
  silently omits a rival reads as well-supported precisely because the
  alternatives went unmentioned; that is the failure mode this exists to catch.
- A trap is handled only as truth.yaml specifies. `expect: flagged` means the
  answer must call out the discrepancy, not merely avoid being wrong about it.
  `expect: avoided` means the answer must not fall into it.
- A gap is satisfied by reporting the thing as unmeasurable. Inventing a value
  fails; so does quietly asserting the conclusion the missing data would have
  supported.
- unsupported_claims is where you earn your keep. Quote any concrete claim --
  a name, a count, a limit, a timestamp, a log line -- that the capture does
  not support. Quote it exactly; do not paraphrase. An empty list is a real
  answer when everything checks out.

Judge the method, not the prose. A confident, well-written answer that dropped
two rivals is worse than a hedged one that dispositioned all of them."""


def apparatus_digest(model: str = GRADER_MODEL) -> str:
    """Identity of the grading apparatus, for versioning a baseline.

    The grader is a measuring instrument, and 004 §6.3 pins the *capture* a
    truth was written against without saying anything about the thing doing the
    grading. Editing the rubric prompt, the schema, the model, or how much of
    the capture the grader is shown moves every number it produces, and a
    baseline compared across such an edit reports the change as a regression in
    the agent.

    That happened: widening the digest and splitting unverifiable claims out
    moved rivals from 0/3 to 2/3 between two runs of one unchanged scenario,
    and the first reading was written up as a stable failure of the agent.

    Covers what changes the verdicts: the rubric, the output schema, the model,
    and how much log context the digest carries.
    """
    material = json.dumps({
        "system": SYSTEM,
        "schema": Grade.model_json_schema(),
        "model": model,
        "log_lines": [DIGEST_LOG_LINES, DIGEST_BYSTANDER_LOG_LINES],
    }, sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def build_request(sd: ScenarioDir, answer: str, tool_calls: list[str]) -> dict:
    """Message shape for one grading call.

    The capture and truth go in `system` behind a cache breakpoint and the
    answer goes in `messages`: across the n runs of one scenario the prefix is
    byte-identical, so every run after the first reads the digest from cache
    instead of paying for it again.
    """
    # Scoping is decided by the truth and by pod health -- deliberately NOT by
    # the answer. Feeding the answer in makes the reference differ on every run,
    # which invalidates the cache breakpoint below and pays full price for the
    # digest each time; across n runs of one scenario that is most of the cost.
    # A claim about a pod whose logs were dropped is still catchable: every pod
    # keeps its structural data, and the deterministic closed-world check runs
    # over the whole capture regardless.
    truth_json = json.dumps(sd.truth.model_dump(), default=str)
    payload = {"truth": sd.truth.model_dump(),
               "capture": digest(sd.capture(), truth_json)}
    architecture = sd.architecture()
    if architecture is not None:
        payload["architecture_skill"] = architecture
    reference = json.dumps(payload, indent=1, default=str)
    return {
        "system": [
            {"type": "text", "text": SYSTEM},
            {"type": "text", "text": f"<reference>\n{reference}\n</reference>",
             "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{
            "role": "user",
            "content": (
                f"<question>\n{sd.scenario.question.strip()}\n</question>\n\n"
                f"<tool_calls>\n{', '.join(tool_calls) or '(none recorded)'}\n</tool_calls>\n\n"
                f"<answer>\n{answer}\n</answer>\n\n"
                "Grade this answer against the truth and capture in <reference>."
            ),
        }],
    }


def grade(sd: ScenarioDir, answer: str, tool_calls: list[str], *,
          client: Any, model: str = GRADER_MODEL) -> Grade:
    request = build_request(sd, answer, tool_calls)
    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=request["system"],
        messages=request["messages"],
        output_format=Grade,
    )
    return response.parsed_output
