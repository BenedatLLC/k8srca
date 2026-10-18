"""k8srca command line."""

from __future__ import annotations

import asyncio
import collections
import json
import os
from dataclasses import dataclass
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
    source: str = typer.Option("vendor/kb/kubernetes_rca_knowledge_base_v2.json", "--source"),
    dest: str = typer.Option("skills/k8s-rca", "--dest", help="The k8s-rca bundle directory"),
):
    """Normalize the RCA knowledge base into the k8s-rca skill bundle."""
    from .kb.generator import KnowledgeBaseGenerator

    src = Path(source)
    if not src.exists():
        typer.secho(f"FAIL  source not found: {src}", fg="red", err=True)
        raise typer.Exit(2)
    _generate(KnowledgeBaseGenerator(source=src), None, Path(dest))


@kb_app.command("mine")
def kb_mine(
    family: str = typer.Argument(None, help="A family from docs/rca/mining/sources.yaml"),
    model: str = typer.Option("claude-opus-5", "--model"),
    refresh: bool = typer.Option(False, "--refresh", help="Fetch the sources again"),
    max_usd: float = typer.Option(10.0, "--max-usd", help="Stop when spending passes this"),
    config: str = CONFIG,
):
    """Mine public debugging guides into a draft knowledge layer (design 007 §8)."""
    from kubewiki.providers import has_credentials, provider_for

    from .kb import mine
    from .mcp_client import connect

    manifest = mine.load_manifest()
    if family is None:
        for name, f in manifest.families.items():
            typer.echo(f"{name:<20} {f.title} ({len(f.sources)} sources)")
        return
    load_dotenv()
    if not has_credentials(model):
        typer.secho("FAIL  no API key for the model in the environment", fg="red", err=True)
        raise typer.Exit(2)
    cfg = _load(config)
    spec = next((s for s in cfg.mcp if "k8stools" in s.capabilities()), None)
    if spec is None:
        typer.secho("FAIL  no MCP server provides k8stools", fg="red", err=True)
        raise typer.Exit(2)

    async def _tools() -> dict[str, str]:
        async with connect(spec.for_host()) as server:
            return {t.name: t.description or "" for t in server.tools}

    try:
        tools = asyncio.run(_tools())
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"FAIL  k8stools at {spec.host_url()}: {exc} (is `k8srca up` done?)",
                    fg="red", err=True)
        raise typer.Exit(1) from exc
    provider = provider_for(model)
    if hasattr(provider, "max_tokens"):
        # A family's merge writes every claim, fact and action at once.
        provider.max_tokens = mine.MAX_TOKENS
    report = mine.run(family, provider=provider, tools=tools, manifest=manifest,
                      refresh=refresh, max_usd=max_usd)
    typer.echo(report.render())
    if report.problems:
        typer.secho(f"draft fails {len(report.problems)} checks: fix it in review",
                    fg="yellow")


def _generate(generator, cfg, dest: Path):
    """Run a generator through the contract (005 §6.2) and print what it did."""
    from .core.generator import REPORTS, run

    try:
        bundle, report = asyncio.run(run(generator, cfg, dest))
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"FAIL  {generator.name}: {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    if report.lines or report.warnings:
        typer.echo(report.render())
    counts = ", ".join(f"{v} {k}" for k, v in report.counts.items())
    typer.secho(f"wrote {bundle.path} ({counts}) format {bundle.format}, "
                f"digest {bundle.content_digest}", fg="green")
    typer.echo(f"report: {REPORTS / (generator.name + '.json')}")
    return bundle, report


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


@app.command("down")
def down_cmd(
    config: str = CONFIG,
    compose: str = typer.Option("docker/compose.yaml", "--compose"),
    env_file: str = typer.Option(".env.container", "--env-file"),
    remove_network: bool = typer.Option(
        False, "--remove-network",
        help="Also delete the docker network. This orphans the egress rules."),
):
    """Stop what `up` started: the k8stools container and the SSH forward.

    Does not stop the poller or the orchestrator -- those are long-running
    processes owned by whoever started them -- and does not delete session
    workspaces, which hold state for sessions that may still be live.
    """
    from .bringup import tear_down

    load_dotenv()
    cfg = _load(config)
    steps = tear_down(cfg, Path(compose), Path(env_file), remove_network=remove_network)
    failed = False
    for step in steps:
        mark, colour = ("ok  ", "green") if step.ok else ("FAIL", "red")
        suffix = "  (changed)" if step.changed else ""
        typer.secho(f"{mark}  {step.name:22} {step.detail}{suffix}", fg=colour)
        failed = failed or not step.ok
    raise typer.Exit(1 if failed else 0)


sandbox_app = typer.Typer(no_args_is_help=True, help="The per-session sandbox image")
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("build")
def sandbox_build(config: str = CONFIG,
                  force: bool = typer.Option(False, "--force", help="Rebuild even if it exists")):
    """Build the sandbox image, tagged with the commit it was built from."""
    from . import sandbox as sbx

    cfg = _load(config)
    ref = sbx.image_ref(cfg.sandbox.image)
    dirty = sbx.dirty_inputs()
    if dirty:
        typer.secho(
            f"working tree differs from HEAD in {len(dirty)} image input(s): "
            f"{', '.join(dirty[:4])}{'...' if len(dirty) > 4 else ''}\n"
            "  The tag carries a content hash so this build is still identifiable,\n"
            "  but commit before building anything others will run.",
            fg="yellow")
    if sbx.image_exists(ref) and not force:
        typer.secho(f"{ref} already built (--force to rebuild)", fg="green")
        raise typer.Exit(0)
    typer.echo(f"building {ref} ...")
    result = sbx.build(ref)
    if result.returncode != 0:
        typer.secho((result.stderr or result.stdout).strip()[-800:], fg="red", err=True)
        raise typer.Exit(1)
    typer.secho(f"built {ref}", fg="green")


@sandbox_app.command("show")
def sandbox_show(config: str = CONFIG):
    """Show the image reference for the current tree, and whether it is built."""
    from . import sandbox as sbx

    cfg = _load(config)
    ref = sbx.image_ref(cfg.sandbox.image)
    built = sbx.image_exists(ref)
    typer.echo(f"  reference  {ref}")
    typer.echo(f"  built      {'yes' if built else 'NO -- run `k8srca sandbox build`'}")
    dirty = sbx.dirty_inputs()
    typer.echo(f"  tree       {'clean' if not dirty else f'{len(dirty)} modified image input(s)'}")
    for path in dirty[:8]:
        typer.echo(f"               {path}")
    raise typer.Exit(0 if built else 1)


@app.command("status")
def status_cmd(config: str = CONFIG):
    """Show what is running, and whether a Slack mention would be answered."""
    from .bringup import status

    load_dotenv()
    cfg = _load(config)
    steps = status(cfg)
    failed = False
    for step in steps:
        if not step.ok:
            mark, colour = "DOWN", "red"
        elif step.warn:
            mark, colour = "warn", "yellow"
        else:
            mark, colour = "ok  ", "green"
        typer.secho(f"{mark}  {step.name:22} {step.detail}", fg=colour)
        failed = failed or not step.ok
    if failed:
        down = {s.name for s in steps if not s.ok}
        if {"poller state", "orchestrator state"} & down:
            typer.secho("\nA process is running on an older `k8srca sync` than the one on disk.\n"
                        "Restart it so it loads the current agents and manifests.", fg="yellow")
        elif {"tool execution", "slack orchestrator"} & down:
            typer.secho("\nNot ready. `k8srca up` prepares cluster access only -- it does not\n"
                        "start the poller or the orchestrator.", fg="yellow")
        else:
            typer.secho("\nProcesses are running but the path to the cluster is broken.\n"
                        "The failed step above says which link.", fg="yellow")
    raise typer.Exit(1 if failed else 0)


