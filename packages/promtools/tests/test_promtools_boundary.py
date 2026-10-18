"""promtools reads Prometheus and nothing else (design 009 §4).

Checked by parsing the package, as k8srca and kubewiki check theirs, so a
docstring explaining the rule does not trip it and a real call cannot hide:

- no Kubernetes client, no kubeconfig, no shelling out to kubectl, helm or oc;
- every HTTP request is made in client.py, with method GET;
- no string in the code names an admin, lifecycle or write endpoint.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "promtools"
FORBIDDEN_IMPORTS = {"kubernetes", "subprocess", "requests", "httpx"}
FORBIDDEN_STRINGS = ("/admin", "/-/", "/api/v1/write", "/otlp", "kubectl", "helm", "kubeconfig")
WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def modules():
    return sorted(SRC.rglob("*.py"))


def code_strings(tree: ast.Module):
    """String constants that are not docstrings or comments."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.value


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
class TestBoundary:
    def test_no_cluster_client_and_no_shell(self, path):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert n.split(".")[0] not in FORBIDDEN_IMPORTS, f"{path.name} imports {n}"
            if isinstance(node, ast.Call):
                fn = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert fn not in {"load_kube_config", "load_incluster_config", "system", "popen"}

    def test_no_string_names_a_write_endpoint(self, path):
        for s in code_strings(ast.parse(path.read_text())):
            for bad in FORBIDDEN_STRINGS:
                assert bad not in s, f"{path.name}: {s!r} names {bad}"

    def test_http_is_made_in_the_client_only_and_only_as_get(self, path):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if fn in {"urlopen", "Request", "HTTPConnection", "HTTPSConnection"}:
                    assert path.name == "client.py", f"{path.name} makes an HTTP call"
                    methods = [k.value.value for k in node.keywords
                               if k.arg == "method" and isinstance(k.value, ast.Constant)]
                    if fn == "Request":
                        assert methods == ["GET"], "every request is built with method GET"
            if isinstance(node, ast.Constant) and node.value in WRITE_METHODS:
                pytest.fail(f"{path.name} names the HTTP method {node.value}")
