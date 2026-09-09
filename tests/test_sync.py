"""Sync planning and payload construction — no control-plane calls."""

from pathlib import Path

import pytest

from k8srca import sync as S
from k8srca.config import AgentConfig, Config, McpServer, ModelConfig, SandboxConfig
from k8srca.state import AgentState, State


def agent(role, model="claude-sonnet-5", roster=(), **kw):
    return AgentConfig(role=role, model=ModelConfig(id=model),
                       system_prompt=Path("nope.md"), roster=list(roster), **kw)


def cfg(**overrides):
    base = dict(
        mcp=[McpServer(name="k8stools", url="http://k8stools:8000/mcp", prefix="k8s_",
                       groups={"triage": ["a"], "full": "*"})],
        agents={"coord": agent("coordinator", roster=["spec", "self"]),
                "spec": agent("spec" if False else "specialist", model="claude-haiku-4-5")},
        sandbox=SandboxConfig(image="img"),
    )
    base.update(overrides)
    return Config(**base)


class TestBuiltinToolset:
    def test_is_opt_in_not_opt_out(self):
        ts = S.builtin_toolset(["read", "bash"])
        assert ts["default_config"]["enabled"] is False
        assert [c["name"] for c in ts["configs"]] == ["read", "bash"]

    def test_web_tools_are_never_enabled_implicitly(self):
        # They run on Anthropic's servers regardless of environment type, so
        # per-tool disablement is the only control (001 §8.3).
        names = {c["name"] for c in S.builtin_toolset(["read", "grep"])["configs"]}
        assert not names & {"web_search", "web_fetch"}


class TestSyncOrder:
    def test_specialists_come_before_the_coordinator(self):
        # The roster references specialists by id, so they must exist first.
        order = cfg().sync_order()
        assert order.index("spec") < order.index("coord")


class TestRoster:
    def test_self_stays_symbolic(self):
        state = State(agents={"spec": AgentState(id="agent_s", version=4, manifest="m", model="x")})
        assert S.resolve_roster(cfg(), "coord", state) == [
            {"type": "agent", "id": "agent_s", "version": 4},
            {"type": "self"},
        ]

    def test_pins_the_specialist_version_just_synced(self):
        # Updating a specialist must re-pin the coordinator, or the roster
        # keeps pointing at the old version (001 §6).
        state = State(agents={"spec": AgentState(id="agent_s", version=9, manifest="m", model="x")})
        assert S.resolve_roster(cfg(), "coord", state)[0]["version"] == 9

    def test_unsynced_member_is_a_clear_error(self):
        with pytest.raises(RuntimeError, match="sync-order bug"):
            S.resolve_roster(cfg(), "coord", State())


class TestSystemPrompt:
    def test_placeholder_only_file_counts_as_absent(self, tmp_path):
        # An HTML-comment placeholder must not be sent as a real system prompt.
        f = tmp_path / "p.md"
        f.write_text("<!-- PLACEHOLDER\n  multi-line\n-->\n")
        assert S.read_system_prompt(agent("coordinator").model_copy(update={"system_prompt": f})) is None

    def test_real_content_is_returned(self, tmp_path):
        f = tmp_path / "p.md"
        f.write_text("<!-- note -->\nYou are an RCA coordinator.\n")
        assert S.read_system_prompt(
            agent("coordinator").model_copy(update={"system_prompt": f})
        ) == "You are an RCA coordinator."


class TestPayload:
    def test_omits_multiagent_for_a_specialist(self):
        p = S.PlannedAgent(key="spec", cfg=cfg().agents["spec"], tools=[], manifest="abc", skills=[])
        assert "multiagent" not in S.agent_payload(p, [], None)

    def test_records_manifest_and_rev_in_metadata(self):
        p = S.PlannedAgent(key="spec", cfg=cfg().agents["spec"], tools=[], manifest="abc", skills=[])
        meta = S.agent_payload(p, [], "deadbee")["metadata"]
        assert meta == {"manifest": "abc", "config_rev": "deadbee"}


class TestHostUrl:
    def test_sync_url_overrides_sandbox_url(self):
        s = McpServer(name="k8stools", url="http://k8stools:8000/mcp",
                      sync_url="http://127.0.0.1:8000/mcp")
        assert s.host_url() == "http://127.0.0.1:8000/mcp"
        assert s.url == "http://k8stools:8000/mcp"  # unchanged for the worker

    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("K8SRCA_MCP_K8STOOLS_URL", "http://elsewhere/mcp")
        s = McpServer(name="k8stools", url="http://k8stools:8000/mcp",
                      sync_url="http://127.0.0.1:8000/mcp")
        assert s.host_url() == "http://elsewhere/mcp"

    def test_defaults_to_url(self):
        assert McpServer(name="x", url="http://u/mcp").host_url() == "http://u/mcp"
