"""Play State Analysis — Completion Matrix (PRD Play State Analysis §§1–7)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from retention_pipeline.bucketing import build_buckets
from retention_pipeline.cleaning.pipeline import PlayRow
from retention_pipeline.metrics.errors import validate_grain

# Storage codes (Play State §3) — compact encoding only; never summed for meaning
ABSENT = 0
LOADED = 1
SOLVING = 2
COMPLETED = 3

STATE_NAMES = {LOADED: "loaded", SOLVING: "solving", COMPLETED: "completed"}


def derive_state(play: PlayRow) -> Optional[int]:
    """Non-absent state derivation order (Play State §3.1).

    1. isLoaded == true → loaded
    2. else playState == inProgress → solving
    3. else playState == completed → completed
    Returns None if the play cannot resolve to a state (null filledBoxes / playState).
    """
    loaded = play.isLoaded
    if loaded is True:
        return LOADED
    if loaded is None:
        return None  # isLoaded undefined → excluded from isLoaded-dependent metrics
    # filledBoxes ≥ 1
    if play.playState == "inProgress":
        return SOLVING
    if play.playState == "completed":
        return COMPLETED
    return None


def compute_play_state(
    plays: List[PlayRow],
    timeline_start: date,
    timeline_end: date,
    grain: str,
    tz: ZoneInfo,
) -> Dict[str, Any]:
    """Per-bucket loaded / solving / completed counts and fractions."""
    validate_grain(timeline_start, timeline_end, grain)
    buckets = build_buckets(timeline_start, timeline_end, grain)

    # counts[date][stateCode] — Play State §4.4
    counts: Dict[date, Dict[int, int]] = defaultdict(lambda: defaultdict(int))

    # Sparse completion: one (resolveDate, state) per (puzzleId, uid)
    # Later update for same key would replace — PRD says one play per (puzzleId, uid)
    by_key: Dict[Tuple[str, str], PlayRow] = {}
    for play in plays:
        key = (play.puzzleId, play.uid)
        existing = by_key.get(key)
        if existing is None or play.updatedTimestamp >= existing.updatedTimestamp:
            by_key[key] = play

    for play in by_key.values():
        resolve = play.updatedTimestamp.astimezone(tz).date()
        if resolve < timeline_start or resolve > timeline_end:
            continue  # §4.3 out-of-timeline dropped
        state = derive_state(play)
        if state is None:
            continue
        counts[resolve][state] += 1

    results: List[Dict[str, Any]] = []
    for bucket in buckets:
        loaded = sum(counts[d][LOADED] for d in bucket.days)
        solving = sum(counts[d][SOLVING] for d in bucket.days)
        completed = sum(counts[d][COMPLETED] for d in bucket.days)
        total = loaded + solving + completed

        def frac(n: int) -> Any:
            if total == 0:
                return "N/A"
            return (n / total) * 100.0

        results.append(
            {
                "bucket": bucket.key,
                "grain": grain,
                "totalPlays": total,
                "loadedCount": loaded,
                "solvingCount": solving,
                "completedCount": completed,
                "loadedFractionPercent": frac(loaded),
                "solvingFractionPercent": frac(solving),
                "completedFractionPercent": frac(completed),
                "coveredStart": bucket.covered_start.isoformat(),
                "coveredEnd": bucket.covered_end.isoformat(),
                "partialBucket": bucket.censor is not None,
                "censorSide": bucket.censor,
            }
        )

    return {
        "metric": "playState",
        "grain": grain,
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
        "buckets": results,
    }
