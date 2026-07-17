"""Runtime configuration schemas (secrets excluded)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Grain = Literal["Daily", "Weekly", "Monthly"]


class RuntimeConfigPatch(BaseModel):
    """Fields the dashboard may update. CLIENT_ID / CLIENT_SECRET are rejected at router."""

    apiBaseUrl: Optional[str] = Field(None, alias="apiBaseUrl")
    seriesId: Optional[str] = Field(None, alias="seriesId")
    timezone: Optional[str] = None
    totalDataTimeStart: Optional[str] = Field(None, alias="totalDataTimeStart")
    puzzleId: Optional[str] = Field(None, alias="puzzleId")
    limit: Optional[int] = Field(None, ge=1, le=1000)
    offset: Optional[int] = Field(None, ge=0)
    rateLimitRps: Optional[float] = Field(None, gt=0, alias="rateLimitRps")
    maxWorkers: Optional[int] = Field(None, ge=1, alias="maxWorkers")
    timelineStart: Optional[str] = Field(None, alias="timelineStart")
    timelineEnd: Optional[str] = Field(None, alias="timelineEnd")
    grain: Optional[Grain] = None
    windowStart: Optional[str] = Field(None, alias="windowStart")
    windowEnd: Optional[str] = Field(None, alias="windowEnd")
    strictHorizons: Optional[List[int]] = Field(None, alias="strictHorizons")
    windowHorizons: Optional[List[int]] = Field(None, alias="windowHorizons")
    usersCsvPath: Optional[str] = Field(None, alias="usersCsvPath")

    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("strictHorizons", "windowHorizons")
    @classmethod
    def horizons_positive(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is None:
            return v
        for n in v:
            if n < 1:
                raise ValueError("horizon days must be ≥ 1")
        return v


class RuntimeConfigResponse(BaseModel):
    apiBaseUrl: str
    seriesId: Optional[str] = None
    timezone: str = "UTC"
    totalDataTimeStart: Optional[str] = None
    puzzleId: Optional[str] = None
    limit: int = 1000
    offset: int = 0
    rateLimitRps: float = 0.5
    maxWorkers: int = 8
    timelineStart: Optional[str] = None
    timelineEnd: Optional[str] = None
    grain: Optional[Grain] = None
    windowStart: Optional[str] = None
    windowEnd: Optional[str] = None
    strictHorizons: List[int] = Field(default_factory=lambda: [1, 3, 7, 30])
    windowHorizons: List[int] = Field(default_factory=lambda: [3, 7, 30])
    usersCsvPath: Optional[str] = None
    clientCredentialsConfigured: bool = False
    dataDir: str
    outputDir: str
