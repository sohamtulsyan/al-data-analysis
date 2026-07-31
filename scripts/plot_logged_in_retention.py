#!/usr/bin/env python3
"""Logged-in user retention — volume + Strict / Cumulative / Consecutive.

Standalone script, also invoked by the API job handler.

Definitions
-----------
* **Logged-in user** — play ``uid`` starts with ``imgl`` (case-insensitive),
  matching India Mini's logged-in detection.
* **D0 (per bucket)** — first active day of the user *inside that bucket*
  (day for Daily, first active day in the ISO week for Weekly, first active day
  in the calendar month for Monthly).
* A logged-in user is counted at most once per bucket (on that D0). Across
  buckets the same uid may appear again with a new D0.

Predicates match NUU / OUU / PRD (no Rolling):
  Strict:      active exactly on D0+N          N ∈ {1, 3, 7, 30}
  Cumulative:  active on any day in D1…DN     N ∈ {3, 7, 30}
  Consecutive: active on every day in D1…DN   N ∈ {3, 7, 30}

Right-censoring: uid eligible for DN only if D0+N ≤ window_end.

Writes:
  output/logged_in_retention.json
  output/charts/logged_in_strictRetention.png (optional)
  output/charts/logged_in_cumulativeRetention.png
  output/charts/logged_in_consecutiveRetention.png
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retention_pipeline.bucketing import build_buckets  # noqa: E402
from retention_pipeline.cleaning.pipeline import (  # noqa: E402
    ActivityMatrix,
    discover_csv_days,
    prepare_analysis,
)
from retention_pipeline.config import DAILY_PLAY_DATA_DIR, OUTPUT_DIR  # noqa: E402
from retention_pipeline.logged_in import is_logged_in_uid  # noqa: E402
from retention_pipeline.metrics.errors import validate_grain  # noqa: E402
from retention_pipeline.summary_stats import (  # noqa: E402
    print_distribution_summaries,
    summarize_retention_payload,
)

DEFAULT_WINDOW_START = date(2026, 7, 1)
DEFAULT_WINDOW_END = date(2026, 7, 16)

STRICT_HORIZONS = (1, 3, 7, 30)
WINDOW_HORIZONS = (3, 7, 30)

Predicate = Callable[[ActivityMatrix, str, date, int], bool]


def _na_or_pct(retained: int, total: int) -> Any:
    if total == 0:
        return "N/A"
    return (retained / total) * 100.0


def _logged_in_d0_in_bucket(
    matrix: ActivityMatrix,
    bucket_days: Tuple[date, ...],
) -> Dict[str, date]:
    """uid → D0 for logged-in users active in this bucket."""
    result: Dict[str, date] = {}
    for uid in matrix.active_uids_on_days(bucket_days):
        if not is_logged_in_uid(uid):
            continue
        d0 = matrix.first_active_day_in(uid, bucket_days)
        if d0 is None:
            continue
        result[uid] = d0
    return result


def _strict(matrix: ActivityMatrix, uid: str, d0: date, n: int) -> bool:
    return matrix.is_active(uid, d0 + timedelta(days=n))


def _cumulative(matrix: ActivityMatrix, uid: str, d0: date, n: int) -> bool:
    for i in range(1, n + 1):
        if matrix.is_active(uid, d0 + timedelta(days=i)):
            return True
    return False


def _consecutive(matrix: ActivityMatrix, uid: str, d0: date, n: int) -> bool:
    for i in range(1, n + 1):
        if not matrix.is_active(uid, d0 + timedelta(days=i)):
            return False
    return True


def _compute_family(
    *,
    name: str,
    matrix: ActivityMatrix,
    window_start: date,
    window_end: date,
    grain: str,
    horizons: Tuple[int, ...],
    predicate: Predicate,
) -> Dict[str, Any]:
    buckets = build_buckets(window_start, window_end, grain)
    results: List[Dict[str, Any]] = []

    for bucket in buckets:
        d0_map = _logged_in_d0_in_bucket(matrix, bucket.days)
        total_active = len(d0_map)
        horizon_results: Dict[str, Any] = {}

        for n in horizons:
            eligible = {
                uid: d0
                for uid, d0 in d0_map.items()
                if d0 + timedelta(days=n) <= window_end
            }
            total = len(eligible)
            retained = sum(
                1
                for uid, d0 in eligible.items()
                if predicate(matrix, uid, d0, n)
            )
            horizon_results[f"D{n}"] = {
                "retentionPercent": _na_or_pct(retained, total),
                "retainedCohort": retained,
                "totalCohort": total,
                "censored": total_active - total,
                "totalLoggedInInBucket": total_active,
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
        "timelineStart": window_start.isoformat(),
        "timelineEnd": window_end.isoformat(),
        "cohort": "loggedInUsers",
        "buckets": results,
    }


def _num(v: Any) -> Optional[float]:
    if v is None or v == "N/A":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bucket_labels(buckets: List[Dict[str, Any]]) -> List[str]:
    labels = []
    for b in buckets:
        label = b["bucket"]
        if b.get("partialBucket"):
            side = b.get("censorSide") or "partial"
            label = f"{label} ({side})"
        labels.append(label)
    return labels


def _plot_retention(
    ret: Dict[str, Any],
    title: str,
    output_path: Path,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    buckets = ret.get("buckets") or []
    if not buckets:
        return output_path
    labels = _bucket_labels(buckets)
    horizons = sorted(buckets[0]["horizons"].keys(), key=lambda h: int(h[1:]))

    fig, ax = plt.subplots(figsize=(10, 4))
    for h in horizons:
        ys = [_num(b["horizons"][h]["retentionPercent"]) for b in buckets]
        xs = [lab for lab, y in zip(labels, ys) if y is not None]
        yy = [y for y in ys if y is not None]
        if yy:
            ax.plot(xs, yy, marker="o", label=h)
    ax.set_title(title)
    ax.set_xlabel("Bucket")
    ax.set_ylabel("Retention (%)")
    ax.legend()
    ax.tick_params(axis="x", rotation=45)
    if len(labels) > 24:
        step = max(1, len(labels) // 16)
        ax.set_xticks(range(0, len(labels), step))
        ax.set_xticklabels(labels[::step], rotation=45, ha="right")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def build_logged_in_retention_payload(
    matrix: ActivityMatrix,
    *,
    window_start: date,
    window_end: date,
    grain: str,
    timezone: str,
    strict_horizons: Tuple[int, ...] = STRICT_HORIZONS,
    window_horizons: Tuple[int, ...] = WINDOW_HORIZONS,
) -> Dict[str, Any]:
    """Compute logged-in retention families; used by CLI and API handler."""
    strict = _compute_family(
        name="loggedInStrictRetention",
        matrix=matrix,
        window_start=window_start,
        window_end=window_end,
        grain=grain,
        horizons=strict_horizons,
        predicate=_strict,
    )
    cumulative = _compute_family(
        name="loggedInCumulativeRetention",
        matrix=matrix,
        window_start=window_start,
        window_end=window_end,
        grain=grain,
        horizons=window_horizons,
        predicate=_cumulative,
    )
    consecutive = _compute_family(
        name="loggedInConsecutiveRetention",
        matrix=matrix,
        window_start=window_start,
        window_end=window_end,
        grain=grain,
        horizons=window_horizons,
        predicate=_consecutive,
    )
    return {
        "timelineStart": window_start.isoformat(),
        "timelineEnd": window_end.isoformat(),
        "grain": grain,
        "timezone": timezone,
        "cohort": "loggedInUsers",
        "uidPrefix": "imgl",
        "metrics": {
            "strictRetention": strict,
            "cumulativeRetention": cumulative,
            "consecutiveRetention": consecutive,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Logged-in user retention (strict / cumulative / consecutive)."
    )
    parser.add_argument("--data-dir", default=str(DAILY_PLAY_DATA_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--window-start", default=DEFAULT_WINDOW_START.isoformat())
    parser.add_argument("--window-end", default=DEFAULT_WINDOW_END.isoformat())
    parser.add_argument(
        "--grain",
        default="Daily",
        choices=["Daily", "Weekly", "Monthly"],
    )
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--skip-chart", action="store_true")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    charts_dir = output_dir / "charts"
    window_start = date.fromisoformat(args.window_start)
    window_end = date.fromisoformat(args.window_end)
    if window_start > window_end:
        print("window-start must be ≤ window-end", file=sys.stderr)
        return 1

    available = discover_csv_days(data_dir)
    if not available:
        print(f"No Daily Play Data CSVs in {data_dir}", file=sys.stderr)
        return 1

    timeframe_start = available[0]
    timeframe_end = max(available[-1], window_end)
    tz = ZoneInfo(args.timezone)

    print(
        f"Activity Matrix timeframe {timeframe_start} → {timeframe_end} "
        f"(logged-in retention window {window_start} → {window_end}, grain={args.grain})"
    )
    _plays, matrix, _dq = prepare_analysis(
        data_dir, timeframe_start, timeframe_end, tz
    )

    try:
        validate_grain(window_start, window_end, args.grain)
    except Exception as exc:
        to_dict = getattr(exc, "to_dict", None)
        if callable(to_dict):
            print(json.dumps(to_dict(), indent=2), file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 2

    payload = build_logged_in_retention_payload(
        matrix,
        window_start=window_start,
        window_end=window_end,
        grain=args.grain,
        timezone=args.timezone,
    )

    logged_in_total = 0
    for b in payload["metrics"]["strictRetention"]["buckets"]:
        h = b["horizons"]
        key = next(iter(h), None)
        if key:
            logged_in_total += h[key].get("totalLoggedInInBucket", 0)
    print(
        f"Logged-in cohort-days in window (sum of per-bucket counts): {logged_in_total}"
    )
    print_distribution_summaries(
        summarize_retention_payload(payload, prefix="loggedIn."),
        title="Logged-in distribution summaries",
    )

    json_path = output_dir / "logged_in_retention.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    if not args.skip_chart:
        metrics = payload["metrics"]
        paths = {
            "strict": _plot_retention(
                metrics["strictRetention"],
                "Logged-in Strict Retention (%)",
                charts_dir / "logged_in_strictRetention.png",
            ),
            "cumulative": _plot_retention(
                metrics["cumulativeRetention"],
                "Logged-in Cumulative Retention (%)",
                charts_dir / "logged_in_cumulativeRetention.png",
            ),
            "consecutive": _plot_retention(
                metrics["consecutiveRetention"],
                "Logged-in Consecutive Retention (%)",
                charts_dir / "logged_in_consecutiveRetention.png",
            ),
        }
        for name, path in paths.items():
            print(f"  {name}: {path}")

    print(f"Results: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
