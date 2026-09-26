"""The self-hosted sandbox worker (design 001 §4.2).

The worker is the MCP client. It connects to every configured MCP server,
registers the *union* of their tools alongside the built-in toolset, and serves
tool calls for every thread in the session. Agent declarations gate which model
sees which tool; this registration is what makes any of them executable.

Two entrypoints, same tool construction:

* ``run_forever`` — always-on, polls for work. Used on the host for
  development, where iterating on a container image per turn is friction.
* ``handle_one``  — services one already-claimed work item and exits. The
  container entrypoint for the sandbox-per-turn pattern; reads its ids from the
  forwarded ANTHROPIC_* environment.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any, Callable

from anthropic import AsyncAnthropic
from anthropic.lib.environments import EnvironmentWorker
from anthropic.lib.tools.agent_toolset import beta_agent_toolset_20260401

from ..config import Config
from ..mcp_client import connect_all
from ..tools import manifest_hash, declarations, wrap_mcp_tool

log = logging.getLogger("k8srca.worker")


class ManifestMismatch(RuntimeError):
    """The live MCP surface differs from what the agents declare (001 §4.3)."""


def verify_manifests(per_server: dict[str, list[dict]], expected: dict[str, str],
                     cfg: Config) -> None:
    """Fail the work item rather than serve a half-broken toolset.

    Each agent's manifest hash covers only the tools that agent declares, so a
    mismatch names the agent that is actually stale.
    """
    problems = []
    for key, agent in cfg.agents.items():
        if key not in expected:
            continue
        decls: list[dict] = []
        for server_name, group_name in agent.mcp_tools.items():
            spec = cfg.server(server_name)
            from ..tools import select
            decls += select(per_server[server_name], spec.group(group_name), spec.prefix)
        live = manifest_hash(decls)
        if live != expected[key]:
            problems.append(f"{key}: agent declares {expected[key]}, server offers {live}")
    if problems:
        raise ManifestMismatch(
            "MCP tool surface has drifted from the synced agents; run `k8srca sync`.\n  "
            + "\n  ".join(problems)
        )


class SkillsNotDelivered(RuntimeError):
    """The agent was configured with skills and received none."""


def expected_skills() -> tuple[str, ...]:
    raw = os.environ.get("K8SRCA_SKILLS", "").strip()
    return tuple(s for s in (part.strip() for part in raw.split(",")) if s)


async def watch_skills(workdir: str, expected: tuple[str, ...], seen: set[str],
                       interval: float = 0.5) -> None:
    """Record which skill directories appear while the turn runs.

    Sampling rather than checking once, because the window is transient: the
    platform downloads skills at the start of the turn and the SDK deletes each
    directory when its toolset context exits, so afterwards a successful
    download and a failed one look identical -- an empty `skills/` dir either
    way. That is the silent failure CLAUDE.md warns about, and the reason it is
    silent is that nothing was ever looking during the only moment you could see.
    """
    root = Path(workdir) / "skills"
    while True:
        try:
            for child in root.iterdir():
                if child.is_dir() and any(child.iterdir()):
                    seen.add(child.name)
        except (OSError, FileNotFoundError):
            pass
        if expected and seen.issuperset(expected):
            return                      # nothing left to watch for
        await asyncio.sleep(interval)


def report_skills(expected: tuple[str, ...], seen: set[str]) -> None:
    """Fail on total non-delivery; complain loudly about a partial one.

    Total failure is fatal because the answer that follows is the failure mode
    worth preventing: an RCA with no knowledge base reads exactly like one with
    it, and 001 §5 makes skills the only channel by which knowledge reaches a
    self-hosted sandbox at all. Serving that quietly is worse than serving
    nothing.

    A partial delivery logs an error instead of raising. Which skill is missing
    decides how much the answer is worth, and that is a judgement the operator
    can make from a log line -- whereas failing the item would discard an answer
    that may be entirely sound.
    """
    if not expected:
        return
    missing = sorted(set(expected) - seen)
    if not seen:
        raise SkillsNotDelivered(
            f"none of the agent's skills arrived in the workspace ({', '.join(expected)}). "
            f"The session would answer from general knowledge and look no different. "
            f"Usual cause: the workspace is not writable by the container user -- "
            f"see docker/spawn.sh."
        )
    if missing:
        log.error("skills_partial expected=%s missing=%s -- the answer is missing "
                  "knowledge it was configured with", ",".join(expected), ",".join(missing))
    else:
        log.info("skills_delivered %s", ",".join(sorted(seen)))


@contextlib.asynccontextmanager
async def build_tools(cfg: Config, *, host_side: bool, expected_manifests: dict[str, str] | None = None):
    """Open every MCP server and yield a tools factory for EnvironmentWorker.

    The sessions stay open for the worker's lifetime; closing them would break
    in-flight tool calls.
    """
    specs = [s.for_host() for s in cfg.mcp] if host_side else list(cfg.mcp)
    async with connect_all(specs) as servers:
        if expected_manifests:
            verify_manifests(
                {s.spec.name: declarations(s.tools, s.spec.prefix) for s in servers},
                expected_manifests, cfg,
            )
        mcp_tools: list[Any] = []
        for srv in servers:
            for tool in srv.tools:
                mcp_tools.append(wrap_mcp_tool(tool, srv.session, prefix=srv.spec.prefix))
            log.info("mcp_connect server=%s url=%s tools=%d",
                     srv.spec.name, srv.spec.url, len(srv.tools))

        def factory(env: Any) -> list[Any]:
            # The union: built-ins plus every wrapped MCP tool, for every thread.
            return [*beta_agent_toolset_20260401(env), *mcp_tools]

        yield factory


async def run_forever(cfg: Config, environment_id: str, environment_key: str,
                      workdir: str, expected_manifests: dict[str, str] | None = None) -> None:
    """Always-on worker. Polls until cancelled."""
    import asyncio
    import signal

    async with AsyncAnthropic(auth_token=environment_key) as client:
        async with build_tools(cfg, host_side=True, expected_manifests=expected_manifests) as factory:
            worker = EnvironmentWorker(
                client,
                environment_id=environment_id,
                environment_key=environment_key,
                workdir=workdir,
                tools=factory,
            )
            task = asyncio.create_task(worker.run())
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                # Cancel, never kill: the worker must finish its work item and
                # flush memory-store uploads before exiting (001 §7.3).
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, task.cancel)
            log.info("worker_ready environment=%s workdir=%s", environment_id, workdir)
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def handle_one(cfg: Config, workdir: str = "/workspace",
                     expected_manifests: dict[str, str] | None = None) -> None:
    """Service one already-claimed work item, then exit (container entrypoint)."""
    import asyncio
    import signal

    key = os.environ["ANTHROPIC_ENVIRONMENT_KEY"]
    async with AsyncAnthropic(auth_token=key) as client:
        async with build_tools(cfg, host_side=False, expected_manifests=expected_manifests) as factory:
            worker = EnvironmentWorker(client, workdir=workdir, tools=factory)
            task = asyncio.create_task(worker.handle_item())
            expected, seen = expected_skills(), set()
            watcher = asyncio.create_task(watch_skills(workdir, expected, seen))
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, task.cancel)
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            report_skills(expected, seen)
