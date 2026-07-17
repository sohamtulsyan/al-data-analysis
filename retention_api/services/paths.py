"""Resolve data/output paths from environment (Render persistent disk)."""

from __future__ import annotations

import os
from pathlib import Path

from retention_pipeline.config import DAILY_PLAY_DATA_DIR, OUTPUT_DIR, PROJECT_ROOT


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
