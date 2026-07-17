"""Signup fraction — weekly new accounts / weekly active users (PRD User Growth).

signupFractionPercent(B) = signups(B) / userGrowth(B) × 100

Signups come from the users export Registered Date (ISO week). Active users
are distinct uids from the Activity Matrix (User Growth §3). Email is never read.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from retention_pipeline.bucketing import build_buckets
from retention_pipeline.cleaning.pipeline import ActivityMatrix
from retention_pipeline.metrics.errors import validate_grain
from retention_pipeline.users_csv import count_by_week, load_registration_dates


def _pct(signups: int, active: int) -> Union[float, str]:
    """Percent; N/A when active users == 0 (determinable vs undefined)."""
    if active == 0:
        return "N/A"
    return round(100.0 * signups / active, 4)


def compute_signup_fraction(
    matrix: ActivityMatrix,
    users_csv: Path,
    timeline_start: date,
    timeline_end: date,
    grain: str = "Weekly",
    tz: Optional[ZoneInfo] = None,  # noqa: ARG001 — dates already in TIMEZONE
) -> Dict[str, Any]:
    """Join weekly signups to userGrowth buckets; report signup fraction %."""
    if grain != "Weekly":
        raise ValueError(
            "signupFraction is defined on ISO weeks only; pass grain='Weekly'."
        )
    validate_grain(timeline_start, timeline_end, grain)

    registration_dates = load_registration_dates(
        users_csv,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
    )
    signups_by_week = count_by_week(registration_dates)

    buckets = build_buckets(timeline_start, timeline_end, grain)
    results: List[Dict[str, Any]] = []
    for bucket in buckets:
        active = len(matrix.active_uids_on_days(bucket.days))
        signups = int(signups_by_week.get(bucket.key, 0))
        results.append(
            {
                "bucket": bucket.key,
                "grain": grain,
                "signups": signups,
                "activeUsers": active,
                "signupFractionPercent": _pct(signups, active),
                "coveredStart": bucket.covered_start.isoformat(),
                "coveredEnd": bucket.covered_end.isoformat(),
                "partialBucket": bucket.censor is not None,
                "censorSide": bucket.censor,
            }
        )

    return {
        "metric": "signupFraction",
        "grain": grain,
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
        "usersCsv": str(users_csv),
        "totalSignups": len(registration_dates),
        "buckets": results,
    }
