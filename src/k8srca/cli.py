"""k8srca command line."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer

from . import config as config_mod
from . import tools as tools_mod
from .mcp_client import connect_all
from .settings import MissingCredential, SlackSettings, load_dotenv

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Kubernetes RCA agent control plane")
tools_app = typer.Typer(no_args_is_help=True, help="Inspect the MCP tool surface and generated declarations")
app.add_typer(tools_app, name="tools")
slack_app = typer.Typer(no_args_is_help=True, help="Slack app configuration")
app.add_typer(slack_app, name="slack")

CONFIG = typer.Option("k8srca.yaml", "--config", "-c", help="Path to k8srca.yaml")


def _load(path: str) -> config_mod.Config:
    try:
        return config_mod.load(path)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
        typer.secho(f"config error: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc


async def _gather(cfg: config_mod.Config) -> dict[str, list[dict]]:
    """Connect to every server and build its full declaration set."""
    async with connect_all([s.for_host() for s in cfg.mcp]) as servers:
        return {
            s.spec.name: tools_mod.declarations(s.tools, s.spec.prefix) for s in servers
        }


@tools_app.command("list")
def tools_list(config: str = CONFIG, verbose: bool = typer.Option(False, "--verbose", "-v")):
    """List the tools each configured MCP server exposes, as the model will see them."""
    cfg = _load(config)
    per_server = asyncio.run(_gather(cfg))
    for server, decls in per_server.items():
        typer.secho(f"\n{server}  ({len(decls)} tools)", bold=True)
        for d in decls:
            props = ", ".join((d["input_schema"].get("properties") or {}).keys())
            typer.echo(f"  {d['name']:38} ({props})")
            if verbose:
                typer.echo(json.dumps(d["input_schema"], indent=4))


@tools_app.command("validate")
def tools_validate(config: str = CONFIG):
    """Verify every agent's tool routing resolves and the surface is servable.

    Runs the same checks `sync` does, without touching the Anthropic API:
    schema legality, cross-server name collisions, and group resolution.
    """
    cfg = _load(config)
    per_server = asyncio.run(_gather(cfg))

    try:
        tools_mod.check_collisions(per_server)
    except tools_mod.ToolSchemaError as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc

    total = sum(len(d) for d in per_server.values())
    typer.secho(f"schemas legal, no collisions ({total} tools across {len(per_server)} server(s))", fg="green")

    failures = 0
    for key in cfg.sync_order():
        agent = cfg.agents[key]
        selected: list[dict] = []
        for server, group_name in agent.mcp_tools.items():
            spec = cfg.server(server)
            try:
                selected += tools_mod.select(per_server[server], spec.group(group_name), spec.prefix)
            except (tools_mod.ToolSchemaError, KeyError) as exc:
                typer.secho(f"FAIL  {key}: {exc}", fg="red", err=True)
                failures += 1
        typer.echo(
            f"  {key:20} {agent.role:11} {agent.model.id:20} "
            f"{len(selected):2} mcp + {len(agent.builtin_tools)} builtin  "
            f"manifest={tools_mod.manifest_hash(selected)}"
        )
    if failures:
        raise typer.Exit(1)
    typer.secho("all agent tool routing resolves", fg="green")


kb_app = typer.Typer(no_args_is_help=True, help="Knowledge base")
app.add_typer(kb_app, name="kb")


@kb_app.command("build")
def kb_build(
    source: str = typer.Option("background/kubernetes_rca_knowledge_base_v2.json", "--source"),
    dest: str = typer.Option("skills/k8s-rca/knowledge_base.json", "--dest"),
):
    """Normalize the RCA knowledge base into the k8s-rca skill bundle."""
    from .kb.build import write

    src = Path(source)
    if not src.exists():
        typer.secho(f"FAIL  source not found: {src}", fg="red", err=True)
        raise typer.Exit(2)
    report = write(Path(dest), src)
    typer.echo(report.render())
    if report.unresolved:
        typer.secho("  (unresolved names are typos in the source; they are reported, not dropped)",
                    fg="yellow")
    typer.secho(f"wrote {dest}", fg="green")


@app.command("up")
def up_cmd(
    config: str = CONFIG,
    compose: str = typer.Option("docker/compose.yaml", "--compose"),
    env_file: str = typer.Option(".env.container", "--env-file"),
):
    """Bring up everything a reboot destroys: network, tunnel, k8stools.

    Idempotent -- safe to run at boot and as a diagnostic. Values are derived
    at run time (docker gateway, TLS server name, uid), so the only
    site-specific configuration is cluster_access.ssh in k8srca.yaml.

    Does not apply the egress rules; those need root and are reported instead.
    """
    from .bringup import bring_up

    load_dotenv()
    cfg = _load(config)
    steps = bring_up(cfg, Path(compose), Path(env_file))
    failed = False
    for step in steps:
        mark, colour = ("ok  ", "green") if step.ok else ("FAIL", "red")
        suffix = "  (changed)" if step.changed else ""
        typer.secho(f"{mark}  {step.name:22} {step.detail}{suffix}", fg=colour)
        failed = failed or not step.ok
    raise typer.Exit(1 if failed else 0)


@app.command("status")
def status_cmd(config: str = CONFIG):
    """Show what is running, and whether a Slack mention would be answered."""
    from .bringup import status

    load_dotenv()
    cfg = _load(config)
    steps = status(cfg)
    failed = False
    for step in steps:
        mark, colour = ("ok  ", "green") if step.ok else ("DOWN", "red")
        typer.secho(f"{mark}  {step.name:22} {step.detail}", fg=colour)
        failed = failed or not step.ok
    if failed:
        down = {s.name for s in steps if not s.ok}
        if {"tool execution", "slack orchestrator"} & down:
            typer.secho("\nNot ready. `k8srca up` prepares cluster access only -- it does not\n"
                        "start the poller or the orchestrator.", fg="yellow")
        else:
            typer.secho("\nProcesses are running but the path to the cluster is broken.\n"
                        "`k8srca up` is idempotent and repairs it.", fg="yellow")
    raise typer.Exit(1 if failed else 0)


@app.command("worker")
def worker_cmd(
    config: str = CONFIG,
    workdir: str = typer.Option("./.k8srca/workspace", "--workdir"),
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    check_manifest: bool = typer.Option(True, "--check-manifest/--no-check-manifest"),
):
    """Run the self-hosted worker in-process (development).

    Polls the environment's work queue and executes tool calls locally, with
    the configured MCP servers wrapped as custom tools. Production uses one
    container per turn instead; see docker/spawn.sh.
    """
    import logging

    from .state import State
    from .worker import runner

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
    cfg = _load(config)
    state = State.load(Path(state_path))
    if not state.environment_id:
        typer.secho("FAIL  no environment; run `k8srca sync` first", fg="red", err=True)
        raise typer.Exit(2)
    key = os.environ.get("ANTHROPIC_ENVIRONMENT_KEY", "").strip()
    if not key:
        typer.secho(
            "FAIL  ANTHROPIC_ENVIRONMENT_KEY is not set.\n"
            f"      Console -> Environments -> {state.environment_id} -> Generate environment key",
            fg="red", err=True)
        raise typer.Exit(2)

    manifests = {k: a.manifest for k, a in state.agents.items()} if check_manifest else None
    Path(workdir).mkdir(parents=True, exist_ok=True)

    # The agent drives `bash` in THIS process's environment. Anything left in
    # os.environ is readable by it -- and, with a kubeconfig present, writable
    # THROUGH it, since kubectl on the host is not restricted by k8stools'
    # read-only tool surface. Scrub before the worker starts (001 §8.2).
    from .settings import scrub_environment

    removed = scrub_environment()
    if removed:
        typer.secho(f"scrubbed from the worker environment: {', '.join(removed)}", fg="yellow")
    typer.secho(
        "NOTE: the in-process worker is a DEVELOPMENT shape. It runs agent-authored\n"
        "      bash directly on this host with no filesystem or network isolation.\n"
        "      Use `k8srca poller` (one container per work item) against anything\n"
        "      you would mind the agent reaching.",
        fg="yellow")
    typer.secho(f"worker starting: environment={state.environment_id} workdir={workdir}", fg="cyan")
    try:
        asyncio.run(runner.run_forever(cfg, state.environment_id, key, workdir, manifests))
    except runner.ManifestMismatch as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        typer.secho("worker stopped", fg="yellow")


@app.command("poller")
def poller_cmd(
    config: str = CONFIG,
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    script: str = typer.Option("docker/spawn.sh", "--spawn"),
):
    """Claim work items and run one sandbox container per turn (production shape).

    Unlike `k8srca worker`, this executes nothing itself: it holds only the
    environment key and hands each work item to an isolated container.
    """
    import logging

    import anthropic

    from .state import State
    from .worker.poller import SpawnConfig, run

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = _load(config)
    state = State.load(Path(state_path))
    if not state.environment_id:
        typer.secho("FAIL  no environment; run `k8srca sync` first", fg="red", err=True)
        raise typer.Exit(2)
    key = os.environ.get("ANTHROPIC_ENVIRONMENT_KEY", "").strip()
    if not key:
        typer.secho("FAIL  ANTHROPIC_ENVIRONMENT_KEY is not set", fg="red", err=True)
        raise typer.Exit(2)
    if not Path(script).exists():
        typer.secho(f"FAIL  spawn script not found: {script}", fg="red", err=True)
        raise typer.Exit(2)

    # The poller authenticates with the environment key only. An API key here
    # would sit on the host that runs agent-authored bash (001 §3.1).
    client = anthropic.Anthropic(auth_token=key, api_key=None)
    spawn_cfg = SpawnConfig(
        script=Path(script).resolve(),
        image=cfg.sandbox.image,
        network=cfg.sandbox.network,
        memory=cfg.sandbox.memory,
        cpus=cfg.sandbox.cpus,
        workspaces=Path(os.environ.get("K8SRCA_WORKSPACES", cfg.sandbox.workspaces)).expanduser(),
        manifests={k: a.manifest for k, a in state.agents.items()},
        max_concurrent=int(os.environ.get("K8SRCA_MAX_CONCURRENT_SESSIONS", "4")),
    )
    spawn_cfg.workspaces.mkdir(parents=True, exist_ok=True)
    typer.secho(f"poller: environment={state.environment_id} image={spawn_cfg.image}", fg="cyan")
    try:
        run(client, state.environment_id, spawn_cfg)
    except KeyboardInterrupt:
        typer.secho("poller stopped", fg="yellow")


@app.command("session")
def session_cmd(
    prompt: str = typer.Argument(..., help="What to ask the agent"),
    config: str = CONFIG,
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream the turn"),
    resume: str = typer.Option(None, "--resume", help="Continue an existing session id"),
):
    """Create a session and stream one turn, or continue one with --resume."""
    import anthropic

    from . import session as session_mod
    from .state import State

    load_dotenv()
    cfg = _load(config)
    state = State.load(Path(state_path))
    if not state.environment_id or cfg.coordinator not in state.agents:
        typer.secho("FAIL  not provisioned; run `k8srca sync` first", fg="red", err=True)
        raise typer.Exit(2)

    client = anthropic.Anthropic()
    if resume:
        sess = client.beta.sessions.retrieve(resume)
        typer.secho(f"session {sess.id} (resumed, status={sess.status})", bold=True)
    else:
        sess = session_mod.create(client, cfg, state, prompt, metadata={"trigger": "cli"})
        typer.secho(f"session {sess.id}", bold=True)
    typer.echo(f"  {session_mod.console_url(sess.id, os.environ.get('K8SRCA_WORKSPACE_ID'))}\n")
    if not follow:
        return

    def render(kind: str, detail: str) -> None:
        colours = {"tool": "blue", "thread": "magenta", "error": "red", "status": "yellow"}
        if kind == "message":
            typer.echo(f"\n{detail}\n")
        else:
            typer.secho(f"  [{kind}] {detail}", fg=colours.get(kind, "white"))

    # Stream before send (001 §7.2): the stream only delivers events emitted
    # after it opens. A new session is already running from initial_events.
    with client.beta.sessions.events.stream(session_id=sess.id) as stream:
        if resume:
            client.beta.sessions.events.send(
                session_id=sess.id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
            )
        turn = session_mod.consume(stream, render)

    typer.echo()
    typer.secho(
        f"stop_reason={turn.stop_reason} tools={len(turn.tool_calls)} "
        f"threads={len(turn.threads)} errors={len(turn.errors)}",
        fg="green" if turn.ok else "red",
    )
    if turn.tool_calls:
        typer.echo(f"  tools used: {', '.join(dict.fromkeys(turn.tool_calls))}")
    raise typer.Exit(0 if turn.ok else 1)


@app.command("timing")
def timing_cmd(
    session_id: str = typer.Argument(None, help="Session id (default: most recent)"),
    config: str = CONFIG,
):
    """Break down where a session's wall clock went."""
    import anthropic

    from .timing import build, render

    load_dotenv()
    client = anthropic.Anthropic()
    if session_id:
        sess = client.beta.sessions.retrieve(session_id)
    else:
        sess = next(iter(client.beta.sessions.list()), None)
        if sess is None:
            typer.secho("no sessions found", fg="red", err=True)
            raise typer.Exit(1)
    events = list(client.beta.sessions.events.list(session_id=sess.id))
    cost = getattr(getattr(sess.usage, "list_cost", None), "amount", None)
    typer.secho(f"session {sess.id}  ({(sess.title or '')[:50]})", bold=True)
    typer.echo(render(build(events), int(cost) if cost else None))


