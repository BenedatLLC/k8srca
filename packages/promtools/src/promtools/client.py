"""The only code that talks to Prometheus, and it can only read.

Read-only is a property of this module, not of whoever calls it (the same
reason k8stools exposes no mutating tools; k8srca CLAUDE.md, reason 2):

- **GET only.** Every request is built here with method GET. Prometheus'
  mutating endpoints (`/api/v1/admin/*`, `/-/reload`, `/-/quit`) take POST or
  PUT, and a GET to them does nothing.
- **Named paths only.** A request goes to one of `PATHS`, or it is refused
  before it is sent. No caller can reach the admin API or the lifecycle
  endpoints by passing a path, whatever the server enables.

tests/test_promtools_boundary.py checks both by parsing this package.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: The Prometheus HTTP API endpoints this package reads. Nothing else is
#: requested. `/api/v1/admin/*`, `/api/v1/write`, `/api/v1/otlp/*`, `/-/*`
#: are absent on purpose.
PATHS = frozenset({
    "/api/v1/query",
    "/api/v1/query_range",
    "/api/v1/series",
    "/api/v1/labels",
    "/api/v1/metadata",
    "/api/v1/targets",
    "/api/v1/status/runtimeinfo",
    "/api/v1/status/buildinfo",
    "/api/v1/status/flags",
})
#: `/api/v1/label/<name>/values` has the name in the path.
LABEL_VALUES = "/api/v1/label/{}/values"


class PromError(RuntimeError):
    """Prometheus answered with an error, or could not be reached."""


class PromClient:
    def __init__(self, url: str, timeout: float = 60.0, headers: dict[str, str] | None = None):
        self.url = url.rstrip("/")
        self.timeout = timeout
        #: e.g. a bearer token for a site whose Prometheus needs one (009 §8.4).
        self.headers = dict(headers or {})

    def get(self, path: str, params: list[tuple[str, Any]] | dict[str, Any] | None = None) -> Any:
        """GET `path` and return the response's `data`."""
        if path not in PATHS and not _is_label_values(path):
            raise PromError(f"refused: {path} is not an endpoint promtools reads")
        items = list(params.items()) if isinstance(params, dict) else list(params or [])
        query = urllib.parse.urlencode([(k, v) for k, v in items if v is not None])
        req = urllib.request.Request(f"{self.url}{path}" + (f"?{query}" if query else ""),
                                     method="GET", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # Prometheus explains a bad query in the body of a 400/422.
            try:
                body = json.loads(exc.read())
            except ValueError:
                raise PromError(f"{path}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise PromError(f"{path}: {exc}") from exc
        if body.get("status") != "success":
            raise PromError(f"{path}: {body.get('errorType', 'error')}: {body.get('error')}")
        return body.get("data")

    # -- the calls the tools use ------------------------------------------
    def query(self, promql: str, time: float | None = None) -> dict:
        return self.get("/api/v1/query", {"query": promql, "time": time})

    def query_range(self, promql: str, start: float, end: float, step: float) -> dict:
        return self.get("/api/v1/query_range",
                        {"query": promql, "start": start, "end": end, "step": step})

    def series(self, match: list[str], start: float | None = None,
               end: float | None = None) -> list[dict]:
        return self.get("/api/v1/series",
                        [("match[]", m) for m in match] + [("start", start), ("end", end)])

    def label_values(self, label: str, match: list[str] | None = None) -> list[str]:
        return self.get(LABEL_VALUES.format(urllib.parse.quote(label, safe="")),
                        [("match[]", m) for m in match or []])

    def metadata(self, metric: str | None = None) -> dict:
        return self.get("/api/v1/metadata", {"metric": metric})

    def targets(self) -> dict:
        return self.get("/api/v1/targets", {"state": "active"})

    def runtime_info(self) -> dict:
        return self.get("/api/v1/status/runtimeinfo")


def _is_label_values(path: str) -> bool:
    prefix, suffix = LABEL_VALUES.split("{}")
    name = path[len(prefix):-len(suffix)] if path.startswith(prefix) and path.endswith(suffix) else ""
    return bool(name) and "/" not in name