scenario_app = typer.Typer(no_args_is_help=True,
                           help="Scenario suite: record captures, run and grade (design 004)")
app.add_typer(scenario_app, name="scenario")

SCENARIO_ROOT = typer.Option("tests/scenarios", "--root",
                             help="Directory holding scenario directories")

#: A run's own results are local state: they describe one invocation on one
#: machine, and promoting them is a deliberate act.
LAST_RUN_PATH = Path(".k8srca/scenario/last-run.json")

#: Full per-run detail -- answer, every grader note and claim untruncated --
#: one file per invocation, so earlier batches are not overwritten.
RUN_DETAIL_DIR = Path(".k8srca/scenario/runs")

#: The baseline is committed, beside the scenarios it was measured against.
#:
#: Left in .k8srca/ it was per-machine, which means a teammate's run reports
#: "new" for every scenario and CI has nothing to compare against -- the suite
#: exists to detect regressions, and an unshared baseline detects none. In the
#: tree it also becomes reviewable: a changed baseline shows up in a diff as
#: "we accepted a new rate", which is the moment that deserves a second pair of
#: eyes. It pins the capture and the skill it was taken against, so a reviewer
#: can tell an accepted regression from a re-recorded world.
BASELINE_PATH = Path("tests/scenarios/baseline.json")

#: Which form of the cluster-architecture skill a scenario runs with
#: (scenario/model.py ARCHES): the legacy snapshot or kubewiki's wiki.
ARCH_OPTION = typer.Option("wiki", "--arch",
                           help="cluster-architecture skill form: legacy or wiki")


#: Gradings per answer, majority per dimension (grader.GRADES). Part of the
#: grader's identity: a run graded otherwise is not comparable to the baseline.
GRADES_OPTION = typer.Option(None, "--grades",
                             help="Independent gradings per answer; each dimension takes "
                                  "the majority (default 3; changes the grader digest)")


@scenario_app.command("list")
def scenario_list(root: str = SCENARIO_ROOT, arch: str = ARCH_OPTION):
    """Every scenario on disk, and whether its truth still matches its capture."""
    from .scenario.model import StaleTruthError, discover

    found = discover(Path(root), arch)
    if not found:
        typer.secho(f"no scenarios under {root}", fg="yellow")
        raise typer.Exit(1)
    for sd in found:
        try:
            sd.check_truth_is_current()
            state, colour = "ok", typer.colors.GREEN
        except StaleTruthError:
            state, colour = "STALE", typer.colors.RED
        except FileNotFoundError:
            state, colour = "no capture", typer.colors.RED
        typer.secho(f"{state:<11}", fg=colour, nl=False)
        typer.echo(f"{sd.scenario.id:<28} {sd.scenario.question.strip().splitlines()[0][:60]}")


@scenario_app.command("record")
def scenario_record(
    scenario_id: str = typer.Argument(..., help="Scenario id; the directory is <root>/<id>"),
    root: str = SCENARIO_ROOT,
    config: str = CONFIG,
    namespace: list[str] = typer.Option([], "--namespace", "-n",
                                        help="Namespaces to capture (default: all)"),
    max_log_lines: int = typer.Option(200, "--max-log-lines"),
    arch_build: bool = typer.Option(True, "--arch-build/--no-arch-build",
                                    help="Rebuild the cluster-architecture skill first, so "
                                         "it and the capture describe the same moment"),
):
    """Capture the live cluster into a scenario directory.

    Runs inside the k8stools container, which is the only process holding a
    kubeconfig (CLAUDE.md).

    Two sources are recorded, not one. The k8stools capture is cluster state;
    the cluster-architecture skill is a *second observed read of the same
    cluster* (96% of its facts are `source: observed`) that the agent also
    cites. Recorded at different moments they describe different worlds, and
    every relative age the skill states -- `last_changed: 145d ago` -- is
    measured from its own build rather than from the replay clock.
    """
    from .scenario.record import (MAX_SKEW_S, RecordError, log_health, record,
                                  skew_seconds, snapshot_skill)

    load_dotenv()
    dest = Path(root) / scenario_id / "k8s.json"
    if arch_build:
        cfg = _load(config)
        if not cfg.architecture.active():
            typer.secho("no architecture sources configured; recording the capture only",
                        fg="yellow")
        else:
            typer.echo("rebuilding the cluster-architecture skill first...")
            from .arch.generator import ArchitectureGenerator

            _generate(ArchitectureGenerator(), cfg, Path("skills/cluster-architecture"))
    try:
        got = record(dest, namespaces=list(namespace), max_log_lines=max_log_lines)
    except RecordError as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc

    typer.secho(f"wrote {got.path} ({got.bytes_written:,} bytes, "
                f"{'redacted' if got.redacted else 'NOT REDACTED'})", fg="green")
    typer.echo(f"  {got.pods} pod(s), {got.containers} container(s)")
    typer.echo(f"  captured_at: {got.captured_at}")

    skill = snapshot_skill(dest.parent)
    if skill is None:
        typer.secho("  WARNING  no cluster-architecture skill to pin "
                    "(uv run k8srca arch build). The scenario cannot run: the agent "
                    "reads that skill as a second view of the cluster", fg="red")
    else:
        from .kb.skills import bundle_digest

        sha = bundle_digest(skill)
        typer.echo(f"  skill pinned: {skill} ({sha})")
        skew = skew_seconds(skill, got.captured_at)
        if skill.parent.name == "wiki":
            typer.echo("  skill is a wiki: it holds no state, so its build time "
                       "does not matter")
        elif skew is None:
            typer.secho("  WARNING  skill has no built_at; cannot check it against "
                        "the capture", fg="yellow")
        elif skew > MAX_SKEW_S:
            typer.secho(f"  WARNING  the skill was built {skew / 86400:.1f} days from the "
                        f"capture. They are two observed reads of one cluster, so this "
                        f"scenario describes two different worlds. Re-record with "
                        f"--arch-build.", fg="red")
        else:
            typer.echo(f"  skill/capture skew: {skew:.0f}s")

    health = log_health(json.loads(got.path.read_text()))
    if health["repr_blobs"]:
        typer.secho(f"  WARNING  {health['repr_blobs']}/{health['containers']} container "
                    f"log(s) are repr blobs -- k8stools < 2.0.4 (k8stools#6)", fg="red")
    else:
        typer.echo(f"  logs: {health['containers']} container(s), "
                   f"{health['identical']} with previous == current "
                   f"(expected for CrashLoopBackOff), {health['both_empty']} empty")
    typer.echo("")
    typer.echo("Next: write truth.yaml against THIS capture, and set")
    typer.echo(f"  capture:\n    captured_at: '{got.captured_at}'")
    if skill is not None:
        from .kb.skills import bundle_digest

        if skill.parent.name == "wiki":
            typer.echo(f"    skill_digests:\n      wiki: {bundle_digest(skill)}")
        else:
            typer.echo(f"    skill_digest: {bundle_digest(skill)}")
    typer.echo("A re-record invalidates truth.yaml until it is re-reviewed (004 §6.3).")


