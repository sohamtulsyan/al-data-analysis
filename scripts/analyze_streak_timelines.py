#!/usr/bin/env python3
"""Streak % comparison for two timelines (logged-in users).

Timelines (default)
-------------------
* **T1** — 2026-06-28 → 2026-07-12
* **T2** — 2026-07-13 → 2026-07-26

Source: Activity Matrix from Daily Play Data (not the Streaks API snapshot).
Population per timeline: ``imgl*`` UIDs with ≥1 active day in that window.

Metrics (per uid, per timeline)
-------------------------------
* **current streak** — consecutive active calendar days ending on the
  timeline end date. ``0`` if inactive on that end date. Plotted for N∈{1…14}.
* **max streak** — longest consecutive run using only active days inside
  the timeline window. Plotted for N∈{1…14}.

Writes two percentage-only charts (current / max separate) plus JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retention_pipeline.cleaning.pipeline import (  # noqa: E402
    ActivityMatrix,
    prepare_analysis,
)
from retention_pipeline.config import DAILY_PLAY_DATA_DIR, OUTPUT_DIR  # noqa: E402
from retention_pipeline.logged_in import is_logged_in_uid  # noqa: E402

T1_START = date(2026, 6, 28)
T1_END = date(2026, 7, 12)
T2_START = date(2026, 7, 13)
T2_END = date(2026, 7, 26)
CURRENT_TARGETS = tuple(range(1, 15))  # 1..14
MAX_TARGETS = tuple(range(1, 15))  # 1..14


def _active_dates(matrix: ActivityMatrix, uid: str) -> List[date]:
    idxs = matrix._active.get(uid)  # noqa: SLF001
    if not idxs:
        return []
    return sorted(matrix.days[i] for i in idxs)


def _streak_runs(active: Sequence[date]) -> List[Tuple[date, date, int]]:
    """Maximal consecutive calendar spans → (start, end, L)."""
    if not active:
        return []
    runs: List[Tuple[date, date, int]] = []
    start = end = active[0]
    for d in active[1:]:
        if d == end + timedelta(days=1):
            end = d
        else:
            runs.append((start, end, (end - start).days + 1))
            start = end = d
    runs.append((start, end, (end - start).days + 1))
    return runs


def current_streak_as_of(active: Sequence[date], as_of: date) -> int:
    """Consecutive days ending on ``as_of``; 0 if ``as_of`` is inactive."""
    active_set: Set[date] = set(active)
    if as_of not in active_set:
        return 0
    streak = 0
    day = as_of
    while day in active_set:
        streak += 1
        day -= timedelta(days=1)
    return streak


def max_streak_in_window(
    active: Sequence[date], window_start: date, window_end: date
) -> int:
    in_window = [d for d in active if window_start <= d <= window_end]
    runs = _streak_runs(in_window)
    return max((L for _s, _e, L in runs), default=0)


def uids_active_in_window(
    matrix: ActivityMatrix,
    window_start: date,
    window_end: date,
    *,
    logged_in_only: bool = True,
) -> List[str]:
    out: List[str] = []
    for uid in matrix.uids():
        if logged_in_only and not is_logged_in_uid(uid):
            continue
        active = _active_dates(matrix, uid)
        if any(window_start <= d <= window_end for d in active):
            out.append(uid)
    return out


def count_streak_levels(
    values: Iterable[int], targets: Sequence[int]
) -> Dict[str, int]:
    counter = Counter(values)
    return {str(t): int(counter.get(t, 0)) for t in targets}


def _pct_map(counts: Dict[str, int], n: int) -> Dict[str, float]:
    return {k: round(100.0 * v / n, 2) if n else 0.0 for k, v in counts.items()}


def analyze_timeline(
    matrix: ActivityMatrix,
    *,
    label: str,
    window_start: date,
    window_end: date,
) -> Dict[str, Any]:
    uids = uids_active_in_window(matrix, window_start, window_end)
    current_vals: List[int] = []
    max_vals: List[int] = []
    for uid in uids:
        active = _active_dates(matrix, uid)
        current_vals.append(current_streak_as_of(active, window_end))
        max_vals.append(max_streak_in_window(active, window_start, window_end))

    current_counts = count_streak_levels(current_vals, CURRENT_TARGETS)
    max_counts = count_streak_levels(max_vals, MAX_TARGETS)
    n = len(uids)
    return {
        "label": label,
        "windowStart": window_start.isoformat(),
        "windowEnd": window_end.isoformat(),
        "activeLoggedInUsers": n,
        "currentStreak": {
            "asOf": window_end.isoformat(),
            "targets": list(CURRENT_TARGETS),
            "definition": (
                "Consecutive active calendar days ending on windowEnd; "
                "0 if inactive on windowEnd."
            ),
            "counts": current_counts,
            "pctOfActive": _pct_map(current_counts, n),
            "inactiveOnEndDate": sum(1 for v in current_vals if v == 0),
            "inactiveOnEndDatePct": round(
                100.0 * sum(1 for v in current_vals if v == 0) / n, 2
            )
            if n
            else 0.0,
        },
        "maxStreak": {
            "targets": list(MAX_TARGETS),
            "definition": (
                "Longest consecutive active-day run using only days inside "
                "[windowStart, windowEnd]."
            ),
            "counts": max_counts,
            "pctOfActive": _pct_map(max_counts, n),
        },
    }


def plot_pct_bars(
    t1: Dict[str, Any],
    t2: Dict[str, Any],
    *,
    metric_key: str,
    targets: Sequence[int],
    title: str,
    ylabel: str,
    output_path: Path,
    figsize: Tuple[float, float] = (10, 4.8),
) -> Path:
    """Single diagram: T1 vs T2 % bars for one metric."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [str(t) for t in targets]
    x = list(range(len(labels)))
    width = 0.38

    p1 = t1[metric_key]["pctOfActive"]
    p2 = t2[metric_key]["pctOfActive"]
    y1 = [p1[str(t)] for t in targets]
    y2 = [p2[str(t)] for t in targets]

    t1_label = (
        f"T1  {t1['windowStart']} → {t1['windowEnd']}\n"
        f"(n={t1['activeLoggedInUsers']:,} active imgl)"
    )
    t2_label = (
        f"T2  {t2['windowStart']} → {t2['windowEnd']}\n"
        f"(n={t2['activeLoggedInUsers']:,} active imgl)"
    )

    fig, ax = plt.subplots(figsize=figsize)
    bars1 = ax.bar(
        [i - width / 2 for i in x], y1, width, label=t1_label, color="#2F6F8F"
    )
    bars2 = ax.bar(
        [i + width / 2 for i in x], y2, width, label=t2_label, color="#C47B3A"
    )
    ax.set_title(title)
    ax.set_xlabel("Streak length N (days)")
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(fontsize=8, loc="upper right")
    ymax = max(y1 + y2 + [0])
    ax.set_ylim(0, ymax * 1.22 if ymax else 1)

    # Annotate: always for current (≤3 bars); for max only if few enough or sparse
    annotate = len(targets) <= 5
    if annotate:
        for bars in (bars1, bars2):
            for bar in bars:
                h = bar.get_height()
                ax.annotate(
                    f"{h:.1f}%",
                    xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )
    else:
        # Label only bars that are relatively tall to avoid clutter on 1..14
        for bars in (bars1, bars2):
            for bar in bars:
                h = bar.get_height()
                if h < max(ymax * 0.08, 0.5):
                    continue
                ax.annotate(
                    f"{h:.1f}%",
                    xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 2),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                )

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return output_path


