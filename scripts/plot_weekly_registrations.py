#!/usr/bin/env python3
"""Plot weekly new-account registrations from a WordPress-style users export CSV.

Uses Registered Date only (account creation). Email ID is never read.
Chart style matches retention_pipeline/visualize.py retention plots.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from retention_pipeline.bucketing import week_key  # noqa: E402
from retention_pipeline.config import OUTPUT_DIR  # noqa: E402
from retention_pipeline.users_csv import load_registration_dates  # noqa: E402

# Default timeline: after February 2026 → from 1 March 2026 onward.
DEFAULT_TIMELINE_START = date(2026, 3, 1)


def weekly_counts(registration_dates: List[date]) -> Tuple[List[str], List[int]]:
    """Dense week series from first to last observed week (inclusive gaps = 0)."""
    if not registration_dates:
        return [], []
    counts = Counter(week_key(d) for d in registration_dates)
    first = min(counts)
    last = max(counts)

    def monday_of(week_label: str) -> date:
        year_s, week_s = week_label.split("-W")
        return date.fromisocalendar(int(year_s), int(week_s), 1)

    labels: List[str] = []
    values: List[int] = []
    cur = monday_of(first)
    end = monday_of(last)
    while cur <= end:
        label = week_key(cur)
        labels.append(label)
        values.append(counts.get(label, 0))
        cur += timedelta(days=7)
    return labels, values


def render_plot(
    labels: List[str],
    values: List[int],
    output_path: Path,
    timeline_start: date = DEFAULT_TIMELINE_START,
) -> Path:
    """Line chart styled like visualize.py retention PNGs."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(labels, values, marker="o", color="#1f4e79")
    ax.set_title(
        f"Weekly registrations — new accounts from {timeline_start.isoformat()}"
    )
    ax.set_xlabel("Bucket")
    ax.set_ylabel("New accounts")
    ax.tick_params(axis="x", rotation=45)
    if len(labels) > 24:
        step = max(1, len(labels) // 16)
        ax.set_xticks(range(0, len(labels), step))
        ax.set_xticklabels(labels[::step], rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plot weekly account registrations from a users CSV export."
    )
    parser.add_argument(
        "csv",
        nargs="?",
        default=str(ROOT / "users_2026_07_16_12_34_42.csv"),
        help="Path to users CSV (default: project users_*.csv)",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_DIR / "charts" / "weekly_registrations.png"),
        help="Output PNG path",
    )
    parser.add_argument(
        "--timeline-start",
        default=DEFAULT_TIMELINE_START.isoformat(),
        help="Include registrations on/after this date (default: 2026-03-01)",
    )
    parser.add_argument(
        "--timeline-end",
        default=None,
        help="Optional inclusive end date (YYYY-MM-DD)",
    )
    args = parser.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1

    timeline_start = date.fromisoformat(args.timeline_start)
    timeline_end = (
        date.fromisoformat(args.timeline_end) if args.timeline_end else None
    )
    dates = load_registration_dates(
        csv_path, timeline_start=timeline_start, timeline_end=timeline_end
    )
    labels, values = weekly_counts(dates)
    if not labels:
        print(
            f"No Registered Date values on/after {timeline_start.isoformat()}.",
            file=sys.stderr,
        )
        return 1

    out = render_plot(
        labels, values, Path(args.output), timeline_start=timeline_start
    )
    print(
        f"Plotted {sum(values)} registrations from {timeline_start.isoformat()} "
        f"across {len(labels)} weeks → {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
