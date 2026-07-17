"""Historical backfill loop (PRD §4) and daily scheduled fetch (PRD §5)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

from retention_pipeline.api.auth import TokenManager
from retention_pipeline.api.fetch import (
    day_is_complete,
    discard_partial,
    fetch_single_day,
)
from retention_pipeline.api.rate_limiter import TokenBucketRateLimiter
from retention_pipeline.config import Config, DAILY_PLAY_DATA_DIR

logger = logging.getLogger(__name__)


def enumerate_days(start: date, end: date) -> List[date]:
    """Inclusive calendar-day range [start, end]."""
    if start > end:
        return []
    days: List[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def yesterday_in_tz(tz: ZoneInfo, now: datetime | None = None) -> date:
    """Yesterday's calendar date in TIMEZONE."""
    now = now or datetime.now(tz)
    return (now.date() - timedelta(days=1))


def pending_days(
    required: List[date],
    directory: Path = DAILY_PLAY_DATA_DIR,
) -> List[date]:
    """Partition into pending (no final CSV); discard stale partials (PRD §7.3)."""
    pending: List[date] = []
    for day in required:
        if day_is_complete(day, directory):
            continue
        discard_partial(day, directory)
        pending.append(day)
    return pending


def run_historical_backfill(
    config: Config,
    directory: Path = DAILY_PLAY_DATA_DIR,
    now: datetime | None = None,
) -> List[Path]:
    """Populate Daily Play Data from TOTAL_DATA_TIME_START through yesterday (§4)."""
    directory.mkdir(parents=True, exist_ok=True)
    tz = config.tz
    start_day = config.TOTAL_DATA_TIME_START.astimezone(tz).date()
    end_day = yesterday_in_tz(tz, now)
    required = enumerate_days(start_day, end_day)
    to_fetch = pending_days(required, directory)

    logger.info(
        "Historical backfill: %d required days, %d pending (workers=%d, rps=%d)",
        len(required),
        len(to_fetch),
        config.MAX_WORKERS,
        config.RATE_LIMIT_RPS,
    )
    if not to_fetch:
        logger.info("All required days already complete; nothing to fetch.")
        return []

    rate_limiter = TokenBucketRateLimiter(config.RATE_LIMIT_RPS)
    token_manager = TokenManager(config, rate_limiter)
    token_manager.acquire()

    completed: List[Path] = []
    errors: List[tuple[date, Exception]] = []

    def _work(day: date) -> Path:
        return fetch_single_day(day, config, token_manager, rate_limiter, directory)

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        futures = {pool.submit(_work, day): day for day in to_fetch}
        for fut in as_completed(futures):
            day = futures[fut]
            try:
                path = fut.result()
                completed.append(path)
            except Exception as exc:  # noqa: BLE001 — surface per-day failures
                logger.exception("Failed fetching day %s", day)
                errors.append((day, exc))

    if errors:
        detail = "; ".join(f"{d.isoformat()}: {e}" for d, e in errors)
        raise RuntimeError(f"Historical backfill incomplete: {detail}")

    return completed


def run_daily_cron(
    config: Config,
    directory: Path = DAILY_PLAY_DATA_DIR,
    now: datetime | None = None,
) -> Path | None:
    """Fetch the just-ended calendar day (PRD §5). Idempotent if CSV exists (§7.5)."""
    directory.mkdir(parents=True, exist_ok=True)
    tz = config.tz
    now = now or datetime.now(tz)
    # Cron at 00:00 on D+1 retrieves day D (the day that has just ended)
    target = (now.date() - timedelta(days=1))

    if day_is_complete(target, directory):
        logger.info(
            "Daily cron no-op: %s already complete (PRD §7.5 idempotency).",
            target.isoformat(),
        )
        return None

    discard_partial(target, directory)
    rate_limiter = TokenBucketRateLimiter(config.RATE_LIMIT_RPS)
    token_manager = TokenManager(config, rate_limiter)
    token_manager.acquire()
    return fetch_single_day(target, config, token_manager, rate_limiter, directory)
