#!/usr/bin/env python3
"""Fetch PuzzleMe user-stats (streaks) for UIDs not already in a streak CSV.

The WordPress users export (`User ID`) is numeric and does **not** match the
`imgl*` PuzzleMe play UIDs stored in streak_stats. By default this script
collects candidate UIDs from Daily Play Data (`imgl*` only), diffs against the
existing streak CSV, and fetches only the missing ones at ≤10 req/s.

Examples
--------
Dry-run (count missing only)::

    python scripts/fetch_streak_stats.py \\
      --streak-csv "/Users/soham/Downloads/streak_stats (3).csv" \\
      --dry-run

Fetch missing logged-in UIDs from play data::

    export STREAK_API_TOKEN='...'   # do not hardcode
    python scripts/fetch_streak_stats.py \\
      --streak-csv "/Users/soham/Downloads/streak_stats (3).csv"

If you truly have a CSV of PuzzleMe userIds (column userId / uid / User ID)::

    python scripts/fetch_streak_stats.py \\
      --streak-csv streak_stats.csv \\
      --uids-csv path/to/puzzleme_uids.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from retention_pipeline.config import DAILY_PLAY_DATA_DIR  # noqa: E402
from retention_pipeline.logged_in import is_logged_in_uid  # noqa: E402

API_URL = "https://puzzleme.amuselabs.com/pmm/api/v2/user-stats"
DEFAULT_SERIES = "pm-content-crossword-india-mini"

FIELDS = [
    "userId",
    "http_status",
    "series",
    "started_count",
    "started_currentStreak",
    "started_longestStreak",
    "completed_count",
    "completed_currentStreak",
    "completed_longestStreak",
    "completed_totalSolveTime",
    "completed_fastestSolveTime",
    "error",
]

UID_COLUMN_CANDIDATES = ("userId", "uid", "User ID", "user_id", "UserId")


def _strip_keys(row: Dict[str, str]) -> Dict[str, str]:
    return {(k or "").strip(): (v or "").strip() for k, v in row.items()}


def _pick_uid(row: Dict[str, str]) -> str:
    stripped = _strip_keys(row)
    for name in UID_COLUMN_CANDIDATES:
        if name in stripped and stripped[name]:
            return stripped[name]
    return ""


def load_done_uids(streak_csv: Path) -> Set[str]:
    """UIDs with a successful (empty error) row — skip on resume."""
    if not streak_csv.is_file():
        return set()
    done: Set[str] = set()
    with streak_csv.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            r = _strip_keys(row)
            uid = r.get("userId") or ""
            if uid and not r.get("error"):
                done.add(uid)
    return done


def load_uids_from_csv(path: Path) -> List[str]:
    uids: List[str] = []
    seen: Set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            uid = _pick_uid(row)
            if uid and uid not in seen:
                seen.add(uid)
                uids.append(uid)
    if not uids:
        raise SystemExit(
            f"No UIDs found in {path}. Expected a column among: "
            f"{', '.join(UID_COLUMN_CANDIDATES)}"
        )
    return uids


def load_imgl_uids_from_play_data(directory: Path) -> List[str]:
    if not directory.is_dir():
        raise SystemExit(f"Play data directory not found: {directory}")
    seen: Set[str] = set()
    for path in sorted(directory.glob("*.csv")):
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                uid = (row.get("uid") or "").strip()
                if uid and is_logged_in_uid(uid):
                    seen.add(uid)
    return sorted(seen)


def looks_imgl(uids: Iterable[str], sample: int = 50) -> bool:
    vals = list(uids)[:sample]
    if not vals:
        return False
    return sum(1 for u in vals if is_logged_in_uid(u)) / len(vals) >= 0.8


def looks_numeric(uids: Iterable[str], sample: int = 50) -> bool:
    vals = list(uids)[:sample]
    if not vals:
        return False
    return sum(1 for u in vals if u.isdigit()) / len(vals) >= 0.8


def get_stats(
    session: requests.Session,
    uid: str,
    token: str,
    series: str,
) -> Dict[str, Optional[str]]:
    row: Dict[str, Optional[str]] = {k: None for k in FIELDS}
    row["userId"] = uid
    try:
        resp = session.get(
            API_URL,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            params={"userId": uid, "series": series},
            timeout=30,
        )
    except requests.RequestException as exc:
        row["error"] = f"request failed: {exc}"
        return row

    row["http_status"] = str(resp.status_code)
    if resp.status_code != 200:
        row["error"] = f"http {resp.status_code}: {resp.text[:150]}"
        return row

    try:
        data = resp.json()
    except ValueError:
        row["error"] = "bad json"
        return row

    stats = data.get("stats") or {}
    started = stats.get("started") or {}
    completed = stats.get("completed") or {}
    row["series"] = data.get("series")
    row["started_count"] = started.get("count")
    row["started_currentStreak"] = started.get("currentStreak")
    row["started_longestStreak"] = started.get("longestStreak")
    row["completed_count"] = completed.get("count")
    row["completed_currentStreak"] = completed.get("currentStreak")
    row["completed_longestStreak"] = completed.get("longestStreak")
    row["completed_totalSolveTime"] = completed.get("totalSolveTime")
    row["completed_fastestSolveTime"] = completed.get("fastestSolveTime")
    return row


def resolve_candidates(args: argparse.Namespace) -> List[str]:
    """Pick UID source, guarding against WordPress vs PuzzleMe ID mismatch."""
    if args.uids_csv is not None:
        uids = load_uids_from_csv(args.uids_csv)
        print(f"Loaded {len(uids)} UIDs from {args.uids_csv}")
        return uids

    if args.users_csv is not None:
        uids = load_uids_from_csv(args.users_csv)
        print(f"Loaded {len(uids)} UIDs from {args.users_csv}")
        # WordPress export vs existing imgl streak file
        done_sample_path = args.streak_csv
        done = load_done_uids(done_sample_path) if done_sample_path.is_file() else set()
        if looks_numeric(uids) and (not done or looks_imgl(done)):
            if args.force_users_ids:
                print(
                    "WARNING: users CSV looks like WordPress numeric IDs, "
                    "streak file looks like imgl* — proceeding because "
                    "--force-users-ids was set."
                )
                return uids
            print(
                "NOTE: users CSV has WordPress numeric User IDs; streak CSV "
                "uses PuzzleMe imgl* UIDs (zero overlap). Falling back to "
                f"logged-in UIDs from play data: {args.play_data_dir}\n"
                "      Pass --uids-csv with PuzzleMe IDs, or --force-users-ids "
                "to override."
            )
            return load_imgl_uids_from_play_data(args.play_data_dir)
        return uids

    print(f"Loading imgl* UIDs from play data: {args.play_data_dir}")
    return load_imgl_uids_from_play_data(args.play_data_dir)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--streak-csv",
        type=Path,
        required=True,
        help="Existing streak_stats CSV (resume target; new rows appended).",
    )
    p.add_argument(
        "--users-csv",
        type=Path,
        default=None,
        help="WordPress users export. Numeric IDs auto-fall back to play data.",
    )
    p.add_argument(
        "--uids-csv",
        type=Path,
        default=None,
        help="CSV of PuzzleMe userIds (columns: userId / uid / User ID).",
    )
    p.add_argument(
        "--play-data-dir",
        type=Path,
        default=DAILY_PLAY_DATA_DIR,
        help="Daily play CSVs used to discover imgl* UIDs (default: Daily Play Data).",
    )
    p.add_argument(
        "--force-users-ids",
        action="store_true",
        help="Use WordPress User IDs even when streak file has imgl* IDs.",
    )
    p.add_argument(
        "--series",
        default=DEFAULT_SERIES,
        help=f"PuzzleMe series id (default: {DEFAULT_SERIES}).",
    )
    p.add_argument(
        "--token",
        default=os.environ.get("STREAK_API_TOKEN", ""),
        help="Bearer JWT (or set STREAK_API_TOKEN). Prompted on 401 if missing.",
    )
    p.add_argument(
        "--rps",
        type=float,
        default=10.0,
        help="Max requests per second (default: 10, hard cap ≤10).",
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Optional JSON dump of the full streak CSV after the run.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts only; do not call the API.",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.rps <= 0:
        raise SystemExit("--rps must be > 0")
    if args.rps > 10:
        raise SystemExit("--rps must be ≤ 10 (hard cap)")
    delay = 1.0 / args.rps

    done = load_done_uids(args.streak_csv)
    candidates = resolve_candidates(args)
    todo = [u for u in candidates if u not in done]

    print(
        f"{len(candidates)} candidates, {len(done)} already done, "
        f"{len(todo)} to fetch"
    )
    if args.dry_run:
        print("Dry run — exiting before API calls.")
        if todo:
            print("First 5 to fetch:")
            for u in todo[:5]:
                print(f"  {u}")
        return 0

    if not todo:
        print("Nothing to fetch.")
        return 0

    token = (args.token or "").strip()
    if not token:
        token = input("Paste JWT (blank=abort): ").strip()
        if not token:
            print("No token — aborting.", file=sys.stderr)
            return 1

    args.streak_csv.parent.mkdir(parents=True, exist_ok=True)
    new_file = not args.streak_csv.is_file()
    session = requests.Session()
    n_ok = n_err = 0
    err_by_code: Dict[Optional[str], int] = {}

    with args.streak_csv.open(
        "a" if not new_file else "w",
        newline="",
        encoding="utf-8",
    ) as csv_f:
        writer = csv.DictWriter(csv_f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()

        i = 0
        while i < len(todo):
            uid = todo[i]
            row = get_stats(session, uid, token, args.series)
            code = row.get("http_status")

            if code == "401":
                fresh = input("401. Paste fresh JWT (blank=stop): ").strip()
                if not fresh:
                    print("Stopped. Progress saved.")
                    break
                token = fresh
                continue  # retry same uid

            writer.writerow({k: row.get(k) for k in FIELDS})
            csv_f.flush()
            os.fsync(csv_f.fileno())

            if code == "200" and not row.get("error"):
                n_ok += 1
            else:
                n_err += 1
                err_by_code[code] = err_by_code.get(code, 0) + 1

            i += 1
            time.sleep(delay)
            if i % 100 == 0 or i == len(todo):
                print(f"[{i}/{len(todo)}] ok={n_ok} err={n_err} {err_by_code}")

    print(f"SUMMARY: ok={n_ok} err={n_err} {err_by_code}")

    if args.out_json is not None and args.streak_csv.is_file():
        with args.streak_csv.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        with args.out_json.open("w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
