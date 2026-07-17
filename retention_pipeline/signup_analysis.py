"""Orchestrate signup-fraction analysis + chart."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from retention_pipeline.cleaning.pipeline import prepare_analysis
from retention_pipeline.config import DAILY_PLAY_DATA_DIR, OUTPUT_DIR
from retention_pipeline.metrics.errors import GrainNotAllowedError
from retention_pipeline.metrics.signup_fraction import compute_signup_fraction

logger = logging.getLogger(__name__)


def run_signup_fraction(
    users_csv: Path,
    timeline_start: date,
    timeline_end: date,
    timezone: str = "UTC",
    data_dir: Path = DAILY_PLAY_DATA_DIR,
    output_dir: Path = OUTPUT_DIR,
) -> Dict[str, Any]:
    """Build Activity Matrix → signup fraction by ISO week → JSON + PNG."""
    tz = ZoneInfo(timezone)
    output_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    _plays, matrix, dq = prepare_analysis(data_dir, timeline_start, timeline_end, tz)

    try:
        metric = compute_signup_fraction(
            matrix,
            users_csv,
            timeline_start,
            timeline_end,
            grain="Weekly",
            tz=tz,
        )
    except GrainNotAllowedError:
        raise

    result: Dict[str, Any] = {
        "timelineStart": timeline_start.isoformat(),
        "timelineEnd": timeline_end.isoformat(),
        "grain": "Weekly",
        "timezone": timezone,
        "dataQuality": dq.to_dict(),
        "metric": metric,
    }

    json_path = output_dir / "signup_fraction.json"
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote signup fraction results to %s", json_path)

    chart_path = _render_chart(metric, charts_dir / "signup_fraction.png")
    result["chartPath"] = str(chart_path)
    result["resultsPath"] = str(json_path)
    return result


def _num(v: Any) -> Optional[float]:
    if v is None or v == "N/A":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bucket_label(b: Dict[str, Any]) -> str:
    label = b["bucket"]
    if b.get("partialBucket"):
        side = b.get("censorSide") or "partial"
        label = f"{label} ({side})"
    return label


def _render_chart(metric: Dict[str, Any], output_path: Path) -> Path:
    """Line chart styled like retention_pipeline/visualize.py."""
    buckets = metric.get("buckets") or []
    labels = [_bucket_label(b) for b in buckets]
    values = [_num(b["signupFractionPercent"]) for b in buckets]
    plot_x = [lab for lab, v in zip(labels, values) if v is not None]
    plot_y = [v for v in values if v is not None]

    fig, ax = plt.subplots(figsize=(10, 4))
    if plot_y:
        ax.plot(plot_x, plot_y, marker="o", color="#1f4e79")
    ax.set_title(
        "Signup fraction — new accounts / active users (%) · "
        f"{metric.get('timelineStart')} → {metric.get('timelineEnd')}"
    )
    ax.set_xlabel("Bucket")
    ax.set_ylabel("Signup fraction (%)")
    ax.tick_params(axis="x", rotation=45)
    if len(plot_x) > 24:
        step = max(1, len(plot_x) // 16)
        ax.set_xticks(range(0, len(plot_x), step))
        ax.set_xticklabels(plot_x[::step], rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path
