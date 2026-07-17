"""WordPress-style users export loader.

Email ID is never indexed or read. Only allow-listed columns may be accessed.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional

# Columns we may load. Email ID is intentionally excluded and never read.
ALLOWED_COLUMNS = frozenset(
    {
        "User ID",
        "User Name",
        "First Name",
        "Last Name",
        "Nick Name",
        "User Role",
        "Registered Date",
    }
)
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def parse_registered_date(value: str) -> datetime:
    raw = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized Registered Date: {value!r}")


def load_registration_dates(
    csv_path: Path,
    timeline_start: Optional[date] = None,
    timeline_end: Optional[date] = None,
) -> List[date]:
    """Return Registered Date values as calendar dates.

    Email ID (and any other non-allowed column) is never indexed or read.
    Optional timeline_start / timeline_end filter inclusively by calendar date.
    """
    dates: List[date] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"No header row in {csv_path}") from exc

        stripped = [h.strip() for h in header]
        col_index = {
            name: i for i, name in enumerate(stripped) if name in ALLOWED_COLUMNS
        }
        if "Registered Date" not in col_index:
            raise ValueError(
                f"{csv_path} missing Registered Date column (found: {stripped})"
            )
        date_i = col_index["Registered Date"]

        for cells in reader:
            if date_i >= len(cells):
                continue
            registered = cells[date_i].strip()
            if not registered:
                continue
            d = parse_registered_date(registered).date()
            if timeline_start is not None and d < timeline_start:
                continue
            if timeline_end is not None and d > timeline_end:
                continue
            dates.append(d)
    return dates


def count_by_week(registration_dates: Iterable[date]) -> dict:
    """Map ISO week key (YYYY-Www) → signup count."""
    from collections import Counter

    from retention_pipeline.bucketing import week_key

    return Counter(week_key(d) for d in registration_dates)
