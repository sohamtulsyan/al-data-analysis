"""Persist dashboard settings to JSON (no secrets)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from retention_api.schemas.config import RuntimeConfigPatch, RuntimeConfigResponse
from retention_api.services.paths import (
    data_dir,
    output_dir,
    runtime_config_path,
    users_csv_default_path,
)


DEFAULT_STRICT = [1, 3, 7, 30]
DEFAULT_WINDOW = [3, 7, 30]


def _defaults() -> Dict[str, Any]:
    return {
        "apiBaseUrl": "https://puzzleme.amuselabs.com/pmm/api/v2",
        "seriesId": None,
        "timezone": "UTC",
        "totalDataTimeStart": None,
        "puzzleId": None,
        "limit": 1000,
        "offset": 0,
        "rateLimitRps": 0.5,
        "maxWorkers": 8,
        "timelineStart": None,
        "timelineEnd": None,
        "grain": None,
        "windowStart": None,
        "windowEnd": None,
        "strictHorizons": list(DEFAULT_STRICT),
        "windowHorizons": list(DEFAULT_WINDOW),
        "usersCsvPath": None,
    }


def load_raw() -> Dict[str, Any]:
    path = runtime_config_path()
    data = _defaults()
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            data.update(stored)
    return data


def save_raw(data: Dict[str, Any]) -> None:
    path = runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def patch(updates: RuntimeConfigPatch) -> Dict[str, Any]:
    data = load_raw()
    key_map = {
        "apiBaseUrl": "apiBaseUrl",
        "seriesId": "seriesId",
        "timezone": "timezone",
        "totalDataTimeStart": "totalDataTimeStart",
        "puzzleId": "puzzleId",
        "limit": "limit",
        "offset": "offset",
        "rateLimitRps": "rateLimitRps",
        "maxWorkers": "maxWorkers",
        "timelineStart": "timelineStart",
        "timelineEnd": "timelineEnd",
        "grain": "grain",
        "windowStart": "windowStart",
        "windowEnd": "windowEnd",
        "strictHorizons": "strictHorizons",
        "windowHorizons": "windowHorizons",
        "usersCsvPath": "usersCsvPath",
    }
    for field, value in updates.model_dump(exclude_none=True).items():
        if field in key_map:
            data[key_map[field]] = value
    save_raw(data)
    return data


def to_response(data: Dict[str, Any], *, client_credentials_configured: bool) -> RuntimeConfigResponse:
    users_path = data.get("usersCsvPath") or str(users_csv_default_path())
    return RuntimeConfigResponse(
        apiBaseUrl=str(data.get("apiBaseUrl") or _defaults()["apiBaseUrl"]).rstrip("/"),
        seriesId=data.get("seriesId"),
        timezone=data.get("timezone") or "UTC",
        totalDataTimeStart=data.get("totalDataTimeStart"),
        puzzleId=data.get("puzzleId"),
        limit=int(data.get("limit") or 1000),
        offset=int(data.get("offset") or 0),
        rateLimitRps=float(data.get("rateLimitRps") or 0.5),
        maxWorkers=int(data.get("maxWorkers") or 8),
        timelineStart=data.get("timelineStart"),
        timelineEnd=data.get("timelineEnd"),
        grain=data.get("grain"),
        windowStart=data.get("windowStart"),
        windowEnd=data.get("windowEnd"),
        strictHorizons=list(data.get("strictHorizons") or DEFAULT_STRICT),
        windowHorizons=list(data.get("windowHorizons") or DEFAULT_WINDOW),
        usersCsvPath=users_path,
        clientCredentialsConfigured=client_credentials_configured,
        dataDir=str(data_dir()),
        outputDir=str(output_dir()),
    )
