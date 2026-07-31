"""Serve analysis, NUU, OUU, and logged-in JSON results."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from retention_api.services.paths import output_dir

router = APIRouter(prefix="/results", tags=["results"])

_RESULT_FILES = {
    "analysis": "analysis_results.json",
    "signup-fraction": "signup_fraction.json",
    "nuu-retention": "nuu_retention.json",
    "ouu-retention": "ouu_retention.json",
    "logged-in-retention": "logged_in_retention.json",
    "nuu-signup-fraction": "nuu_signup_fraction.json",
}


def _pct_value(raw: str):
    if raw in ("", "N/A", None):
        return "N/A"
    try:
        return float(raw)
    except (TypeError, ValueError):
        return "N/A"


def _nuu_signup_fraction_from_csv(path: Path) -> dict:
    """Build JSON shaped like signup-fraction from the CSV artifact."""
    buckets = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pct = _pct_value(row.get("signupFractionPercent"))
            buckets.append(
                {
                    "bucket": row.get("week") or row.get("bucket"),
                    "signups": int(row.get("signups") or 0),
                    "nuuCount": int(row.get("nuu_count") or row.get("nuuCount") or 0),
                    "signupFractionPercent": pct,
                }
            )
    return {
        "metric": "nuuSignupFraction",
        "grain": "Weekly",
        "buckets": buckets,
        "totalSignups": sum(b["signups"] for b in buckets),
        "totalNuu": sum(b["nuuCount"] for b in buckets),
    }


@router.get("/{name}")
def get_result(name: str) -> dict:
    if name not in _RESULT_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown result '{name}'. Choose from: {sorted(_RESULT_FILES)}",
        )

    odir = output_dir()
    path = odir / _RESULT_FILES[name]

    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))

    # Fallback: existing CSV from nuu_signup_fraction job (no JSON yet)
    if name == "nuu-signup-fraction":
        csv_path = odir / "nuu_signup_fraction.csv"
        if csv_path.is_file():
            return _nuu_signup_fraction_from_csv(csv_path)

    raise HTTPException(status_code=404, detail=f"Result file not found: {path}")