def _local_skill_paths(cfg, names: list[str]) -> dict[str, Path]:
    """`--local-skill` names -> their directories: the working tree's, from the config,
    or `name=DIR` for a variant kept elsewhere (an ablation, say).

    A variant's directory must be named for the skill: the upload names the
    skill after its directory, and the agent reads it at that path.
    """
    from .scenario.runner import SCENARIO_SCOPED_SKILLS

    known = {Path(p).name: Path(p) for a in cfg.agents.values() for p in a.skills}
    out = {}
    for spec in names:
        name, _, where = spec.partition("=")
        if where:
            variant = Path(where)
            if variant.name != name or not (variant / "SKILL.md").exists():
                typer.secho(f"FAIL  --local-skill {spec}: the directory must be named "
                            f"{name!r} and hold a SKILL.md", fg="red", err=True)
                raise typer.Exit(2)
            if name not in known:
                typer.secho(f"FAIL  no skill {name!r} in the config's agents to stand in "
                            f"for", fg="red", err=True)
                raise typer.Exit(2)
            out[name] = variant
            continue
        if name in SCENARIO_SCOPED_SKILLS:
            typer.secho(f"FAIL  {name} is already the scenario's own (pinned); "
                        f"--local-skill is for skills inherited from production",
                        fg="red", err=True)
            raise typer.Exit(2)
        if name not in known or not (known[name] / "SKILL.md").exists():
            typer.secho(f"FAIL  no skill {name!r} in the config's agents "
                        f"(known: {', '.join(sorted(known)) or 'none'})", fg="red", err=True)
            raise typer.Exit(2)
        out[name] = known[name]
    return out


@scenario_app.command("backfill-histories")
def scenario_backfill_histories(
    scenario_id: str = typer.Argument(..., help="Scenario id; the directory is <root>/<id>"),
    root: str = SCENARIO_ROOT,
    config: str = CONFIG,
    dry_run: bool = typer.Option(False, "--dry-run", help="Read and check, write nothing"),
):
    """Add workload histories to a capture recorded before k8stools 2.4.0.

    Reads each captured workload's history through the k8stools MCP server,
    shifts its ages back to the capture's instant, and refuses if any revision
    or ConfigMap write is newer than the capture. The capture's state is
    otherwise untouched; its truth still needs re-reading against what the
    histories now show.
    """
    import asyncio

    from kubewiki.mcp import connect

    from .scenario.backfill import BackfillError, backfill
    from .scenario.model import discover

    load_dotenv()
    cfg = _load(config)
    sd = next((s for s in discover(Path(root)) if s.scenario.id == scenario_id), None)
    if sd is None:
        typer.secho(f"no scenario {scenario_id!r} under {root}", fg="red", err=True)
        raise typer.Exit(1)
    capture = json.loads(sd.capture_path.read_text())
    server = cfg.mcp[0]

    async def go():
        async with connect(server.host_url(), float(server.timeout_s)) as (tools, call):
            if "get_workload_history" not in tools:
                raise BackfillError(f"{server.name} has no get_workload_history: "
                                    f"it needs k8stools 2.4.0 (uv run k8srca up)")
            return await backfill(capture, call)

    try:
        merged = asyncio.run(go())
    except BackfillError as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    for h in merged["workload_histories"]:
        revs = ", ".join(f"r{r['revision']} {r['age_seconds'] / 86400:.0f}d"
                         f" ({len(r['changes'])} change(s))" for r in h["revisions"])
        refs = ", ".join(f"{c['kind']} {c['name']}" for c in h["config"]) or "no ConfigMaps/Secrets"
        typer.echo(f"  {h['kind']}/{h['name']}: {revs}; {refs}")
    if dry_run:
        typer.echo("dry run: nothing written")
        return
    sd.capture_path.write_text(json.dumps(merged, indent=2))
    typer.secho(f"wrote {len(merged['workload_histories'])} histories into {sd.capture_path}",
                fg="green")
    typer.echo("Next: re-read truth.yaml against them (candidates about recent changes or "
               "configuration especially).")


@scenario_app.command("run")
def scenario_run(
    scenario_ids: list[str] = typer.Argument(None, help="Scenario ids (default: all)"),
    root: str = SCENARIO_ROOT,
    config: str = CONFIG,
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    environment: str = typer.Option(None, "--environment",
                                    help="Scenario environment id "
                                         "(default: K8SRCA_SCENARIO_ENVIRONMENT_ID)"),
    env_key_var: str = typer.Option("ANTHROPIC_TEST_ENVIRONMENT_KEY", "--env-key-var"),
    n: int = typer.Option(6, "--n",
                          help="Runs per scenario. 6 is the floor at which unanimity "
                               "says anything: the rule of three puts the 95% bound on "
                               "the unseen outcome at 3/n, so n=3 excludes nothing."),
    grader_model: str = typer.Option(None, "--grader-model",
                                     help="Model for rubric grading (default: opus, a "
                                          "different model from the one under test)"),
    full: bool = typer.Option(False, "--full",
                              help="Print grader notes and claims whole, and list "
                                   "unverifiable claims instead of counting them"),
    local_skill: list[str] = typer.Option([], "--local-skill",
                                          help="Use this skill (e.g. k8s-rca) from the "
                                               "working tree as the scenario's own copy, "
                                               "not production's, to measure a change "
                                               "before syncing it; NAME=DIR for a variant "
                                               "elsewhere (repeatable)"),
    arch: str = ARCH_OPTION,
    save: str = typer.Option(None, "--save",
                             help="Also save the results here, for `scenario compare`"),
    grades: int = GRADES_OPTION,
    no_review: bool = typer.Option(False, "--no-review",
                                   help="Skip the host's checks of the answer and its "
                                        "revision turn (007 §6.1): for a baseline "
                                        "skill that has no `kb_query.py open`"),
    reviewer: bool = typer.Option(None, "--reviewer/--no-reviewer",
                                  help="Also run the model reviewer on the answer "
                                       "(default: review.reviewer in the config)"),
):
    """Stand up each scenario's sources, run the agent, and check the answer.

    This spends money on every invocation, which is why it is a command and not
    a pytest target (004 §7).
    """
    from datetime import datetime, timezone

    from .scenario.model import discover

    setup = _suite_setup(config, state_path, environment)
    if no_review or reviewer is not None:
        update = {"completeness": False, "reviewer": False} if no_review else {"reviewer": reviewer}
        setup.cfg = setup.cfg.model_copy(
            update={"review": setup.cfg.review.model_copy(update=update)})
    found = [s for s in discover(Path(root), arch)
             if not scenario_ids or s.scenario.id in scenario_ids]
    if not found:
        typer.secho(f"no matching scenarios under {root}", fg="yellow")
        raise typer.Exit(1)

    from .scenario import report as rp

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    detail_path = _detail_path(RUN_DETAIL_DIR, stamp)
    runs, failures = _run_scenarios(
        [(sd.scenario.id, sd) for sd in found], n, setup,
        env_key_var=env_key_var, workdir=Path(".k8srca/scenario"),
        grader_model=grader_model, local_skills=_local_skill_paths(setup.cfg, local_skill),
        detail_path=detail_path, full=full, grades=grades)

    if runs:
        aggregates = rp.aggregate(runs)
        typer.echo("")
        for line in rp.table(aggregates, rp.load_baseline(BASELINE_PATH), grades):
            typer.echo(line)
        typer.echo("")
        typer.echo(rp.cost_summary(aggregates))
        for sid in sorted(aggregates):
            note = rp.variance_note(aggregates[sid])
            if note:
                typer.secho(f"{sid}: {note}", fg="yellow")
        rp.save_run(aggregates, LAST_RUN_PATH, grades)
        typer.echo(f"results saved to {LAST_RUN_PATH}; "
                   f"`k8srca scenario baseline` makes them the comparison point")
        if save:
            rp.save_run(aggregates, Path(save), grades)
            typer.echo(f"and to {save}, for `k8srca scenario compare`")
        typer.echo(f"full answers and grader notes: {detail_path}")
    raise typer.Exit(1 if failures else 0)


