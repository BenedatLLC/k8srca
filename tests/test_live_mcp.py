"""Live round-trip against a real k8stools MCP server in --mock mode.

This is the test for the highest-risk assumption in design 001 (F1): that the
worker can serve an MCP server's tools as prefixed custom tools. It is skipped
unless K8SRCA_TEST_MCP_URL points at a running server.

    k8s-mcp-server --transport=streamable-http --port 8009 --mock &
    K8SRCA_TEST_MCP_URL=http://127.0.0.1:8009/mcp pytest tests/test_live_mcp.py
"""

import os

import pytest

from k8srca import tools as T
from k8srca.config import McpServer
from k8srca.mcp_client import connect

URL = os.environ.get("K8SRCA_TEST_MCP_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set K8SRCA_TEST_MCP_URL to run")


def spec(**kw):
    return McpServer(name="k8stools", url=URL, prefix="k8s_", **kw)


async def test_every_tool_yields_a_legal_declaration():
    """No k8stools schema may be unrepresentable as a custom tool."""
    async with connect(spec()) as srv:
        decls = T.declarations(srv.tools, srv.spec.prefix)  # raises on any illegal schema
        assert len(decls) == len(srv.tools) > 0
        assert all(d["name"].startswith("k8s_") for d in decls)


async def test_no_declaration_retains_a_top_level_union():
    """The Optional-collapse must leave nothing the API would reject."""
    async with connect(spec()) as srv:
        for d in T.declarations(srv.tools, srv.spec.prefix):
            for forbidden in ("anyOf", "oneOf", "allOf", "$ref"):
                assert forbidden not in d["input_schema"], f"{d['name']} kept {forbidden}"


async def test_prefixed_wrapper_calls_the_unprefixed_remote_tool():
    """The core of design 001 §4.2.

    async_mcp_tool cannot do this — it closes over tool.name for both the
    declared name and the outbound call. Our wrapper splits them, so the model
    sees `k8s_get_namespaces` while the server receives `get_namespaces`.
    """
    async with connect(spec()) as srv:
        tool = next(t for t in srv.tools if t.name == "get_namespaces")
        wrapped = T.wrap_mcp_tool(tool, srv.session, prefix="k8s_")

        assert wrapped.name == "k8s_get_namespaces"
        result = await wrapped.call({})
        assert result, "wrapper returned nothing; the remote call did not land"


async def test_wrapper_passes_arguments_through():
    async with connect(spec()) as srv:
        tool = next(t for t in srv.tools if t.name == "get_pod_summaries")
        wrapped = T.wrap_mcp_tool(tool, srv.session, prefix="k8s_")
        assert await wrapped.call({"namespace": "default"})


async def test_config_group_resolves_against_the_real_surface():
    """Guards against a triage tool being renamed out from under k8srca.yaml."""
    from k8srca.config import load

    cfg = load()
    async with connect(spec()) as srv:
        decls = T.declarations(srv.tools, srv.spec.prefix)
        triage = T.select(decls, cfg.server("k8stools").group("triage"), "k8s_")
        assert len(triage) == 7
