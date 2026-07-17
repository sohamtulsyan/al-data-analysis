"""Cleaning steps and Activity Matrix (PRD Cleaning §§1–2)."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union
from zoneinfo import ZoneInfo

from retention_pipeline.api.fetch import csv_path_for_day
from retention_pipeline.bucketing import enumerate_timeline_days
from retention_pipeline.config import CSV_COLUMNS, DAILY_PLAY_DATA_DIR, PLAY_STATES

logger = logging.getLogger(__name__)

DateSeq = Union[List[date], tuple]


@dataclass
class PlayRow:
    uid: str
    seriesId: str
    puzzleId: str
    startTimestamp: datetime
    updatedTimestamp: datetime
    playState: Optional[str]
    totalBoxes: Optional[int]
    filledBoxes: Optional[int]
    screenTimeSeconds: Optional[int]
    source_day: date  # calendar day of the CSV file the row came from

    @property
    def isLoaded(self) -> Optional[bool]:
        """Variables §2.2 — undefined when filledBoxes is null."""
        if self.filledBoxes is None:
            return None
        return self.filledBoxes == 0

    @property
    def serverTimeSeconds(self) -> Optional[int]:
        """Variables §2.1 — undefined if updated < start (anomaly)."""
        if self.updatedTimestamp < self.startTimestamp:
            return None
        return int((self.updatedTimestamp - self.startTimestamp).total_seconds())


@dataclass
class DataQualityReport:
    excluded_null_uid: int = 0
    excluded_null_startTimestamp: int = 0
    null_updatedTimestamp_filled: int = 0
    null_totalBoxes: int = 0
    null_filledBoxes: int = 0
    null_screenTimeSeconds: int = 0
    null_or_unrecognized_playState: int = 0
    exact_duplicates_collapsed: int = 0
    malformed_files: List[str] = field(default_factory=list)
    serverTime_anomalies: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "excluded_null_uid": self.excluded_null_uid,
            "excluded_null_startTimestamp": self.excluded_null_startTimestamp,
            "null_updatedTimestamp_filled": self.null_updatedTimestamp_filled,
            "null_totalBoxes": self.null_totalBoxes,
            "null_filledBoxes": self.null_filledBoxes,
            "null_screenTimeSeconds": self.null_screenTimeSeconds,
            "null_or_unrecognized_playState": self.null_or_unrecognized_playState,
            "exact_duplicates_collapsed": self.exact_duplicates_collapsed,
            "malformed_files": list(self.malformed_files),
            "serverTime_anomalies": self.serverTime_anomalies,
        }


class ActivityMatrix:
    """Transient in-memory Activity Matrix (Cleaning §2). Not persisted."""

    def __init__(self, days: DateSeq) -> None:
        self.days: List[date] = list(days)
        self._day_index: Dict[date, int] = {d: i for i, d in enumerate(self.days)}
        # uid -> set of active day indices (sparse; unmarked = inactive)
        self._active: Dict[str, Set[int]] = defaultdict(set)
        self._uids: List[str] = []
        self._uid_set: Set[str] = set()

    def _ensure_uid(self, uid: str) -> None:
        if uid not in self._uid_set:
            self._uid_set.add(uid)
            self._uids.append(uid)

    def mark(self, uid: str, day: date) -> None:
        idx = self._day_index.get(day)
        if idx is None:
            return
        self._ensure_uid(uid)
        self._active[uid].add(idx)

    def is_active(self, uid: str, day: date) -> bool:
        idx = self._day_index.get(day)
        if idx is None:
            return False
        return idx in self._active.get(uid, ())

    def uids(self) -> List[str]:
        return list(self._uids)

    def daily_active_users(self, day: date) -> int:
        """Column sum of day D (Variables §2.4 / Cleaning §2.3)."""
        idx = self._day_index.get(day)
        if idx is None:
            return 0
        return sum(1 for uid in self._uids if idx in self._active.get(uid, ()))

    def active_uids_on_days(self, days: Iterable[date]) -> Set[str]:
        """UIDs with at least one active cell among the given days (row-wise OR)."""
        indices = [self._day_index[d] for d in days if d in self._day_index]
        if not indices:
            return set()
        result: Set[str] = set()
        for uid in self._uids:
            active = self._active.get(uid)
            if active and any(i in active for i in indices):
                result.add(uid)
        return result

    def first_active_day_in(self, uid: str, days: DateSeq) -> Optional[date]:
        """Earliest day the user is active among days (Retention Common §2 D0)."""
        for d in sorted(days):
            if self.is_active(uid, d):
                return d
        return None

    def extend_columns_if_needed(self, extra_days: Iterable[date]) -> None:
        """Add out-of-timeline columns needed for foresight / lookback (Cleaning §2.4)."""
        for d in sorted(set(extra_days)):
            if d not in self._day_index:
                self._day_index[d] = len(self.days)
                self.days.append(d)


def _parse_instant(raw: str, tz: ZoneInfo) -> Optional[datetime]:
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)
    return dt


def _parse_int(raw: str) -> Optional[int]:
    s = raw.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _strip_bom(text: str) -> str:
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def load_daily_csv(
    path: Path,
    source_day: date,
    tz: ZoneInfo,
    report: DataQualityReport,
) -> List[PlayRow]:
    """Parse one daily CSV (Cleaning §1.1). Rejects malformed headers."""
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    text = _strip_bom(text)
    lines = text.splitlines()
    if not lines:
        report.malformed_files.append(str(path))
        raise ValueError(f"Empty CSV: {path}")

    reader = csv.reader(lines)
    try:
        header = next(reader)
    except StopIteration:
        report.malformed_files.append(str(path))
        raise ValueError(f"Empty CSV: {path}") from None

    header = [h.strip() for h in header]
    expected = list(CSV_COLUMNS)
    if header != expected:
        report.malformed_files.append(str(path))
        raise ValueError(
            f"Malformed header in {path}: expected {expected}, got {header}"
        )

    rows: List[PlayRow] = []
    seen_exact: Set[Tuple[str, ...]] = set()

    for fields in reader:
        if len(fields) < len(CSV_COLUMNS):
            fields = fields + [""] * (len(CSV_COLUMNS) - len(fields))
        fields = fields[: len(CSV_COLUMNS)]
        # Exact duplicate collapse (Cleaning §1.2)
        key = tuple(fields)
        if key in seen_exact:
            report.exact_duplicates_collapsed += 1
            continue
        seen_exact.add(key)

        uid = fields[0].strip()
        series_id = fields[1].strip()
        puzzle_id = fields[2].strip()
        start_raw = fields[3].strip()
        updated_raw = fields[4].strip()
        play_state_raw = fields[5].strip()
        total_boxes = _parse_int(fields[6])
        filled_boxes = _parse_int(fields[7])
        screen_time = _parse_int(fields[8])

        # Null tracking
        if total_boxes is None and fields[6].strip() == "":
            report.null_totalBoxes += 1
        elif total_boxes is None:
            report.null_totalBoxes += 1
        if filled_boxes is None:
            report.null_filledBoxes += 1
        if screen_time is None:
            report.null_screenTimeSeconds += 1

        if not uid:
            report.excluded_null_uid += 1
            continue
        start_ts = _parse_instant(start_raw, tz)
        if start_ts is None:
            report.excluded_null_startTimestamp += 1
            continue

        updated_ts = _parse_instant(updated_raw, tz)
        if updated_ts is None:
            updated_ts = start_ts
            report.null_updatedTimestamp_filled += 1

        play_state: Optional[str]
        if not play_state_raw or play_state_raw not in PLAY_STATES:
            play_state = None
            report.null_or_unrecognized_playState += 1
        else:
            play_state = play_state_raw

        row = PlayRow(
            uid=uid,
            seriesId=series_id,
            puzzleId=puzzle_id,
            startTimestamp=start_ts,
            updatedTimestamp=updated_ts,
            playState=play_state,
            totalBoxes=total_boxes,
            filledBoxes=filled_boxes,
            screenTimeSeconds=screen_time,
            source_day=source_day,
        )
        if row.serverTimeSeconds is None:
            report.serverTime_anomalies += 1
        rows.append(row)

    return rows


def discover_csv_days(directory: Path = DAILY_PLAY_DATA_DIR) -> List[date]:
    """List completed day CSVs present in Daily Play Data."""
    days: List[date] = []
    if not directory.is_dir():
        return days
    for path in directory.glob("*.csv"):
        if path.name.endswith(".partial"):
            continue
        name = path.stem  # DD-MM-YYYY
        try:
            dd, mm, yyyy = name.split("-")
            days.append(date(int(yyyy), int(mm), int(dd)))
        except ValueError:
            logger.warning("Skipping non-day CSV filename: %s", path.name)
    return sorted(days)


def load_plays_for_range(
    directory: Path,
    start_day: date,
    end_day: date,
    tz: ZoneInfo,
) -> tuple[List[PlayRow], DataQualityReport]:
    """Load and clean all daily CSVs from start_day through end_day inclusive.

    For Activity Matrix foresight, callers should pass a start_day early enough
    that plays started before the timeline but updated inside it are included.
    """
    report = DataQualityReport()
    all_rows: List[PlayRow] = []
    available = set(discover_csv_days(directory))
    cur = start_day
    from datetime import timedelta

    while cur <= end_day:
        if cur in available:
            path = csv_path_for_day(cur, directory)
            try:
                rows = load_daily_csv(path, cur, tz, report)
                all_rows.extend(rows)
            except ValueError as exc:
                logger.error("%s", exc)
        cur += timedelta(days=1)
    return all_rows, report


def build_activity_matrix(
    plays: Iterable[PlayRow],
    column_days: List[date],
    tz: ZoneInfo,
) -> ActivityMatrix:
    """Populate Activity Matrix via foresight forward-pass (Cleaning §2.2 / Variables §2.3).

    For each row: mark (uid, startDay); if updateDay > startDay mark (uid, updateDay).
    """
    # Include foresight landing days beyond the base range
    extra: List[date] = []
    play_list = list(plays)
    for row in play_list:
        update_day = row.updatedTimestamp.astimezone(tz).date()
        start_day = row.startTimestamp.astimezone(tz).date()
        if update_day not in column_days and update_day > (column_days[-1] if column_days else update_day):
            extra.append(update_day)
        if start_day not in column_days and (
            not column_days or start_day < column_days[0] or start_day > column_days[-1]
        ):
            extra.append(start_day)

    matrix_days = sorted(set(column_days) | set(extra))
    matrix = ActivityMatrix(matrix_days)

    # Chronological by source CSV day (forward pass)
    play_list.sort(key=lambda r: (r.source_day, r.startTimestamp))
    for row in play_list:
        start_day = row.startTimestamp.astimezone(tz).date()
        update_day = row.updatedTimestamp.astimezone(tz).date()
        matrix.mark(row.uid, start_day)
        if update_day > start_day:
            matrix.mark(row.uid, update_day)
    return matrix


def prepare_analysis(
    directory: Path,
    timeline_start: date,
    timeline_end: date,
    tz: ZoneInfo,
) -> tuple[List[PlayRow], ActivityMatrix, DataQualityReport]:
    """Canonical cleaning order (§1.6): parse → dedupe → nulls → Activity Matrix.

    Loads CSVs from the earliest available day through timeline_end so that
    foresight updates landing on in-window days (from earlier starts) are seen.
    """
    available = discover_csv_days(directory)
    if not available:
        empty = ActivityMatrix(enumerate_timeline_days(timeline_start, timeline_end))
        return [], empty, DataQualityReport()

    load_start = min(available[0], timeline_start)
    load_end = max(available[-1], timeline_end)
    plays, report = load_plays_for_range(directory, load_start, load_end, tz)
    columns = enumerate_timeline_days(timeline_start, timeline_end)
    matrix = build_activity_matrix(plays, columns, tz)
    return plays, matrix, report
