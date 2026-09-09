"""Cluster-access derivation (design 003). Nothing here touches a real cluster."""

from pathlib import Path

import pytest
import yaml

from k8srca import cluster as C
from k8srca.bringup import resolve_mode
from k8srca.config import ClusterAccess, Config, McpServer, SandboxConfig, SshTunnelConfig
from k8srca.config import AgentConfig, ModelConfig


def kubeconfig(tmp_path: Path, server: str) -> Path:
    p = tmp_path / "kc.yaml"
    p.write_text(yaml.safe_dump({
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "c", "cluster": {"server": server, "certificate-authority-data": "QUJD"}}],
        "contexts": [{"name": "c", "context": {"cluster": "c", "user": "u"}}],
        "current-context": "c",
        "users": [{"name": "u", "user": {"token": "t"}}],
    }))
    return p


def make_config(**access) -> Config:
    return Config(
        mcp=[McpServer(name="k8stools", url="http://k8stools:8000/mcp", groups={"full": "*"})],
        agents={"c": AgentConfig(role="coordinator", model=ModelConfig(id="m"),
                                 system_prompt=Path("p.md"), mcp_tools={"k8stools": "full"})},
        sandbox=SandboxConfig(image="img"),
        cluster_access=ClusterAccess(**access),
    )


class TestLoopbackDetection:
    @pytest.mark.parametrize("server", [
        "https://127.0.0.1:6443", "https://localhost:6443", "https://[::1]:6443",
        "http://LOCALHOST:8080",
    ])
    def test_recognises_loopback(self, tmp_path, server):
        # These work from the host and fail inside a container, which is the
        # whole problem this module exists to solve.
        assert C.read_endpoint(kubeconfig(tmp_path, server)).on_loopback

    @pytest.mark.parametrize("server", [
        "https://192.168.49.2:8443", "https://k8s.example.com:6443", "https://10.0.0.1:443",
    ])
    def test_routable_addresses_are_not_loopback(self, tmp_path, server):
        assert not C.read_endpoint(kubeconfig(tmp_path, server)).on_loopback

    def test_parses_host_and_port(self, tmp_path):
        e = C.read_endpoint(kubeconfig(tmp_path, "https://example.com:6443"))
        assert (e.host, e.port) == ("example.com", 6443)

    def test_defaults_port_when_absent(self, tmp_path):
        assert C.read_endpoint(kubeconfig(tmp_path, "https://example.com")).port == 443


class TestResolveMode:
    def test_loopback_plus_ssh_means_tunnel(self, tmp_path):
        cfg = make_config(kubeconfig=kubeconfig(tmp_path, "https://localhost:6443"),
                          ssh=SshTunnelConfig(host="h", remote_endpoint="1.2.3.4:8443"))
        assert resolve_mode(cfg)[0] == "ssh_tunnel"

    def test_routable_server_needs_no_tunnel(self, tmp_path):
        cfg = make_config(kubeconfig=kubeconfig(tmp_path, "https://10.1.2.3:6443"),
                          ssh=SshTunnelConfig(host="h", remote_endpoint="1.2.3.4:8443"))
        assert resolve_mode(cfg)[0] == "direct"

    def test_loopback_without_ssh_is_an_actionable_error(self, tmp_path):
        # Failing loudly here beats a container that starts and whose every
        # tool call is refused.
        cfg = make_config(kubeconfig=kubeconfig(tmp_path, "https://localhost:6443"))
        mode, _, problem = resolve_mode(cfg)
        assert mode == "error" and "loopback" in problem and "docs/cluster-setup.md" in problem

    def test_explicit_mode_overrides_detection(self, tmp_path):
        cfg = make_config(mode="direct", kubeconfig=kubeconfig(tmp_path, "https://localhost:6443"))
        assert resolve_mode(cfg)[0] == "direct"


class TestTlsServerName:
    def test_prefers_localhost(self):
        assert C.pick_tls_server_name(["minikube", "localhost", "kubernetes"]) == "localhost"

    def test_falls_back_through_the_preference_list(self):
        assert C.pick_tls_server_name(["minikube", "kubernetes"]) == "kubernetes"

    def test_uses_any_name_rather_than_none(self):
        assert C.pick_tls_server_name(["minikube"]) == "minikube"

    def test_none_when_certificate_has_no_dns_names(self):
        # Better to omit tls-server-name than to invent one.
        assert C.pick_tls_server_name([]) is None


class TestContainerKubeconfig:
    def test_rewrites_server_and_keeps_the_credential(self, tmp_path):
        src = kubeconfig(tmp_path, "https://localhost:6443")
        dest = C.write_container_kubeconfig(src, tmp_path / "out.yaml",
                                            "https://172.20.0.1:6443", "localhost")
        cfg = yaml.safe_load(dest.read_text())
        cluster = cfg["clusters"][0]["cluster"]
        assert cluster["server"] == "https://172.20.0.1:6443"
        assert cluster["tls-server-name"] == "localhost"
        assert cluster["certificate-authority-data"] == "QUJD"      # carried through
        assert cfg["users"][0]["user"]["token"] == "t"

    def test_written_private(self, tmp_path):
        dest = C.write_container_kubeconfig(kubeconfig(tmp_path, "https://localhost:6443"),
                                            tmp_path / "out.yaml", "https://x:1", None)
        assert oct(dest.stat().st_mode)[-3:] == "600"

    def test_omits_tls_server_name_when_unknown(self, tmp_path):
        dest = C.write_container_kubeconfig(kubeconfig(tmp_path, "https://localhost:6443"),
                                            tmp_path / "out.yaml", "https://x:1", None)
        assert "tls-server-name" not in yaml.safe_load(dest.read_text())["clusters"][0]["cluster"]


class TestTunnelCommand:
    def test_binds_the_given_address_not_loopback(self):
        t = C.Tunnel("host", "192.168.49.2:8443", "172.20.0.1", 6443)
        assert "-L" in t.command()
        assert "172.20.0.1:6443:192.168.49.2:8443" in t.command()

    def test_fails_rather_than_prompting(self):
        # A boot-time unit must never block on a password prompt.
        assert "BatchMode=yes" in C.Tunnel("h", "r:1", "a", 1).command()


class TestHostPortSplit:
    def test_ipv6_literal_is_not_cut_at_the_first_colon(self):
        assert C.split_host_port("[::1]:6443", 443) == ("[::1]", 6443)

    def test_ipv6_without_port_uses_the_default(self):
        assert C.split_host_port("[fd00::1]", 443) == ("[fd00::1]", 443)

    def test_ordinary_host_port(self):
        assert C.split_host_port("example.com:6443", 443) == ("example.com", 6443)
