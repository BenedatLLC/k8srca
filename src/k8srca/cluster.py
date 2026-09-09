"""Making a cluster reachable from the k8stools container, generically.

Almost everything here is *derived* rather than configured. Only one fact is
genuinely site-specific: how to reach the API server when it is not routable
from a container. Everything else -- the docker gateway, the subnet, the TLS
server name, the uid to run as -- is read from the environment at the moment
it is needed, so the same configuration works on another machine.

The problem being solved: a kubeconfig whose server is on loopback
(`127.0.0.1`, `localhost`) works from the host and fails inside a container,
because a container's loopback is its own. That happens with an SSH tunnel,
with minikube, and with kind -- i.e. most development setups.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

LOOPBACK = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?", re.I)


def run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------
# Reading a kubeconfig
# --------------------------------------------------------------------------

@dataclass
class ApiEndpoint:
    server: str          # https://host:port
    host: str
    port: int

    @property
    def on_loopback(self) -> bool:
        return bool(LOOPBACK.match(self.server))


def split_host_port(authority: str, default_port: int) -> tuple[str, int]:
    """Split host:port, handling bracketed IPv6 (`[::1]:6443`).

    Partitioning on the first colon would cut an IPv6 literal in half.
    """
    if authority.startswith("["):
        host, _, rest = authority.partition("]")
        return host + "]", int(rest.lstrip(":") or default_port)
    host, _, port = authority.partition(":")
    return host, int(port or default_port)


def read_endpoint(kubeconfig: Path) -> ApiEndpoint:
    cfg = yaml.safe_load(Path(kubeconfig).expanduser().read_text())
    server = cfg["clusters"][0]["cluster"]["server"]
    scheme, _, authority = server.rpartition("://")
    host, port = split_host_port(authority, 443 if scheme != "http" else 80)
    return ApiEndpoint(server=server, host=host, port=port)


# --------------------------------------------------------------------------
# Docker network facts
# --------------------------------------------------------------------------

@dataclass
class Network:
    name: str
    subnet: str
    gateway: str
    bridge: str

    @classmethod
    def inspect(cls, name: str) -> "Network | None":
        r = run("docker", "network", "inspect", name,
                "-f", "{{range .IPAM.Config}}{{.Subnet}} {{.Gateway}}{{end}} {{.Id}}")
        if r.returncode != 0 or not r.stdout.strip():
            return None
        parts = r.stdout.split()
        if len(parts) < 3:
            return None
        subnet, gateway, net_id = parts[0], parts[1], parts[2]
        return cls(name=name, subnet=subnet, gateway=gateway, bridge=f"br-{net_id[:12]}")


def ensure_network(name: str) -> Network:
    """Create the network if absent. Idempotent, so it is safe at every boot."""
    net = Network.inspect(name)
    if net is None:
        run("docker", "network", "create", name)
        net = Network.inspect(name)
    if net is None:
        raise RuntimeError(f"could not create or inspect docker network {name!r}")
    return net


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------

def certificate_names(host: str, port: int) -> list[str]:
    """DNS names on the API server's certificate.

    Connecting to the gateway address instead of the kubeconfig's own means the
    certificate will not match. Rather than disable verification, we redirect
    it with `tls-server-name` -- which requires knowing a name the certificate
    actually carries.
    """
    if not shutil.which("openssl"):
        return []
    try:
        r = subprocess.run(
            ["openssl", "s_client", "-connect", f"{host}:{port}", "-showcerts"],
            input="", capture_output=True, text=True, timeout=20,
        )
        cert = subprocess.run(["openssl", "x509", "-noout", "-text"],
                              input=r.stdout, capture_output=True, text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return []
    match = re.search(r"Subject Alternative Name:\s*\n\s*(.+)", cert.stdout)
    if not match:
        return []
    return [p.strip().removeprefix("DNS:") for p in match.group(1).split(",")
            if p.strip().startswith("DNS:")]


def pick_tls_server_name(names: list[str]) -> str | None:
    """Prefer the name most likely to stay valid: localhost, then kubernetes."""
    for preferred in ("localhost", "kubernetes.default.svc", "kubernetes"):
        if preferred in names:
            return preferred
    return names[0] if names else None


# --------------------------------------------------------------------------
# Container kubeconfig
# --------------------------------------------------------------------------

def write_container_kubeconfig(source: Path, dest: Path, server: str,
                               tls_server_name: str | None) -> Path:
    """Rewrite a host kubeconfig so a container can use it.

    Only the server address and TLS server name change; the credential and CA
    are carried through untouched.
    """
    cfg = yaml.safe_load(Path(source).expanduser().read_text())
    cluster = cfg["clusters"][0]["cluster"]
    cluster["server"] = server
    if tls_server_name:
        cluster["tls-server-name"] = tls_server_name
    else:
        cluster.pop("tls-server-name", None)
    dest = Path(dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(cfg, default_flow_style=False))
    dest.chmod(0o600)
    return dest


# --------------------------------------------------------------------------
# SSH forward
# --------------------------------------------------------------------------

@dataclass
class Tunnel:
    ssh_host: str
    remote_endpoint: str     # host:port as seen from the SSH host
    bind_address: str
    port: int

    def command(self) -> list[str]:
        return [
            "ssh", "-N",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-o", "BatchMode=yes",
            "-L", f"{self.bind_address}:{self.port}:{self.remote_endpoint}",
            self.ssh_host,
        ]

    def listening(self) -> bool:
        with socket.socket() as s:
            s.settimeout(2)
            return s.connect_ex((self.bind_address, self.port)) == 0
