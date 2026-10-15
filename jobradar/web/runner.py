"""One background collection per web process, using its own database connection."""

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock

from jobradar.config import Settings, SourcesConfig
from jobradar.database import Repository
from jobradar.models import utc_now
from jobradar.services.collection import collect_jobs
from jobradar.services.configuration import ConfigurationStore

logger = logging.getLogger(__name__)


class CollectionInProgressError(Exception):
    """A collection is already running; its configuration must stay consistent."""


class CollectionManager:
    def __init__(self, settings: Settings, store: ConfigurationStore) -> None:
        self.settings = settings
        self.store = store
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jobradar-collect")
        self._closed = False
        self._state: dict = {
            "running": False,
            "status": "idle",
            "source": None,
            "started_at": None,
            "finished_at": None,
            "completed_boards": 0,
            "total_boards": 0,
            "results": [],
            "errors": [],
        }

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._state)

    @contextmanager
    def configuration_guard(self) -> Iterator[None]:
        """Keep configuration writes and collection submission mutually exclusive."""
        with self._lock:
            if self._state["running"]:
                raise CollectionInProgressError
            yield

    def start(self, source: str | None = None) -> dict:
        with self.configuration_guard():
            if self._closed:
                raise RuntimeError("Collection worker is shutting down")
            config = self.store.load_sources().model_copy(deep=True)
            self._state = {
                "running": True,
                "status": "running",
                "source": source,
                "started_at": utc_now().isoformat(),
                "finished_at": None,
                "completed_boards": 0,
                "total_boards": sum(
                    board.enabled and (source is None or board.source == source)
                    for board in config.sources
                ),
                "results": [],
                "errors": [],
            }
            try:
                self._executor.submit(self._run, config, source)
            except RuntimeError:
                self._state.update(
                    running=False, status="failed", finished_at=utc_now().isoformat()
                )
                raise
            return self.snapshot()

    @staticmethod
    def _errors(results: list[dict]) -> list[str]:
        return [
            f"{result.get('company', result.get('board', 'Source'))}: {error}"
            for result in results
            for error in result.get("errors", [])
        ]

    def _progress(self, result: dict) -> None:
        with self._lock:
            self._state["results"].append(deepcopy(result))
            self._state["completed_boards"] = len(self._state["results"])
            self._state["errors"] = self._errors(self._state["results"])

    def _run(self, config: SourcesConfig, source: str | None) -> None:
        try:
            with Repository(self.settings.database_url) as repository:
                results = collect_jobs(config, repository, source=source, on_board=self._progress)
            complete = all(
                result.get("complete") and not result.get("errors") for result in results
            )
            useful = any(result.get("complete") or result.get("fetched", 0) for result in results)
            with self._lock:
                self._state.update(
                    status="completed" if complete else ("partial" if useful else "failed"),
                    results=deepcopy(results),
                    completed_boards=len(results),
                    errors=self._errors(results),
                )
        except Exception as error:
            # SQL exception messages can include vacancy text as bound parameters.
            logger.error("Background collection failed (%s)", type(error).__name__)
            with self._lock:
                self._state["status"] = "failed"
                self._state["errors"].append(
                    f"Collection failed: {type(error).__name__}. Check configuration and database."
                )
        finally:
            with self._lock:
                self._state.update(running=False, finished_at=utc_now().isoformat())

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)
