"""Retention metrics — Common Framework + Strict / Cumulative / Consecutive / Rolling.

PRD: Retention — Common Framework; Strict; Cumulative; Consecutive; Rolling.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from retention_pipeline.bucketing import Bucket, build_buckets
from retention_pipeline.cleaning.pipeline import ActivityMatrix
from retention_pipeline.metrics.errors import validate_grain

STRICT_HORIZONS = (1, 3, 7, 30)
WINDOW_HORIZONS = (3, 7, 30)  # Cumulative / Consecutive / Rolling


def _na_or_pct(retained: int, total: int) -> Any:
    """retention = |retained| / |total| × 100; N/A if total == 0 (Common §4.3)."""
    if total == 0:
        return "N/A"
    return (retained / total) * 100.0


def _users_with_d0_in_bucket(
    matrix: ActivityMatrix, bucket: Bucket
) -> Dict[str, date]:
    """Map uid → D0 for users whose first active day in the bucket exists (Common §2)."""
    result: Dict[str, date] = {}
    day_set = set(bucket.days)
    for uid in matrix.uids():
        d0 = matrix.first_active_day_in(uid, bucket.days)
        if d0 is not None and d0 in day_set:
            result[uid] = d0
    return result


def _eligible(
    d0_map: Dict[str, date],
    n: int,
    timeline_end: date,
) -> Dict[str, date]:
    """totalCohort: D0 in B AND D0+N ≤ timelineEnd (Common §4.1)."""
    return {uid: d0 for uid, d0 in d0_map.items() if d0 + timedelta(days=n) <= timeline_end}


Predicate = Callable[[ActivityMatrix, str, date, int, date], bool]


def _strict_pred(
    matrix: ActivityMatrix, uid: str, d0: date, n: int, timeline_end: date
) -> bool:
    """Active exactly on D0+N (Strict §2)."""
    return matrix.is_active(uid, d0 + timedelta(days=n))


def _cumulative_pred(
    matrix: ActivityMatrix, uid: str, d0: date, n: int, timeline_end: date
) -> bool:
    """OR over D1…DN (Cumulative §2)."""
    for i in range(1, n + 1):
        if matrix.is_active(uid, d0 + timedelta(days=i)):
            return True
    return False


def _consecutive_pred(
    matrix: ActivityMatrix, uid: str, d0: date, n: int, timeline_end: date
) -> bool:
    """AND over D1…DN (Consecutive §2)."""
    for i in range(1, n + 1):
        if not matrix.is_active(uid, d0 + timedelta(days=i)):
            return False
    return True


def _rolling_pred(
    matrix: ActivityMatrix, uid: str, d0: date, n: int, timeline_end: date
) -> bool:
    """OR over DN…timelineEnd (Rolling §2)."""
    day = d0 + timedelta(days=n)
    while day <= timeline_end:
        if matrix.is_active(uid, day):
            return True
        day += timedelta(days=1)
    return False


def _compute_retention_family(
    *,
    name: str,
    matrix: ActivityMatrix,
    timeline_start: date,
    timeline_end: date,
    grain: str,
    horizons: tuple[int, ...],
    predicate: Predicate,
) -> Dict[str, Any]:
    validate_grain(timeline_start, timeline_end, grain)
    buckets = build_buckets(timeline_start, timeline_end, grain)
    results: List[Dict[str, Any]] = []

    for bucket in buckets:
        d0_map = _users_with_d0_in_bucket(matrix, bucket)
        total_active = len(d0_map)  # fixed base (Rolling §3)
        horizon_results: Dict[str, Any] = {}

        for n in horizons:
            eligible = _eligible(d0_map, n, timeline_end)
            total = len(eligible)
            retained_uids = [
                uid
                for uid, d0 in eligible.items()
                if predicate(matrix, uid, d0, n, timeline_end)
            ]
            retained = len(retained_uids)
            censored = total_active - total
            horizon_results[f"D{n}"] = {
                "retentionPercent": _na_or_pct(retained, total),
                "retainedCohort": retained,
                "totalCohort": total,
                "censored": censored,
                "totalActiveInBucket": total_active,
            }

        results.append(
            {
                "bucket": bucket.key,
                "grain": grain,
                "horizons": horizon_results,
                "coveredStart": bucket.covered_start.isoformat(),
                "coveredEnd": bucket.covered_end.isoformat(),
                "partialBucket": bucket.censor is not None,
                "censorSide": bucket.censor,
            }
        )

    return {
        "metric": name,
        "grain": grain,
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
        "buckets": results,
    }


def compute_strict_retention(
    matrix: ActivityMatrix,
    timeline_start: date,
    timeline_end: date,
    grain: str,
    tz: ZoneInfo | None = None,
    horizons: tuple[int, ...] | None = None,
) -> Dict[str, Any]:
    return _compute_retention_family(
        name="strictRetention",
        matrix=matrix,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        grain=grain,
        horizons=horizons if horizons is not None else STRICT_HORIZONS,
        predicate=_strict_pred,
    )


def compute_cumulative_retention(
    matrix: ActivityMatrix,
    timeline_start: date,
    timeline_end: date,
    grain: str,
    tz: ZoneInfo | None = None,
    horizons: tuple[int, ...] | None = None,
) -> Dict[str, Any]:
    return _compute_retention_family(
        name="cumulativeRetention",
        matrix=matrix,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        grain=grain,
        horizons=horizons if horizons is not None else WINDOW_HORIZONS,
        predicate=_cumulative_pred,
    )


def compute_consecutive_retention(
    matrix: ActivityMatrix,
    timeline_start: date,
    timeline_end: date,
    grain: str,
    tz: ZoneInfo | None = None,
    horizons: tuple[int, ...] | None = None,
) -> Dict[str, Any]:
    return _compute_retention_family(
        name="consecutiveRetention",
        matrix=matrix,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        grain=grain,
        horizons=horizons if horizons is not None else WINDOW_HORIZONS,
        predicate=_consecutive_pred,
    )


def compute_rolling_retention(
    matrix: ActivityMatrix,
    timeline_start: date,
    timeline_end: date,
    grain: str,
    tz: ZoneInfo | None = None,
    horizons: tuple[int, ...] | None = None,
) -> Dict[str, Any]:
    return _compute_retention_family(
        name="rollingRetention",
        matrix=matrix,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        grain=grain,
        horizons=horizons if horizons is not None else WINDOW_HORIZONS,
        predicate=_rolling_pred,
    )
