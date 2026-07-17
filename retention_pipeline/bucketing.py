"""Calendar bucketing helpers (User Growth §5 — Daily / ISO Weekly / Monthly)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Literal, Optional, Sequence

CensorSide = Optional[Literal["left", "right", "both"]]


@dataclass(frozen=True)
class Bucket:
    """One aggregation bucket at the requested grain."""

    key: str
    grain: str
    days: tuple[date, ...]
    censor: CensorSide
    covered_start: date
    covered_end: date

    @property
    def start(self) -> date:
        return self.days[0]

    @property
    def end(self) -> date:
        return self.days[-1]


def timeline_length_days(timeline_start: date, timeline_end: date) -> int:
    """Inclusive calendar-day count (User Growth §1.1)."""
    return (timeline_end - timeline_start).days + 1


def allowed_grains(timeline_length: int) -> List[str]:
    """Grain permission matrix (User Growth §4.2)."""
    if timeline_length < 1:
        return []
    if timeline_length < 7:
        return ["Daily"]
    if timeline_length < 30:
        return ["Daily", "Weekly"]
    return ["Daily", "Weekly", "Monthly"]


def enumerate_timeline_days(timeline_start: date, timeline_end: date) -> List[date]:
    days: List[date] = []
    cur = timeline_start
    while cur <= timeline_end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def iso_week_monday(d: date) -> date:
    """Monday of the ISO calendar week containing d (Monday-start)."""
    return d - timedelta(days=d.weekday())  # Monday=0


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def build_buckets(
    timeline_start: date,
    timeline_end: date,
    grain: str,
) -> List[Bucket]:
    """Build calendar buckets; flag partial first/last weekly/monthly (User Growth §6)."""
    days = enumerate_timeline_days(timeline_start, timeline_end)
    if not days:
        return []

    if grain == "Daily":
        return [
            Bucket(
                key=d.isoformat(),
                grain="Daily",
                days=(d,),
                censor=None,
                covered_start=d,
                covered_end=d,
            )
            for d in days
        ]

    if grain == "Weekly":
        groups: dict[str, List[date]] = {}
        order: List[str] = []
        for d in days:
            k = week_key(d)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(d)
        buckets: List[Bucket] = []
        for i, k in enumerate(order):
            group = tuple(sorted(groups[k]))
            monday = iso_week_monday(group[0])
            sunday = monday + timedelta(days=6)
            missing_left = monday not in group
            missing_right = sunday not in group
            censor: CensorSide = None
            if missing_left and missing_right:
                censor = "both"
            elif missing_left:
                censor = "left"
            elif missing_right:
                censor = "right"
            buckets.append(
                Bucket(
                    key=k,
                    grain="Weekly",
                    days=group,
                    censor=censor,
                    covered_start=group[0],
                    covered_end=group[-1],
                )
            )
        return buckets

    if grain == "Monthly":
        groups = {}
        order = []
        for d in days:
            k = month_key(d)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(d)
        buckets = []
        for i, k in enumerate(order):
            group = tuple(sorted(groups[k]))
            y, m = group[0].year, group[0].month
            first = date(y, m, 1)
            if m == 12:
                next_month = date(y + 1, 1, 1)
            else:
                next_month = date(y, m + 1, 1)
            last = next_month - timedelta(days=1)
            missing_left = first not in group
            missing_right = last not in group
            censor = None
            if missing_left and missing_right:
                censor = "both"
            elif missing_left:
                censor = "left"
            elif missing_right:
                censor = "right"
            buckets.append(
                Bucket(
                    key=k,
                    grain="Monthly",
                    days=group,
                    censor=censor,
                    covered_start=group[0],
                    covered_end=group[-1],
                )
            )
        return buckets

    raise ValueError(f"Unknown grain: {grain!r}")


def bucket_datetime_bounds(bucket: Bucket, tz) -> tuple:
    """B_start = 00:00:00 first day; B_end = 23:59:59 last day (Engagement §4)."""
    from datetime import datetime, time

    b_start = datetime.combine(bucket.start, time(0, 0, 0), tzinfo=tz)
    b_end = datetime.combine(bucket.end, time(23, 59, 59), tzinfo=tz)
    return b_start, b_end
