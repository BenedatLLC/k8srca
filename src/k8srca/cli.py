"""k8srca command line."""

from __future__ import annotations

import asyncio
import json
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

    try:
        planned = asyncio.run(sync_mod.plan(cfg))
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

    state = State.load(Path(state_path))
    rev = sync_mod.git_rev()
    typer.secho("\napply", bold=True)

    def log(line: str) -> None:
        typer.echo(f"  {line}")

    try:
        state.environment_id = sync_mod.ensure_environment(client, cfg, state, log)
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
