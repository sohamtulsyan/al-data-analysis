"""User Growth metric (PRD User Growth §§1–6)."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from retention_pipeline.bucketing import Bucket, build_buckets
from retention_pipeline.cleaning.pipeline import ActivityMatrix
from retention_pipeline.metrics.errors import validate_grain


def compute_user_growth(
    matrix: ActivityMatrix,
    timeline_start: date,
    timeline_end: date,
    grain: str,
    tz: ZoneInfo,  # noqa: ARG001 — bucketing uses calendar dates already in TIMEZONE
) -> Dict[str, Any]:
    """Distinct-uid count per bucket from the Activity Matrix (User Growth §3)."""
    validate_grain(timeline_start, timeline_end, grain)
    buckets = build_buckets(timeline_start, timeline_end, grain)
    results: List[Dict[str, Any]] = []

    for bucket in buckets:
        uids = matrix.active_uids_on_days(bucket.days)
        entry: Dict[str, Any] = {
            "bucket": bucket.key,
            "grain": grain,
            "userGrowth": len(uids),
            "coveredStart": bucket.covered_start.isoformat(),
            "coveredEnd": bucket.covered_end.isoformat(),
            "partialBucket": bucket.censor is not None,
            "censorSide": bucket.censor,
        }
        results.append(entry)

    return {
        "metric": "userGrowth",
        "grain": grain,
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
        "buckets": results,
    }