@app.command("sync")
def sync_cmd(
    config: str = CONFIG,
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; make no control-plane writes"),
    state_path: str = typer.Option(".k8srca/state.json", "--state", help="Where resolved IDs are stored"),
):
    """Apply k8srca.yaml to the Anthropic control plane.

    Creates the self-hosted environment and each agent on first run; on later
    runs updates agents in place, which produces a new version. Sessions pin
    their version at creation, so in-flight investigations are unaffected.
    """
    import anthropic

    from . import sync as sync_mod
    from .state import State

    load_dotenv()
    cfg = _load(config)

    absent = sync_mod.missing_skills(cfg)
    if absent:
        typer.secho(
            "note: skill directories not built yet, agents will be synced without them "
            f"({', '.join(str(p) for p in absent)}) -- Phase 2",
            fg="yellow",
        )
    for key, agent in cfg.agents.items():
        if sync_mod.read_system_prompt(agent) is None:
            typer.secho(f"note: {key} has no system prompt yet ({agent.system_prompt})", fg="yellow")

    state = State.load(Path(state_path))
    try:
        planned = asyncio.run(sync_mod.plan(cfg, state.skills))
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"FAIL  planning: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc

    typer.secho("\nplan", bold=True)
    for key in cfg.sync_order():
        p = planned[key]
        typer.echo(
            f"  {key:20} {p.cfg.role:11} {p.cfg.model.id:18} "
            f"{len(p.tools) - 1:2} mcp + {len(p.cfg.builtin_tools)} builtin  manifest={p.manifest}"
        )
        if p.cfg.roster:
            typer.echo(f"  {'':20} roster: {', '.join(p.cfg.roster)}")

    if dry_run:
        typer.secho("\ndry run: no changes made", fg="yellow")
        raise typer.Exit(0)

    try:
        client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"FAIL  no Anthropic credentials: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    rev = sync_mod.git_rev()
    typer.secho("\napply", bold=True)

    def log(line: str) -> None:
        typer.echo(f"  {line}")

    try:
        state.environment_id = sync_mod.ensure_environment(client, cfg, state, log)
        sync_mod.upload_skills(client, cfg, state, log)
        # Re-plan so agents reference the versions just uploaded.
        planned = asyncio.run(sync_mod.plan(cfg, state.skills))
        for key in cfg.sync_order():
            roster = sync_mod.resolve_roster(cfg, key, state)
            state.agents[key] = sync_mod.ensure_agent(client, planned[key], roster, state, rev, log)
        state.config_rev = rev
    finally:
        # Persist whatever succeeded: a partial sync must not orphan the IDs it
        # already created.
        state.save(Path(state_path))

    typer.secho(f"\nstate written to {state_path}", fg="green")
    typer.echo(
        "\nNext: open the environment in the Console and generate an environment key,\n"
        f"  then set ANTHROPIC_ENVIRONMENT_KEY in .env.\n"
        f"  Environment: {state.environment_id}"
    )


