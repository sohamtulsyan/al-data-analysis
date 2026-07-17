"""Single-day fetch with pagination, atomic CSV commit (PRD §3, §7)."""

from __future__ import annotations

import csv
import logging
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from retention_pipeline.api.auth import TokenManager
from retention_pipeline.api.rate_limiter import TokenBucketRateLimiter
from retention_pipeline.api.retry import retry_transient
from retention_pipeline.config import CSV_COLUMNS, Config, DAILY_PLAY_DATA_DIR

logger = logging.getLogger(__name__)


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Closed interval [D 00:00:00, D 23:59:59] in TIMEZONE (PRD Variables)."""
    start = datetime.combine(day, time(0, 0, 0), tzinfo=tz)
    end = datetime.combine(day, time(23, 59, 59), tzinfo=tz)
    return start, end


def csv_filename_for_day(day: date) -> str:
    """DD-MM-YYYY.csv (PRD §4.5)."""
    return f"{day.day:02d}-{day.month:02d}-{day.year:04d}.csv"


def csv_path_for_day(day: date, directory: Path = DAILY_PLAY_DATA_DIR) -> Path:
    return directory / csv_filename_for_day(day)


def day_is_complete(day: date, directory: Path = DAILY_PLAY_DATA_DIR) -> bool:
    """A day is complete iff its final CSV exists (PRD §7.1)."""
    return csv_path_for_day(day, directory).is_file()


def discard_partial(day: date, directory: Path = DAILY_PLAY_DATA_DIR) -> None:
    """Delete stale .partial temp files for a pending day (PRD §7.3)."""
    partial = directory / f"{csv_filename_for_day(day)}.partial"
    if partial.exists():
        partial.unlink()
        logger.info("Discarded stale partial file %s", partial)


def _play_to_row(play: Dict[str, Any], series_id: str) -> Dict[str, Any]:
    progress = play.get("playProgress") or {}
    return {
        "uid": play.get("userId", ""),
        "seriesId": series_id,
        "puzzleId": play.get("puzzleId", ""),
        "startTimestamp": play.get("startedAt", ""),
        "updatedTimestamp": play.get("updatedAt", ""),
        "playState": progress.get("playState", ""),
        "totalBoxes": progress.get("totalBoxes", ""),
        "filledBoxes": progress.get("filledBoxes", ""),
        "screenTimeSeconds": play.get("screenTimeSeconds", ""),
    }


def fetch_single_day(
    day: date,
    config: Config,
    token_manager: TokenManager,
    rate_limiter: TokenBucketRateLimiter,
    directory: Path = DAILY_PLAY_DATA_DIR,
) -> Path:
    """Retrieve all plays for exactly one calendar day and atomically write CSV (§3).

    Writes to DD-MM-YYYY.csv.partial then renames to DD-MM-YYYY.csv only on success.
    """
    directory.mkdir(parents=True, exist_ok=True)
    discard_partial(day, directory)

    from_dt, to_dt = day_bounds(day, config.tz)
    # ISO 8601 in TIMEZONE
    from_str = from_dt.isoformat()
    to_str = to_dt.isoformat()

    final_path = csv_path_for_day(day, directory)
    # Write to a unique temp then rename to .partial name for clarity, then to final.
    # PRD requires e.g. DD-MM-YYYY.csv.partial then atomic rename to final.
    partial_path = directory / f"{csv_filename_for_day(day)}.partial"

    offset = 0
    page_size = 1000  # PRD §3.3: advance by 1000 (API default / configured limit)

    with open(partial_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
        writer.writeheader()

        while True:
            plays, has_more = _fetch_page(
                config=config,
                token_manager=token_manager,
                rate_limiter=rate_limiter,
                from_str=from_str,
                to_str=to_str,
                offset=offset,
            )
            for play in plays:
                writer.writerow(_play_to_row(play, config.SERIES_ID))
            if not has_more:
                break
            offset += page_size

    # Atomic rename (§3.5, §7.2)
    os.replace(partial_path, final_path)
    logger.info("Completed day %s → %s", day.isoformat(), final_path.name)
    return final_path


def _fetch_page(
    *,
    config: Config,
    token_manager: TokenManager,
    rate_limiter: TokenBucketRateLimiter,
    from_str: str,
    to_str: str,
    offset: int,
) -> tuple[List[Dict[str, Any]], bool]:
    """Fetch one page; on 401/errorCode 98 refresh token and retry same offset (§3.4)."""
    url = f"{config.API_BASE_URL}/analytics/plays"
    params: Dict[str, Any] = {
        "series": config.SERIES_ID,
        "from": from_str,
        "to": to_str,
        "limit": 1000,
        "offset": offset,
        "getUserInfo": "false",
    }
    if config.PUZZLE_ID:
        params["puzzleId"] = config.PUZZLE_ID

    # At most one refresh cycle for this page (then resume from same offset)
    for _ in range(2):
        token = token_manager.access_token
        rate_limiter.acquire()

        def _do_get(tok: str = token) -> requests.Response:
            return requests.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Accept": "application/json",
                },
                timeout=120,
            )

        response = retry_transient(_do_get, rate_limiter=rate_limiter)

        if response.status_code == 401:
            try:
                err = response.json()
            except ValueError:
                err = {}
            # PRD §3.4: HTTP 401 with errorCode 98 → refresh and resume same offset
            if err.get("errorCode") == 98:
                token_manager.refresh_single_flight(stale_token=token)
                continue
            response.raise_for_status()

        response.raise_for_status()
        data = response.json()
        if data.get("status", 0) != 0:
            raise RuntimeError(
                f"Plays API non-zero status: {data.get('status')}, "
                f"errorCode={data.get('errorCode')}, "
                f"errorMessage={data.get('errorMessage')}"
            )
        plays = data.get("plays") or []
        has_more = bool(data.get("hasMore", False))
        return plays, has_more

    raise RuntimeError(f"Failed to fetch page at offset={offset} after token refresh.")
