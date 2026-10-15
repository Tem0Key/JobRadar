import json

from typer.testing import CliRunner

from jobradar.cli import app
from jobradar.config import SourceConfig
from jobradar.database import Repository
from jobradar.models import Job

runner = CliRunner()


def populated_db(tmp_path):
    url = f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}"
    source = SourceConfig(name="Example", source="greenhouse", identifier="example")
    with Repository(url) as repository:
        repository.save_snapshot(
            source,
            [
                Job(
                    source="greenhouse",
                    source_job_id="42",
                    source_board=source.board_key,
                    company=source.name,
                    title="Firewall Engineer",
                    description="NGFW remote worldwide",
                    location="Remote worldwide",
                    remote_type="remote",
                    url="https://example.com/jobs/42",
                )
            ],
            complete=True,
        )
    return url


def test_cli_list_search_stats_and_exports(tmp_path):
    url = populated_db(tmp_path)
    prefix = ["--database-url", url]
    result = runner.invoke(app, [*prefix, "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["source_job_id"] == "42"
    result = runner.invoke(app, [*prefix, "search", "--query", "firewall", "--json"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)) == 1
    result = runner.invoke(app, [*prefix, "search", "--profile", "Network Security", "--explain"])
    assert result.exit_code == 0, result.output
    assert "Firewall Engineer" in result.stdout
    result = runner.invoke(app, [*prefix, "stats", "--duplicates"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["total"] == 1
    for format in ("csv", "json"):
        path = tmp_path / f"jobs.{format}"
        result = runner.invoke(
            app,
            [
                *prefix,
                "export",
                "--format",
                format,
                "--output",
                str(path),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Firewall Engineer" in path.read_text(encoding="utf-8")


def test_cli_usage_errors(tmp_path):
    url = populated_db(tmp_path)
    prefix = ["--database-url", url]
    assert runner.invoke(app, [*prefix, "search"]).exit_code == 2
    result = runner.invoke(app, [*prefix, "search", "--profile", "missing"])
    assert result.exit_code == 1
    assert "Unknown profile" in result.output
    assert runner.invoke(app, [*prefix, "export", "--format", "xml"]).exit_code == 2


def test_cli_empty_and_partial_collect(tmp_path, monkeypatch):
    prefix = ["--database-url", f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}"]
    monkeypatch.setattr("jobradar.cli.collect_jobs", lambda *a, **kw: [])
    result = runner.invoke(app, [*prefix, "collect"])
    assert result.exit_code == 0
    assert "No enabled sources" in result.output
    monkeypatch.setattr(
        "jobradar.cli.collect_jobs",
        lambda *a, **kw: [
            {"complete": False, "errors": ["network"]},
        ],
    )
    assert runner.invoke(app, [*prefix, "collect"]).exit_code == 2


def test_cli_displays_untrusted_brackets_as_text(tmp_path):
    url = populated_db(tmp_path)
    source = SourceConfig(name="Example", source="greenhouse", identifier="example")
    with Repository(url) as repository:
        repository.save_snapshot(
            source,
            [
                Job(
                    source="greenhouse",
                    source_job_id="43",
                    source_board=source.board_key,
                    company=source.name,
                    title="Network [/bold] Engineer",
                    location="Remote [US]",
                    url="https://example.com/jobs/43",
                )
            ],
            complete=True,
        )
    result = runner.invoke(app, ["--database-url", url, "list"])
    assert result.exit_code == 0, result.output
    assert "[/bold]" in result.stdout


def test_cli_reports_yaml_and_database_errors_cleanly(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("sources: [invalid:", encoding="utf-8")
    result = runner.invoke(app, ["--sources-file", str(path), "collect"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    database = tmp_path / "corrupt.db"
    database.write_text("not a SQLite database", encoding="utf-8")
    result = runner.invoke(app, ["--database-url", f"sqlite:///{database.as_posix()}", "stats"])
    assert result.exit_code == 1
    assert "Error:" in result.output
