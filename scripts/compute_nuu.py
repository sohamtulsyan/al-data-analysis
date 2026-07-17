#!/usr/bin/env python3
"""Personal analysis: New Unique Users (NUU) — standalone, not part of the CLI.

Uses the existing Activity Matrix via prepare_analysis() only. Does not modify
pipeline code.

Definitions
-----------
* **Timeframe** — full Daily Play Data history loaded into the matrix
  (earliest CSV → window end). Used for lookback.
* **Window** — days we report on (default 2026-03-01 … 2026-07-16).

For each day D in the window, each uid active on D:
  - active on any timeframe day strictly before D → returning user
  - otherwise → new unique user (NUU) on D

Equivalently: NUU on D iff the uid's global first active day in the matrix is D.
Each uid is an NUU on at most one day.

Week/month totals = sum of daily NUU counts (not distinct-active / row-OR).

Writes (under output/):
  nuu_daily.csv
  nuu_weekly.csv
  nuu_monthly.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retention_pipeline.bucketing import (  # noqa: E402
    enumerate_timeline_days,
    month_key,
    week_key,
)
from retention_pipeline.cleaning.pipeline import (  # noqa: E402
    ActivityMatrix,
    discover_csv_days,
    prepare_analysis,
)
from retention_pipeline.config import DAILY_PLAY_DATA_DIR, OUTPUT_DIR  # noqa: E402

DEFAULT_WINDOW_START = date(2026, 3, 1)
DEFAULT_WINDOW_END = date(2026, 7, 16)


def compute_daily_nuu(
    matrix: ActivityMatrix,
    window_start: date,
    window_end: date,
) -> Dict[date, int]:
    """Daily NUU counts for each day in the window (0-filled).

    Uses only public ActivityMatrix APIs (uids / first_active_day_in / days).
    """
    window_days = enumerate_timeline_days(window_start, window_end)
    counts: Dict[date, int] = {d: 0 for d in window_days}
    # Lookback universe = every column present in the matrix (full timeframe).
    timeframe_days = list(matrix.days)

    for uid in matrix.uids():
        first = matrix.first_active_day_in(uid, timeframe_days)
        if first is None:
            continue
        if window_start <= first <= window_end:
            counts[first] = counts.get(first, 0) + 1

    return counts


def _sum_by_key(
    daily: Mapping[date, int],
    key_fn,
) -> List[Tuple[str, int]]:
    totals: Dict[str, int] = defaultdict(int)
    for d, n in daily.items():
        totals[key_fn(d)] += n
    return sorted(totals.items())


def _write_csv(path: Path, rows: Sequence[dict], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute New Unique Users (NUU) — personal analysis script."
    )
    parser.add_argument("--data-dir", default=str(DAILY_PLAY_DATA_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--window-start", default=DEFAULT_WINDOW_START.isoformat())
    parser.add_argument("--window-end", default=DEFAULT_WINDOW_END.isoformat())
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    window_start = date.fromisoformat(args.window_start)
    window_end = date.fromisoformat(args.window_end)
    if window_start > window_end:
        print("window-start must be ≤ window-end", file=sys.stderr)
        return 1

    available = discover_csv_days(data_dir)
    if not available:
        print(f"No Daily Play Data CSVs in {data_dir}", file=sys.stderr)
        return 1

    # Full timeframe for lookback; window is only what we report.
    timeframe_start = available[0]
    timeframe_end = max(available[-1], window_end)
    tz = ZoneInfo(args.timezone)

    print(
        f"Activity Matrix timeframe {timeframe_start} → {timeframe_end} "
        f"(NUU window {window_start} → {window_end})"
    )
    _plays, matrix, _dq = prepare_analysis(
        data_dir, timeframe_start, timeframe_end, tz
    )

    daily = compute_daily_nuu(matrix, window_start, window_end)
    weekly = _sum_by_key(daily, week_key)
    monthly = _sum_by_key(daily, month_key)

    daily_path = output_dir / "nuu_daily.csv"
    weekly_path = output_dir / "nuu_weekly.csv"
    monthly_path = output_dir / "nuu_monthly.csv"

    _write_csv(
        daily_path,
        [{"date": d.isoformat(), "nuu_count": daily[d]} for d in sorted(daily)],
        ["date", "nuu_count"],
    )
    _write_csv(
        weekly_path,
        [{"week": w, "nuu_count": n} for w, n in weekly],
        ["week", "nuu_count"],
    )
    _write_csv(
        monthly_path,
        [{"month": m, "nuu_count": n} for m, n in monthly],
        ["month", "nuu_count"],
    )

    print(f"Total NUU in window: {sum(daily.values())}")
    print(f"Daily:   {daily_path}")
    print(f"Weekly:  {weekly_path}")
    print(f"Monthly: {monthly_path}")
    for month, n in monthly:
        print(f"  {month}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