@slack_app.command("run")
def slack_run(
    config: str = CONFIG,
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    db: str = typer.Option(".k8srca/sessions.db", "--db"),
):
    """Run the Slack orchestrator (Socket Mode).

    Needs a worker or poller running separately -- this process drives
    sessions but executes no tools.
    """
    import logging

    from .slack.app import run as run_orchestrator
    from .slack.sessions import SessionStore
    from .state import State

    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    cfg = _load(config)
    state = State.load(Path(state_path))
    if not state.environment_id or cfg.coordinator not in state.agents:
        typer.secho("FAIL  not provisioned; run `k8srca sync` first", fg="red", err=True)
        raise typer.Exit(2)
    try:
        settings = SlackSettings.from_env()
    except MissingCredential as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    store = SessionStore(Path(db))
    pruned = store.prune_events()
    if pruned:
        typer.echo(f"pruned {pruned} old event ids")
    typer.secho(
        f"orchestrator starting: agent={state.agents[cfg.coordinator].id} "
        f"channels={sorted(settings.allowed_channels) or 'ALL'}", fg="cyan")
    try:
        run_orchestrator(cfg, state, settings, store, os.environ.get("K8SRCA_WORKSPACE_ID"))
    except KeyboardInterrupt:
        typer.secho("orchestrator stopped", fg="yellow")


