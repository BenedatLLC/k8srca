"""The frozen-evidence screen's replay (004 §6.4), without a model or a container."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from k8srca.scenario import screen
from k8srca.scenario.screen import Prefix, Unscreenable, Usage, converse, cut, refresh


def ev(type_, **kw):
    return {"type": type_, "id": kw.pop("id", f"e{id(kw)}"), **kw}


def text(s):
    return [{"type": "text", "text": s}]


def session(*, answer="the answer", revision=True):
    events = [
        ev("user.message", content=text("why is checkout slow?")),
        ev("agent.message", content=text("Looking.")),
        ev("agent.tool_use", id="b1", name="bash",
           input={"command": "cat /workspace/skills/k8s-rca/SKILL.md"}),
        ev("agent.custom_tool_use", id="c1", name="k8s_get_events", input={"namespace": "d"}),
        ev("session.status_idle", stop_reason={"type": "requires_action"}),
        ev("user.tool_result", tool_use_id="b1", content=text("OLD SKILL")),
        ev("user.custom_tool_result", custom_tool_use_id="c1", content=text("events")),
        ev("agent.tool_use", id="b2", name="bash", input={"command": "echo x > /tmp/f"}),
        ev("user.tool_result", tool_use_id="b2", content=text("")),
        ev("agent.thread_message_sent", to_agent_name="k8s-investigator",
           to_session_thread_id="t1", content=text("read the logs")),
        ev("agent.message", content=text("Still waiting on the investigator.")),
        ev("agent.custom_tool_use", id="c2", name="k8s_get_logs", input={"pod": "p"},
           session_thread_id="t1"),
        ev("user.custom_tool_result", custom_tool_use_id="c2", content=text("log lines"),
           session_thread_id="t1"),
        ev("agent.thread_message_received", from_agent_name="k8s-investigator",
           from_session_thread_id="t1", content=text("logs are clean")),
        ev("agent.message", content=text(answer)),
        ev("session.status_idle", stop_reason={"type": "end_turn"}),
    ]
    if revision:
        events += [ev("user.message", content=text("Before this answer goes out...")),
                   ev("agent.message", content=text("revised")),
                   ev("session.status_idle", stop_reason={"type": "end_turn"})]
    return events


class TestCut:
    def test_stops_before_the_first_answer(self):
        p = cut(session())
        assert p.first_answer == "the answer"
        assert p.messages[-1]["role"] == "user"
        flat = str(p.messages)
        assert "revised" not in flat and "Before this answer" not in flat

    def test_roles_alternate_and_every_tool_use_has_its_result_next(self):
        p = cut(session())
        roles = [m["role"] for m in p.messages]
        assert all(a != b for a, b in zip(roles, roles[1:]))
        for m, nxt in zip(p.messages, p.messages[1:]):
            uses = {b["id"] for b in m["content"] if b["type"] == "tool_use"}
            got = {b["tool_use_id"] for b in nxt["content"] if b["type"] == "tool_result"}
            assert uses <= got

    def test_delegation_is_the_message_sent_and_the_report_received(self):
        flat = str(cut(session()).messages)
        assert "[message to k8s-investigator]" in flat
        assert "[report from k8s-investigator]" in flat and "logs are clean" in flat
        # The investigator's own calls are its business; the coordinator saw the report.
        assert "log lines" not in flat

    def test_interim_notes_while_waiting_are_dropped(self):
        assert "Still waiting" not in str(cut(session()).messages)

    def test_cluster_results_from_every_thread_are_kept_for_repeat_calls(self):
        p = cut(session())
        assert screen._key("k8s_get_events", {"namespace": "d"}) in p.frozen
        assert screen._key("k8s_get_logs", {"pod": "p"}) in p.frozen

    def test_tool_calls_count_every_thread_like_a_live_run(self):
        assert cut(session()).tool_calls == ["bash", "k8s_get_events", "bash", "k8s_get_logs"]

    def test_bash_is_listed_in_order_for_re_running(self):
        assert [c for _, c in cut(session()).bash] == [
            "cat /workspace/skills/k8s-rca/SKILL.md", "echo x > /tmp/f"]

    def test_a_session_that_never_answered_cannot_be_screened(self):
        events = [e for e in session(revision=False)
                  if e.get("stop_reason") != {"type": "end_turn"}]
        with pytest.raises(Unscreenable):
            cut(events)


class FakeBox:
    def __init__(self):
        self.ran = []

    def bash(self, command):
        self.ran.append(command)
        return ("NEW SKILL" if "SKILL.md" in command else "side effect only"), False

    def tool(self, name, arguments):
        return self.bash(arguments.get("command", f"{name} {arguments}"))


class TestRefresh:
    def test_every_command_re_runs_and_only_skill_output_is_replaced(self):
        p = cut(session())
        box = FakeBox()
        assert refresh(p, box) == 1
        assert box.ran == ["cat /workspace/skills/k8s-rca/SKILL.md", "echo x > /tmp/f"]
        flat = str(p.messages)
        assert "NEW SKILL" in flat and "OLD SKILL" not in flat
        assert "side effect only" not in flat


def block(type_, **kw):
    b = SimpleNamespace(type=type_, **kw)
    b.model_dump = lambda exclude_none=True: {"type": type_, **kw}
    return b


class FakeClient:
    """Answers with the queued content lists, one per call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kw):
        self.sent.append(kw)
        msg = SimpleNamespace(content=self.replies.pop(0), usage=SimpleNamespace(
            input_tokens=1, cache_creation_input_tokens=2, cache_read_input_tokens=3,
            output_tokens=4))
        return nullcontext(SimpleNamespace(get_final_message=lambda: msg))