def _detail_path(directory: Path, stamp: str, kind: str = "") -> Path:
    """A run-detail file no other invocation is writing.

    Named by the second alone, two regrades started together appended to one
    file and their gradings could not be told apart afterwards. The pid keeps
    concurrent invocations apart; the stamp still sorts them.
    """
    return directory / f"{stamp}-{os.getpid()}{'-' + kind if kind else ''}.jsonl"


@dataclass
class _SuiteSetup:
    cfg: object
    state: object
    env_id: str
    image: str
    k8stools_image: str


def _suite_setup(config: str, state_path: str, environment: str | None) -> _SuiteSetup:
    """What every scenario run needs before it spends anything, or exit saying what is missing."""
    from .state import State
    from . import sandbox as sbx

    load_dotenv()
    cfg = _load(config)
    env_id = environment or os.environ.get("K8SRCA_SCENARIO_ENVIRONMENT_ID", "").strip()
    if not env_id:
        typer.secho("FAIL  no scenario environment. Pass --environment or set "
                    "K8SRCA_SCENARIO_ENVIRONMENT_ID in .env", fg="red", err=True)
        raise typer.Exit(2)
    image = sbx.image_ref(cfg.sandbox.image)
    if not sbx.image_exists(image):
        typer.secho(f"FAIL  sandbox image {image} is not built "
                    "(uv run k8srca sandbox build)", fg="red", err=True)
        raise typer.Exit(2)
    k8stools_image = os.environ.get("K8SRCA_SCENARIO_K8STOOLS_IMAGE") or _k8stools_image()
    return _SuiteSetup(cfg, State.load(Path(state_path)), env_id, image, k8stools_image)


def _run_scenarios(labelled, n: int, setup: _SuiteSetup, *, env_key_var: str, workdir: Path,
                   grader_model, local_skills, detail_path: Path, full: bool,
                   grades: int | None = None):
    """Run each (label, scenario) n times; return the runs and how many failed.

    Stops the suite on exhausted billing but keeps what completed: those runs
    were paid for and are the only thing the invocation bought.
    """
    from .scenario import report as rp
    from .scenario.model import StaleTruthError
    from .scenario.runner import BillingExhausted, RunError, run_once

    workdir.mkdir(parents=True, exist_ok=True)
    failures, runs = 0, []
    for name, sd in labelled:
        for attempt in range(1, n + 1):
            label = name + (f" [{attempt}/{n}]" if n > 1 else "")
            try:
                run = run_once(sd, setup.cfg, setup.state, environment_id=setup.env_id,
                               image=setup.k8stools_image, sandbox_image=setup.image,
                               env_key_var=env_key_var, workdir=workdir,
                               grader_model=grader_model, local_skills=local_skills,
                               grades=grades)
            except BillingExhausted as exc:
                typer.secho(f"STOP  {label}: {exc}", fg="red", err=True)
                typer.secho("      add credits, then re-run; results so far follow",
                            fg="yellow", err=True)
                return runs, failures
            except (RunError, StaleTruthError) as exc:
                typer.secho(f"FAIL  {label}: {exc}", fg="red", err=True)
                failures += 1
                continue
            _report_run(label, run, full=full)
            rp.append_run(rp.run_record(run, attempt), detail_path)
            runs.append(run)
            failures += 0 if run.passed or run.invalid else 1
    return runs, failures


@scenario_app.command("build-wiki")
def scenario_build_wiki(
    scenario_ids: list[str] = typer.Argument(None, help="Scenario ids (default: all)"),
    root: str = SCENARIO_ROOT,
    config: str = CONFIG,
    model: str = typer.Option(None, "--model", help="synthesis model, overriding k8srca.yaml"),
    synthesise: bool = typer.Option(True, "--synthesis/--no-synthesis"),
    max_usd: float = typer.Option(None, "--max-usd",
                                  help="refuse a synthesis estimated over this"),
):
    """Build each scenario's wiki from its own capture (kubewiki design §10, step 4).

    Writes skill/wiki/cluster-architecture/ beside the legacy snapshot, so
    `scenario run --arch wiki` can measure the agent with it. Synthesis spends
    money unless cached (~$0.90 for our install). truth.yaml must then be
    re-read against the wiki and its digest pinned before the scenario runs.
    """
    from .scenario.model import discover
    from .scenario.record import build_wiki

    load_dotenv()
    cfg = _load(config)
    found = [s for s in discover(Path(root), "wiki")
             if not scenario_ids or s.scenario.id in scenario_ids]
    if not found:
        typer.secho(f"no matching scenarios under {root}", fg="yellow")
        raise typer.Exit(1)
    image = os.environ.get("K8SRCA_SCENARIO_K8STOOLS_IMAGE") or _k8stools_image()
    for sd in found:
        typer.echo(f"{sd.scenario.id}: building from {sd.capture_path.name}, replayed")
        try:
            bundle, report = build_wiki(sd, cfg, image, model=model, synthesise=synthesise,
                                        max_usd=max_usd,
                                        reports=Path(".k8srca/scenario") / sd.scenario.id)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"FAIL  {sd.scenario.id}: {exc}", fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.echo(report.render())
        from .kb.skills import bundle_digest

        pinned = sd.truth.capture.pinned("wiki")
        digest = bundle_digest(bundle.path)
        typer.secho(f"wrote {bundle.path} ({', '.join(f'{v} {k}' for k, v in report.counts.items())})",
                    fg="green")
        if pinned == digest:
            typer.echo("  truth.yaml already pins this wiki")
        else:
            typer.echo("  Next: re-read truth.yaml's candidates and traps against this wiki, "
                       "then pin it:")
            typer.echo(f"    capture:\n      skill_digests:\n        wiki: {digest}")


