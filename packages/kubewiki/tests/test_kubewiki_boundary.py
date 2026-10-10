"""kubewiki must never touch the Kubernetes API directly (docs/design.md, principle 6).

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

SRC = Path(__file__).parent.parent / "src" / "kubewiki"

FORBIDDEN_MODULES = {"kubernetes"}
FORBIDDEN_CALLS = {"load_kube_config", "load_incluster_config"}
# Argv entries that would mean shelling out to the cluster.
FORBIDDEN_BINARIES = {"kubectl", "oc", "helm"}

#: The one exception: `helm template` renders a chart to manifests offline, in
#: the module that fetches pinned charts for the architecture skill. Allowed
#: there only as `helm template`, and never with a flag that contacts a cluster.
HELM_TEMPLATE_MODULE = SRC / "fetch.py"
CLUSTER_FLAGS = ("--validate", "--kube", "--is-upgrade", "--dry-run=server")


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
                        f"k8stools MCP server; add a tool to k8stools instead."
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in FORBIDDEN_MODULES, (
                    f"{path} imports from {node.module}. Cluster access goes through the "
                    f"k8stools MCP server; add a tool to k8stools instead (docs/design.md)."
                )

    def test_does_not_load_a_kubeconfig(self, path):
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in FORBIDDEN_CALLS, (
                    f"{path} calls {name}(). Only the k8stools container holds a "
                    f"kubeconfig (docs/design.md)."
                )

    def test_does_not_shell_out_to_a_cluster_cli(self, path):
        """kubectl/helm/oc named as a command, in any call or any argv literal.

        Every list or tuple literal is checked, not only literals passed straight
        to a call: an argv built in a variable first is the same command.
        """
        tree = parse(path)
        which_args = {id(a) for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) == "which"
                      for a in n.args}
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)):
                consts = [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str)
                          else None for e in node.elts]
                if consts and consts[0] in FORBIDDEN_BINARIES:
                    if path == HELM_TEMPLATE_MODULE and consts[0] == "helm":
                        assert len(consts) > 1 and consts[1] == "template", (
                            f"{path}: the only helm command k8srca may run is `helm template`")
                        assert not any(c and c.startswith(CLUSTER_FLAGS) for c in consts), (
                            f"{path}: `helm template` must not be given a flag that "
                            f"contacts a cluster ({', '.join(CLUSTER_FLAGS)})")
                        continue
                    raise AssertionError(
                        f"{path} builds a {consts[0]!r} command. Cluster access goes "
                        f"through the k8stools MCP server (docs/design.md).")
            elif isinstance(node, ast.Call):
                for arg in node.args:
                    if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                            and id(arg) not in which_args):
                        head = arg.value.strip().split()[:1]
                        assert not (head and head[0] in FORBIDDEN_BINARIES), (
                            f"{path} shells out to {head[0]!r}. Cluster access goes "
                            f"through the k8stools MCP server (docs/design.md).")


def test_the_helm_exception_is_narrow(tmp_path, monkeypatch):
    """The exception admits `helm template` in one module and nothing else."""
    import textwrap

    cases = {
        "argv = ['helm', 'install', 'x', 'chart']": False,
        "argv = ['helm', 'template', 'x', 'chart', '--validate']": False,
        "argv = ['helm', 'template', 'x', 'chart', '--namespace', 'ns']": True,
        "argv = ['kubectl', 'get', 'pods']": False,
    }
    for code, allowed in cases.items():
        mod = tmp_path / "fetch.py"
        mod.write_text(textwrap.dedent(code))
        monkeypatch.setattr(__import__(__name__), "HELM_TEMPLATE_MODULE", mod)
        try:
            TestNoDirectClusterAccess().test_does_not_shell_out_to_a_cluster_cli(mod)
            ok = True
        except AssertionError:
            ok = False
        assert ok is allowed, code


def test_the_rule_is_documented():
    """The test enforces it; the design has to explain why, or it reads as arbitrary."""
    text = (Path(__file__).parent.parent / "docs" / "design.md").read_text()
    assert "Cluster access only through MCP" in text


def test_kubewiki_never_imports_k8srca():
    """kubewiki is standalone: k8srca depends on it, never the reverse (docs/design.md §9)."""
    import ast as _ast

    for path in sorted(SRC.rglob("*.py")):
        for node in _ast.walk(_ast.parse(path.read_text(), filename=str(path))):
            names = ([a.name for a in node.names] if isinstance(node, _ast.Import)
                     else [node.module] if isinstance(node, _ast.ImportFrom) and node.module
                     else [])
            for name in names:
                assert name.split(".")[0] != "k8srca", (
                    f"{path} imports {name}: kubewiki must run without k8srca")