class TestConverse:
    def run(self, client, frozen=None):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "q"}]}]
        usage = Usage()
        turn = converse(client, {"id": "m", "effort": {"type": "high"}}, "sys",
                        screen.BUILTIN_TOOLS, msgs, FakeBox(), frozen or {}, usage)
        return turn, msgs, usage

    def test_a_repeat_cluster_call_is_served_and_a_new_one_refused(self):
        frozen = {screen._key("k8s_get_events", {"namespace": "d"}): text("events")}
        c = FakeClient(
            [block("tool_use", id="u1", name="k8s_get_events", input={"namespace": "d"}),
             block("tool_use", id="u2", name="k8s_get_events", input={"namespace": "x"})],
            [block("text", text="final")])
        turn, msgs, usage = self.run(c, frozen)
        assert turn.answer == "final" and turn.refused == 1
        results = {b["tool_use_id"]: b for b in msgs[2]["content"]}
        assert results["u1"]["content"] == text("events") and not results["u1"]["is_error"]
        assert results["u2"]["is_error"] and "Not available" in str(results["u2"]["content"])
        assert usage.output == 8 and usage.cache_read == 6

    def test_bash_runs_in_the_sandbox(self):
        c = FakeClient([block("tool_use", id="u1", name="bash",
                              input={"command": "cat skills/k8s-rca/SKILL.md"})],
                       [block("text", text="done")])
        turn, msgs, _ = self.run(c)
        assert msgs[2]["content"][0]["content"] == text("NEW SKILL")
        assert turn.tool_calls == ["bash"] and turn.refused == 0

    def test_the_prefix_is_cached_and_effort_passed(self):
        c = FakeClient([block("text", text="a")])
        self.run(c)
        sent = c.sent[0]
        assert sent["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert sent["output_config"] == {"effort": "high"}
        assert sent["thinking"] == {"type": "adaptive"}


def test_tools_are_the_sessions_custom_declarations_plus_the_builtins():
    s = {"agent": {"tools": [{"type": "agent_toolset_20260401"},
                             {"type": "custom", "name": "k8s_get_events", "description": "d",
                              "input_schema": {"type": "object", "properties": {}}}]}}
    names = [t["name"] for t in screen.tools_for(s)]
    assert names == ["bash", "read", "grep", "glob", "k8s_get_events"]


def test_prefix_dataclass_defaults():
    p = Prefix(question="q", messages=[], first_answer="", tool_calls=[], bash=[], frozen={})
    assert p.stale == []
