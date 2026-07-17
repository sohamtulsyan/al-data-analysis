"""Serve analysis and NUU JSON results."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from retention_api.services.paths import output_dir

router = APIRouter(prefix="/results", tags=["results"])

_RESULT_FILES = {
    "analysis": "analysis_results.json",
    "signup-fraction": "signup_fraction.json",
    "nuu-retention": "nuu_retention.json",
}


@router.get("/{name}")
def get_result(name: str) -> dict:
    if name not in _RESULT_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown result '{name}'. Choose from: {sorted(_RESULT_FILES)}",
        )
    path = output_dir() / _RESULT_FILES[name]
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Result file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
