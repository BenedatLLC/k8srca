"""The frozen-evidence screen (004 §6.4): a saved run's investigation held
fixed, and its answer written again under the skill and prompts in this tree.

A change to how the answer is written does not need a new investigation to be
measured, only a new answer. A live run pays for both, nine minutes and about
a dollar each, one at a time behind the shared replay container, and most of
its spread is the investigation's. The screen replays what the session saw and
asks only for what comes after it.

**What is replayed.** The session's history, fetched once by its id and
cached under TRANSCRIPTS, is cut where the coordinator wrote its first answer.
Its main thread becomes a Messages API conversation: tool calls and their
results as they happened, and the investigator's delegated work as the message
sent and the report received. The system prompt and tool declarations are the
session's own (its agent snapshot), except that the system prompt is this
tree's `agents/rca-coordinator.system.md`, which is one of the things under
test. The thinking the session did is not in its history and is not replayed.

**What is re-run.** Every bash command of the cut-off main thread, in order,
in a sandbox container with this tree's k8s-rca skill and the scenario's
pinned architecture skill: no network, no credentials, the sandbox image's own
limits. A command that touches a skill (reads SKILL.md, runs `kb_query.py` or
`wiki.py`) gets its new output in place of the old one, so the answer is
written from the current skill's text. Any other command keeps its recorded
output; it is re-run only for its side effects, such as a facts file a later
`kb_query.py open` reads. `read`, `grep` and `glob` calls keep their recorded
output and are counted as stale when they touched a skill.

**What the model may still do.** Run bash, read, grep and glob in the sandbox,
and call a cluster tool with the arguments of a call the session already made
(served from its recorded result). Any other cluster call, and any delegation,
is refused, said so, and counted (`refused`): a screen whose answer keeps
asking for more evidence is measuring an investigation it does not have.

Then the host's checks run as they do live (review.check), and a revision turn
follows when they find something. The answer is graded as a live one is.

Every model call and command runs on this host or in the sandbox container;
nothing reaches the cluster (CLAUDE.md). Cluster results come from the saved
session only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("k8srca.screen")

TRANSCRIPTS = Path(".k8srca/scenario/transcripts")
SYSTEM_PROMPT = Path("agents/rca-coordinator.system.md")

#: A command whose output depends on a skill's files, so is re-run for its text.
TOUCHES_SKILL = re.compile(r"skills/|kb_query|wiki\.py|answer_check")
#: Model rounds after the cut, per answer; the live first answers take one or two.
MAX_ROUNDS = 25
#: Per command, as a ceiling, not an expectation: kb_query runs in well under a second.
COMMAND_TIMEOUT_S = 120
#: Per tool result, matching what a bash result can carry live.
RESULT_CHARS = 100_000

REFUSED = ("Not available in this replay: the investigation is finished and the "
           "cluster tools are closed. Only calls the investigation already made "
           "can be answered. Answer from the results above.")

BUILTIN_TOOLS = [
    {"name": "bash", "description": "Run a bash command in the workspace.",
     "input_schema": {"type": "object", "required": ["command"],
                      "properties": {"command": {"type": "string"}}}},
    {"name": "read", "description": "Read a file, with line numbers.",
     "input_schema": {"type": "object", "required": ["file_path"],
                      "properties": {"file_path": {"type": "string"},
                                     "offset": {"type": "integer"},
                                     "limit": {"type": "integer"}}}},
    {"name": "grep", "description": "Search file contents with a regular expression.",
     "input_schema": {"type": "object", "required": ["pattern"],
                      "properties": {"pattern": {"type": "string"},
                                     "path": {"type": "string"}}}},
    {"name": "glob", "description": "Find files by glob pattern.",
     "input_schema": {"type": "object", "required": ["pattern"],
                      "properties": {"pattern": {"type": "string"},
                                     "path": {"type": "string"}}}},
]


class Unscreenable(ValueError):
    """A saved session the screen cannot replay faithfully."""


def fetch(client: Any, session_id: str, cache: Path = TRANSCRIPTS) -> dict:
    """The session and its full history, fetched once and kept: a screen must
    not depend on how long the platform retains a session."""
    path = cache / f"{session_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    session = client.beta.sessions.retrieve(session_id=session_id).model_dump(mode="json")
    events = [e.model_dump(mode="json")
              for e in client.beta.sessions.events.list(session_id=session_id)]
    record = {"session": session, "events": events}
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return record


def _text(event: dict) -> str:
    return "".join(b.get("text", "") for b in event.get("content") or []
                   if b.get("type") == "text")


def _key(name: str, arguments: dict) -> str:
    return name + json.dumps(arguments or {}, sort_keys=True)


@dataclass
class Prefix:
    """A session cut where its first answer begins."""

    question: str
    messages: list[dict]
    first_answer: str
    #: Every tool called before the cut, across threads: what a live run reports.
    tool_calls: list[str]
    #: Main-thread bash calls before the cut, in order: (tool_use_id, command).
    bash: list[tuple[str, str]]
    #: Cluster tool results by call, from every thread.
    frozen: dict[str, list[dict]]
    #: read, grep and glob calls on a skill: their output is the old skill's.
    stale: list[str] = field(default_factory=list)


def _main(event: dict) -> bool:
    return not event.get("session_thread_id")


def cut(events: list[dict]) -> Prefix:
    """The main thread up to its first answer, as Messages API messages.

    Interim notes written while a result or a delegated report is outstanding
    ("still waiting on the investigator") are dropped: they are not part of
    the conversation the API can represent, and they carry no evidence.
    """
    end = next((i for i, e in enumerate(events)
                if e["type"] == "session.status_idle"
                and (e.get("stop_reason") or {}).get("type") == "end_turn"), None)
    if end is None:
        raise Unscreenable("the session never finished its first turn")
    turn = events[:end]

    calls: dict[str, tuple[str, dict]] = {}
    frozen: dict[str, list[dict]] = {}
    tool_calls: list[str] = []
    for e in turn:
        if e["type"] in ("agent.tool_use", "agent.custom_tool_use", "agent.mcp_tool_use"):
            calls[e["id"]] = (e["name"], e.get("input") or {})
            tool_calls.append(e["name"])
        elif e["type"] == "user.custom_tool_result":
            name, arguments = calls.get(e.get("custom_tool_use_id"), ("", {}))
            if name:
                frozen.setdefault(_key(name, arguments), e.get("content") or [])

    messages: list[dict] = []
    role, blocks = None, []
    pending: set[str] = set()
    question, bash, stale = None, [], []

    def push(r: str, block: dict) -> None:
        nonlocal role, blocks
        if r != role:
            if blocks:
                messages.append({"role": role, "content": blocks})
            role, blocks = r, []
        blocks.append(block)

    for e in turn:
        if not _main(e):
            continue
        t = e["type"]
        if t == "user.message":
            if question is not None:
                raise Unscreenable("a second user message before the first answer")
            question = _text(e)
            push("user", {"type": "text", "text": question})
        elif t == "agent.message":
            if not pending:
                push("assistant", {"type": "text", "text": _text(e)})
        elif t in ("agent.tool_use", "agent.custom_tool_use"):
            if pending and role == "user":
                raise Unscreenable("a tool call made while earlier results were outstanding")
            arguments = e.get("input") or {}
            push("assistant", {"type": "tool_use", "id": e["id"], "name": e["name"],
                               "input": arguments})
            pending.add(e["id"])
            if e["name"] == "bash":
                bash.append((e["id"], arguments.get("command", "")))
            elif e["name"] in ("read", "grep", "glob") and TOUCHES_SKILL.search(
                    json.dumps(arguments)):
                stale.append(f"{e['name']}({json.dumps(arguments)})")
        elif t in ("user.tool_result", "user.custom_tool_result"):
            tid = e.get("tool_use_id") or e.get("custom_tool_use_id")
            content = [{"type": "text", "text": b.get("text", "")}
                       for b in e.get("content") or [] if b.get("type") == "text"]
            push("user", {"type": "tool_result", "tool_use_id": tid,
                          "content": content or [{"type": "text", "text": "(no output)"}],
                          "is_error": bool(e.get("is_error"))})
            pending.discard(tid)
        elif t == "agent.thread_message_sent":
            push("assistant", {"type": "text",
                               "text": f"[message to {e.get('to_agent_name')}]\n{_text(e)}"})
            pending.add(f"thread:{e.get('to_session_thread_id')}")
        elif t == "agent.thread_message_received":
            push("user", {"type": "text",
                          "text": f"[report from {e.get('from_agent_name')}]\n{_text(e)}"})
            pending.discard(f"thread:{e.get('from_session_thread_id')}")
    if blocks:
        messages.append({"role": role, "content": blocks})

    # Tool results lead a user message; a delegated report arriving among them follows.
    for m in messages:
        if m["role"] == "user":
            m["content"].sort(key=lambda b: b["type"] != "tool_result")

    answer = []
    while messages and messages[-1]["role"] == "assistant":
        last = messages.pop()
        if any(b["type"] != "text" for b in last["content"]):
            raise Unscreenable("the turn ended on a tool call with no result")
        answer.insert(0, "\n\n".join(b["text"] for b in last["content"]))
    if question is None or not messages:
        raise Unscreenable("no question before the first answer")
    return Prefix(question=question, messages=messages, first_answer="\n\n".join(answer),
                  tool_calls=tool_calls, bash=bash, frozen=frozen, stale=stale)


class Sandbox:
    """One container from the sandbox image, for one screened answer.

    Run as 001 §8.2 runs a session's: no network at all, no credentials, the
    workspace bind-mounted, and the same resource limits as docker/spawn.sh.
    Commands are the agent's own, so they run here and never on the host.
    """

    def __init__(self, image: str, workspace: Path, capabilities: str = "k8stools"):
        self.workspace = workspace
        self.name = f"k8srca-screen-{uuid.uuid4().hex[:12]}"
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", self.name,
             "--user", f"{os.getuid()}:{os.getgid()}",
             "--network", "none", "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
             "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev",
             "-v", f"{workspace.resolve()}:/workspace",
             "-e", f"K8SRCA_CAPABILITIES={capabilities}",
             "--entrypoint", "sleep", image, "infinity"],
            check=True, capture_output=True, text=True)

    def bash(self, command: str) -> tuple[str, bool]:
        try:
            p = subprocess.run(["docker", "exec", "-w", "/workspace", self.name,
                                "bash", "-c", command],
                               capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return f"command timed out after {COMMAND_TIMEOUT_S}s", True
        out = (p.stdout + p.stderr)[:RESULT_CHARS]
        return out or "(no output)", p.returncode != 0

    def tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        q = lambda s: "'" + str(s).replace("'", "'\\''") + "'"  # noqa: E731
        if name == "bash":
            return self.bash(arguments.get("command", ""))
        if name == "read":
            start = int(arguments.get("offset") or 1)
            stop = start + int(arguments.get("limit") or 2000) - 1
            return self.bash(f"cat -n {q(arguments['file_path'])} | sed -n '{start},{stop}p'")
        if name == "grep":
            return self.bash(f"grep -rnE -- {q(arguments['pattern'])} "
                             f"{q(arguments.get('path') or '.')}")
        if name == "glob":
            return self.bash(f"cd {q(arguments.get('path') or '.')} && "
                             f"python3 -c 'import glob,sys; print(\"\\n\".join(sorted("
                             f"glob.glob(sys.argv[1], recursive=True))))' "
                             f"{q(arguments['pattern'])}")
        raise KeyError(name)

    def close(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


def sandbox_image(repository: str) -> str:
    """The sandbox image for this tree if it is built, else the newest one.

    The screen uses the image only as a place to run the agent's commands,
    with python and bash and the sandbox's limits; the skills are mounted, not
    baked in. So a tree with uncommitted changes, whose own tag is never built,
    can still be screened.
    """
    from .. import sandbox as sbx

    ref = sbx.image_ref(repository)
    if sbx.image_exists(ref):
        return ref
    tags = subprocess.run(["docker", "images", repository, "--format", "{{.Tag}}"],
                          capture_output=True, text=True).stdout.split()
    if not tags:
        raise RuntimeError(f"no {repository} image is built (uv run k8srca sandbox build)")
    return f"{repository}:{tags[0]}"


def workspace(root: Path, k8s_rca: Path, architecture: Path,
              evidence: Path | None, session_id: str) -> Path:
    """A fresh workspace laid out as a session's: the skills under test, and
    the session's evidence record for the reviewer. No `open` records: those
    come from this replay's own `kb_query.py open` runs."""
    ws = Path(tempfile.mkdtemp(prefix="screen-", dir=root))
    shutil.copytree(k8s_rca, ws / "skills" / "k8s-rca")
    shutil.copytree(architecture, ws / "skills" / "cluster-architecture")
    if evidence is not None and evidence.is_dir():
        shutil.copytree(evidence, ws / "evidence" / session_id)
    (ws / "evidence").mkdir(exist_ok=True)
    return ws


