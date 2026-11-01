"""The MCP client against a real server on loopback: results over the SDK's
1 MiB event limit arrive whole (issue #12).

In design 012 step 1, 7 long log reads failed with "Server-sent event exceeded
the 1048576 byte limit". The server here streams its results as k8stools does,
so the first test fails if `connect` goes back to the SDK's default, and the
second shows the default still refuses the same result.
"""

import socket
import threading
import time

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer

from k8srca.config import McpServer
from k8srca.mcp_client import MAX_RESULT_BYTES, connect

BIG = 3 * 1024 * 1024  # a full log read, as k8stools returns it, is 2-3 MiB


@pytest.fixture(scope="module")
def url():
    server = MCPServer("big")

    @server.tool()
    def big(n: int) -> str:
        """n bytes of log-like text."""
        line = "2025-12-15T17:51:21Z GET /api/cart 503 UC upstream_reset\n"
        return (line * (n // len(line) + 1))[:n]

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port,
                            log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline, "test server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    thread.join(timeout=5)


async def test_a_result_over_one_mib_arrives_whole(url):
    async with connect(McpServer(name="big", url=url)) as srv:
        result = await srv.session.call_tool("big", {"n": BIG})
    assert not result.is_error
    assert len(result.content[0].text) == BIG


async def test_the_sdk_default_refuses_it(url):
    """The limit is real: without ours, the same call fails."""
    assert MAX_RESULT_BYTES > 2 * BIG
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1], read_timeout_seconds=30.0) as session:
            await session.initialize()
            with pytest.raises(Exception, match="byte limit"):
                await session.call_tool("big", {"n": BIG})
