"""FastAPI application entrypoint."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load project .env before routers read CLIENT_ID / CLIENT_SECRET / CORS_ORIGINS.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from retention_api import __version__
from retention_api.routers import artifacts, config, data, health, jobs, results

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Retention Pipeline API",
        description=(
            "REST API wrapping the Retention Pipeline. "
            "CLIENT_ID and CLIENT_SECRET are read from the server environment only."
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    origins_raw = os.environ.get("CORS_ORIGINS", "").strip()
    origins = [o.strip() for o in origins_raw.split(",") if o.strip()]
    # Local + ngrok: allow all origins when unset so a public dashboard can call the API.
    if not origins:
        origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(config.router, prefix=API_PREFIX)
    app.include_router(jobs.router, prefix=API_PREFIX)
    app.include_router(data.router, prefix=API_PREFIX)
    app.include_router(results.router, prefix=API_PREFIX)
    app.include_router(artifacts.router, prefix=API_PREFIX)

    @app.get("/")
    def root() -> dict:
        return {
            "service": "retention-pipeline-api",
            "version": __version__,
            "docs": "/docs",
            "api": API_PREFIX,
        }

    return app


app = create_app()
