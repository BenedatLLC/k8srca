"""A fact: one observation, stated by code from one tool result (design 010 §3.2)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Fact:
    #: `<namespace>/<Kind>/<name>[/<container>]`, `node/<name>`, or `<source>/horizon`.
    subject: str
    #: What is observed, in the knowledge base's vocabulary where it has a word for it.
    key: str
    value: Any
    #: The sentence a footnote prints: subject, value and scope, no reasoning.
    statement: str
    #: What the record covers, in plain words.
    scope: str = ""
    #: The knowledge base's fact lines this observation is, role letters for the
    #: subject's workload and container (`last_exit_code[C, 137]`,
    #: `not liveness_probe_configured[C]`). `open` reads these.
    kb: list[str] = field(default_factory=list)
    #: `code`, or `agent` for an observation the agent read from text (010 §3.4).
    by: str = "code"
    id: str = ""
    source: dict = field(default_factory=dict)

    @property
    def workload(self) -> str | None:
        """`<namespace>/<name>` of a workload or container subject."""
        parts = self.subject.split("/")
        return f"{parts[0]}/{parts[2]}" if len(parts) >= 3 and parts[1][:1].isupper() else None

    def same(self, other: "Fact") -> bool:
        return (self.subject, self.key, self.value, self.statement) == \
               (other.subject, other.key, other.value, other.statement)

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# -- durations, as k8stools writes them (ISO 8601) and as a reader wants them ------

_ISO = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?$")


def seconds(iso: str | None) -> float | None:
    """`P3DT20H22M22S` -> seconds; None for anything else."""
    m = _ISO.fullmatch(iso or "")
    if not m or iso in ("P", "PT"):
        return None
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def human(secs: float | None) -> str:
    """The two largest units: `3d20h`, `9m22s`, `8s`."""
    if secs is None:
        return "unknown"
    secs = int(round(secs))
    parts = []
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if secs >= n or (unit == "s" and not parts):
            parts.append(f"{secs // n}{unit}")
            secs %= n
        if len(parts) == 2:
            break
    return "".join(p for p in parts if not p.startswith("0") or len(parts) == 1)
