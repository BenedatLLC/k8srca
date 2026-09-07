"""MCP tool -> Managed Agents custom-tool declarations, and the worker-side wrapper.

Two halves of one truth, per design 001 §4.2:

* ``declaration()`` runs at ``sync`` time and produces what an *agent* declares,
  which gates the model's visibility of a tool.
* ``wrap_mcp_tool()`` runs in the *worker* and produces the runnable tool that
  actually executes the call. The worker registers the union across all servers
  and all agents; the agent declarations are subsets of it.

Both must agree on the exposed name, hence the shared ``exposed_name()``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

# mcp<2 exposes camelCase (`tool.inputSchema`); mcp>=2 exposes snake_case
# (`tool.input_schema`). The Anthropic SDK has the same shim internally.
def mcp_input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    return dict(schema or {"type": "object", "properties": {}})


class ToolSchemaError(ValueError):
    """A tool's schema cannot be expressed as a Managed Agents custom tool."""


def exposed_name(tool_name: str, prefix: str = "") -> str:
    """The name the model sees. Must match between declaration and worker."""
    return f"{prefix}{tool_name}"


# --------------------------------------------------------------------------
# Schema normalization
# --------------------------------------------------------------------------

def _collapse_optional(node: Any) -> Any:
    """Collapse Pydantic's ``Optional[T]`` encoding into a plain type.

    FastMCP renders ``namespace: str | None = None`` as
    ``{"anyOf": [{"type": "string"}, {"type": "null"}], "default": null}``.
    12 of k8stools' 18 tools do this. Nested ``anyOf`` is not forbidden by the
    custom-tool schema rules (only *top-level* ``oneOf``/``anyOf`` is), but
    collapsing it removes any doubt and produces a schema the model reads more
    easily. Optionality is already carried by ``required``, so nothing is lost.
    """
    if not isinstance(node, dict):
        return [_collapse_optional(v) for v in node] if isinstance(node, list) else node

    node = {k: _collapse_optional(v) for k, v in node.items()}

    variants = node.get("anyOf")
    if isinstance(variants, list) and len(variants) == 2:
        non_null = [v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")]
        has_null = len(non_null) == 1
        if has_null and isinstance(non_null[0], dict):
            merged = {k: v for k, v in node.items() if k != "anyOf"}
            # the variant's own keys win over the wrapper's (e.g. "type")
            merged.update(non_null[0])
            # `default: null` contradicts the collapsed type, which no longer
            # admits null. Optionality is carried by `required`, so drop it.
            if merged.get("default", ...) is None:
                merged.pop("default")
            return merged
    return node


def _strip_noise(node: Any) -> Any:
    """Drop generated titles: they are pure token cost in a tool declaration."""
    if isinstance(node, list):
        return [_strip_noise(v) for v in node]
    if not isinstance(node, dict):
        return node
    return {k: _strip_noise(v) for k, v in node.items() if k != "title"}


def normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    normalized = _strip_noise(_collapse_optional(schema))
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    return normalized


# --------------------------------------------------------------------------
# Validation (design 001 §4.2, "Schema constraint")
# --------------------------------------------------------------------------

_FORBIDDEN_TOP_LEVEL = ("oneOf", "anyOf", "allOf")


def _find_refs(node: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("$ref", "$defs", "definitions"):
                found.append(f"{path}.{key}")
            found.extend(_find_refs(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_find_refs(value, f"{path}[{i}]"))
    return found


def validate_schema(name: str, schema: dict[str, Any]) -> None:
    """Raise if the schema cannot be used as a custom tool.

    Custom-tool schemas may not use ``$ref`` (anywhere) or ``oneOf``/``anyOf``
    at the top level. Nested ``anyOf`` inside a property is permitted; see
    ``_collapse_optional`` for why we remove the common case anyway.
    """
    for key in _FORBIDDEN_TOP_LEVEL:
        if key in schema:
            raise ToolSchemaError(
                f"{name}: top-level {key!r} is not supported in custom tool schemas"
            )
    refs = _find_refs(schema)
    if refs:
        raise ToolSchemaError(
            f"{name}: $ref/$defs are not supported in custom tool schemas (at {', '.join(refs)})"
        )
    if schema.get("type") != "object":
        raise ToolSchemaError(f"{name}: top-level schema type must be 'object'")


# --------------------------------------------------------------------------
# Declarations (sync side)
# --------------------------------------------------------------------------

def declaration(tool: Any, prefix: str = "") -> dict[str, Any]:
    """Build one Managed Agents ``custom`` tool declaration from an MCP tool."""
    name = exposed_name(tool.name, prefix)
    schema = normalize_schema(mcp_input_schema(tool))
    validate_schema(name, schema)
    return {
        "type": "custom",
        "name": name,
        "description": tool.description or tool.name,
        "input_schema": schema,
    }


def declarations(tools: Iterable[Any], prefix: str = "") -> list[dict[str, Any]]:
    return [declaration(t, prefix) for t in tools]


def select(decls: list[dict[str, Any]], group: list[str] | str, prefix: str = "") -> list[dict[str, Any]]:
    """Filter declarations by a config tool group.

    ``group`` is either ``"*"`` (everything) or a list of *unprefixed* tool
    names, so k8srca.yaml stays readable and prefix changes don't churn it.
    """
    if group == "*":
        return list(decls)
    if not isinstance(group, list):
        raise ValueError(f"tool group must be a list or '*', got {group!r}")
    wanted = {exposed_name(n, prefix) for n in group}
    by_name = {d["name"]: d for d in decls}
    missing = sorted(wanted - by_name.keys())
    if missing:
        raise ToolSchemaError(
            "tool group names not present on the MCP server: " + ", ".join(missing)
        )
    return [by_name[n] for n in sorted(wanted)]


def manifest_hash(decls: list[dict[str, Any]]) -> str:
    """Stable digest of an agent's tool surface (design 001 §4.3).

    Recorded in the agent's ``metadata`` at sync time and recomputed by the
    sandbox at startup; a mismatch means the agent's declarations and the live
    MCP server have drifted, and the work item is failed rather than served
    with a half-broken toolset.
    """
    canonical = json.dumps(
        sorted(
            ({"name": d["name"], "input_schema": d["input_schema"]} for d in decls),
            key=lambda d: d["name"],
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def check_collisions(named: dict[str, list[dict[str, Any]]]) -> None:
    """Reject cross-server name collisions.

    The worker registers every server's tools into one flat namespace, so two
    servers exposing the same exposed name cannot both be served. Prefixes exist
    to prevent this; this check is what makes them load-bearing rather than
    decorative (design 001 §4.2).
    """
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for server, decls in named.items():
        for d in decls:
            if d["name"] in seen:
                clashes.append(f"{d['name']} (from {seen[d['name']]} and {server})")
            else:
                seen[d["name"]] = server
    if clashes:
        raise ToolSchemaError(
            "tool name collisions across MCP servers; change a prefix: " + "; ".join(clashes)
        )


# --------------------------------------------------------------------------
# Worker-side wrapper
# --------------------------------------------------------------------------

def wrap_mcp_tool(tool: Any, session: Any, prefix: str = "") -> Any:
    """Runnable tool that exposes a prefixed name but calls the unprefixed one.

    ``anthropic.lib.tools.mcp.async_mcp_tool`` cannot do this: it closes over
    ``tool.name`` for both the declared name and the outbound
    ``client.call_tool(name=...)``, so renaming the Tool would break the call.
    We rebuild it with the two names decoupled, reusing the SDK's own result
    conversion so content handling stays identical.
    """
    from anthropic.lib.tools._beta_functions import beta_async_tool
    from anthropic.lib.tools.mcp import _convert_tool_result  # type: ignore[attr-defined]

    remote_name = tool.name
    local_name = exposed_name(remote_name, prefix)
    schema = normalize_schema(mcp_input_schema(tool))
    validate_schema(local_name, schema)

    async def call_mcp(**kwargs: Any) -> Any:
        result = await session.call_tool(name=remote_name, arguments=kwargs)
        return _convert_tool_result(result)

    return beta_async_tool(
        call_mcp,
        name=local_name,
        description=tool.description or remote_name,
        input_schema=schema,
    )
