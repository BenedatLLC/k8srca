"""A session's facts: numbered, saved beside the evidence record (design 010 §3.1)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..evidence import DIRNAME
from .derive import derive
from .model import Fact

log = logging.getLogger(__name__)

FILENAME = "facts.jsonl"


class FactLog:
    """`<workdir>/evidence/facts.jsonl`, where `kb_query.py` reads it. Ids run on
    across the turns of a thread; a fact already recorded keeps its id."""

    def __init__(self, workdir: str | os.PathLike[str]):
        self.path = Path(workdir) / DIRNAME / FILENAME
        self.facts: list[Fact] = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    self.facts.append(Fact.from_dict(json.loads(line)))
                except (ValueError, TypeError):
                    continue

    def add(self, facts: list[Fact], source: dict | None = None) -> list[Fact]:
        """Number and save `facts`, returning each as recorded."""
        out, new = [], []
        for f in facts:
            known = next((k for k in self.facts if k.same(f)), None)
            if known is not None:
                out.append(known)
                continue
            f.id = f"F{len(self.facts) + 1}"
            f.source = f.source or dict(source or {})
            self.facts.append(f)
            new.append(f)
            out.append(f)
        if new:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as fh:
                    for f in new:
                        fh.write(json.dumps(f.as_dict(), default=str) + "\n")
            except OSError as exc:
                log.warning("facts_write_failed error=%s", exc)
        return out

    def observe(self, tool: str, remote: str, arguments: dict, result: Any,
                n: int | None = None) -> list[Fact]:
        """The facts in one MCP result. Never raises: a derivation that fails
        leaves the result as it was."""
        try:
            structured = _structured(result)
            facts = derive(remote, arguments, structured) if structured is not None else []
        except Exception:  # noqa: BLE001 - facts are an addition, never a failure
            log.exception("facts_derive_failed tool=%s", tool)
            return []
        return self.add(facts, {"tool": tool, **({"n": n} if n is not None else {})})


def block(facts: list[Fact]) -> str:
    """The facts as the agent reads them, above the raw result."""
    if not facts:
        return ""
    lines = [f"[{f.id}] {f.statement}" + (f" ({f.scope})" if f.scope else "") for f in facts]
    return "facts (cite as [F<n>]):\n" + "\n".join(lines)


def _structured(result: Any) -> Any:
    for name in ("structured_content", "structuredContent"):
        value = getattr(result, name, None)
        if value is not None:
            return value
    if isinstance(result, (dict, list)):
        return result
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return None
    return None
