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


@dataclass
class ConnectedServer:
    spec: McpServer
    session: ClientSession
    tools: list[Any]


@contextlib.asynccontextmanager
async def connect(spec: McpServer) -> AsyncIterator[ConnectedServer]:
    """Open one MCP session and list its tools."""
    async with streamable_http_client(spec.url) as streams:
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
