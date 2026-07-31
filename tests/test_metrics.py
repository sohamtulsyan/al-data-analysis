"""Unit tests for PRD-defined metric rules using schema-conformant fixture CSVs."""

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from retention_pipeline.bucketing import allowed_grains, build_buckets, timeline_length_days
from retention_pipeline.cleaning.pipeline import prepare_analysis
from retention_pipeline.config import CSV_COLUMNS
from retention_pipeline.metrics.engagement import compute_engagement, mean_from_histogram, median_from_histogram
from retention_pipeline.metrics.errors import GrainNotAllowedError, validate_grain
from retention_pipeline.metrics.play_state import compute_play_state
from retention_pipeline.metrics.retention import (
    compute_consecutive_retention,
    compute_cumulative_retention,
    compute_rolling_retention,
    compute_strict_retention,
)
from retention_pipeline.metrics.user_growth import compute_user_growth

TZ = ZoneInfo("UTC")


def _write_day(directory: Path, day: date, rows: list[dict]) -> None:
    path = directory / f"{day.day:02d}-{day.month:02d}-{day.year:04d}.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _iso(day: date, h: int = 12, m: int = 0, s: int = 0) -> str:
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=TZ).isoformat()


def _row(
    uid: str,
    puzzle: str,
    start: date,
    updated: date,
    play_state: str = "inProgress",
    filled: int = 1,
    total: int = 10,
    screen: int = 60,
    start_h: int = 10,
    updated_h: int = 12,
) -> dict:
    return {
        "uid": uid,
        "seriesId": "test-series",
        "puzzleId": puzzle,
        "startTimestamp": _iso(start, start_h),
        "updatedTimestamp": _iso(updated, updated_h),
        "playState": play_state,
        "totalBoxes": total,
        "filledBoxes": filled,
        "screenTimeSeconds": screen,
    }


class GrainGatingTests(unittest.TestCase):
    def test_permission_matrix(self):
        self.assertEqual(allowed_grains(1), ["Daily"])
        self.assertEqual(allowed_grains(6), ["Daily"])
        self.assertEqual(allowed_grains(7), ["Daily", "Weekly"])
        self.assertEqual(allowed_grains(29), ["Daily", "Weekly"])
        self.assertEqual(allowed_grains(30), ["Daily", "Weekly", "Monthly"])

    def test_disallowed_grain_errors(self):
        with self.assertRaises(GrainNotAllowedError) as ctx:
            validate_grain(date(2026, 1, 1), date(2026, 1, 5), "Weekly")
        self.assertEqual(ctx.exception.payload["errorType"], "GRAIN_NOT_ALLOWED_FOR_TIMELINE")
        self.assertEqual(ctx.exception.payload["timelineLengthDays"], 5)