@scenario_app.command("regrade")
def scenario_regrade(
    details: list[str] = typer.Argument(..., help="Run details (.k8srca/scenario/runs/*.jsonl)"),
    root: str = SCENARIO_ROOT,
    grader_model: str = typer.Option(None, "--grader-model"),
    full: bool = typer.Option(False, "--full", help="Print grader notes and claims whole"),
    save: str = typer.Option(None, "--save",
                             help="Also save the results here, for `scenario compare`"),
    grades: int = GRADES_OPTION,
):
    """Grade saved answers again under the current grader (004 §6.2).

    No sessions are run: only the grading, a few cents a run, is paid for
    again. Use it when the grader changes, so runs measured before the change
    can still be compared with runs after it. Pass the runs of one side of a
    comparison together; they are aggregated as one batch.
    """
    from datetime import datetime, timezone

    from anthropic import Anthropic

    from .scenario import report as rp
    from .scenario.model import discover
    from .scenario.runner import BillingExhausted, regrade

    load_dotenv()
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    scenarios: dict[tuple[str, str], object] = {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    detail_path = _detail_path(RUN_DETAIL_DIR, stamp, "regrade")
    runs = []
    stopped = False
    for source in details:
        if stopped:
            break
        for n, line in enumerate(Path(source).read_text().splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)
            arch = rp.record_arch(record)
            if (record["scenario_id"], arch) not in scenarios:
                for sd in discover(Path(root), arch):
                    scenarios[(sd.scenario.id, arch)] = sd
            sd = scenarios.get((record["scenario_id"], arch))
            if sd is None:
                typer.secho(f"FAIL  {source}:{n}: no scenario {record['scenario_id']} "
                            f"under {root}", fg="red", err=True)
                raise typer.Exit(1)
            try:
                run = regrade(sd, record, client=client, grader_model=grader_model,
                              grades=grades)
            except BillingExhausted as exc:
                # Every remaining grade would fail the same way. What was graded
                # is saved; re-run on the rest once there is credit.
                typer.secho(f"STOP  {source}:{n}: {exc}; results so far follow",
                            fg="red", err=True)
                stopped = True
                break
            _report_run(f"{run.scenario_id} [{arch} {record.get('attempt', n)}]", run, full=full)
            rp.append_run(rp.run_record(run, record.get("attempt", n)), detail_path)
            runs.append(run)
    if not runs:
        typer.secho("no runs to regrade", fg="yellow")
        raise typer.Exit(1)
    aggregates = rp.aggregate(runs)
    typer.echo("")
    for line in rp.table(aggregates, rp.load_baseline(BASELINE_PATH), grades):
        typer.echo(line)
    if save and stopped:
        typer.secho(f"not saved to {save}: the batch is incomplete, and a partial "
                    f"batch compared as a whole reads as a change", fg="yellow")
    elif save:
        rp.save_run(aggregates, Path(save), grades)
        typer.echo(f"saved to {save}, for `k8srca scenario compare`")
    typer.echo(f"full answers and grader notes: {detail_path}")
    raise typer.Exit(1 if stopped else 0)


@scenario_app.command("compare")
def scenario_compare(
    before: str = typer.Argument(..., help="A run saved with `scenario run --save`"),
    after: str = typer.Argument(None, help=f"Another (default: {LAST_RUN_PATH})"),
):
    """Two runs of the same scenarios side by side, e.g. legacy skill vs wiki."""
    from .scenario import report as rp

    b = rp.load_run(Path(before))
    a = rp.load_run(Path(after) if after else LAST_RUN_PATH)
    if b is None or a is None:
        typer.secho(f"no such run: {before if b is None else after or LAST_RUN_PATH}",
                    fg="red", err=True)
        raise typer.Exit(1)
    for line in rp.compare(b, a):
        typer.echo(line)


@scenario_app.command("baseline")
def scenario_baseline(
    force: bool = typer.Option(False, "--force",
                              help="Record even when dimensions disagreed with themselves"),
):
    """Make the last run's results the comparison point (004 §7).

    Promotes what was already measured rather than running the suite again: the
    numbers are the same either way, and a second suite costs what the first one
    did.
    """
    from .scenario import report as rp

    last = rp.load_run(LAST_RUN_PATH)
    if last is None:
        typer.secho(f"no run to promote ({LAST_RUN_PATH} does not exist); "
                    "run `k8srca scenario run` first", fg="red", err=True)
        raise typer.Exit(1)
    if not rp.baseline_is_comparable(last):
        typer.secho("the grader has changed since that run, so its numbers are not "
                    "comparable to anything this grader produces. Re-run, then "
                    "baseline.", fg="red", err=True)
        raise typer.Exit(2)

    unstable = rp.unstable_dimensions(last)
    if unstable and not force:
        typer.secho("refusing: these dimensions disagreed with themselves in that "
                    "run, so a delta against them is noise --", fg="red", err=True)
        for name in unstable:
            typer.secho(f"    {name}", fg="yellow", err=True)
        typer.echo("Raise n until they settle, or pass --force to record anyway "
                   "and read those columns as indicative.", err=True)
        raise typer.Exit(1)

    rp.promote(last, BASELINE_PATH)
    scenarios = rp.scenarios(last)
    typer.secho(f"baseline written to {BASELINE_PATH}", fg="green")
    typer.echo(f"  from the run of {last.get('taken_at')}, grader {last.get('grader')}")
    for sid, entry in sorted(scenarios.items()):
        typer.echo(f"  {sid}: {entry.get('runs')} run(s), "
                   f"capture {entry.get('capture_captured_at')}")
    if unstable:
        typer.secho(f"  recorded with --force; {len(unstable)} unstable dimension(s)",
                    fg="yellow")


holdout_app = typer.Typer(no_args_is_help=True,
                          help="Hold-out suite kit: author and score a suite we never see "
                               "(design 006)")
app.add_typer(holdout_app, name="holdout")

HOLDOUT_ROOT = typer.Option(..., "--root",
                            help="The hold-out suite: a directory outside this repository")

#: This repository, which a suite must not live inside, and whose commit a
#: score report names.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _holdout_suite(root: str) -> Path:
    from .scenario.holdout import HoldoutError, check_suite_location

    suite = Path(root)
    try:
        check_suite_location(suite, REPO_ROOT)
    except HoldoutError as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc
    suite.mkdir(parents=True, exist_ok=True)
    return suite


def _holdout_scenarios(suite: Path, ids: list[str] | None):
    """Every scenario in the suite that has a truth, or exit naming the broken one."""
    from .scenario.model import ScenarioDir

    found = []
    for path in sorted(p for p in suite.iterdir() if (p / "scenario.yaml").exists()):
        try:
            sd = ScenarioDir(path, "wiki")
        except FileNotFoundError:
            continue                          # no truth yet: not a scenario to run
        except ValueError as exc:
            typer.secho(f"FAIL  {path.name}: {exc}", fg="red", err=True)
            raise typer.Exit(1) from exc
        if not ids or sd.scenario.id in ids:
            found.append(sd)
    if not found:
        typer.secho(f"no matching scenarios with a truth.yaml under {suite}", fg="yellow")
        raise typer.Exit(1)
    return found


def _holdout_lint_or_exit(found) -> None:
    from .scenario.holdout import lint

    bad = 0
    for sd in found:
        result = lint(sd)
        colour = typer.colors.GREEN if result.passed else typer.colors.RED
        typer.secho(f"{'ok' if result.passed else 'FAIL':<5}", fg=colour, nl=False)
        typer.echo(sd.scenario.id)
        for problem in result.problems:
            typer.secho(f"      {problem}", fg="red")
        for note in result.advisories:
            typer.secho(f"      advisory: {note}", fg="yellow")
        bad += not result.passed
    if bad:
        raise typer.Exit(1)


@holdout_app.command("doctor")
def holdout_doctor(
    root: str = HOLDOUT_ROOT,
    config: str = CONFIG,
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    env_key_var: str = typer.Option("ANTHROPIC_TEST_ENVIRONMENT_KEY", "--env-key-var"),
):
    """Check this machine can author and score a hold-out suite, before anything is spent."""
    import subprocess

    from .scenario.holdout import HoldoutError, Manifest, check_suite_location
    from .state import State
    from . import sandbox as sbx

    load_dotenv()
    results: list[tuple[str, bool | None, str]] = []      # None: advisory

    def check(label: str, ok: bool | None, detail: str = "") -> None:
        results.append((label, ok, detail))

    try:
        check_suite_location(Path(root), REPO_ROOT)
        check("suite outside this repository", True, root)
    except HoldoutError as exc:
        check("suite outside this repository", False, str(exc))
    if (Path(root) / "holdout.yaml").exists():
        sealed = len(Manifest.load(Path(root)).entries)
        check("manifest", True, f"{sealed} sealed scenario(s)")
    for var in ("ANTHROPIC_API_KEY", env_key_var):
        check(f"{var} set", bool(os.environ.get(var, "").strip()),
              "" if os.environ.get(var) else "in .env, from your own workspace")
    state = State.load(Path(state_path))
    env_id = os.environ.get("K8SRCA_SCENARIO_ENVIRONMENT_ID", "").strip()
    check("scenario environment set", bool(env_id),
          "" if env_id else "create one in the Console; K8SRCA_SCENARIO_ENVIRONMENT_ID in .env")
    if env_id:
        check("scenario environment is not production's", env_id != state.environment_id,
              "" if env_id != state.environment_id else
              "K8SRCA_SCENARIO_ENVIRONMENT_ID equals the production environment")
    check("k8srca sync has run", "k8s-rca" in state.skills and bool(state.agents),
          "" if "k8s-rca" in state.skills else f"no k8s-rca skill in {state_path}")
    try:
        cfg = _load(config)
        image = sbx.image_ref(cfg.sandbox.image)
        check("sandbox image built for this commit", sbx.image_exists(image),
              image if sbx.image_exists(image) else f"{image}: uv run k8srca sandbox build")
    except Exception as exc:  # noqa: BLE001
        check("sandbox image built for this commit", False, str(exc))
    k8stools = os.environ.get("K8SRCA_SCENARIO_K8STOOLS_IMAGE") or _k8stools_image()
    check("k8stools image present", sbx.image_exists(k8stools),
          k8stools if sbx.image_exists(k8stools) else f"docker pull {k8stools}")
    running = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                              "k8srca-k8stools"], capture_output=True, text=True)
    check("k8stools container running (needed to record)",
          True if running.stdout.strip() == "true" else None,
          "" if running.stdout.strip() == "true" else "uv run k8srca up")
    from kubewiki.fetch import can_render_charts

    check("charts can be rendered (helm, needed for a chart source)",
          True if can_render_charts() else None, "" if can_render_charts() else
          "https://helm.sh/docs/intro/install/")

    failed = 0
    for label, ok, detail in results:
        mark, colour = (("ok", "green") if ok else ("FAIL", "red") if ok is False
                        else ("note", "yellow"))
        typer.secho(f"{mark:<5}", fg=colour, nl=False)
        typer.echo(f"{label}" + (f"  ({detail})" if detail else ""))
        failed += ok is False
    raise typer.Exit(1 if failed else 0)


