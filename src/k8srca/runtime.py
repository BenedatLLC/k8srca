"""What each long-running process loaded at startup, so `status` can tell stale.

The poller and the Slack orchestrator read `.k8srca/state.json` once, when they
start. `k8srca sync` rewrites it -- new agent versions, new tool manifests --
and neither process notices: the orchestrator keeps creating sessions on the
old coordinator version, and the poller keeps checking the old manifests,
which fails the new sessions' work. The poller likewise pins its sandbox image
at startup. `status` used to report both as "running", which was true and
useless.

So each writes a record beside the state file when it starts, and `status`
compares the record with what is on disk now.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .state import State

#: Restart commands, for the hint. Both run as systemd user units in production
#: (design 003); in development they are whatever terminal started them.
UNITS = {"poller": "k8srca-poller", "slack": "k8srca-slack"}


@dataclass
class Record:
    pid: int
    started: float
    state: str                  # State.fingerprint() of what it loaded
    image: str | None = None    # the sandbox image, for the poller


def path(state_path: Path, role: str) -> Path:
    return Path(state_path).parent / "run" / f"{role}.json"


def write(state_path: Path, role: str, state: State, image: str | None = None) -> None:
    p = path(state_path, role)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(Record(os.getpid(), time.time(),
                                          state.fingerprint(), image))) + "\n")


def read(state_path: Path, role: str) -> Record | None:
    try:
        return Record(**json.loads(path(state_path, role).read_text()))
    except (OSError, ValueError, TypeError):
        return None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def staleness(role: str, record: Record | None, current: State,
              image: str | None = None) -> tuple[bool, bool, str]:
    """(ok, warn, detail) for a running process against what is on disk now.

    A state mismatch is a failure: sessions created from one sync and executed
    against another's manifests do not work. An image mismatch only means the
    poller runs older sandbox code, which still works, so it warns.
    """
    restart = f"restart it (systemctl --user restart {UNITS[role]})"
    if record is None or not alive(record.pid):
        return True, True, (f"started without a startup record, so whether it loaded "
                            f"the current sync is unknown -- {restart} once")
    if record.state != current.fingerprint():
        return False, False, (f"loaded an older `k8srca sync` than .k8srca/state.json "
                              f"-- new sessions will fail; {restart}")
    if image is not None and record.image is not None and record.image != image:
        return True, True, (f"runs sandbox {record.image}; the tree builds {image} "
                            f"-- `k8srca sandbox build`, then {restart}")
    return True, False, "loaded the current sync" + (f", sandbox {record.image}"
                                                       if record.image else "")
