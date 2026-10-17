"""The evidence record (design 007 §6.1): every MCP result saved for scripts,
without changing what the model receives."""

import asyncio
import json
from types import SimpleNamespace

import mcp.types as mt
import pytest

from k8srca.evidence import EvidenceRecord
from k8srca.tools import wrap_mcp_tool
from k8srca.worker.runner import session_record

TOOL = mt.Tool(name="get_events", description="events",
               input_schema={"type": "object", "properties": {"namespace": {"type": "string"}}})


class FakeSession:
    """An MCP client session that answers every call with one result, or raises."""

    def __init__(self, result=None, raises=None):
        self.result, self.raises, self.calls = result, raises, []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raises:
            raise self.raises
        return self.result


def result(text="3 events", structured=None, is_error=False):
    return mt.CallToolResult(content=[mt.TextContent(type="text", text=text)],
                             structured_content=structured, is_error=is_error)


def call(tool, **kwargs):
    return asyncio.run(tool.call(kwargs))


def saved(record):
    return [json.loads(p.read_text()) for p in sorted(record.root.glob("*.json"))]


class TestEvidenceRecord:
    def test_a_call_is_saved_with_what_the_model_saw(self, tmp_path):
        record = EvidenceRecord(tmp_path, "sesn_1")
        session = FakeSession(result("3 events", structured={"events": [{"count": 3}]}))
        tool = wrap_mcp_tool(TOOL, session, prefix="k8s_", record=record)
        call(tool, namespace="default")
        [entry] = saved(record)
        assert entry["n"] == 1 and entry["tool"] == "k8s_get_events"
        assert entry["arguments"] == {"namespace": "default"}
        assert entry["text"] == ["3 events"]
        assert entry["structured"] == {"events": [{"count": 3}]}
        assert record.root == tmp_path / "evidence" / "sesn_1"

    def test_the_model_receives_the_same_result_with_or_without_a_record(self, tmp_path):
        r = result("3 events")
        plain = call(wrap_mcp_tool(TOOL, FakeSession(r), prefix="k8s_"))
        recorded = call(wrap_mcp_tool(TOOL, FakeSession(r), prefix="k8s_",
                                      record=EvidenceRecord(tmp_path, "s")))
        assert plain == recorded

    def test_numbering_resumes_across_turns(self, tmp_path):
        """A container serves one turn; the next turn's container continues the count."""
        tool = wrap_mcp_tool(TOOL, FakeSession(result()), record=EvidenceRecord(tmp_path, "s"))
        call(tool)
        call(tool)
        record = EvidenceRecord(tmp_path, "s")
        call(wrap_mcp_tool(TOOL, FakeSession(result()), record=record))
        assert [e["n"] for e in saved(record)] == [1, 2, 3]

    def test_a_failed_call_is_saved_and_still_raises(self, tmp_path):
        record = EvidenceRecord(tmp_path, "s")
        tool = wrap_mcp_tool(TOOL, FakeSession(raises=RuntimeError("connection reset")),
                             record=record)
        with pytest.raises(Exception):
            call(tool)
        [entry] = saved(record)
        assert entry["error"] == "RuntimeError: connection reset"

    def test_an_error_result_is_marked(self, tmp_path):
        record = EvidenceRecord(tmp_path, "s")
        with pytest.raises(Exception):
            call(wrap_mcp_tool(TOOL, FakeSession(result("not found", is_error=True)),
                               record=record))
        [entry] = saved(record)
        assert entry["is_error"] is True and entry["text"] == ["not found"]

    def test_a_record_that_cannot_be_written_does_not_fail_the_call(self, tmp_path, monkeypatch):
        """Losing a check is better than losing the investigation."""
        record = EvidenceRecord(tmp_path, "s")
        monkeypatch.setattr("pathlib.Path.write_text",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        assert call(wrap_mcp_tool(TOOL, FakeSession(result("ok")), record=record))
        assert record.write("t", {}, result()) is None

    def test_sessions_sharing_a_workdir_keep_separate_records(self, tmp_path):
        """The in-process worker serves every session from one workdir."""
        a, b = EvidenceRecord(tmp_path, "sesn_a"), EvidenceRecord(tmp_path, "sesn_b")
        call(wrap_mcp_tool(TOOL, FakeSession(result()), record=a))
        assert saved(a) and not saved(b)


class TestSessionRecord:
    def test_bound_to_the_context_session(self, tmp_path):
        env = SimpleNamespace(workdir=str(tmp_path), session=SimpleNamespace(id="sesn_9"))
        assert session_record(env).root == tmp_path / "evidence" / "sesn_9"

    def test_no_session_means_no_record_not_a_failure(self, tmp_path):
        assert session_record(SimpleNamespace(workdir=str(tmp_path), session=None)) is None
