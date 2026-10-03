"""Architecture notes from runbooks and upstream documentation.

Answers what neither the cluster nor the charts can: why a service exists, who
owns it, what it is expected to do, and what happens to the system when it
fails. Not how to diagnose it: that is the k8s-rca skill's job, and notes that
carry it, or that describe current failure behaviour, mislead the agent
(docs/generators.md).

Markdown files from a local directory. To use upstream documentation -- the
OpenTelemetry demo's docs, say -- clone or vendor it and point at the
directory, rather than fetching at build time: a skill bundle that changes
because someone edited a web page is not reproducible, and the sandbox has no
network path to fetch it anyway.

A file is attached to a service when its name matches, or when a heading names
one. Everything else is kept as general documentation.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import ArchSource
from .model import Architecture, Fact

HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.M)
MAX_CHARS = 4000     # a runbook section, not a book


def collect_docs(source: ArchSource, arch: Architecture) -> int:
    if source.path is None:
        return 0
    root = Path(source.path).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"docs path does not exist: {root}")

    known = set(arch.services)
    attached = 0
    for f in sorted(root.rglob("*.md")):
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        origin = str(f.relative_to(root))
        stem = f.stem.lower()

        targets = {name for name in known if name == stem}
        if not targets:
            for heading in HEADING.findall(text):
                targets |= {n for n in known
                            if re.search(rf"\b{re.escape(n)}\b", heading, re.I)}
        excerpt = text[:MAX_CHARS] + ("\n\n[...truncated]" if len(text) > MAX_CHARS else "")
        if not targets:
            # "Everything else is kept as general documentation" -- this file has
            # said so since it was written, and dropped it instead. A document
            # about how the system fits together names no single service in a
            # heading, so it matched nothing and vanished silently: exactly the
            # content the skill was missing.
            arch.general.append(Fact(excerpt, "documented", origin))
            attached += 1
            continue
        for name in targets:
            arch.service(name).notes.append(Fact(excerpt, "documented", origin))
            attached += 1
    arch.sources.append({"type": "docs", "origin": str(root)})
    return attached
