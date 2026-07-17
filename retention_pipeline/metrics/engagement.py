"""Engagement by Timeline — median screenTimeSeconds (PRD Engagement §§1–8)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from retention_pipeline.bucketing import Bucket, bucket_datetime_bounds, build_buckets
from retention_pipeline.cleaning.pipeline import PlayRow
from retention_pipeline.metrics.errors import validate_grain


def median_from_histogram(counts: Dict[int, int]) -> Optional[float]:
    """Exact median from frequency histogram (Engagement §8.1 / §6.1).

    Odd n → middle value; even n → arithmetic mean of the two middle values.
    Zero qualifying plays → None (N/A).
    """
    n = sum(counts.values())
    if n == 0:
        return None
    # Walk ascending until positions ceil(n/2) or n/2 and n/2+1
    # For 1-indexed positions: odd → (n+1)/2; even → n/2 and n/2+1
    target_lo = (n + 1) // 2  # 1-indexed first middle (odd) or left middle (even)
    target_hi = n // 2 + 1 if n % 2 == 0 else target_lo

    accumulated = 0
    lo_val: Optional[int] = None
    hi_val: Optional[int] = None
    for value in sorted(counts.keys()):
        accumulated += counts[value]
        if lo_val is None and accumulated >= target_lo:
            lo_val = value
        if hi_val is None and accumulated >= target_hi:
            hi_val = value
            break
    assert lo_val is not None and hi_val is not None
    if n % 2 == 1:
        return float(lo_val)
    return (lo_val + hi_val) / 2.0


def play_qualifies_for_bucket(play: PlayRow, bucket: Bucket, tz: ZoneInfo) -> bool:
    """Both start and updated timestamps fall within B (Engagement §4)."""
    b_start, b_end = bucket_datetime_bounds(bucket, tz)
    return (b_start <= play.startTimestamp <= b_end) and (
        b_start <= play.updatedTimestamp <= b_end
    )


def compute_engagement(
    plays: List[PlayRow],
    timeline_start: date,
    timeline_end: date,
    grain: str,
    tz: ZoneInfo,
) -> Dict[str, Any]:
    """Median screenTimeSeconds of fully-contained plays per bucket."""
    validate_grain(timeline_start, timeline_end, grain)
    buckets = build_buckets(timeline_start, timeline_end, grain)

    # Per-bucket frequency histograms (Engagement §8.1)
    histograms: Dict[str, Dict[int, int]] = {
        b.key: defaultdict(int) for b in buckets
    }
    excluded: Dict[str, int] = {b.key: 0 for b in buckets}
    qualifying: Dict[str, int] = {b.key: 0 for b in buckets}

    # Only consider plays that could intersect the timeline window
    for play in plays:
        if play.screenTimeSeconds is None:
            continue
        start_day = play.startTimestamp.astimezone(tz).date()
        update_day = play.updatedTimestamp.astimezone(tz).date()
        # Skip if entirely outside timeline (neither timestamp in window)
        if update_day < timeline_start and start_day < timeline_start:
            continue
        if start_day > timeline_end and update_day > timeline_end:
            continue

        for bucket in buckets:
            if play_qualifies_for_bucket(play, bucket, tz):
                histograms[bucket.key][play.screenTimeSeconds] += 1
                qualifying[bucket.key] += 1
            else:
                # Count exclusion only if the play's start CSV day intersects bucket
                # (Engagement §7: per-bucket count of excluded plays)
                b_days = set(bucket.days)
                if start_day in b_days or update_day in b_days:
                    excluded[bucket.key] += 1

    results: List[Dict[str, Any]] = []
    for bucket in buckets:
        med = median_from_histogram(dict(histograms[bucket.key]))
        results.append(
            {
                "bucket": bucket.key,
                "grain": grain,
                "medianScreenTimeSeconds": med if med is not None else "N/A",
                "qualifyingPlays": qualifying[bucket.key],
                "excludedBoundarySpanningPlays": excluded[bucket.key],
                "coveredStart": bucket.covered_start.isoformat(),
                "coveredEnd": bucket.covered_end.isoformat(),
                "partialBucket": bucket.censor is not None,
                "censorSide": bucket.censor,
            }
        )

    return {
        "metric": "engagement",
        "grain": grain,
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
        "buckets": results,
    }