def refresh(prefix: Prefix, sandbox: Sandbox) -> int:
    """Re-run the cut-off main thread's bash in order; replace the output of
    each command that touches a skill. Returns how many were replaced."""
    replaced = {}
    for tid, command in prefix.bash:
        out, err = sandbox.bash(command)
        if TOUCHES_SKILL.search(command):
            replaced[tid] = (out, err)
    for m in prefix.messages:
        if m["role"] != "user":
            continue
        for b in m["content"]:
            if b["type"] == "tool_result" and b["tool_use_id"] in replaced:
                out, err = replaced[b["tool_use_id"]]
                b["content"] = [{"type": "text", "text": out}]
                b["is_error"] = err
    return len(replaced)


def tools_for(session: dict) -> list[dict]:
    """The coordinator's tools as the session declared them."""
    custom = [{"name": t["name"], "description": t.get("description", ""),
               "input_schema": t.get("input_schema") or {"type": "object"}}
              for t in session["agent"].get("tools") or [] if t.get("type") == "custom"]
    return BUILTIN_TOOLS + custom


def _cached(messages: list[dict]) -> list[dict]:
    """A copy with a cache breakpoint on the last block: across rounds, and
    across arms screening the same session, the prefix is read from cache."""
    out = [dict(m, content=[dict(b) for b in m["content"]]) for m in messages]
    out[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
    return out


@dataclass
class Usage:
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0

    def add(self, u: Any) -> None:
        self.input += getattr(u, "input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.output += getattr(u, "output_tokens", 0) or 0


@dataclass
class Turn:
    answer: str = ""
    tool_calls: list[str] = field(default_factory=list)
    refused: int = 0


def converse(client: Any, model: dict, system: str, tools: list[dict],
             messages: list[dict], sandbox: Sandbox, frozen: dict[str, list[dict]],
             usage: Usage) -> Turn:
    """Let the model go on from `messages` (extended in place) until it answers."""
    turn = Turn()
    names = {t["name"] for t in BUILTIN_TOOLS}
    params: dict[str, Any] = {"thinking": {"type": "adaptive"}}
    effort = (model.get("effort") or {}).get("type")
    if effort:
        params["output_config"] = {"effort": effort}
    for _ in range(MAX_ROUNDS):
        with client.messages.stream(model=model["id"], max_tokens=32000, system=system,
                                    tools=tools, messages=_cached(messages), **params) as s:
            r = s.get_final_message()
        usage.add(r.usage)
        messages.append({"role": "assistant",
                         "content": [b.model_dump(exclude_none=True) for b in r.content]})
        uses = [b for b in r.content if b.type == "tool_use"]
        if not uses:
            turn.answer = "\n\n".join(b.text for b in r.content if b.type == "text")
            return turn
        results = []
        for u in uses:
            turn.tool_calls.append(u.name)
            if u.name in names:
                out, err = sandbox.tool(u.name, u.input or {})
                content = [{"type": "text", "text": out}]
            elif _key(u.name, u.input or {}) in frozen:
                content, err = frozen[_key(u.name, u.input or {})], False
            else:
                content, err = [{"type": "text", "text": REFUSED}], True
                turn.refused += 1
            results.append({"type": "tool_result", "tool_use_id": u.id,
                            "content": content, "is_error": err})
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"no answer after {MAX_ROUNDS} rounds")


def screen_once(sd: Any, record: dict, *, client: Any, cfg: Any, image: str,
                k8s_rca: Path, work: Path, grader_model: str | None = None,
                grades: int | None = None) -> Any:
    """One saved run's investigation, answered again, checked, revised and graded."""
    from ..kb.skills import bundle_digest
    from ..review import check
    from .checks import run_all
    from .grader import grade
    from .runner import Run

    sid = record["session_id"]
    saved = fetch(client, sid)
    prefix = cut(saved["events"])
    session = saved["session"]
    system = SYSTEM_PROMPT.read_text() if SYSTEM_PROMPT.exists() else session["agent"]["system"]
    tools = tools_for(session)
    run = Run(scenario_id=sd.scenario.id, answer="", session_id=sid, arch=sd.arch,
              local_skills={"k8s-rca": bundle_digest(k8s_rca)},
              skill_digest=f"screen:{bundle_digest(k8s_rca)}",
              capture_captured_at=sd.truth.capture.captured_at,
              truth_digest=sd.truth_sha())
    usage = Usage()
    ws = workspace(work, k8s_rca, sd.skill_dir,
                   cfg.sandbox.workspaces_dir() / sid / "evidence" / sid, sid)
    box = Sandbox(image, ws, ",".join(cfg.capabilities()))
    info: dict[str, Any] = {"source": sid, "stale": prefix.stale,
                            "source_first_answer": prefix.first_answer}
    try:
        info["refreshed"] = refresh(prefix, box)
        messages = prefix.messages
        first = converse(client, session["agent"]["model"], system, tools, messages,
                         box, prefix.frozen, usage)
        run.answer = first.answer
        run.tool_calls = prefix.tool_calls + first.tool_calls
        refused = first.refused
        review = (check(run.answer, ws, sid, cfg.review, client)
                  if cfg.review.completeness or cfg.review.reviewer else None)
        if review is not None:
            run.review = review.as_dict()
            if review.prompt:
                messages.append({"role": "user",
                                 "content": [{"type": "text", "text": review.prompt}]})
                nxt = converse(client, session["agent"]["model"], system, tools, messages,
                               box, prefix.frozen, usage)
                run.tool_calls += nxt.tool_calls
                refused += nxt.refused
                if nxt.answer.strip():
                    run.first_answer, run.answer = run.answer, nxt.answer
        info["refused"] = refused
    finally:
        box.close()
        shutil.rmtree(ws, ignore_errors=True)
    info["usage"] = usage.__dict__
    run.screen = info
    run.checks = run_all(sd, run.answer, run.tool_calls, len(run.tool_calls), 0.0)
    if run.answer.strip():
        kwargs: dict[str, Any] = {"model": grader_model} if grader_model else {}
        if grades:
            kwargs["grades"] = grades
        run.grade = grade(sd, run.answer, run.tool_calls, client=client, **kwargs)
    return run
