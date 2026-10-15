import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.exc import SQLAlchemyError
from yaml import YAMLError

from jobradar.config import Settings, load_profiles, load_sources
from jobradar.database import Repository
from jobradar.filters import evaluate
from jobradar.services.collection import collect_jobs
from jobradar.services.export import render_export, write_export

app = typer.Typer(no_args_is_help=True, help="JobRadar — public job board aggregation.")
console = Console(markup=False)
OPERATIONAL_ERRORS = (OSError, ValueError, YAMLError, SQLAlchemyError)


class Status(StrEnum):
    active = "active"
    inactive = "inactive"
    unknown = "unknown"
    all = "all"


class ExportFormat(StrEnum):
    csv = "csv"
    json = "json"


@app.callback()
def main(
    ctx: typer.Context,
    database_url: Annotated[str | None, typer.Option(help="Override JOBRADAR_DATABASE_URL")] = None,
    sources_file: Annotated[Path | None, typer.Option(help="Sources YAML path")] = None,
    profiles_file: Annotated[Path | None, typer.Option(help="Profiles YAML path")] = None,
) -> None:
    values = {
        key: value
        for key, value in {
            "database_url": database_url,
            "sources_file": sources_file,
            "profiles_file": profiles_file,
        }.items()
        if value is not None
    }
    try:
        ctx.obj = Settings(**values)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _error(exc: Exception) -> None:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(1) from exc


def _json(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _select(ctx, source, status, company, query=None, profile=None):
    settings: Settings = ctx.obj
    selected_profile = load_profiles(settings.profiles_file).get(profile) if profile else None
    with Repository(settings.database_url) as repository:
        jobs = repository.list_jobs(
            source=source, status=None if status == Status.all else status.value, company=company
        )
    matches = {}
    selected = []
    for job in jobs:
        result = evaluate(job, selected_profile, query)
        if result.matched:
            selected.append(job)
            matches[job.id] = result
    return selected, matches


def _show(jobs, matches, as_json, limit, explain=False):
    if as_json:
        _json(
            [
                {**job.model_dump(mode="json"), "match": matches[job.id].model_dump(mode="json")}
                for job in jobs[:limit]
            ]
        )
        return
    table = Table("Company", "Title", "Location", "Remote", "Status", "Eligibility")
    for job in jobs[:limit]:
        result = matches[job.id]
        table.add_row(
            job.company,
            job.title,
            job.location or "?",
            job.remote_type,
            job.status,
            result.eligibility,
        )
    console.print(table)
    typer.echo(f"Showing {min(limit, len(jobs))} / {len(jobs)}")
    if explain:
        for job in jobs[:limit]:
            result = matches[job.id]
            typer.echo(f"\n{job.company} — {job.title}\n{job.url}")
            for reason in [*result.reasons, *result.eligibility_reasons]:
                typer.echo(f"  - {reason}")


@app.command()
def collect(
    ctx: typer.Context,
    source: Annotated[str | None, typer.Option(help="greenhouse or lever")] = None,
) -> None:
    """Fetch all enabled boards. Exit 2 means one or more incomplete collections."""
    try:
        config = load_sources(ctx.obj.sources_file)
        with Repository(ctx.obj.database_url) as repository:
            summaries = collect_jobs(config, repository, source=source)
    except OPERATIONAL_ERRORS as exc:
        _error(exc)
    _json(summaries)
    if not summaries:
        typer.echo("No enabled sources selected.", err=True)
    if any(not summary["complete"] or summary["errors"] for summary in summaries):
        raise typer.Exit(2)


@app.command("list")
def list_command(
    ctx: typer.Context,
    source: str | None = None,
    company: str | None = None,
    status: Status = Status.active,
    limit: Annotated[int, typer.Option(min=1)] = 50,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List saved jobs; --json includes full descriptions and raw source data."""
    try:
        jobs, matches = _select(ctx, source, status, company)
        _show(jobs, matches, as_json, limit)
    except OPERATIONAL_ERRORS as exc:
        _error(exc)


@app.command()
def search(
    ctx: typer.Context,
    query: str | None = None,
    profile: str | None = None,
    source: str | None = None,
    company: str | None = None,
    status: Status = Status.active,
    limit: Annotated[int, typer.Option(min=1)] = 50,
    explain: bool = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Search by phrase, configured profile, or both (AND)."""
    if not (query and query.strip()) and not profile:
        raise typer.BadParameter("Provide --query or --profile")
    try:
        jobs, matches = _select(ctx, source, status, company, query, profile)
        _show(jobs, matches, as_json, limit, explain)
    except OPERATIONAL_ERRORS as exc:
        _error(exc)


@app.command()
def stats(ctx: typer.Context, duplicates: bool = False) -> None:
    """Show persisted job counts, collection runs and optional duplicate candidates."""
    try:
        with Repository(ctx.obj.database_url) as repository:
            result = repository.stats()
            if duplicates:
                result["possible_duplicates"] = repository.possible_duplicates()
        _json(result)
    except OPERATIONAL_ERRORS as exc:
        _error(exc)


@app.command()
def export(
    ctx: typer.Context,
    format: ExportFormat = ExportFormat.json,
    output: Path | None = None,
    query: str | None = None,
    profile: str | None = None,
    source: str | None = None,
    company: str | None = None,
    status: Status = Status.active,
) -> None:
    """Export all selected results to stdout, or atomically to --output."""
    try:
        jobs, matches = _select(ctx, source, status, company, query, profile)
        content = render_export(jobs, format.value, matches if profile or query else None)
        if output:
            write_export(output, content)
            typer.echo(f"Exported {len(jobs)} jobs to {output}", err=True)
        else:
            typer.echo(content, nl=False)
    except OPERATIONAL_ERRORS as exc:
        _error(exc)


@app.command()
def serve(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(min=1024, max=65535, help="Local web panel port")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open a browser")] = False,
) -> None:
    """Open local web management at http://127.0.0.1:8765. Stop with Ctrl+C."""
    from jobradar.web.launch import run_web

    typer.echo(f"JobRadar: http://127.0.0.1:{port} — stop with Ctrl+C")
    try:
        run_web(ctx.obj, port=port, open_browser=open_browser)
    except OPERATIONAL_ERRORS as exc:
        _error(exc)
