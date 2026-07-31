#!/usr/bin/env python3
"""Break-to-return churn (A), gap distribution (B), anon vs logged-in (C).

Source: Activity Matrix (calendar days in TIMEZONE). Not Streaks API.

Window: analysis breaks with t in [window_start, window_end]. Pre-window
history is loaded so streak length L is not left-truncated. Horizons are
right-censored: break eligible for N only if t+N <= window_end.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
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

DEFAULT_WINDOW_START = date(2026, 3, 5)
DEFAULT_WINDOW_END = date(2026, 7, 27)
RETURN_HORIZONS = (1, 3, 7)
NEVER_RETURN_WINDOW = 14
L_BUCKET_ORDER = ("1", "2", "3-6", "7-13", "14-29", "30+")
GAP_COVERAGE_K = (1, 2, 3)
LONGEST_THRESHOLDS = (3, 7, 14)


@dataclass(frozen=True)
class BreakEvent:
    uid: str
    t: date  # first inactive day after streak
    L: int
    next_active: Optional[date]  # None if never returns in matrix
    logged_in: bool


def _l_bucket(L: int) -> str:
    if L == 1:
        return "1"
    if L == 2:
        return "2"
    if 3 <= L <= 6:
        return "3-6"
    if 7 <= L <= 13:
        return "7-13"
    if 14 <= L <= 29:
        return "14-29"
    return "30+"


def _active_dates(matrix: ActivityMatrix, uid: str) -> List[date]:
    idxs = matrix._active.get(uid)  # noqa: SLF001 — sparse set needed for runs
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
            L = (end - start).days + 1
            runs.append((start, end, L))
            start = end = d
    L = (end - start).days + 1
    runs.append((start, end, L))
    return runs


def extract_breaks(
    matrix: ActivityMatrix,
    window_start: date,
    window_end: date,
    uids: Optional[Iterable[str]] = None,
) -> List[BreakEvent]:
    """Break at t = day after a streak end, with t in [window_start, window_end].

    Open streaks that reach window_end produce no break (still active / censored).
    """
    breaks: List[BreakEvent] = []
    uid_iter = uids if uids is not None else matrix.uids()
    for uid in uid_iter:
        active = _active_dates(matrix, uid)
        if not active:
            continue
        runs = _streak_runs(active)
        logged_in = is_logged_in_uid(uid)
        for i, (_start, end, L) in enumerate(runs):
            t = end + timedelta(days=1)
            if t < window_start or t > window_end:
                continue
            # If end is the last observed active day and t has no later activity
            # in matrix, next_active is None. (t itself is inactive by construction.)
            next_active = runs[i + 1][0] if i + 1 < len(runs) else None
            breaks.append(
                BreakEvent(
                    uid=uid,
                    t=t,
                    L=L,
                    next_active=next_active,
                    logged_in=logged_in,
                )
            )
    return breaks


def _returned_within(ev: BreakEvent, n: int) -> bool:
    if ev.next_active is None:
        return False
    # Return on any day in (t, t+N] ≡ t < next_active <= t+N
    return ev.t < ev.next_active <= ev.t + timedelta(days=n)


def _gap_days(ev: BreakEvent) -> Optional[int]:
    """Inactive days from t through day before next active. None if never returns."""
    if ev.next_active is None:
        return None
    return (ev.next_active - ev.t).days


def _rate(num: int, den: int) -> Optional[float]:
    if den == 0:
        return None
    return num / den


def _pct(num: int, den: int) -> Optional[float]:
    r = _rate(num, den)
    return None if r is None else round(100.0 * r, 4)


def summarize_break_return(
    breaks: Sequence[BreakEvent],
    window_end: date,
    horizons: Sequence[int] = RETURN_HORIZONS,
) -> Dict[str, Any]:
    """Event-level primary; user-level = first break per uid only."""

    def _one(level_breaks: Sequence[BreakEvent]) -> Dict[str, Any]:
        overall: Dict[str, Any] = {}
        by_L: Dict[str, Any] = {b: {} for b in L_BUCKET_ORDER}

        for n in horizons:
            eligible = [e for e in level_breaks if e.t + timedelta(days=n) <= window_end]
            returned = sum(1 for e in eligible if _returned_within(e, n))
            den = len(eligible)
            ret_rate = _rate(returned, den)
            overall[f"D{n}"] = {
                "breaks": den,
                "returned": returned,
                "returnRate": None if ret_rate is None else round(ret_rate, 6),
                "churnRate": None if ret_rate is None else round(1.0 - ret_rate, 6),
                "returnPct": _pct(returned, den),
                "churnPct": None if ret_rate is None else round(100.0 * (1.0 - ret_rate), 4),
            }

            bucket_counts: Dict[str, List[BreakEvent]] = defaultdict(list)
            for e in eligible:
                bucket_counts[_l_bucket(e.L)].append(e)
            for bucket in L_BUCKET_ORDER:
                group = bucket_counts.get(bucket, [])
                ret = sum(1 for e in group if _returned_within(e, n))
                d = len(group)
                rr = _rate(ret, d)
                by_L[bucket][f"D{n}"] = {
                    "breaks": d,
                    "returned": ret,
                    "returnRate": None if rr is None else round(rr, 6),
                    "churnRate": None if rr is None else round(1.0 - rr, 6),
                    "returnPct": _pct(ret, d),
                    "churnPct": None if rr is None else round(100.0 * (1.0 - rr), 4),
                }

        return {"overall": overall, "byL": by_L}

    # User-level: earliest break per uid
    first_by_uid: Dict[str, BreakEvent] = {}
    for e in sorted(breaks, key=lambda x: (x.uid, x.t)):
        if e.uid not in first_by_uid:
            first_by_uid[e.uid] = e

    return {
        "eventLevel": _one(breaks),
        "userLevel": _one(list(first_by_uid.values())),
        "breakEventCount": len(breaks),
        "usersWithBreak": len(first_by_uid),
    }


def summarize_gaps(
    breaks: Sequence[BreakEvent],
    window_end: date,
    never_return_window: int = NEVER_RETURN_WINDOW,
) -> Dict[str, Any]:
    # Gap histogram among breaks that eventually return (next_active known)
    returners = [e for e in breaks if e.next_active is not None]
    gaps = [_gap_days(e) for e in returners]
    assert all(g is not None and g >= 1 for g in gaps)
    hist = Counter(gaps)
    gap_hist = {str(k): hist[k] for k in sorted(hist)}
    n_returners = len(returners)
    gap1 = hist.get(1, 0)

    # Cumulative coverage among returners (freeze dosage on the returning population)
    coverage = {}
    for k in GAP_COVERAGE_K:
        covered = sum(1 for g in gaps if g is not None and g <= k)
        coverage[f"K{k}"] = {
            "breaksWithGapLeK": covered,
            "returnerBreaks": n_returners,
            "pctOfReturners": _pct(covered, n_returners),
        }

    # Never-return in 14d among fully observable 14d horizons
    eligible_14 = [
        e for e in breaks if e.t + timedelta(days=never_return_window) <= window_end
    ]
    never_14 = [e for e in eligible_14 if not _returned_within(e, never_return_window)]
    coverage_all = {}
    for k in GAP_COVERAGE_K:
        # Eligible for K: can observe whether gap <= K (t+K <= window_end)
        eligible_k = [e for e in breaks if e.t + timedelta(days=k) <= window_end]
        covered = sum(
            1
            for e in eligible_k
            if e.next_active is not None and _gap_days(e) is not None and _gap_days(e) <= k
        )
        coverage_all[f"K{k}"] = {
            "breaksCovered": covered,
            "eligibleBreaks": len(eligible_k),
            "pctCovered": _pct(covered, len(eligible_k)),
            "note": "gap<=K among breaks with fully observable K-day window; never-return not counted as covered",
        }

    return {
        "returnerBreaks": n_returners,
        "gapHistogram": gap_hist,
        "gapEq1": {
            "count": gap1,
            "pctOfReturners": _pct(gap1, n_returners),
            "note": "One freeze fully rescues these breaks",
        },
        "cumulativeCoverageOfReturners": coverage,
        "cumulativeCoverageOfAllObservableBreaks": coverage_all,
        "neverReturn": {
            "windowDays": never_return_window,
            "eligibleBreaks": len(eligible_14),
            "neverReturned": len(never_14),
            "pct": _pct(len(never_14), len(eligible_14)),
            "note": "Tracked separately; not counted as freeze-covered",
        },
    }


def summarize_identity_and_anon(
    matrix: ActivityMatrix,
    breaks: Sequence[BreakEvent],
    window_start: date,
    window_end: date,
) -> Dict[str, Any]:
    """C: volume split, anon longest-streak sizing, A rerun on anon vs logged-in."""
    window_days = []
    d = window_start
    while d <= window_end:
        window_days.append(d)
        d += timedelta(days=1)

    active_in_window = matrix.active_uids_on_days(window_days)
    imgl = {u for u in active_in_window if is_logged_in_uid(u)}
    anon = active_in_window - imgl
    n = len(active_in_window)

    # OUU: global first active day strictly before first active day in window
    ouu_imgl = 0
    ouu_anon = 0
    for uid in active_in_window:
        all_active = _active_dates(matrix, uid)
        if not all_active:
            continue
        global_first = all_active[0]
        in_window_first = next((x for x in all_active if window_start <= x <= window_end), None)
        if in_window_first is None:
            continue
        if global_first < in_window_first:
            if is_logged_in_uid(uid):
                ouu_imgl += 1
            else:
                ouu_anon += 1
    ouu_total = ouu_imgl + ouu_anon

    # Anon longest streak (max run length over full matrix history for that uid)
    longest_counts = {f"ge{t}": 0 for t in LONGEST_THRESHOLDS}
    longest_dist = Counter()
    for uid in anon:
        runs = _streak_runs(_active_dates(matrix, uid))
        if not runs:
            continue
        longest = max(L for _, _, L in runs)
        longest_dist[longest] += 1
        for t in LONGEST_THRESHOLDS:
            if longest >= t:
                longest_counts[f"ge{t}"] += 1

    anon_breaks = [e for e in breaks if not e.logged_in]
    imgl_breaks = [e for e in breaks if e.logged_in]

    return {
        "identityField": {
            "status": "ok",
            "note": (
                "Play-by-Play exposes a single userId→uid. Logged-in = prefix 'imgl'; "
                "anon = all other uids (typically 64-char hex). No separate deviceId column. "
                "Anon streaks are a lower bound if device IDs are unstable."
            ),
            "dayIndexBasis": "calendar_day",
        },
        "volumeInWindow": {
            "activeUsers": n,
            "imglUsers": len(imgl),
            "anonUsers": len(anon),
            "pctImgl": _pct(len(imgl), n),
            "pctAnon": _pct(len(anon), n),
        },
        "ouuInWindow": {
            "ouuUsers": ouu_total,
            "imgl": ouu_imgl,
            "anonDeviceIdOnly": ouu_anon,
            "pctImgl": _pct(ouu_imgl, ouu_total),
            "pctAnon": _pct(ouu_anon, ouu_total),
            "definition": (
                "OUU = active in window and global first active day (in matrix, "
                "including pre-window history) is strictly before first in-window active day"
            ),
        },
        "anonLongestStreak": {
            "anonUsers": len(anon),
            "thresholds": longest_counts,
            "convertiblePopulationNote": (
                "Anon users whose longest reconstructed streak (maximal consecutive "
                "isActiveOnDay run) meets threshold — login-to-save-streak target"
            ),
        },
        "breakReturnByIdentity": {
            "anon": summarize_break_return(anon_breaks, window_end),
            "loggedIn": summarize_break_return(imgl_breaks, window_end),
        },
    }


def run(
    window_start: date,
    window_end: date,
    timezone: str = "UTC",
    data_dir: Path = DAILY_PLAY_DATA_DIR,
    output_dir: Path = OUTPUT_DIR,
) -> Dict[str, Any]:
    tz = ZoneInfo(timezone)
    print(f"Loading Activity Matrix with history through {window_end} …")
    # Matrix columns = window; prepare_analysis loads earlier CSVs so pre-window
    # active days land as foresight/extra columns → L is not left-truncated.
    _plays, matrix, dq = prepare_analysis(data_dir, window_start, window_end, tz)
    print(
        f"Matrix users={len(matrix.uids())} days_cols={len(matrix.days)} "
        f"plays={len(_plays)} dq_null_uid={dq.to_dict().get('nullUidDropped', dq.to_dict())}"
    )

    breaks = extract_breaks(matrix, window_start, window_end)
    print(f"Break events in window: {len(breaks)}")

    result: Dict[str, Any] = {
        "analysis": "break_return_gap_identity",
        "windowStart": window_start.isoformat(),
        "windowEnd": window_end.isoformat(),
        "timezone": timezone,
        "dayIndexBasis": "calendar_day",
        "publishingCalendar": (
            "No publishing calendar in pipeline. Breaks defined on calendar days "
            "in TIMEZONE (Activity Matrix columns). Confirm daily puzzle publish "
            "separately; if gaps exist, redefine on published-day index."
        ),
        "definitions": {
            "streakRun": "maximal consecutive span of isActiveOnDay=1",
            "breakEvent": "isActiveOnDay[t-1]=1 and isActiveOnDay[t]=0; L=length of run ending t-1",
            "return": "active on any day in (t, t+N]; N in {1,3,7}",
            "freezeAddressableChurn": "1 - returnRate among breaks with fully observable horizon",
            "censoring": "drop break for horizon N if t+N > windowEnd",
            "gap": "for breaks that return, days inactive from t until next active day",
            "loggedIn": "uid prefix imgl (case-insensitive)",
        },
        "A_breakToReturn": summarize_break_return(breaks, window_end),
        "B_gapDistribution": summarize_gaps(breaks, window_end),
        "C_anonVsLoggedIn": summarize_identity_and_anon(
            matrix, breaks, window_start, window_end
        ),
        "dataQuality": dq.to_dict(),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "break_return_analysis.json"
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out_path}")
    return result


def _print_summary(result: Dict[str, Any]) -> None:
    a = result["A_breakToReturn"]["eventLevel"]["overall"]
    print("\n=== A. Break → return (event-level) ===")
    for key in ("D1", "D3", "D7"):
        row = a[key]
        print(
            f"  {key}: breaks={row['breaks']} return={row['returnPct']}% "
            f"churn={row['churnPct']}%"
        )
    print("  By L (event-level churn %):")
    for bucket in L_BUCKET_ORDER:
        parts = []
        for key in ("D1", "D3", "D7"):
            row = result["A_breakToReturn"]["eventLevel"]["byL"][bucket][key]
            parts.append(f"{key} n={row['breaks']} churn={row['churnPct']}%")
        print(f"    L={bucket}: " + " | ".join(parts))

    b = result["B_gapDistribution"]
    print("\n=== B. Gap distribution ===")
    print(
        f"  Returner breaks={b['returnerBreaks']}  gap=1: {b['gapEq1']['count']} "
        f"({b['gapEq1']['pctOfReturners']}% of returners)"
    )
    for k, row in b["cumulativeCoverageOfAllObservableBreaks"].items():
        print(f"  Coverage {k}: {row['pctCovered']}% of eligible breaks ({row['breaksCovered']}/{row['eligibleBreaks']})")
    nr = b["neverReturn"]
    print(
        f"  Never-return {nr['windowDays']}d: {nr['pct']}% "
        f"({nr['neverReturned']}/{nr['eligibleBreaks']})"
    )

    c = result["C_anonVsLoggedIn"]
    print("\n=== C. Anon vs logged-in ===")
    v = c["volumeInWindow"]
    print(
        f"  Active users: {v['activeUsers']}  imgl={v['imglUsers']} ({v['pctImgl']}%)  "
        f"anon={v['anonUsers']} ({v['pctAnon']}%)"
    )
    o = c["ouuInWindow"]
    print(
        f"  OUU: {o['ouuUsers']}  imgl={o['imgl']} ({o['pctImgl']}%)  "
        f"anon={o['anonDeviceIdOnly']} ({o['pctAnon']}%)"
    )
    ls = c["anonLongestStreak"]["thresholds"]
    print(
        f"  Anon longestStreak >=3/7/14: {ls['ge3']} / {ls['ge7']} / {ls['ge14']}"
    )
    for label, key in (("anon", "anon"), ("logged-in", "loggedIn")):
        ov = c["breakReturnByIdentity"][key]["eventLevel"]["overall"]
        print(
            f"  {label} churn D1/D3/D7: "
            f"{ov['D1']['churnPct']}% / {ov['D3']['churnPct']}% / {ov['D7']['churnPct']}% "
            f"(n={ov['D1']['breaks']}/{ov['D3']['breaks']}/{ov['D7']['breaks']})"
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-start", default=DEFAULT_WINDOW_START.isoformat())
    p.add_argument("--window-end", default=DEFAULT_WINDOW_END.isoformat())
    p.add_argument("--timezone", default="UTC")
    args = p.parse_args()
    start = date.fromisoformat(args.window_start)
    end = date.fromisoformat(args.window_end)
    result = run(start, end, timezone=args.timezone)
    _print_summary(result)


if __name__ == "__main__":
    main()
