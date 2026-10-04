"""The MCP client dkgg needs: call a k8stools tool and get rows back.

dkgg reads a cluster only through a k8stools MCP server, never with a
Kubernetes client (docs/design.md, principle 6). Results are parsed as k8srca's
generator always parsed them: each text block of a tool result is one JSON row,
and blocks that are not JSON are skipped.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, AsyncIterator, Awaitable, Callable

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

#: Call a tool by name with keyword arguments; get its rows.
Caller = Callable[..., Awaitable[list[dict]]]


@contextlib.asynccontextmanager
async def connect(url: str, timeout_s: float = 60.0) -> AsyncIterator[tuple[set[str], Caller]]:
    """Open one session; yield the server's tool names and a caller."""
    async with streamable_http_client(url) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write, read_timeout_seconds=float(timeout_s)) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}

            # Positional-only: a tool may take an argument called `name`
            # (get_configmap does), which must not collide with the tool's.
            async def call(tool: str, /, **kwargs: Any) -> list[dict]:
                result = await session.call_tool(name=tool, arguments=kwargs)
                return rows(result)

            yield names, call


def rows(result: Any) -> list[dict]:
    """The JSON rows in a tool result: one per text block."""
    out = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) != "text":
            continue
        try:
            out.append(json.loads(block.text))
        except json.JSONDecodeError:
            continue
    return out
