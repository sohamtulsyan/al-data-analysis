"""Daily Play Data inventory and users CSV upload."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from retention_pipeline.cleaning.pipeline import discover_csv_days

from retention_api.services.paths import data_dir, output_dir
from retention_api.services import config_store

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/coverage")
def data_coverage() -> dict:
    days = discover_csv_days(data_dir())
    return {
        "dataDir": str(data_dir()),
        "dayCount": len(days),
        "firstDay": days[0].isoformat() if days else None,
        "lastDay": days[-1].isoformat() if days else None,
        "days": [d.isoformat() for d in days],
    }


@router.post("/users-csv")
async def upload_users_csv(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload must be a .csv file")
    dest_dir = output_dir() / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(file.filename).name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    data = config_store.load_raw()
    data["usersCsvPath"] = str(dest)
    config_store.save_raw(data)
    return {"path": str(dest), "filename": file.filename}
