"""k8srca must never touch the Kubernetes API directly (CLAUDE.md).

All cluster access goes through the k8stools MCP server. A second code path
would mean a second process holding cluster credentials, and would not be bound
by k8stools' read-only tool surface -- so "read-only" would become a property of
whoever wrote the call rather than of the system.

This is enforced by parsing the AST rather than grepping, so a docstring
explaining the rule does not trip it and a real import cannot hide in one.
"""

import ast
from pathlib import Path

import pytest

SRC = Path("src/k8srca")

FORBIDDEN_MODULES = {"kubernetes"}
FORBIDDEN_CALLS = {"load_kube_config", "load_incluster_config"}
# Argv entries that would mean shelling out to the cluster.
FORBIDDEN_BINARIES = {"kubectl", "oc", "helm"}


def modules():
    return sorted(p for p in SRC.rglob("*.py"))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p))
class TestNoDirectClusterAccess:
    def test_does_not_import_the_kubernetes_client(self, path):
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in FORBIDDEN_MODULES, (
                        f"{path} imports {alias.name}. Cluster access goes through the "
                        f"k8stools MCP server; add a tool to k8stools instead (CLAUDE.md)."
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in FORBIDDEN_MODULES, (
                    f"{path} imports from {node.module}. Cluster access goes through the "
                    f"k8stools MCP server; add a tool to k8stools instead (CLAUDE.md)."
                )

    def test_does_not_load_a_kubeconfig(self, path):
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in FORBIDDEN_CALLS, (
                    f"{path} calls {name}(). Only the k8stools container holds a "
                    f"kubeconfig (design 001 §8.2)."
                )

    def test_does_not_shell_out_to_a_cluster_cli(self, path):
        """String literals in argv position naming kubectl/helm/oc."""
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Call):
                continue
            for arg in node.args:
                items = arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
                for item in items:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        head = item.value.strip().split()[:1]
                        assert not (head and head[0] in FORBIDDEN_BINARIES), (
                            f"{path} shells out to {head[0]!r}. Cluster access goes "
                            f"through the k8stools MCP server (CLAUDE.md)."
                        )


def test_the_rule_is_documented():
    """The test enforces it; CLAUDE.md has to explain why, or it reads as arbitrary."""
    text = Path("CLAUDE.md").read_text()
    assert "never touches the Kubernetes API directly" in text