class ActivityAndGrowthTests(unittest.TestCase):
    def test_foresight_activity_and_intervening_gap(self):
        """Assumption 5 / Variables §2.3: start Mon, update Wed → active Mon+Wed, not Tue."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            mon = date(2026, 3, 2)
            tue = date(2026, 3, 3)
            wed = date(2026, 3, 4)
            # Play started Monday, last updated Wednesday — lives in Monday's CSV
            _write_day(
                d,
                mon,
                [_row("u1", "p1", mon, wed, filled=2, screen=120)],
            )
            _write_day(d, tue, [])
            _write_day(d, wed, [])

            plays, matrix, _ = prepare_analysis(d, mon, wed, TZ)
            self.assertTrue(matrix.is_active("u1", mon))
            self.assertFalse(matrix.is_active("u1", tue))
            self.assertTrue(matrix.is_active("u1", wed))
            self.assertEqual(matrix.daily_active_users(mon), 1)
            self.assertEqual(matrix.daily_active_users(tue), 0)
            self.assertEqual(matrix.daily_active_users(wed), 1)

            ug = compute_user_growth(matrix, mon, wed, "Daily", TZ)
            by_bucket = {b["bucket"]: b["userGrowth"] for b in ug["buckets"]}
            self.assertEqual(by_bucket["2026-03-02"], 1)
            self.assertEqual(by_bucket["2026-03-03"], 0)
            self.assertEqual(by_bucket["2026-03-04"], 1)


class EngagementTests(unittest.TestCase):
    def test_median_histogram(self):
        self.assertEqual(median_from_histogram({1: 1, 2: 1, 3: 1}), 2.0)
        self.assertEqual(median_from_histogram({1: 1, 3: 1}), 2.0)
        self.assertIsNone(median_from_histogram({}))

    def test_mean_histogram(self):
        self.assertEqual(mean_from_histogram({10: 1, 30: 1}), 20.0)
        self.assertEqual(mean_from_histogram({40: 2, 100: 1}), 60.0)
        self.assertIsNone(mean_from_histogram({}))

    def test_fully_contained_eligibility(self):
        """Play Mon→Wed excluded from daily buckets; included in weekly (Engagement §4.1)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Week of Mon 2 Mar 2026
            days = [date(2026, 3, d_) for d_ in range(2, 9)]
            mon, wed = days[0], days[2]
            for day in days:
                rows = []
                if day == mon:
                    rows.append(_row("u1", "p1", mon, wed, screen=100))
                    rows.append(_row("u2", "p2", mon, mon, screen=40))  # contained
                _write_day(d, day, rows)

            plays, matrix, _ = prepare_analysis(d, days[0], days[-1], TZ)
            daily = compute_engagement(plays, days[0], days[-1], "Daily", TZ)
            mon_bucket = next(b for b in daily["buckets"] if b["bucket"] == "2026-03-02")
            # Only u2's fully-contained play qualifies on Monday
            self.assertEqual(mon_bucket["qualifyingPlays"], 1)
            self.assertEqual(mon_bucket["medianScreenTimeSeconds"], 40.0)
            self.assertEqual(mon_bucket["averageScreenTimeSeconds"], 40.0)

            weekly = compute_engagement(plays, days[0], days[-1], "Weekly", TZ)
            # Both plays fully inside the ISO week
            week = weekly["buckets"][0]
            self.assertEqual(week["qualifyingPlays"], 2)
            self.assertEqual(week["medianScreenTimeSeconds"], 70.0)  # mean of 40 and 100
            self.assertEqual(week["averageScreenTimeSeconds"], 70.0)


