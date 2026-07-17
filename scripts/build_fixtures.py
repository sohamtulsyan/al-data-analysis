"""Build a 45-day fixture dataset under Daily Play Data for local E2E demo.

Data follows the Play-by-Play CSV schema exactly. Patterns exercise foresight
activity, fully-contained engagement, retention horizons, and play states.
Not used by the production fetch path — only for offline analysis demos/tests.
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from retention_pipeline.config import CSV_COLUMNS, PROJECT_ROOT

TZ = ZoneInfo("UTC")
START = date(2026, 1, 1)
DAYS = 45
RNG = random.Random(42)


def iso(day: date, hour: int = 12, minute: int = 0) -> str:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).isoformat()


def main() -> None:
    out = PROJECT_ROOT / "fixtures" / "Daily Play Data"
    out.mkdir(parents=True, exist_ok=True)

    # Clear prior fixture CSVs
    for p in out.glob("*.csv"):
        p.unlink()

    users = [f"user_{i:03d}" for i in range(1, 81)]
    rows_by_day: dict[date, list[dict]] = {
        START + timedelta(days=i): [] for i in range(DAYS)
    }

    for uid in users:
        # Cohort entry day staggered across first 15 days
        d0 = START + timedelta(days=RNG.randint(0, 14))
        # Always a start-day play
        state_roll = RNG.random()
        if state_roll < 0.15:
            filled, play_state = 0, "inProgress"  # loaded
        elif state_roll < 0.55:
            filled, play_state = RNG.randint(1, 8), "inProgress"  # solving
        else:
            filled, play_state = 10, "completed"

        # Some plays update same day; some foresight to later day
        if RNG.random() < 0.35:
            lag = RNG.randint(1, 5)
            updated = min(d0 + timedelta(days=lag), START + timedelta(days=DAYS - 1))
        else:
            updated = d0

        screen = 0 if filled == 0 else RNG.randint(20, 600)
        puzzle = f"puzzle_{(hash(uid) % 20) + 1:02d}"
        rows_by_day[d0].append(
            {
                "uid": uid,
                "seriesId": "fixture-series",
                "puzzleId": puzzle,
                "startTimestamp": iso(d0, 9),
                "updatedTimestamp": iso(updated, 15 if updated != d0 else 11),
                "playState": play_state,
                "totalBoxes": 10,
                "filledBoxes": filled,
                "screenTimeSeconds": screen,
            }
        )

        # Return pattern for retention
        # ~40% return on exact D1, ~25% D3, ~15% D7, ~8% D30; some consecutive streaks
        for n, p in ((1, 0.40), (3, 0.25), (7, 0.15), (30, 0.08)):
            target = d0 + timedelta(days=n)
            if target > START + timedelta(days=DAYS - 1):
                continue
            if RNG.random() < p:
                rows_by_day[target].append(
                    {
                        "uid": uid,
                        "seriesId": "fixture-series",
                        "puzzleId": f"puzzle_ret_{n}",
                        "startTimestamp": iso(target, 10),
                        "updatedTimestamp": iso(target, 11),
                        "playState": "completed" if RNG.random() < 0.5 else "inProgress",
                        "totalBoxes": 10,
                        "filledBoxes": RNG.randint(1, 10),
                        "screenTimeSeconds": RNG.randint(30, 400),
                    }
                )

        # Consecutive streak users (~10%)
        if RNG.random() < 0.10:
            for i in range(1, 4):
                day = d0 + timedelta(days=i)
                if day > START + timedelta(days=DAYS - 1):
                    break
                # Avoid duplicate exact rows
                rows_by_day[day].append(
                    {
                        "uid": uid,
                        "seriesId": "fixture-series",
                        "puzzleId": f"puzzle_streak_{i}",
                        "startTimestamp": iso(day, 14),
                        "updatedTimestamp": iso(day, 15),
                        "playState": "inProgress",
                        "totalBoxes": 10,
                        "filledBoxes": RNG.randint(1, 5),
                        "screenTimeSeconds": RNG.randint(40, 200),
                    }
                )

    for day, rows in rows_by_day.items():
        path = out / f"{day.day:02d}-{day.month:02d}-{day.year:04d}.csv"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)

    print(f"Wrote {DAYS} daily CSVs to {out}")


if __name__ == "__main__":
    main()
