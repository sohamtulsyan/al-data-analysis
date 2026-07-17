"""Chart and HTML artifact files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from retention_api.services.paths import output_dir

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _safe_chart_path(name: str) -> Path:
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid artifact name")
    charts = output_dir() / "charts"
    path = charts / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {name}")
    return path.resolve()


@router.get("/charts/{name}")
def get_chart(name: str) -> FileResponse:
    path = _safe_chart_path(name)
    media = "text/html" if path.suffix.lower() == ".html" else "image/png"
    return FileResponse(path, media_type=media, filename=name)


@router.get("/charts")
def list_charts() -> dict:
    charts_dir = output_dir() / "charts"
    if not charts_dir.is_dir():
        return {"charts": []}
    names = sorted(p.name for p in charts_dir.iterdir() if p.is_file())
    return {"charts": names}
