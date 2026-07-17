"""End-to-end analysis orchestration over Daily Play Data CSVs."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from retention_pipeline.bucketing import timeline_length_days
from retention_pipeline.cleaning.pipeline import prepare_analysis
from retention_pipeline.config import DAILY_PLAY_DATA_DIR, OUTPUT_DIR
from retention_pipeline.metrics.engagement import compute_engagement
from retention_pipeline.metrics.errors import GrainNotAllowedError
from retention_pipeline.metrics.play_state import compute_play_state
from retention_pipeline.metrics.retention import (
    compute_consecutive_retention,
    compute_cumulative_retention,
    compute_rolling_retention,
    compute_strict_retention,
)
from retention_pipeline.metrics.user_growth import compute_user_growth

logger = logging.getLogger(__name__)


def run_analysis(
    timeline_start: date,
    timeline_end: date,
    grain: str,
    timezone: str = "UTC",
    data_dir: Path = DAILY_PLAY_DATA_DIR,
    output_dir: Path = OUTPUT_DIR,
    strict_horizons: Tuple[int, ...] | None = None,
    window_horizons: Tuple[int, ...] | None = None,
) -> Dict[str, Any]:
    """Clean → Activity Matrix → all PRD metrics. Writes JSON under output_dir."""
    tz = ZoneInfo(timezone)
    output_dir.mkdir(parents=True, exist_ok=True)

    plays, matrix, dq = prepare_analysis(data_dir, timeline_start, timeline_end, tz)
    length = timeline_length_days(timeline_start, timeline_end)

    result: Dict[str, Any] = {
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
        "grain": grain,
        "timezone": timezone,
        "timelineLengthDays": length,
        "playRowCount": len(plays),
        "distinctUsersInMatrix": len(matrix.uids()),
        "dataQuality": dq.to_dict(),
        "metrics": {},
    }

    try:
        result["metrics"]["userGrowth"] = compute_user_growth(
            matrix, timeline_start, timeline_end, grain, tz
        )
        result["metrics"]["engagement"] = compute_engagement(
            plays, timeline_start, timeline_end, grain, tz
        )
        result["metrics"]["strictRetention"] = compute_strict_retention(
            matrix,
            timeline_start,
            timeline_end,
            grain,
            tz,
            horizons=strict_horizons,
        )
        result["metrics"]["cumulativeRetention"] = compute_cumulative_retention(
            matrix,
            timeline_start,
            timeline_end,
            grain,
            tz,
            horizons=window_horizons,
        )
        result["metrics"]["consecutiveRetention"] = compute_consecutive_retention(
            matrix,
            timeline_start,
            timeline_end,
            grain,
            tz,
            horizons=window_horizons,
        )
        result["metrics"]["rollingRetention"] = compute_rolling_retention(
            matrix,
            timeline_start,
            timeline_end,
            grain,
            tz,
            horizons=window_horizons,
        )
        result["metrics"]["playState"] = compute_play_state(
            plays, timeline_start, timeline_end, grain, tz
        )
    except GrainNotAllowedError as exc:
        out = exc.to_dict()
        path = output_dir / "analysis_error.json"
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        raise

    out_path = output_dir / "analysis_results.json"
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote analysis results to %s", out_path)
    return result