class RetentionTests(unittest.TestCase):
    def test_strict_vs_cumulative_vs_consecutive(self):
        """Same cohort: cumulative ≥ strict ≥ consecutive (PRD relationships)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # 40-day timeline so D30 is observable for early cohorts
            start = date(2026, 1, 1)
            end = date(2026, 2, 15)
            # u_strict: active D0 and exactly D3
            # u_cum: active D0 and D2 (not D3) → cumulative D3 yes, strict D3 no
            # u_consec: active D0,D1,D2,D3 → consecutive D3 yes
            d0 = date(2026, 1, 1)
            rows_by_day: dict[date, list] = {d0 + timedelta(days=i): [] for i in range(0, 46)}

            def add(uid, day, puzzle):
                rows_by_day[day].append(_row(uid, puzzle, day, day, filled=2, screen=30))

            add("u_strict", d0, "p1")
            add("u_strict", d0 + timedelta(days=3), "p2")

            add("u_cum", d0, "p1")
            add("u_cum", d0 + timedelta(days=2), "p2")

            add("u_consec", d0, "p1")
            for i in range(1, 4):
                add("u_consec", d0 + timedelta(days=i), f"p{i+1}")

            for day, rows in rows_by_day.items():
                if start <= day <= end or rows:
                    _write_day(d, day, rows)

            plays, matrix, _ = prepare_analysis(d, start, end, TZ)
            strict = compute_strict_retention(matrix, start, end, "Daily", TZ)
            cum = compute_cumulative_retention(matrix, start, end, "Daily", TZ)
            consec = compute_consecutive_retention(matrix, start, end, "Daily", TZ)

            b0 = next(b for b in strict["buckets"] if b["bucket"] == "2026-01-01")
            s3 = b0["horizons"]["D3"]
            c3 = next(b for b in cum["buckets"] if b["bucket"] == "2026-01-01")["horizons"]["D3"]
            n3 = next(b for b in consec["buckets"] if b["bucket"] == "2026-01-01")["horizons"]["D3"]

            # Denominators identical across metrics (Common §4.1)
            self.assertEqual(s3["totalCohort"], c3["totalCohort"])
            self.assertEqual(s3["totalCohort"], n3["totalCohort"])
            self.assertEqual(s3["totalCohort"], 3)

            # u_strict + u_consec strict; all three cumulative (D3 ∈ cumulative window);
            # only u_consec consecutive
            self.assertEqual(s3["retainedCohort"], 2)
            self.assertEqual(c3["retainedCohort"], 3)
            self.assertEqual(n3["retainedCohort"], 1)
            self.assertGreaterEqual(c3["retentionPercent"], s3["retentionPercent"])
            self.assertGreaterEqual(s3["retentionPercent"], n3["retentionPercent"])

    def test_rolling_nested_denominators(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            start = date(2026, 1, 1)
            end = date(2026, 1, 20)  # 20 days — D30 censored for everyone; D3/D7 partial
            d0 = start
            _write_day(d, d0, [_row("u1", "p1", d0, d0)])
            # Activity on day 10 for rolling
            day10 = date(2026, 1, 10)
            _write_day(d, day10, [_row("u1", "p2", day10, day10)])
            for day in (d0 + timedelta(days=i) for i in range(1, 20)):
                if day != day10:
                    path = d / f"{day.day:02d}-{day.month:02d}-{day.year:04d}.csv"
                    if not path.exists():
                        _write_day(d, day, [])

            plays, matrix, _ = prepare_analysis(d, start, end, TZ)
            roll = compute_rolling_retention(matrix, start, end, "Daily", TZ)
            b0 = next(b for b in roll["buckets"] if b["bucket"] == "2026-01-01")
            e3 = b0["horizons"]["D3"]["totalCohort"]
            e7 = b0["horizons"]["D7"]["totalCohort"]
            e30 = b0["horizons"]["D30"]["totalCohort"]
            self.assertGreaterEqual(e3, e7)
            self.assertGreaterEqual(e7, e30)
            self.assertEqual(e30, 0)  # D0+30 > timelineEnd → fully censored
            self.assertEqual(b0["horizons"]["D30"]["retentionPercent"], "N/A")


class PlayStateTests(unittest.TestCase):
    def test_state_derivation_and_updated_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            mon = date(2026, 3, 2)
            tue = date(2026, 3, 3)
            # Loaded on Mon (filledBoxes=0), even if inProgress
            # Solving started Mon, updated Tue
            # Completed on Tue
            _write_day(
                d,
                mon,
                [
                    _row("u1", "p_load", mon, mon, play_state="inProgress", filled=0),
                    _row("u2", "p_solve", mon, tue, play_state="inProgress", filled=3),
                ],
            )
            _write_day(
                d,
                tue,
                [
                    _row(
                        "u3",
                        "p_done",
                        tue,
                        tue,
                        play_state="completed",
                        filled=10,
                    ),
                ],
            )
            plays, matrix, _ = prepare_analysis(d, mon, tue, TZ)
            ps = compute_play_state(plays, mon, tue, "Daily", TZ)
            by = {b["bucket"]: b for b in ps["buckets"]}
            # loaded attributed to Mon (updatedTimestamp Mon)
            self.assertEqual(by["2026-03-02"]["loadedCount"], 1)
            # solving attributed to Tue (updatedTimestamp Tue), not Mon
            self.assertEqual(by["2026-03-02"]["solvingCount"], 0)
            self.assertEqual(by["2026-03-03"]["solvingCount"], 1)
            self.assertEqual(by["2026-03-03"]["completedCount"], 1)


class WeeklyPartialBucketTests(unittest.TestCase):
    def test_partial_week_flagged(self):
        # Timeline Wed–Sun → left-censored first week
        start = date(2026, 3, 4)  # Wednesday
        end = date(2026, 3, 8)  # Sunday
        buckets = build_buckets(start, end, "Weekly")
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].censor, "left")
        self.assertEqual(timeline_length_days(start, end), 5)


class LoggedInRetentionTests(unittest.TestCase):
    def test_is_logged_in_uid(self):
        from retention_pipeline.logged_in import is_logged_in_uid

        self.assertTrue(is_logged_in_uid("imglabc"))
        self.assertTrue(is_logged_in_uid("IMGLXYZ"))
        self.assertFalse(is_logged_in_uid("deadbeef"))
        self.assertFalse(is_logged_in_uid(""))

    def test_volume_and_retention_filter_to_imgl(self):
        from retention_api.jobs.script_loader import load_script_module

        mod = load_script_module("plot_logged_in_retention.py")
        d0 = date(2026, 3, 2)
        d1 = date(2026, 3, 3)
        d2 = date(2026, 3, 4)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            _write_day(
                d,
                d0,
                [
                    _row("imgl_a", "p1", d0, d0),
                    _row("guest_hex", "p1", d0, d0),
                ],
            )
            _write_day(
                d,
                d1,
                [
                    _row("imgl_a", "p1", d1, d1),
                    _row("guest_hex", "p1", d1, d1),
                ],
            )
            _write_day(d, d2, [_row("guest_hex", "p1", d2, d2)])
            _plays, matrix, _ = prepare_analysis(d, d0, d2, TZ)
            payload = mod.build_logged_in_retention_payload(
                matrix,
                window_start=d0,
                window_end=d2,
                grain="Daily",
                timezone="UTC",
            )
            strict = payload["metrics"]["strictRetention"]["buckets"]
            by = {b["bucket"]: b for b in strict}
            # Only imgl_a counts as logged-in volume on d0
            self.assertEqual(by["2026-03-02"]["horizons"]["D1"]["totalLoggedInInBucket"], 1)
            self.assertEqual(by["2026-03-02"]["horizons"]["D1"]["retainedCohort"], 1)
            self.assertEqual(by["2026-03-02"]["horizons"]["D1"]["totalCohort"], 1)
            # No logged-in activity on d2 alone as a D0 cohort with guests only
            self.assertEqual(by["2026-03-04"]["horizons"]["D1"]["totalLoggedInInBucket"], 0)


class SummaryStatsTests(unittest.TestCase):
    def test_mean_median_and_na_exclusion(self):
        from retention_pipeline.summary_stats import mean_median, summarize_metric_block

        self.assertEqual(mean_median([1.0, 3.0, 5.0]), (3.0, 3.0, 3))
        self.assertEqual(mean_median([1.0, 5.0]), (3.0, 3.0, 2))
        self.assertEqual(mean_median([]), (None, None, 0))

        metric = {
            "buckets": [
                {
                    "bucket": "a",
                    "horizons": {
                        "D1": {"retentionPercent": 10.0, "totalActiveInBucket": 100},
                        "D3": {"retentionPercent": "N/A", "totalActiveInBucket": 100},
                    },
                },
                {
                    "bucket": "b",
                    "horizons": {
                        "D1": {"retentionPercent": 30.0, "totalActiveInBucket": 200},
                        "D3": {"retentionPercent": 20.0, "totalActiveInBucket": 200},
                    },
                },
            ]
        }
        lines = summarize_metric_block("strictRetention", metric)
        joined = "\n".join(lines)
        self.assertIn("strictRetention.D1.retentionPercent", joined)
        self.assertIn("n=2", joined)
        self.assertIn("mean=    20.00%", joined)
        self.assertIn("strictRetention.totalActiveInBucket", joined)
        # D3 excludes the N/A bucket → n=1
        d3 = next(line for line in lines if "D3.retentionPercent" in line)
        self.assertIn("n=1", d3)


if __name__ == "__main__":
    unittest.main()