@slack_app.command("check")
def slack_check(
    channel: str = typer.Option(None, "--channel", help="Channel id (default: first of SLACK_ALLOWED_CHANNELS)"),
    listen_seconds: int = typer.Option(45, "--listen", help="Seconds to wait for an inbound event; 0 to skip"),
    post: bool = typer.Option(True, "--post/--no-post", help="Post a test message to the channel"),
):
    """Verify the Slack app is configured correctly, end to end."""
    from slack_sdk import WebClient

    from .slack import check as chk

    load_dotenv()
    try:
        settings = SlackSettings.from_env()
    except MissingCredential as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    channel_id = channel or next(iter(sorted(settings.allowed_channels)), None)
    client = WebClient(token=settings.bot_token)
    failed = False

    def report(label: str, result: chk.CheckResult) -> None:
        nonlocal failed
        mark, colour = ("PASS", "green") if result.ok else ("FAIL", "red")
        typer.secho(f"{mark}  {label:22} {result.detail}", fg=colour)
        failed = failed or not result.ok

    auth, granted = chk.check_auth(client)
    report("bot token", auth)
    if not auth.ok:
        raise typer.Exit(1)
    report("scopes", chk.check_scopes(granted))

    if not channel_id:
        typer.secho("SKIP  channel                 no channel: pass --channel or set SLACK_ALLOWED_CHANNELS",
                    fg="yellow")
    else:
        report("channel membership", chk.check_channel(client, channel_id))
        if post and not failed:
            result, _ = chk.post_test_message(
                client, channel_id,
                ":wave: k8srca connectivity check - if you can see this, the bot token and channel are good.",
            )
            report("send", result)

    if listen_seconds > 0:
        typer.secho(
            f"\n  Listening {listen_seconds}s via Socket Mode. "
            f"Mention the app or post in {channel_id or 'a channel it is in'} now...",
            fg="cyan",
        )
        try:
            events = chk.listen(settings, channel_id, listen_seconds)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim
            typer.secho(f"FAIL  socket mode           {exc}", fg="red")
            raise typer.Exit(1) from exc
        if events:
            typer.secho(f"PASS  receive                {len(events)} event(s):", fg="green")
            for e in events:
                typer.echo(f"        {e['type']:18} user={e['user']} text={e['text'][:60]!r}")
        else:
            typer.secho(
                "FAIL  receive                socket mode connected but no events arrived.\n"
                "        Check: Event Subscriptions -> app_mention, message.channels, message.im\n"
                "        and that the bot is invited to the channel.",
                fg="red",
            )
            failed = True

    raise typer.Exit(1 if failed else 0)


if __name__ == "__main__":
    app()
