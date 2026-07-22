"""Runtime configuration (no secrets)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from retention_api.schemas.config import RuntimeConfigPatch, RuntimeConfigResponse
from retention_api.services import config_store
from retention_api.services.config_merge import client_credentials_configured

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=RuntimeConfigResponse)
def get_config() -> RuntimeConfigResponse:
    data = config_store.load_raw()
    return config_store.to_response(data, client_credentials_configured=client_credentials_configured())


@router.patch("", response_model=RuntimeConfigResponse)
def patch_config(body: RuntimeConfigPatch) -> RuntimeConfigResponse:
    try:
        data = config_store.patch(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return config_store.to_response(data, client_credentials_configured=client_credentials_configured())
