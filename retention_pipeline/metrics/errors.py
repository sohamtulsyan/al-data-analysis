"""Structured grain validation error (User Growth §4.3)."""

from __future__ import annotations

from typing import Any, Dict, List

from retention_pipeline.bucketing import allowed_grains, timeline_length_days


class GrainNotAllowedError(Exception):
    """GRAIN_NOT_ALLOWED_FOR_TIMELINE — no computation performed."""

    def __init__(
        self,
        requested_grain: str,
        timeline_length: int,
        allowed: List[str],
    ) -> None:
        self.payload: Dict[str, Any] = {
            "status": "error",
            "errorType": "GRAIN_NOT_ALLOWED_FOR_TIMELINE",
            "message": (
                f"Grain '{requested_grain}' is not allowed for a timeline of "
                f"{timeline_length} days. Allowed grains for this timeline: "
                f"{', '.join(allowed)}."
            ),
            "requestedGrain": requested_grain,
            "timelineLengthDays": timeline_length,
            "allowedGrains": allowed,
        }
        super().__init__(self.payload["message"])

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


def validate_grain(timeline_start_day, timeline_end_day, grain: str) -> int:
    length = timeline_length_days(timeline_start_day, timeline_end_day)
    allowed = allowed_grains(length)
    if grain not in allowed:
        raise GrainNotAllowedError(grain, length, allowed)
    return length
