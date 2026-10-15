import csv
import io
import json
import os
import tempfile
from pathlib import Path

from jobradar.models import Job


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        # Prevent spreadsheet formula execution when opening third-party text.
        return "'" + value
    return value


def render_export(jobs: list[Job], format: str, matches: dict | None = None) -> str:
    records = []
    for job in jobs:
        record = job.model_dump(mode="json")
        if matches is not None:
            record["match"] = matches[job.id].model_dump(mode="json")
        records.append(record)
    if format == "json":
        return json.dumps(records, ensure_ascii=False, indent=2) + "\n"
    if format != "csv":
        raise ValueError("Supported export formats: csv, json")
    stream = io.StringIO(newline="")
    fields = [*Job.model_fields, *(["match"] if matches is not None else [])]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows({key: _csv_value(value) for key, value in row.items()} for row in records)
    return stream.getvalue()


def write_export(path: Path, content: str) -> None:
    """Replace a file atomically; a failed export never leaves a half-written result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