@holdout_app.command("record")
def holdout_record(
    scenario_id: str = typer.Argument(..., help="Scenario directory under the suite"),
    root: str = HOLDOUT_ROOT,
    namespace: list[str] = typer.Option(..., "--namespace", "-n",
                                        help="Namespaces to capture (repeatable)"),
    max_log_lines: int = typer.Option(200, "--max-log-lines"),
):
    """Capture the live cluster into a scenario whose scenario.yaml is already written.

    scenario.yaml comes first because it names the wiki's sources: a scenario
    without them would describe our OpenTelemetry demo. Never rebuilds or copies
    this repository's own architecture skill; `holdout build-wiki` builds the
    scenario's from its capture.
    """
    from .scenario.holdout import Draft, HoldoutError, require_own_architecture
    from .scenario.record import RecordError, log_health, record

    load_dotenv()
    suite = _holdout_suite(root)
    try:
        draft = Draft.load(suite / scenario_id)
        require_own_architecture(draft.scenario)
    except (FileNotFoundError, HoldoutError, ValueError) as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    try:
        got = record(draft.capture_path, namespaces=list(namespace),
                     max_log_lines=max_log_lines)
    except RecordError as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"wrote {got.path} ({got.bytes_written:,} bytes, "
                f"{'redacted' if got.redacted else 'NOT REDACTED'})", fg="green")
    typer.echo(f"  {got.pods} pod(s), {got.containers} container(s)")
    health = log_health(json.loads(got.path.read_text()))
    if health["repr_blobs"]:
        typer.secho(f"  WARNING  {health['repr_blobs']} container log(s) are repr blobs: "
                    f"the k8stools image is too old", fg="red")
    typer.echo("")
    typer.echo(f"Next: uv run k8srca holdout build-wiki {scenario_id} --root {root}")
    typer.echo(f"then write truth.yaml against this capture, with\n"
               f"  capture:\n    captured_at: '{got.captured_at}'")


@holdout_app.command("build-wiki")
def holdout_build_wiki(
    scenario_ids: list[str] = typer.Argument(..., help="Scenario directories under the suite"),
    root: str = HOLDOUT_ROOT,
    config: str = CONFIG,
    model: str = typer.Option(None, "--model", help="synthesis model, overriding k8srca.yaml"),
    max_usd: float = typer.Option(None, "--max-usd",
                                  help="refuse a synthesis estimated over this"),
):
    """Build a scenario's wiki from its capture, from the sources its scenario.yaml names."""
    from .kb.skills import bundle_digest
    from .scenario.holdout import RUNS_DIR, Draft, HoldoutError, require_own_architecture
    from .scenario.record import build_wiki

    load_dotenv()
    suite = _holdout_suite(root)
    cfg = _load(config)
    image = os.environ.get("K8SRCA_SCENARIO_K8STOOLS_IMAGE") or _k8stools_image()
    for scenario_id in scenario_ids:
        try:
            draft = Draft.load(suite / scenario_id)
            require_own_architecture(draft.scenario)
            if not draft.capture_path.exists():
                raise HoldoutError(f"{scenario_id}: no capture yet (holdout record)")
            typer.echo(f"{scenario_id}: building from {draft.capture_path.name}, replayed")
            bundle, report = build_wiki(draft, cfg, image, model=model, max_usd=max_usd,
                                        reports=suite / RUNS_DIR / "wiki" / scenario_id)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"FAIL  {scenario_id}: {exc}", fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.echo(report.render())
        typer.secho(f"wrote {bundle.path}", fg="green")
        typer.echo(f"  pin it in truth.yaml:\n    capture:\n      skill_digests:\n"
                   f"        wiki: {bundle_digest(bundle.path)}")


@holdout_app.command("lint")
def holdout_lint(
    scenario_ids: list[str] = typer.Argument(None, help="Scenario ids (default: all)"),
    root: str = HOLDOUT_ROOT,
):
    """Mechanical checks on each truth.yaml: free, and run before anything is spent."""
    suite = _holdout_suite(root)
    _holdout_lint_or_exit(_holdout_scenarios(suite, scenario_ids))


@holdout_app.command("calibrate")
def holdout_calibrate(
    scenario_ids: list[str] = typer.Argument(..., help="Scenario ids"),
    root: str = HOLDOUT_ROOT,
    n: int = typer.Option(3, "--n", help="Runs per scenario"),
    config: str = CONFIG,
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    environment: str = typer.Option(None, "--environment"),
    env_key_var: str = typer.Option("ANTHROPIC_TEST_ENVIRONMENT_KEY", "--env-key-var"),
    grader_model: str = typer.Option(None, "--grader-model"),
):
    """Run scenarios with everything shown, to find errors in their truth before sealing.

    Read the answers for mistakes in the truth only. When a dimension disagrees
    with itself across runs, suspect the truth first (004 §5).
    """
    from datetime import datetime, timezone

    from .scenario import report as rp
    from .scenario.holdout import RUNS_DIR

    suite = _holdout_suite(root)
    found = _holdout_scenarios(suite, scenario_ids)
    _holdout_lint_or_exit(found)
    setup = _suite_setup(config, state_path, environment)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    detail_path = _detail_path(suite / RUNS_DIR, stamp, "calibrate")
    runs, _ = _run_scenarios([(sd.scenario.id, sd) for sd in found], n, setup,
                             env_key_var=env_key_var, workdir=suite / RUNS_DIR / "work",
                             grader_model=grader_model, local_skills={},
                             detail_path=detail_path, full=True)
    if runs:
        aggregates = rp.aggregate(runs)
        typer.echo("")
        for line in rp.table(aggregates):
            typer.echo(line)
        typer.echo(rp.cost_summary(aggregates))
        typer.echo(f"full answers and grader notes: {detail_path}")
        typer.echo("When the truth is right: uv run k8srca holdout seal <id> --root ...")


