"""Connecting to the configured MCP servers.

Used by both halves of design 001 §4.2: `sync` connects to enumerate tools and
generate declarations; the worker connects to serve them.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .config import McpServer

#: The largest tool result the client accepts, as one streamed event. The SDK's
#: default is 1 MiB, which a full log read crosses: k8stools keeps each read
#: within about 1 MiB of log, and the result carries it twice (text and
#: structured content), JSON-escaped. In design 012 step 1, 7 reads failed on it
#: (issue #12). Large enough for that with room to spare, and still a bound.
MAX_RESULT_BYTES = 8 * 1024 * 1024


@dataclass
class ConnectedServer:
    spec: McpServer
    session: ClientSession
    tools: list[Any]


@contextlib.asynccontextmanager
async def connect(spec: McpServer) -> AsyncIterator[ConnectedServer]:
    """Open one MCP session and list its tools."""
    async with streamable_http_client(spec.url, max_sse_event_size=MAX_RESULT_BYTES) as streams:
        # mcp>=2 yields (read, write); mcp<2 yielded (read, write, get_session_id)
        read, write = streams[0], streams[1]
        # mcp>=2 takes a float here; mcp<2 took a timedelta.
        async with ClientSession(read, write, read_timeout_seconds=float(spec.timeout_s)) as session:
            await session.initialize()
            listed = await session.list_tools()
            yield ConnectedServer(spec=spec, session=session, tools=list(listed.tools))


@contextlib.asynccontextmanager
async def connect_all(specs: list[McpServer]) -> AsyncIterator[list[ConnectedServer]]:
    """Open every configured server, keeping all sessions live together."""
    async with contextlib.AsyncExitStack() as stack:
        connected = [await stack.enter_async_context(connect(s)) for s in specs]
        yield connected
