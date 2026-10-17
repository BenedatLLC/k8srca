"""The evidence record: every MCP tool result, saved for scripts (design 007 §6.1).

The model sees each tool result once, in its context. A script in the sandbox
sees nothing unless the model types it in again, so nothing can check an
answer against what the tools actually returned. The worker's tool wrapper
also writes each result here, one JSON file per call:

    <workdir>/evidence/<session id>/<n>-<tool>.json

``n`` counts from 1 across the session's turns. A container serves one turn,
so the count resumes from the files already there. The record takes nothing
the model did not already receive: results have passed the MCP server's
redaction, and no new cluster access is involved (CLAUDE.md). It lives in the
session's workspace, with the investigation record, and is reaped with it.

Writing is best-effort. A record that cannot be written is logged, and the
tool call still succeeds: losing a check is better than losing the
investigation.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DIRNAME = "evidence"
_FILE = re.compile(r"^(\d+)-")


def _field(result: Any, *names: str) -> Any:
    """mcp<2 uses camelCase attributes, mcp>=2 snake_case (as in tools.py)."""
    for n in names:
        if hasattr(result, n):
            return getattr(result, n)
    return None


def _text(item: Any) -> str | None:
    return getattr(item, "text", None)


class EvidenceRecord:
    """One session's record. Not thread-safe; the worker is one event loop."""

    def __init__(self, workdir: str | os.PathLike[str], session_id: str):
        self.root = Path(workdir) / DIRNAME / session_id
        self.root.mkdir(parents=True, exist_ok=True)
        done = [int(m.group(1)) for p in self.root.iterdir() if (m := _FILE.match(p.name))]
        self._n = itertools.count(max(done, default=0) + 1)

    def write(self, tool: str, arguments: dict[str, Any], result: Any = None,
              error: BaseException | None = None) -> Path | None:
        """Save one call: the MCP result, or the error the call raised."""
        n = next(self._n)
        entry: dict[str, Any] = {
            "n": n,
            "tool": tool,
            "arguments": arguments,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if error is not None:
            entry["error"] = f"{type(error).__name__}: {error}"
        else:
            entry["is_error"] = bool(_field(result, "is_error", "isError"))
            entry["text"] = [t for t in map(_text, _field(result, "content") or []) if t is not None]
            structured = _field(result, "structured_content", "structuredContent")
            if structured is not None:
                entry["structured"] = structured
        path = self.root / f"{n:04d}-{re.sub(r'[^A-Za-z0-9_.-]', '_', tool)}.json"
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, default=str, ensure_ascii=False))
            tmp.replace(path)  # a reader never sees half a file
            return path
        except OSError as exc:
            log.warning("evidence_write_failed tool=%s n=%d error=%s", tool, n, exc)
            return None