@holdout_app.command("seal")
def holdout_seal(
    scenario_id: str = typer.Argument(..., help="Scenario id"),
    root: str = HOLDOUT_ROOT,
    reason: str = typer.Option("", "--reason",
                               help="Why the truth changed (required to re-seal)"),
):
    """Freeze a calibrated scenario's truth and give it its opaque id."""
    from .scenario.holdout import HoldoutError, Manifest

    suite = _holdout_suite(root)
    found = _holdout_scenarios(suite, [scenario_id])
    _holdout_lint_or_exit(found)
    manifest = Manifest.load(suite)
    try:
        hid = manifest.seal(found[0], reason=reason)
    except HoldoutError as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    manifest.save(suite)
    typer.secho(f"{scenario_id} sealed as {hid}", fg="green")


@holdout_app.command("score")
def holdout_score(
    root: str = HOLDOUT_ROOT,
    n: int = typer.Option(6, "--n", help="Runs per scenario"),
    config: str = CONFIG,
    state_path: str = typer.Option(".k8srca/state.json", "--state"),
    environment: str = typer.Option(None, "--environment"),
    env_key_var: str = typer.Option("ANTHROPIC_TEST_ENVIRONMENT_KEY", "--env-key-var"),
    allow_dirty: bool = typer.Option(False, "--allow-dirty",
                                     help="Score an agent with uncommitted changes "
                                          "(the report says so)"),
):
    """Run every sealed scenario and write the score report: the only thing you send back.

    The report holds opaque ids and counts -- no answers, notes, claims,
    questions or scenario names (design 006 §6).
    """
    from datetime import datetime, timezone

    from .scenario import report as rp
    from .scenario.grader import apparatus_digest
    from .scenario.holdout import (REPORTS_DIR, RUNS_DIR, HoldoutError, Manifest,
                                   release_of, score_report)

    suite = _holdout_suite(root)
    manifest = Manifest.load(suite)
    try:
        sealed = manifest.sealed(_holdout_scenarios(suite, None))
    except HoldoutError as exc:
        typer.secho(f"FAIL  {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    if not sealed:
        typer.secho("nothing sealed yet (holdout seal)", fg="yellow")
        raise typer.Exit(1)
    release = release_of(REPO_ROOT)
    if release["dirty"] and not allow_dirty:
        typer.secho("FAIL  this checkout has uncommitted changes, so it is not the release "
                    "you were handed. Check out the tag, or pass --allow-dirty.",
                    fg="red", err=True)
        raise typer.Exit(2)
    _holdout_lint_or_exit([sd for _, sd in sealed])
    setup = _suite_setup(config, state_path, environment)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs, _ = _run_scenarios(sealed, n, setup, env_key_var=env_key_var,
                             workdir=suite / RUNS_DIR / "work", grader_model=None,
                             local_skills={},
                             detail_path=_detail_path(suite / RUNS_DIR, stamp, "score"),
                             full=False)
    if not runs:
        raise typer.Exit(1)
    aggregates = rp.aggregate(runs)
    report = score_report(aggregates, manifest, release=release, grader=apparatus_digest())
    name = release["tag"] or release["commit"][:12]
    out = suite / REPORTS_DIR / f"{name}-{stamp}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    out.write_text(yaml.safe_dump(report, sort_keys=False))
    typer.echo("")
    for line in rp.table({manifest.hid_of(sid): agg for sid, agg in aggregates.items()}):
        typer.echo(line)
    typer.secho(f"\nscore report: {out}", fg="green")
    typer.echo("Send that file, and nothing else from this suite.")


def _k8stools_image() -> str:
    """The image tag the compose file pins, so the replay matches production."""
    from .bringup import compose_image

    image = compose_image(Path("docker/compose.yaml"))
    if not image:
        raise typer.BadParameter("could not find the k8stools image in docker/compose.yaml")
    return image


def _report_run(label: str, run, full: bool = False) -> None:
    # Clipped by default so a batch stays scannable; --full because whether an
    # "unsupported" claim is the agent's error or the grader's often turns on
    # the part a clip removes.
    def clip(text: str) -> str:
        return text if full else text[:110]

    if run.invalid:
        # Not a result: the turn never finished, so nothing was measured (#2).
        typer.secho(f"{'SKIP':<5}", fg=typer.colors.YELLOW, nl=False)
        typer.echo(f"{label:<32} {len(run.tool_calls):>3} tool calls  ${run.usd:.4f}  "
                   f"invalid, not graded: {run.invalid}")
        if run.session_id:
            typer.echo(f"        session {run.session_id}")
        return
    colour = typer.colors.GREEN if run.passed else typer.colors.RED
    typer.secho(f"{'PASS' if run.passed else 'FAIL':<5}", fg=colour, nl=False)
    typer.echo(f"{label:<32} {len(run.tool_calls):>3} tool calls  ${run.usd:.4f}")
    for err in run.errors:
        typer.secho(f"        error: {err}", fg="red")
    for finding in (run.checks.findings if run.checks else []):
        typer.secho(f"        {finding.check}: {finding.detail}", fg="yellow")
    for note in (run.checks.advisories if run.checks else []):
        typer.echo(f"        advisory {note.check}: {note.detail}")
    if run.grade is not None:
        dims = run.grade.dimensions()
        votes = getattr(run.grade, "votes", {}) or {}

        def split(name: str) -> str:
            # A majority that was not unanimous says so: "evidence=NO(1/3)".
            cast = [v for v in votes.get(name, []) if v is not None]
            return f"({sum(cast)}/{len(cast)})" if cast and 0 < sum(cast) < len(cast) else ""

        cells = "  ".join(
            f"{name}={'-' if v is None else ('ok' if v else 'NO')}{split(name)}"
            for name, v in dims.items())
        typer.echo(f"        {cells}")
        if run.grade.cause_calibrated is False:
            typer.secho(f"        cause strength: {run.grade.cause_strength_given}",
                        fg="yellow")
        for c in run.grade.candidates:
            if not (c.standing_ok and c.calibrated):
                given = c.standing_given + ("" if c.strength_given in ("n/a", "unstated")
                                            else f" ({c.strength_given})")
                if c.standing_given == "ruled_out" and c.grounded is False:
                    given += ", on no excluding fact"
                typer.secho(f"        candidate {c.id}: {given} -- {clip(c.note)}",
                            fg="yellow")
        if run.grade.next_steps_ok is False:
            typer.secho(f"        next steps: {clip(run.grade.next_steps_note)}", fg="yellow")
        for trap in run.grade.traps:
            if not trap.handled:
                typer.secho(f"        trap {trap.id}: {clip(trap.note)}", fg="yellow")
        for gap in run.grade.gaps:
            if not gap.reported_unavailable:
                typer.secho(f"        gap {gap.id}: {clip(gap.note)}", fg="yellow")
        for claim in run.grade.unsupported_claims:
            typer.secho(f"        unsupported: {clip(claim)}", fg="yellow")
        if run.grade.unverifiable_claims and full:
            for claim in run.grade.unverifiable_claims:
                typer.echo(f"        unverifiable (not a failure): {claim}")
        elif run.grade.unverifiable_claims:
            typer.echo(f"        unverifiable (not failures): "
                       f"{len(run.grade.unverifiable_claims)}")
    review = getattr(run, "review", None)
    if review is not None:
        typer.echo(f"        review: {_review_line(review)}"
                   + ("; revised" if getattr(run, "first_answer", "") else ""))
    if run.session_id:
        typer.echo(f"        session {run.session_id}")


def _review_line(review: dict) -> str:
    bits = []
    if review.get("opens") == 0:
        bits.append("no `open` run; asked for it")
    elif review.get("opens") is not None:
        missing = review.get("missing") or []
        bits.append(f"{len(missing)} owed claim(s) unnamed: "
                    + ", ".join(m["id"] for m in missing) if missing else "complete")
    flags = review.get("flags")
    if flags is not None:
        kinds = collections.Counter(f["kind"] for f in flags)
        bits.append(f"reviewer {len(flags)} flag(s)"
                    + (" (" + ", ".join(f"{n} {k}" for k, n in kinds.most_common()) + ")"
                       if flags else ""))
    return "; ".join(bits) or "nothing checked"


eval_app = typer.Typer(no_args_is_help=True,
                       help="Component evals: score a generator without running an agent")
app.add_typer(eval_app, name="eval")


@eval_app.command("arch")
def eval_arch(
    cases: list[str] = typer.Argument(None, help="Case names (default: all)"),
    config: str = CONFIG,
    detail: int = typer.Option(8, "--detail", help="Items listed per finding"),
    judge: bool = typer.Option(False, "--judge",
                               help="Also check the documentation against the observed "
                                    "facts with a model (one call per case, ~$0.15)"),
    model: str = typer.Option(None, "--model",
                              help="Synthesise the wiki with this model and score its kinds "
                                   "(one call per case, ~$1 with claude-opus-5; cached)"),
    max_usd: float = typer.Option(2.0, "--max-usd",
                                  help="Refuse a synthesis estimated above this"),
):
    """Score the cluster-architecture generator against each case (005 §8.1).

    Replays each case's capture through k8stools (Docker, no cluster), builds
    kubewiki's wiki against it, checks it, and scores it on inventory, declared
    configuration, dependencies and documentation coverage. No model is
    called unless --judge or --model is given, so by default it costs nothing.
    """
    import json
    from datetime import datetime, timezone

    from .evals.architecture import discover, render_wiki as render, run_case

    load_dotenv()
    cfg = _load(config)
    found = [c for c in discover() if not cases or c.name in cases]
    if not found:
        typer.secho("no matching cases under tests/evals/architecture", fg="yellow")
        raise typer.Exit(1)
    judge_with = None
    if judge:
        from anthropic import Anthropic

        from .scenario.grader import GRADER_MODEL

        judge_with = (Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]), GRADER_MODEL)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    workdir = Path(".k8srca/evals/architecture") / stamp
    results = []
    for case_dir in found:
        try:
            score = run_case(case_dir, cfg, _k8stools_image(), workdir, judge=judge_with,
                             model=model, max_usd=max_usd)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"FAIL  {case_dir.name}: {exc}", fg="red", err=True)
            raise typer.Exit(1) from exc
        typer.echo(render(score, detail))
        typer.echo("")
        results.append(score.to_json())
    out = workdir / "results.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    typer.echo(f"skills and results: {workdir}")


