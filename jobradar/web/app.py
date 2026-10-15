"""FastAPI application for the local JobRadar dashboard."""

import asyncio
import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from yaml import YAMLError

from jobradar.config import ProfilesConfig, Settings, SourcesConfig
from jobradar.database import Repository
from jobradar.models.locations import workplace_locations
from jobradar.services.configuration import ConfigurationStore
from jobradar.services.export import render_export
from jobradar.services.query import VacancyQuery
from jobradar.web.runner import CollectionInProgressError, CollectionManager

logger = logging.getLogger(__name__)
STATIC_PATH = Path(__file__).parent / "static"


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["greenhouse", "lever"] | None = None


def job_filters(
    query: str | None = None,
    profile: str | None = None,
    source: Literal["greenhouse", "lever"] | None = None,
    status: Literal["active", "inactive", "unknown", "all"] = "active",
    company: str | None = None,
    remote_type: Literal["remote", "hybrid", "onsite", "unknown"] | None = None,
    eligibility: Literal["allowed", "excluded", "needs_review"] | None = None,
    location: str | None = None,
    seniority: str | None = None,
    language: str | None = None,
) -> dict:
    return {
        "query": query,
        "profile": profile,
        "source": source,
        "status": status,
        "company": company,
        "remote_type": remote_type,
        "eligibility": eligibility,
        "location": location,
        "seniority": seniority,
        "language": language,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    store = ConfigurationStore(settings)
    query_service = VacancyQuery(settings)
    manager = CollectionManager(settings, store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await asyncio.to_thread(manager.close)

    app = FastAPI(title="JobRadar", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.configuration = store
    app.state.query = query_service
    app.state.collection = manager

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            valid_origin = True
            if origin := request.headers.get("origin"):
                try:
                    parsed = urlsplit(origin)
                    valid_origin = (
                        parsed.scheme in {"http", "https"}
                        and parsed.scheme == request.url.scheme
                        and parsed.netloc.casefold() == request.url.netloc.casefold()
                        and not parsed.path
                        and not parsed.query
                        and not parsed.fragment
                    )
                except ValueError:
                    valid_origin = False
            if request.headers.get("x-jobradar-request") != "1" or not valid_origin:
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Use the local JobRadar interface for changes."},
                )
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            }
        )
        return response

    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @app.exception_handler(CollectionInProgressError)
    async def collection_busy(_: Request, __: CollectionInProgressError):
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Collection is running. Wait before collecting or editing settings."
            },
        )

    @app.exception_handler(ValueError)
    async def invalid_input(_: Request, error: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(YAMLError)
    async def invalid_yaml(_: Request, __: YAMLError):
        return JSONResponse(status_code=422, content={"detail": "Invalid YAML configuration."})

    async def unavailable(_: Request, error: Exception):
        logger.error("JobRadar operation unavailable (%s)", type(error).__name__)
        return JSONResponse(
            status_code=503,
            content={"detail": "Cannot access configuration or database. Check server settings."},
        )

    app.add_exception_handler(OSError, unavailable)
    app.add_exception_handler(SQLAlchemyError, unavailable)

    def stats() -> dict:
        with Repository(settings.database_url) as repository:
            return repository.stats()

    @app.get("/")
    def index():
        return FileResponse(STATIC_PATH / "index.html")

    # check_dir=False permits app factories before optional frontend assets are copied.
    app.mount("/static", StaticFiles(directory=STATIC_PATH, check_dir=False), name="static")

    @app.get("/api/bootstrap")
    def bootstrap():
        profiles = store.load_profiles()
        return {
            "stats": stats(),
            "sources": store.load_sources().model_dump(mode="json"),
            "profiles": profiles.model_dump(mode="json"),
            "collection": manager.snapshot(),
        }

    @app.get("/api/stats")
    def get_stats():
        return stats()

    @app.get("/api/jobs")
    def jobs(
        filters: Annotated[dict, Depends(job_filters)],
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ):
        records, matches = query_service.select(**filters)
        start = (page - 1) * page_size
        return {
            "items": [
                {
                    **job.model_dump(mode="json", exclude={"description", "raw_data"}),
                    "workplace_locations": workplace_locations(job.raw_data),
                    "match": matches[job.id].model_dump(mode="json"),
                }
                for job in records[start : start + page_size]
            ],
            "total": len(records),
            "page": page,
            "page_size": page_size,
            "pages": math.ceil(len(records) / page_size),
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str, profile: str | None = None, query: str | None = None):
        result = query_service.detail(job_id, profile=profile, query=query)
        if result is None:
            raise HTTPException(404, "Vacancy not found")
        job, match = result
        return {
            **job.model_dump(mode="json"),
            "workplace_locations": workplace_locations(job.raw_data),
            "match": match.model_dump(mode="json"),
        }

    @app.get("/api/collection")
    def collection():
        return manager.snapshot()

    @app.post("/api/collect", status_code=202)
    def collect(body: CollectionRequest):
        return manager.start(source=body.source)

    @app.get("/api/export")
    def export(
        filters: Annotated[dict, Depends(job_filters)],
        format: Literal["csv", "json"] = "csv",
    ):
        records, matches = query_service.select(**filters)
        content = render_export(records, format, matches)
        # A BOM makes UTF-8 CSV readable in Windows Excel without an import dialog.
        if format == "csv":
            content = "\ufeff" + content
        return Response(
            content=content.encode("utf-8"),
            media_type="text/csv; charset=utf-8" if format == "csv" else "application/json",
            headers={"Content-Disposition": f'attachment; filename="jobs.{format}"'},
        )

    @app.get("/api/sources")
    def get_sources():
        return store.load_sources().model_dump(mode="json")

    @app.put("/api/sources")
    def put_sources(config: SourcesConfig):
        with manager.configuration_guard():
            store.save_sources(config)
            return jsonable_encoder(store.load_sources())

    @app.get("/api/profiles")
    def get_profiles():
        return store.load_profiles().model_dump(mode="json")

    @app.put("/api/profiles")
    def put_profiles(config: ProfilesConfig):
        with manager.configuration_guard():
            store.save_profiles(config)
            return jsonable_encoder(store.load_profiles())

    return app
