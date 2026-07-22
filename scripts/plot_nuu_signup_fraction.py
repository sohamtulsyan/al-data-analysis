#!/usr/bin/env python3
"""Personal analysis: signup fraction vs New Unique Users (NUU).

Standalone — not wired into the CLI. Does not modify pipeline code.

Per ISO week:
  signupFractionPercent = signups(week) / nuu(week) × 100

* signups — users CSV Registered Date → ISO week (Email ID never read)
* nuu     — from output/nuu_weekly.csv produced by scripts/compute_nuu.py

Writes:
  output/nuu_signup_fraction.csv
  output/nuu_signup_fraction.json
  output/charts/nuu_signup_fraction.png
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Union

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retention_pipeline.bucketing import build_buckets
from retention_pipeline.config import OUTPUT_DIR, PROJECT_ROOT  # noqa: E402
from retention_pipeline.users_csv import (  # noqa: E402
    count_by_week,
    load_registration_dates,
)

DEFAULT_WINDOW_START = date(2026, 3, 1)
DEFAULT_WINDOW_END = date(2026, 7, 16)


def _pct(signups: int, nuu: int) -> Union[float, str]:
    if nuu == 0:
        return "N/A"
    return round(100.0 * signups / nuu, 4)


def load_nuu_weekly(path: Path) -> Dict[str, int]:
    out: Dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            week = (row.get("week") or "").strip()
            if not week:
                continue
            out[week] = int(row["nuu_count"])
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Signup fraction = weekly signups / weekly NUU (%)."
    )
    parser.add_argument(
        "--users-csv",
        default=str(PROJECT_ROOT / "users_2026_07_16_12_34_42.csv"),
        help="Users export CSV (Email ID is never read)",
    )
    parser.add_argument(
        "--nuu-weekly",
        default=str(OUTPUT_DIR / "nuu_weekly.csv"),
        help="Weekly NUU CSV from scripts/compute_nuu.py",
    )
    parser.add_argument(
        "--window-start",
        default=DEFAULT_WINDOW_START.isoformat(),
    )
    parser.add_argument(
        "--window-end",
        default=DEFAULT_WINDOW_END.isoformat(),
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
    )
    parser.add_argument(
        "--skip-chart",
        action="store_true",
        help="Write CSV only (no matplotlib PNG)",
    )
    args = parser.parse_args(argv)

    users_csv = Path(args.users_csv)
    nuu_path = Path(args.nuu_weekly)
    output_dir = Path(args.output_dir)
    charts_dir = output_dir / "charts"
    window_start = date.fromisoformat(args.window_start)
    window_end = date.fromisoformat(args.window_end)

    if not users_csv.is_file():
        print(f"Users CSV not found: {users_csv}", file=sys.stderr)
        return 1
    if not nuu_path.is_file():
        print(
            f"NUU weekly CSV not found: {nuu_path}\n"
            "Run: PYTHONPATH=. python scripts/compute_nuu.py",
            file=sys.stderr,
        )
        return 1

    nuu_by_week = load_nuu_weekly(nuu_path)
    if not nuu_by_week:
        print(f"No NUU rows in {nuu_path}", file=sys.stderr)
        return 1

    registration_dates = load_registration_dates(
        users_csv,
        timeline_start=window_start,
        timeline_end=window_end,
    )
    signups_by_week = count_by_week(registration_dates)

    window_buckets = build_buckets(window_start, window_end, "Weekly")
    bucket_by_key = {b.key: b for b in window_buckets}
    weeks = sorted(w for w in nuu_by_week if w in bucket_by_key)
    rows: List[dict] = []
    for week in weeks:
        signups = int(signups_by_week.get(week, 0))
        nuu = nuu_by_week[week]
        bucket = bucket_by_key[week]
        rows.append(
            {
                "week": week,
                "signups": signups,
                "nuu_count": nuu,
                "signupFractionPercent": _pct(signups, nuu),
                "coveredStart": bucket.covered_start.isoformat(),
                "coveredEnd": bucket.covered_end.isoformat(),
                "partialBucket": bucket.censor is not None,
                "censorSide": bucket.censor,
            }
        )

    csv_path = output_dir / "nuu_signup_fraction.csv"
    json_path = output_dir / "nuu_signup_fraction.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_fields = ["week", "signups", "nuu_count", "signupFractionPercent"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "metric": "nuuSignupFraction",
        "grain": "Weekly",
        "timelineStart": window_start.isoformat(),
        "timelineEnd": window_end.isoformat(),
        "buckets": [
            {
                "bucket": r["week"],
                "signups": r["signups"],
                "nuuCount": r["nuu_count"],
                "signupFractionPercent": r["signupFractionPercent"],
                "coveredStart": r.get("coveredStart"),
                "coveredEnd": r.get("coveredEnd"),
                "partialBucket": r.get("partialBucket"),
                "censorSide": r.get("censorSide"),
            }
            for r in rows
        ],
        "totalSignups": sum(r["signups"] for r in rows),
        "totalNuu": sum(r["nuu_count"] for r in rows),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Weeks: {len(rows)}")
    print(f"CSV:   {csv_path}")
    print(f"JSON:  {json_path}")

    if not args.skip_chart:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = [r["week"] for r in rows]
        values = [
            None
            if r["signupFractionPercent"] == "N/A"
            else float(r["signupFractionPercent"])
            for r in rows
        ]
        plot_x = [lab for lab, v in zip(labels, values) if v is not None]
        plot_y = [v for v in values if v is not None]

        charts_dir.mkdir(parents=True, exist_ok=True)
        png_path = charts_dir / "nuu_signup_fraction.png"
        fig, ax = plt.subplots(figsize=(10, 4))
        if plot_y:
            ax.plot(plot_x, plot_y, marker="o", color="#1f4e79")
        ax.set_title(
            "Signup fraction vs NUU — signups / new unique users (%) · "
            f"{window_start.isoformat()} → {window_end.isoformat()}"
        )
        ax.set_xlabel("Bucket")
        ax.set_ylabel("Signup fraction (%)")
        ax.tick_params(axis="x", rotation=45)
        if len(plot_x) > 24:
            step = max(1, len(plot_x) // 16)
            ax.set_xticks(range(0, len(plot_x), step))
            ax.set_xticklabels(plot_x[::step], rotation=45, ha="right")
        fig.tight_layout()
        fig.savefig(png_path, dpi=140)
        plt.close(fig)
        print(f"Chart: {png_path}")

    for r in rows[:3]:
        print(
            f"  {r['week']}: signups={r['signups']} nuu={r['nuu_count']} "
            f"→ {r['signupFractionPercent']}%"
        )
    if len(rows) > 3:
        print("  …")
        r = rows[-1]
        print(
            f"  {r['week']}: signups={r['signups']} nuu={r['nuu_count']} "
            f"→ {r['signupFractionPercent']}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
