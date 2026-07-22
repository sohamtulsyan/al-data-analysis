"""Resolve data/output paths from environment (Render persistent disk)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from retention_pipeline.config import DAILY_PLAY_DATA_DIR, OUTPUT_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

_DISK_HINT = (
    "On Render: Dashboard → your web service → Disks → add a disk mounted at "
    "/var/data (size ≥ 1 GB), then set DATA_DIR=/var/data/Daily Play Data, "
    "OUTPUT_DIR=/var/data/output, RUNTIME_CONFIG_PATH=/var/data/runtime_config.json. "
    "Without a disk, unset those vars so the API uses the repo-local defaults "
    "(ephemeral — lost on every deploy)."
)


def data_dir() -> Path:
    raw = os.environ.get("DATA_DIR", "").strip()
    return Path(raw) if raw else DAILY_PLAY_DATA_DIR


def output_dir() -> Path:
    raw = os.environ.get("OUTPUT_DIR", "").strip()
    return Path(raw) if raw else OUTPUT_DIR


def runtime_config_path() -> Path:
    raw = os.environ.get("RUNTIME_CONFIG_PATH", "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / "data" / "runtime_config.json"


def jobs_dir() -> Path:
    return output_dir() / "jobs"


def users_csv_default_path() -> Path:
    raw = os.environ.get("USERS_CSV_PATH", "").strip()
    if raw:
        return Path(raw)
    return PROJECT_ROOT / "users_2026_07_16_12_34_42.csv"


def ensure_writable_dir(path: Path, *, label: str) -> Path:
    """Create ``path`` if needed; raise PermissionError with a Render disk hint."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return path
    except OSError as exc:
        raise PermissionError(
            f"Cannot write {label} at {path}: {exc}. {_DISK_HINT}"
        ) from exc


def ensure_writable_parent(file_path: Path, *, label: str) -> Path:
    """Ensure parent of ``file_path`` exists and is writable."""
    ensure_writable_dir(file_path.parent, label=label)
    return file_path
