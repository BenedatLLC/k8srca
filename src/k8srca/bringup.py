"""Idempotent bring-up of everything a reboot destroys (design 003 §4).

Safe to run repeatedly: every step checks before acting, so this doubles as
both the boot path and a diagnostic. Nothing here needs root -- the one step
that does, the egress rules, is reported rather than performed.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import cluster as C
from .config import Config


def root_cause(exc: BaseException, depth: int = 0) -> str:
    """Innermost message of a nested ExceptionGroup.

    anyio wraps failures from a task group, so the outermost message is
    "unhandled errors in a TaskGroup" -- true and useless. The diagnosis is
    several layers down.
    """
    inner = getattr(exc, "exceptions", None)
    if inner and depth < 6:
        return root_cause(inner[0], depth + 1)
    return str(exc) or type(exc).__name__


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    changed: bool = False
    # A warning does not mean the system is broken now -- only that something
    # will not survive a reboot. It must not fail the exit code, or callers
    # start ignoring it.
    warn: bool = False


def resolve_mode(cfg: Config) -> tuple[str, C.ApiEndpoint | None, str | None]:
    """Decide how the container should reach the API server."""
    access = cfg.cluster_access
    if access.kubeconfig is None:
        return "direct", None, "no kubeconfig configured; k8stools uses its own default"
    try:
        endpoint = C.read_endpoint(access.kubeconfig)
    except Exception as exc:  # noqa: BLE001
        return "error", None, f"cannot read {access.kubeconfig}: {exc}"
    if access.mode != "auto":
        return access.mode, endpoint, None
    if endpoint.on_loopback:
        if access.ssh_settings() is None:
            return "error", endpoint, (
                f"kubeconfig server {endpoint.server} is on loopback, which no container can "
                "reach. Set K8SRCA_SSH_HOST and K8SRCA_SSH_REMOTE in .env "
                "(see docs/cluster-setup.md)"
            )
        return "ssh_tunnel", endpoint, None
    return "direct", endpoint, None


def ensure_tunnel(tunnel: C.Tunnel) -> Step:
    if tunnel.listening():
        return Step("ssh tunnel", True, f"already listening on {tunnel.bind_address}:{tunnel.port}")
    proc = subprocess.Popen(
        tunnel.command(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,   # survives this process exiting
    )
    for _ in range(20):
        if tunnel.listening():
            return Step("ssh tunnel", True,
                        f"started, listening on {tunnel.bind_address}:{tunnel.port}", changed=True)
        try:
            proc.wait(timeout=0.5)
            err = (proc.stderr.read().decode() if proc.stderr else "").strip()
            return Step("ssh tunnel", False, f"ssh exited: {err[:160] or 'no output'}")
        except subprocess.TimeoutExpired:
            continue
    return Step("ssh tunnel", False, "started but never began listening")


def mounted_kubeconfig(container: str = "k8srca-k8stools") -> str | None:
    r = C.run("docker", "inspect", container,
              "-f", '{{range .Mounts}}{{if eq .Destination "/etc/k8srca/kubeconfig"}}{{.Source}}{{end}}{{end}}')
    return r.stdout.strip() or None


def ensure_k8stools(cfg: Config, kubeconfig: Path, compose_file: Path, env_file: Path) -> Step:
    """(Re)start the k8stools container with the right uid and kubeconfig.

    The variables are passed in the subprocess environment, not only via
    --env-file: docker compose lets the *shell* environment win over an env
    file, and `load_dotenv()` has already put the host-side K8SRCA_KUBECONFIG
    there. Relying on --env-file alone silently mounts the host kubeconfig,
    whose server is on loopback -- so the container starts fine and every tool
    fails with connection refused.
    """
    values = {
        "K8SRCA_UID": str(os.getuid()),
        "K8SRCA_GID": str(os.getgid()),
        "K8SRCA_KUBECONFIG": str(kubeconfig),
    }
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    running = C.run("docker", "inspect", "-f", "{{.State.Running}}", "k8srca-k8stools")
    correct = mounted_kubeconfig() == str(kubeconfig)
    if running.stdout.strip() == "true" and correct:
        return Step("k8stools", True, "already running with the right kubeconfig")

    env = {**os.environ, **values}
    args = ["docker", "compose", "--env-file", str(env_file), "-f", str(compose_file),
            "up", "-d"]
    if running.stdout.strip() == "true" and not correct:
        args.append("--force-recreate")   # a stale mount cannot be fixed in place
    args.append("k8stools")
    r = subprocess.run(args, capture_output=True, text=True, timeout=180, env=env)
    if r.returncode != 0:
        return Step("k8stools", False, (r.stderr or r.stdout).strip()[:200])

    actual = mounted_kubeconfig()
    if actual != str(kubeconfig):
        return Step("k8stools", False,
                    f"started but mounted {actual}, expected {kubeconfig}")
    return Step("k8stools", True, f"started with {kubeconfig}", changed=True)


def egress_status(net: C.Network) -> Step:
    """Report whether the sandbox is confined. Cannot apply -- needs root."""
    probe = C.run(
        "docker", "run", "--rm", "--network", net.name, "alpine:latest",
        "sh", "-c", f"timeout 3 nc -z -w2 {net.gateway} 6443 && echo OPEN || echo blocked",
        timeout=90,
    )
    if "OPEN" in probe.stdout:
        return Step("egress rules", False,
                    "sandbox can reach the API server -- run: sudo ./docker/egress-rules.sh apply")
    if "blocked" in probe.stdout:
        return Step("egress rules", True, "sandbox is confined")
    return Step("egress rules", False, f"could not probe: {(probe.stderr or '').strip()[:120]}")


def bring_up(cfg: Config, compose_file: Path, env_file: Path) -> list[Step]:
    steps: list[Step] = []

    net = C.ensure_network(cfg.sandbox.network)
    steps.append(Step("docker network", True,
                      f"{net.name} subnet={net.subnet} gateway={net.gateway}"))

    mode, endpoint, problem = resolve_mode(cfg)
    if mode == "error":
        steps.append(Step("cluster access", False, problem or "unresolved"))
        return steps
    steps.append(Step("cluster access", True, f"mode={mode}"
                      + (f" server={endpoint.server}" if endpoint else "")))

    container_kubeconfig = cfg.cluster_access.container_kubeconfig or cfg.cluster_access.kubeconfig

    if mode == "ssh_tunnel":
        ssh = cfg.cluster_access.ssh_settings()
        assert ssh and endpoint
        tunnel = C.Tunnel(ssh_host=ssh.host, remote_endpoint=ssh.remote_endpoint,
                          bind_address=net.gateway, port=ssh.port)
        step = ensure_tunnel(tunnel)
        steps.append(step)
        if not step.ok:
            return steps
        # Derive rather than hardcode: the certificate is asked what name it
        # will answer to, so verification is redirected, never disabled.
        names = C.certificate_names(endpoint.host, endpoint.port)
        tls_name = C.pick_tls_server_name(names)
        C.write_container_kubeconfig(
            cfg.cluster_access.kubeconfig, container_kubeconfig,
            server=f"https://{net.gateway}:{ssh.port}", tls_server_name=tls_name,
        )
        steps.append(Step("container kubeconfig", True,
                          f"{container_kubeconfig} -> https://{net.gateway}:{ssh.port}"
                          f" (tls-server-name={tls_name or 'none'})", changed=True))
    else:
        container_kubeconfig = cfg.cluster_access.kubeconfig
        steps.append(Step("container kubeconfig", True, f"{container_kubeconfig} used as-is"))

    steps.append(ensure_k8stools(cfg, Path(container_kubeconfig).expanduser(),
                                 compose_file, env_file))
    if steps[-1].ok:
        steps.append(egress_status(net))
    return steps


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------

def process_running(pattern: str) -> bool:
    """Is a k8srca subcommand running? Matched against the venv entrypoint."""
    r = C.run("ps", "-eo", "args")
    return any(f"/k8srca {pattern}" in line for line in r.stdout.splitlines())


def cluster_reachable(cfg: Config) -> Step:
    """Call a real tool through k8stools, end to end.

    Checking that processes are running is not the same as checking that the
    data path works. A dead SSH forward leaves every process healthy and every
    tool broken, and reporting that as green is worse than reporting nothing:
    it sends you looking in the wrong place while the agent tells users it
    cannot reach the cluster.
    """
    import asyncio

    from .mcp_client import connect
    from .tools import wrap_mcp_tool

    server = cfg.mcp[0] if cfg.mcp else None
    if server is None:
        return Step("cluster reachable", False, "no MCP server configured")

    async def probe() -> str | None:
        async with connect(server.for_host()) as srv:
            tool = next((t for t in srv.tools if t.name == "get_namespaces"), None)
            if tool is None:
                return "k8stools exposes no get_namespaces tool"
            await wrap_mcp_tool(tool, srv.session, prefix=server.prefix).call({})
            return None

    try:
        problem = asyncio.run(asyncio.wait_for(probe(), timeout=45))
    except Exception as exc:  # noqa: BLE001 - the message is the diagnosis
        detail = root_cause(exc)
        hint = ""
        lowered = detail.lower()
        if "refused" in lowered or "executing tool" in lowered or "timed out" in lowered:
            hint = "\n        k8stools cannot reach the API server. Most likely the SSH forward\n" \
                   "        died -- run `k8srca up` to restore it."
        return Step("cluster reachable", False, f"{detail[:140]}{hint}")
    if problem:
        return Step("cluster reachable", False, problem)
    return Step("cluster reachable", True, "k8stools answered a live query")


def linger_check() -> Step | None:
    """Warn when user units are enabled but linger is off.

    systemd user units do not start at boot unless the user lingers; without
    it they wait for a first interactive login. The supervised tunnel would
    therefore be absent after a reboot until someone logged in -- and the
    symptom is the agent reporting it cannot reach the cluster while kubectl
    works, which is a slow thing to diagnose twice.

    Only relevant if units are actually enabled; otherwise this is noise.
    """
    import getpass

    units = C.run("systemctl", "--user", "list-unit-files", "k8srca-*", "--no-legend")
    enabled = [ln.split()[0] for ln in units.stdout.splitlines()
               if len(ln.split()) > 1 and ln.split()[1] == "enabled"]
    if not enabled:
        return None

    linger = C.run("loginctl", "show-user", getpass.getuser(), "--property=Linger")
    if "Linger=yes" in linger.stdout:
        return Step("boot persistence", True,
                    f"linger on; {len(enabled)} unit(s) start at boot")
    return Step(
        "boot persistence", True,
        f"{', '.join(enabled)} enabled but linger is OFF -- they will NOT start\n"
        f"        until you log in. Fix: sudo loginctl enable-linger {getpass.getuser()}",
        warn=True,
    )


def status(cfg: Config) -> list[Step]:
    """What is up, and -- more usefully -- what a Slack mention would do.

    `k8srca up` prepares cluster access only. It starts neither of the two
    long-running processes, so it is entirely possible to have every step of
    `up` green and a bot that ignores you.
    """
    steps: list[Step] = []

    net = C.Network.inspect(cfg.sandbox.network)
    steps.append(Step("docker network", net is not None,
                      f"{net.name} gateway={net.gateway}" if net else
                      f"{cfg.sandbox.network} missing -- run `k8srca up`"))

    k8stools_up = C.run("docker", "inspect", "-f", "{{.State.Running}}",
                        "k8srca-k8stools").stdout.strip() == "true"
    steps.append(Step("k8stools", k8stools_up,
                      "running" if k8stools_up else "not running -- run `k8srca up`"))

    # Either shape provides tool execution; neither means sessions hang.
    poller = process_running("poller")
    worker = process_running("worker")
    if poller or worker:
        steps.append(Step("tool execution", True,
                          "poller (containerised)" if poller else "worker (in-process, development)"))
    else:
        steps.append(Step("tool execution", False,
                          "neither poller nor worker -- sessions will start and never progress"))

    orchestrator = process_running("slack run")
    steps.append(Step("slack orchestrator", orchestrator,
                      "running -- mentions will be answered" if orchestrator else
                      "not running -- mentioning the bot does NOTHING; run `k8srca slack run`"))

    if k8stools_up:
        steps.append(cluster_reachable(cfg))
    if net:
        steps.append(egress_status(net))
    linger = linger_check()
    if linger is not None:
        steps.append(linger)
    return steps


# --------------------------------------------------------------------------
# Teardown
# --------------------------------------------------------------------------

def tear_down(cfg: Config, compose_file: Path, env_file: Path,
              remove_network: bool = False) -> list[Step]:
    """Stop what `up` started. The inverse, and deliberately not more.

    Leaves alone three things `up` did not create and cannot safely reclaim:

    * **The docker network**, unless asked. Removing it destroys the bridge the
      egress rules reference, silently orphaning them -- so the next `up`
      returns an *unconfined* sandbox. Removal takes an explicit flag, and says
      to reapply the rules.
    * **Session workspaces**, which hold investigation state for sessions that
      may still be live on Anthropic's side.
    * **The poller and orchestrator**, which are long-running processes owned by
      whoever started them, not by `up`.
    """
    steps: list[Step] = []

    running = C.run("docker", "inspect", "-f", "{{.State.Running}}", "k8srca-k8stools")
    if running.stdout.strip() == "true":
        r = C.run("docker", "compose", "--env-file", str(env_file), "-f", str(compose_file),
                  "stop", "k8stools", timeout=120)
        steps.append(Step("k8stools", r.returncode == 0,
                          "stopped" if r.returncode == 0 else (r.stderr or "").strip()[:160],
                          changed=r.returncode == 0))
    else:
        steps.append(Step("k8stools", True, "not running"))

    # Only the forward this project started, matched by its bind address: the
    # user's own loopback tunnel is not ours to stop.
    net = C.Network.inspect(cfg.sandbox.network)
    if net is not None:
        ps = C.run("ps", "-eo", "pid,args")
        killed = []
        for line in ps.stdout.splitlines():
            if "ssh -N" in line and f"{net.gateway}:" in line:
                pid = line.split()[0]
                if C.run("kill", pid).returncode == 0:
                    killed.append(pid)
        steps.append(Step("ssh tunnel", True,
                          f"stopped {len(killed)} forward(s) on {net.gateway}" if killed
                          else "none running on the docker gateway",
                          changed=bool(killed)))
        if killed:
            unit = C.run("systemctl", "--user", "is-enabled", "k8srca-tunnel")
            if unit.stdout.strip() == "enabled":
                steps.append(Step("note", True,
                                  "k8srca-tunnel is enabled and will restart it; "
                                  "`systemctl --user stop k8srca-tunnel` to keep it down"))

    if remove_network and net is not None:
        r = C.run("docker", "network", "rm", net.name, timeout=60)
        ok = r.returncode == 0
        steps.append(Step("docker network", ok,
                          f"{net.name} removed -- egress rules referenced its bridge and are "
                          f"now orphaned; reapply after the next `up`" if ok
                          else (r.stderr or "").strip()[:160], changed=ok))
    elif net is not None:
        steps.append(Step("docker network", True,
                          f"{net.name} left in place (--remove-network to delete; "
                          f"that orphans the egress rules)"))
    return steps