def run(
    *,
    data_dir: Path,
    output_dir: Path,
    t1_start: date,
    t1_end: date,
    t2_start: date,
    t2_end: date,
    timezone: str,
    skip_charts: bool = False,
) -> Dict[str, Any]:
    if t1_start <= t2_end and t2_start <= t1_end:
        raise SystemExit("T1 and T2 windows overlap.")

    tz = ZoneInfo(timezone)
    load_start = min(t1_start, t2_start)
    load_end = max(t1_end, t2_end)
    _plays, matrix, report = prepare_analysis(data_dir, load_start, load_end, tz)

    t1 = analyze_timeline(matrix, label="T1", window_start=t1_start, window_end=t1_end)
    t2 = analyze_timeline(matrix, label="T2", window_start=t2_start, window_end=t2_end)

    result: Dict[str, Any] = {
        "timezone": timezone,
        "cohort": "loggedInUsers",
        "uidPrefix": "imgl",
        "currentTargets": list(CURRENT_TARGETS),
        "maxTargets": list(MAX_TARGETS),
        "dataQuality": report.to_dict(),
        "timelines": {"T1": t1, "T2": t2},
        "charts": {},
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    out_json = output_dir / "streak_timeline_comparison.json"
    if not skip_charts:
        charts_dir = output_dir / "charts"
        current_path = charts_dir / "streak_current_pct.png"
        max_path = charts_dir / "streak_max_pct.png"
        plot_pct_bars(
            t1,
            t2,
            metric_key="currentStreak",
            targets=CURRENT_TARGETS,
            title=(
                "Current streak (% of active logged-in) — T1 vs T2\n"
                f"as of each timeline end · N ∈ {{1…{CURRENT_TARGETS[-1]}}}"
            ),
            ylabel="% of active logged-in users",
            output_path=current_path,
            figsize=(12, 5),
        )
        plot_pct_bars(
            t1,
            t2,
            metric_key="maxStreak",
            targets=MAX_TARGETS,
            title=(
                "Max streak (% of active logged-in) — T1 vs T2\n"
                f"longest in-window run · N ∈ {{1…{MAX_TARGETS[-1]}}}"
            ),
            ylabel="% of active logged-in users",
            output_path=max_path,
            figsize=(12, 5),
        )
        result["charts"] = {
            "currentPct": str(current_path),
            "maxPct": str(max_path),
        }

    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["_outputJson"] = str(out_json)
    return result


def _print_summary(result: Dict[str, Any]) -> None:
    print("\n=== Streak timeline comparison (logged-in, % of active) ===")
    for key in ("T1", "T2"):
        t = result["timelines"][key]
        print(
            f"\n{key}: {t['windowStart']} → {t['windowEnd']}  "
            f"active imgl={t['activeLoggedInUsers']:,}"
        )
        cur_p = t["currentStreak"]["pctOfActive"]
        cur_parts = [f"{n}={cur_p[str(n)]:.2f}%" for n in CURRENT_TARGETS]
        print("  current 1…14: " + "  ".join(cur_parts))
        print(
            f"  inactive on end: {t['currentStreak']['inactiveOnEndDatePct']:.1f}%"
        )
        mx_p = t["maxStreak"]["pctOfActive"]
        parts = [f"{n}={mx_p[str(n)]:.2f}%" for n in MAX_TARGETS]
        print("  max 1…14: " + "  ".join(parts))
    charts = result.get("charts") or {}
    if charts:
        print("\nCharts:")
        for name, path in charts.items():
            print(f"  {name}: {path}")
    print(f"\nJSON: {result.get('_outputJson')}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default=str(DAILY_PLAY_DATA_DIR))
    p.add_argument("--output-dir", default=str(OUTPUT_DIR))
    p.add_argument("--t1-start", default=T1_START.isoformat())
    p.add_argument("--t1-end", default=T1_END.isoformat())
    p.add_argument("--t2-start", default=T2_START.isoformat())
    p.add_argument("--t2-end", default=T2_END.isoformat())
    p.add_argument("--timezone", default="UTC")
    p.add_argument("--skip-charts", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result = run(
        data_dir=Path(args.data_dir),
        output_dir=Path(args.output_dir),
        t1_start=date.fromisoformat(args.t1_start),
        t1_end=date.fromisoformat(args.t1_end),
        t2_start=date.fromisoformat(args.t2_start),
        t2_end=date.fromisoformat(args.t2_end),
        timezone=args.timezone,
        skip_charts=args.skip_charts,
    )
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