arch_app = typer.Typer(no_args_is_help=True, help="Cluster architecture skill")
app.add_typer(arch_app, name="arch")


@arch_app.command("build")
def arch_build(config: str = CONFIG,
               dest: str = typer.Option("skills/cluster-architecture", "--dest"),
               model: str = typer.Option(None, "--model",
                                         help="synthesis model, overriding k8srca.yaml"),
               synthesise: bool = typer.Option(True, "--synthesis/--no-synthesis",
                                               help="--no-synthesis: the deterministic wiki"),
               max_usd: float = typer.Option(None, "--max-usd",
                                             help="refuse a synthesis estimated over this")):
    """Build the cluster-architecture skill: kubewiki's wiki of the deployment.

    Synthesis is cached by its inputs, so an unchanged deployment rebuilds free.
    The wiki must pass `kubewiki check`, or the skill in place is kept.
    """
    from .arch.generator import ArchitectureGenerator

    load_dotenv()
    cfg = _load(config)
    if not cfg.architecture.active():
        typer.secho("no architecture sources configured (see k8srca.yaml)", fg="yellow")
        raise typer.Exit(1)
    _generate(ArchitectureGenerator(model=model, synthesise=synthesise, max_usd=max_usd),
              cfg, Path(dest))


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
    # What the poller passes each sandbox, for kb_query.py (design 007 §4.1).
    os.environ["K8SRCA_CAPABILITIES"] = ",".join(cfg.capabilities())
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
    network: str = typer.Option(None, "--network",
                                help="Docker network for spawned sandboxes "
                                     "(default: sandbox.network from k8srca.yaml)"),
    env_key_var: str = typer.Option("ANTHROPIC_ENVIRONMENT_KEY", "--env-key-var",
                                    help="Environment variable holding the environment key"),
    image: str = typer.Option(None, "--image",
                              help="Sandbox image to spawn (default: derived from the "
                                   "working tree). Pin it when a caller has already "
                                   "resolved one, so a tree that changes mid-run cannot "
                                   "move the tag underneath."),
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
    key = os.environ.get(env_key_var, "").strip()
    if not key:
        typer.secho(f"FAIL  {env_key_var} is not set", fg="red", err=True)
        raise typer.Exit(2)
    if not Path(script).exists():
        typer.secho(f"FAIL  spawn script not found: {script}", fg="red", err=True)
        raise typer.Exit(2)

    # The poller authenticates with the environment key only. An API key here
    # would sit on the host that runs agent-authored bash (001 §3.1).
    client = anthropic.Anthropic(auth_token=key, api_key=None)
    from . import sandbox as sbx

    image_ref = image or sbx.image_ref(cfg.sandbox.image)
    if not sbx.image_exists(image_ref):
        typer.secho(
            f"FAIL  sandbox image {image_ref} is not built.\n"
            "      The tag is derived from the commit, so code changes need a rebuild:\n"
            "        uv run k8srca sandbox build",
            fg="red", err=True)
        raise typer.Exit(2)

    spawn_cfg = SpawnConfig(
        script=Path(script).resolve(),
        image=image_ref,
        network=network or cfg.sandbox.network,
        memory=cfg.sandbox.memory,
        cpus=cfg.sandbox.cpus,
        workspaces=cfg.sandbox.workspaces_dir(),
        manifests={k: a.manifest for k, a in state.agents.items()},
        skills=tuple(sorted(state.skills)),
        capabilities=tuple(cfg.capabilities()),
        max_concurrent=int(os.environ.get("K8SRCA_MAX_CONCURRENT_SESSIONS", "4")),
    )
    spawn_cfg.workspaces.mkdir(parents=True, exist_ok=True)
    from . import runtime
    runtime.write(Path(state_path), "poller", state, image=spawn_cfg.image)
    typer.secho(f"poller: environment={state.environment_id} image={spawn_cfg.image} "
                f"network={spawn_cfg.network} "
                f"capabilities={','.join(spawn_cfg.capabilities)}", fg="cyan")
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
        after = None
        if resume:
            after = session_mod.sent_event_id(client.beta.sessions.events.send(
                session_id=sess.id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
            ))
        events = session_mod.turn_events(client, sess.id, stream,
                                         from_start=not resume, after=after)
        turn = session_mod.consume(events, render)

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
    from . import runtime
    runtime.write(Path(state_path), "slack", state)
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
