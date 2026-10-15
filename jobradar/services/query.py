"""Saved vacancy selection shared by web views, exports and future transports."""

import hashlib
import re
import threading
import unicodedata
from collections import OrderedDict

from jobradar.config import SearchProfile, Settings, load_profiles
from jobradar.database import Repository
from jobradar.filters import MatchResult, evaluate
from jobradar.filters.rules import matches_title
from jobradar.models import Job
from jobradar.models.locations import workplace_locations


def _normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "").casefold()).strip()


class VacancyQuery:
    """Read current configuration and apply explainable matching to saved vacancies.

    Database predicates and inexpensive text filters run before geography/profile
    evaluation. Cached results include every normalized job field and the complete
    profile contents, so changing a description or saving a profile takes effect
    immediately even if a source did not change its update timestamp.
    """

    def __init__(self, settings: Settings, *, cache_size: int = 2048) -> None:
        if cache_size < 1:
            raise ValueError("cache_size must be positive")
        self.settings = settings
        self._cache_size = cache_size
        self._cache: OrderedDict[bytes, MatchResult] = OrderedDict()
        self._cache_lock = threading.Lock()

    def _profile(self, name: str | None) -> SearchProfile | None:
        # Web configuration saves may replace settings.profiles_file at runtime.
        return load_profiles(self.settings.profiles_file).get(name) if name else None

    def _evaluate(
        self, job: Job, profile: SearchProfile | None, profile_json: str, query: str | None
    ) -> MatchResult:
        fingerprint = hashlib.sha256(job.model_dump_json(exclude={"raw_data"}).encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(profile_json.encode("utf-8"))
        fingerprint.update(str(workplace_locations(job.raw_data)).encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update((query or "").encode("utf-8"))
        key = fingerprint.digest()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached.model_copy(deep=True)
        result = evaluate(job, profile, query)
        with self._cache_lock:
            self._cache[key] = result.model_copy(deep=True)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return result

    def select(
        self,
        *,
        query: str | None = None,
        profile: str | None = None,
        source: str | None = None,
        status: str | None = "active",
        company: str | None = None,
        remote_type: str | None = None,
        eligibility: str | None = None,
        location: str | None = None,
        seniority: str | None = None,
        language: str | None = None,
    ) -> tuple[list[Job], dict[str, MatchResult]]:
        if source is not None and source not in {"greenhouse", "lever"}:
            raise ValueError(f"Unknown source: {source}")
        if status not in {None, "all", "active", "inactive", "unknown"}:
            raise ValueError(f"Unknown status: {status}")
        if remote_type is not None and remote_type not in {"remote", "hybrid", "onsite", "unknown"}:
            raise ValueError(f"Unknown remote type: {remote_type}")
        if eligibility is not None and eligibility not in {"allowed", "needs_review", "excluded"}:
            raise ValueError(f"Unknown eligibility: {eligibility}")
        selected_profile = self._profile(profile)
        profile_json = selected_profile.model_dump_json() if selected_profile else "null"
        query = query.strip() if query else None
        with Repository(self.settings.database_url) as repository:
            jobs = repository.list_jobs(
                source=source, status=None if status == "all" else status, company=company
            )
        selected: list[Job] = []
        matches: dict[str, MatchResult] = {}
        for job in jobs:
            if selected_profile is not None and not matches_title(job.title, selected_profile):
                continue
            if query:
                searchable = "\n".join(
                    [
                        job.title,
                        job.description,
                        job.location or "",
                        job.company,
                        *workplace_locations(job.raw_data),
                    ]
                )
                if query.casefold() not in searchable.casefold():
                    continue
            if any(
                expected is not None and _normalized(value) != _normalized(expected)
                for value, expected in [
                    (job.remote_type, remote_type),
                    (job.seniority, seniority),
                    (job.language, language),
                ]
            ):
                continue
            location_text = "; ".join([job.location or "", *workplace_locations(job.raw_data)])
            if location is not None and _normalized(location) not in _normalized(location_text):
                continue
            result = self._evaluate(job, selected_profile, profile_json, query)
            if result.matched and (eligibility is None or result.eligibility == eligibility):
                selected.append(job)
                matches[job.id] = result
        return selected, matches

    def detail(
        self, id: str, *, profile: str | None = None, query: str | None = None
    ) -> tuple[Job, MatchResult] | None:
        selected_profile = self._profile(profile)
        with Repository(self.settings.database_url) as repository:
            job = next((job for job in repository.list_jobs(status=None) if job.id == id), None)
        if job is None:
            return None
        profile_json = selected_profile.model_dump_json() if selected_profile else "null"
        # Details explain nonmatches as well; selecting a profile must never hide a saved job.
        return job, self._evaluate(
            job, selected_profile, profile_json, query.strip() if query else None
        )
