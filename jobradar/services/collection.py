import logging
from collections.abc import Callable
from copy import deepcopy

from jobradar.collectors import GreenhouseCollector, LeverCollector, RetryingHTTPClient
from jobradar.config import SourcesConfig
from jobradar.database import Repository
from jobradar.models import utc_now

logger = logging.getLogger(__name__)


def collect_jobs(
    config: SourcesConfig,
    repository: Repository,
    *,
    source: str | None = None,
    http: RetryingHTTPClient | None = None,
    on_board: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Collect independently per board; transport and persistence are replaceable for tests."""
    if source is not None and source not in {"greenhouse", "lever"}:
        raise ValueError(f"Unknown source: {source}")
    owns_http = http is None
    http = http or RetryingHTTPClient(config.http)
    collectors = {"greenhouse": GreenhouseCollector(http), "lever": LeverCollector(http)}
    summaries = []
    try:
        for board in config.sources:
            if not board.enabled or (source and board.source != source):
                continue
            started_at = utc_now()
            jobs, complete, errors = [], False, []
            try:
                result = collectors[board.source].collect(board)
                jobs, complete, errors = result.jobs, result.complete, result.errors
            except Exception as exc:
                # The per-board boundary must survive unexpected adapter errors too.
                logger.exception("Collection failed for %s", board.board_key)
                errors = [f"{type(exc).__name__}: {exc}"]
            try:
                summary = repository.save_snapshot(
                    board,
                    jobs,
                    complete=complete,
                    mark_inactive=config.mark_inactive_on_complete,
                    allow_empty_deactivation=config.allow_empty_deactivation,
                    errors=errors,
                    started_at=started_at,
                )
            except Exception as exc:
                # Exception text/tracebacks from SQLAlchemy may include bound raw vacancy data.
                logger.error("Storage failed for %s (%s)", board.board_key, type(exc).__name__)
                summary = {
                    "source": board.source,
                    "company": board.name,
                    "board": board.board_key,
                    "complete": False,
                    "fetched": len(jobs),
                    "inserted": 0,
                    "updated": 0,
                    "inactivated": 0,
                    "errors": [*errors, f"Storage {type(exc).__name__}: database unavailable"],
                }
            summaries.append(summary)
            if on_board is not None:
                try:
                    on_board(deepcopy(summary))
                except Exception:
                    # A UI progress observer cannot change a persisted board outcome.
                    logger.exception("Collection progress callback failed for %s", board.board_key)
    finally:
        if owns_http:
            http.close()
    return summaries
