"""Configuration inputs per PRD Final.md §1 — Configuration Inputs.

All values are supplied via environment variables (or a .env file).
None are hardcoded as run-specific secrets; defaults match the PRD where specified.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Project roots
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAILY_PLAY_DATA_DIR = PROJECT_ROOT / "Daily Play Data"
OUTPUT_DIR = PROJECT_ROOT / "output"

CSV_COLUMNS = (
    "uid",
    "seriesId",
    "puzzleId",
    "startTimestamp",
    "updatedTimestamp",
    "playState",
    "totalBoxes",
    "filledBoxes",
    "screenTimeSeconds",
)

PLAY_STATES = frozenset({"inProgress", "completed"})
GRAINS = frozenset({"Daily", "Weekly", "Monthly"})

# Transient server-error retry (PRD §6.4: "small fixed number of attempts",
# exponential backoff doubling, capped, with jitter).
RETRY_MAX_ATTEMPTS = 5
RETRY_BACKOFF_BASE_SECONDS = 0.5
RETRY_BACKOFF_CAP_SECONDS = 30.0


@dataclass(frozen=True)
class Config:
    """Immutable configuration for a single pipeline run (PRD §1)."""

    API_BASE_URL: str
    CLIENT_ID: str
    CLIENT_SECRET: str
    TIMEZONE: str
    SERIES_ID: str
    PUZZLE_ID: Optional[str]
    LIMIT: int
    OFFSET: int
    TOTAL_DATA_TIME_START: datetime
    RATE_LIMIT_RPS: float
    MAX_WORKERS: int
    # Analysis inputs (Variables §1) — optional for fetch-only runs
    timelineStart: Optional[datetime]
    timelineEnd: Optional[datetime]
    grain: Optional[str]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.TIMEZONE)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required configuration `{name}` is missing or empty.")
    return value


def _parse_iso_datetime(value: str, tz: ZoneInfo, name: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"`{name}` must be ISO 8601, got {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt


def load_config(env_file: Optional[Path] = None) -> Config:
    """Load configuration from environment / .env. Values are immutable for the run."""
    if env_file is not None:
        load_dotenv(env_file)
    else:
        load_dotenv(PROJECT_ROOT / ".env")

    timezone = os.environ.get("TIMEZONE", "UTC").strip() or "UTC"
    tz = ZoneInfo(timezone)

    puzzle_id = os.environ.get("PUZZLE_ID", "").strip() or None

    limit = int(os.environ.get("LIMIT", "1000"))
    if not (1 <= limit <= 1000):
        raise ValueError("`LIMIT` must be in 1–1000 (PRD §1).")

    offset = int(os.environ.get("OFFSET", "0"))
    if offset < 0:
        raise ValueError("`OFFSET` must be ≥ 0 (PRD §1).")

    # Default 0.5 RPS ≈ 30 requests/minute
    rate_limit_rps = float(os.environ.get("RATE_LIMIT_RPS", "0.5"))
    if rate_limit_rps <= 0:
        raise ValueError("`RATE_LIMIT_RPS` must be > 0 (requests per second).")

    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    if max_workers < 1:
        raise ValueError("`MAX_WORKERS` must be ≥ 1 (PRD §1).")

    total_start_raw = _require("TOTAL_DATA_TIME_START")
    total_start = _parse_iso_datetime(total_start_raw, tz, "TOTAL_DATA_TIME_START")

    timeline_start = None
    timeline_end = None
    grain = None
    if os.environ.get("TIMELINE_START", "").strip():
        timeline_start = _parse_iso_datetime(
            os.environ["TIMELINE_START"].strip(), tz, "TIMELINE_START"
        )
    if os.environ.get("TIMELINE_END", "").strip():
        timeline_end = _parse_iso_datetime(
            os.environ["TIMELINE_END"].strip(), tz, "TIMELINE_END"
        )
    grain_raw = os.environ.get("GRAIN", "").strip()
    if grain_raw:
        if grain_raw not in GRAINS:
            raise ValueError(f"`GRAIN` must be one of {sorted(GRAINS)}, got {grain_raw!r}")
        grain = grain_raw

    return Config(
        API_BASE_URL=os.environ.get(
            "API_BASE_URL", "https://puzzleme.amuselabs.com/pmm/api/v2"
        ).rstrip("/"),
        CLIENT_ID=_require("CLIENT_ID"),
        CLIENT_SECRET=_require("CLIENT_SECRET"),
        TIMEZONE=timezone,
        SERIES_ID=_require("SERIES_ID"),
        PUZZLE_ID=puzzle_id,
        LIMIT=limit,
        OFFSET=offset,
        TOTAL_DATA_TIME_START=total_start,
        RATE_LIMIT_RPS=rate_limit_rps,
        MAX_WORKERS=max_workers,
        timelineStart=timeline_start,
        timelineEnd=timeline_end,
        grain=grain,
    )


def load_analysis_config(
    timeline_start: str,
    timeline_end: str,
    grain: str,
    timezone: str = "UTC",
) -> tuple[datetime, datetime, str, ZoneInfo]:
    """Parse analysis inputs (Variables §1) without requiring API credentials."""
    if grain not in GRAINS:
        raise ValueError(f"`grain` must be one of {sorted(GRAINS)}, got {grain!r}")
    tz = ZoneInfo(timezone)
    start = _parse_iso_datetime(timeline_start, tz, "timelineStart")
    end = _parse_iso_datetime(timeline_end, tz, "timelineEnd")
    if start.date() > end.date():
        raise ValueError("Constraint: timelineStart ≤ timelineEnd (Variables §1.1).")
    return start, end, grain, tz
