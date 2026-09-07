"""Unit tests for declaration generation (design 001 §4.2, §4.3)."""

import pytest
from mcp.types import Tool

from k8srca import tools as T


def mk(name, schema, description="d"):
    return Tool(name=name, description=description, inputSchema=schema)


OBJ = {"type": "object", "properties": {}}


class TestNormalize:
    def test_collapses_pydantic_optional(self):
        # 12 of k8stools' 18 tools use this encoding.
        out = T.normalize_schema({
            "type": "object",
            "properties": {"ns": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}},
        })
        assert out["properties"]["ns"] == {"type": "string"}

    def test_keeps_meaningful_default(self):
        out = T.normalize_schema({
            "type": "object",
            "properties": {"ns": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": "default"}},
        })
        assert out["properties"]["ns"] == {"type": "string", "default": "default"}

    def test_leaves_real_union_alone(self):
        # A genuine union is not the Optional pattern and must survive.
        schema = {"type": "object",
                  "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
        assert T.normalize_schema(schema)["properties"]["x"]["anyOf"]

    def test_strips_titles(self):
        out = T.normalize_schema({"type": "object", "title": "Args",
                                  "properties": {"a": {"type": "string", "title": "A"}}})
        assert "title" not in out and "title" not in out["properties"]["a"]


class TestValidate:
    def test_rejects_top_level_anyof(self):
        with pytest.raises(T.ToolSchemaError, match="top-level"):
            T.validate_schema("t", {"type": "object", "anyOf": [OBJ]})

    def test_rejects_ref_anywhere(self):
        with pytest.raises(T.ToolSchemaError, match=r"\$ref"):
            T.validate_schema("t", {"type": "object", "properties": {"a": {"$ref": "#/$defs/X"}}})

    def test_allows_nested_anyof(self):
        # Only *top-level* oneOf/anyOf is forbidden; nested is legal.
        T.validate_schema("t", {"type": "object",
                                "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}})


class TestDeclaration:
    def test_applies_prefix(self):
        d = T.declaration(mk("get_pods", OBJ), prefix="k8s_")
        assert d["name"] == "k8s_get_pods" and d["type"] == "custom"

    def test_falls_back_to_name_when_description_missing(self):
        assert T.declaration(Tool(name="x", inputSchema=OBJ))["description"] == "x"


class TestSelect:
    def test_star_selects_all(self):
        decls = T.declarations([mk("a", OBJ), mk("b", OBJ)], "k8s_")
        assert len(T.select(decls, "*", "k8s_")) == 2

    def test_group_names_are_unprefixed(self):
        decls = T.declarations([mk("a", OBJ), mk("b", OBJ)], "k8s_")
        assert [d["name"] for d in T.select(decls, ["a"], "k8s_")] == ["k8s_a"]

    def test_unknown_group_member_is_an_error(self):
        # Catches a typo in k8srca.yaml at validate time, not session time.
        decls = T.declarations([mk("a", OBJ)], "k8s_")
        with pytest.raises(T.ToolSchemaError, match="not present"):
            T.select(decls, ["a", "typo"], "k8s_")


class TestManifestHash:
    def test_is_order_independent(self):
        a = T.declarations([mk("a", OBJ), mk("b", OBJ)])
        assert T.manifest_hash(a) == T.manifest_hash(list(reversed(a)))

    def test_changes_when_a_schema_changes(self):
        before = T.declarations([mk("a", OBJ)])
        after = T.declarations([mk("a", {"type": "object", "properties": {"n": {"type": "string"}}})])
        assert T.manifest_hash(before) != T.manifest_hash(after)

    def test_ignores_description_churn(self):
        # Reworded docs must not force a re-sync of every sandbox.
        assert T.manifest_hash(T.declarations([mk("a", OBJ, "one")])) == \
               T.manifest_hash(T.declarations([mk("a", OBJ, "two")]))


class TestCollisions:
    def test_detects_cross_server_clash(self):
        with pytest.raises(T.ToolSchemaError, match="collision"):
            T.check_collisions({"k8s": T.declarations([mk("get_events", OBJ)]),
                                "loki": T.declarations([mk("get_events", OBJ)])})

    def test_prefixes_resolve_it(self):
        T.check_collisions({"k8s": T.declarations([mk("get_events", OBJ)], "k8s_"),
                            "loki": T.declarations([mk("get_events", OBJ)], "loki_")})
