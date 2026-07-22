"""Merge runtime JSON settings with load_config() for fetch jobs."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from retention_pipeline.config import Config, load_config, load_analysis_config
from retention_pipeline.metrics.retention import STRICT_HORIZONS, WINDOW_HORIZONS

from retention_api.services import config_store
from retention_api.services.paths import users_csv_default_path


def client_credentials_configured() -> bool:
    # Ensure .env is loaded even if the process was started without dotenv.
    from retention_pipeline.config import PROJECT_ROOT

    load_dotenv(PROJECT_ROOT / ".env")
    cid = os.environ.get("CLIENT_ID", "").strip()
    secret = os.environ.get("CLIENT_SECRET", "").strip()
    return bool(cid and secret)


def _apply_runtime_to_env_if_missing(raw: Dict[str, Any]) -> None:
    """Allow dashboard-only series/time start when env vars are unset (fetch jobs)."""
    if raw.get("seriesId") and not os.environ.get("SERIES_ID", "").strip():
        os.environ["SERIES_ID"] = str(raw["seriesId"])
    if raw.get("totalDataTimeStart") and not os.environ.get(
        "TOTAL_DATA_TIME_START", ""
    ).strip():
        os.environ["TOTAL_DATA_TIME_START"] = str(raw["totalDataTimeStart"])
    if raw.get("timezone") and not os.environ.get("TIMEZONE", "").strip():
        os.environ["TIMEZONE"] = str(raw["timezone"])


def load_fetch_config() -> Config:
    """load_config() with dashboard overrides via dataclasses.replace."""
    raw = config_store.load_raw()
    _apply_runtime_to_env_if_missing(raw)
    base = load_config()
    kwargs: Dict[str, Any] = {}

    if raw.get("apiBaseUrl"):
        kwargs["API_BASE_URL"] = str(raw["apiBaseUrl"]).rstrip("/")
    if raw.get("seriesId"):
        kwargs["SERIES_ID"] = raw["seriesId"]
    if raw.get("timezone"):
        kwargs["TIMEZONE"] = raw["timezone"]
    if raw.get("totalDataTimeStart"):
        tz = ZoneInfo(kwargs.get("TIMEZONE", base.TIMEZONE))
        kwargs["TOTAL_DATA_TIME_START"] = _parse_dt(raw["totalDataTimeStart"], tz)
    if "puzzleId" in raw:
        kwargs["PUZZLE_ID"] = raw["puzzleId"] or None
    if raw.get("limit") is not None:
        kwargs["LIMIT"] = int(raw["limit"])
    if raw.get("offset") is not None:
        kwargs["OFFSET"] = int(raw["offset"])
    if raw.get("rateLimitRps") is not None:
        kwargs["RATE_LIMIT_RPS"] = float(raw["rateLimitRps"])
    if raw.get("maxWorkers") is not None:
        kwargs["MAX_WORKERS"] = int(raw["maxWorkers"])

    if kwargs:
        return replace(base, **kwargs)
    return base


def _parse_dt(value: str, tz: ZoneInfo) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def resolve_analysis_params(
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[datetime, datetime, str, str, Tuple[int, ...], Tuple[int, ...]]:
    """Return start, end datetimes, grain, timezone, strict/window horizons.

    Single analysis period for all jobs (analyze, pipeline, NUU, signup).
    """
    raw = config_store.load_raw()
    p = params or {}

    timeline_start = p.get("timelineStart") or raw.get("timelineStart")
    timeline_end = p.get("timelineEnd") or raw.get("timelineEnd")
    grain = p.get("grain") or raw.get("grain") or "Weekly"
    timezone = p.get("timezone") or raw.get("timezone") or "UTC"

    if not timeline_start or not timeline_end:
        raise ValueError(
            "timelineStart and timelineEnd are required "
            "(set via PATCH /api/v1/config or job params)."
        )

    start, end, grain, _tz = load_analysis_config(
        timeline_start, timeline_end, grain, timezone
    )

    strict = p.get("strictHorizons") or raw.get("strictHorizons") or list(STRICT_HORIZONS)
    window = p.get("windowHorizons") or raw.get("windowHorizons") or list(WINDOW_HORIZONS)
    return (
        start,
        end,
        grain,
        timezone,
        tuple(int(x) for x in strict),
        tuple(int(x) for x in window),
    )


def resolve_users_csv(params: Optional[Dict[str, Any]] = None):
    from pathlib import Path

    raw = config_store.load_raw()
    p = params or {}
    path_str = p.get("usersCsvPath") or raw.get("usersCsvPath")
    return Path(path_str) if path_str else users_csv_default_path()
