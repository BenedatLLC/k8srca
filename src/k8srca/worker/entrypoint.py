"""Container entrypoint: service one work item, then exit.

One container per turn (design 001 §3.2). Ids arrive as forwarded ANTHROPIC_*
environment variables; the per-session secret as ANTHROPIC_WORK_SECRET.

Config is baked into the image, so the image tag pins the agent's tool routing
as well as its code -- which is what makes the per-session image pin in §3.2
meaningful.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from ..config import load
from .runner import ManifestMismatch, handle_one


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("K8SRCA_LOG_LEVEL", "INFO"),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    )
    log = logging.getLogger("k8srca.sandbox")
    session_id = os.environ.get("ANTHROPIC_SESSION_ID", "?")
    log.info("sandbox_start session=%s work=%s", session_id, os.environ.get("ANTHROPIC_WORK_ID", "?"))

    cfg = load(os.environ.get("K8SRCA_CONFIG", "/app/k8srca.yaml"))
    # Manifests are per-deployment (they live in state.json on the host), so
    # the poller forwards them rather than the image carrying them.
    raw = os.environ.get("K8SRCA_MANIFESTS", "")
    manifests = json.loads(raw) if raw else None

    try:
        asyncio.run(handle_one(cfg, workdir=os.environ.get("K8SRCA_WORKDIR", "/workspace"),
                               expected_manifests=manifests))
    except ManifestMismatch as exc:
        # Fail the work item loudly rather than serving a half-broken toolset.
        # The session will sit idle with requires_action and emit no error
        # event, so this log is the only evidence -- see 001 §7.4.
        log.error("manifest_mismatch session=%s %s", session_id, exc)
        return 3
    except Exception as exc:  # noqa: BLE001
        log.exception("sandbox_failed session=%s %s", session_id, exc)
        return 1
    log.info("sandbox_done session=%s", session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
