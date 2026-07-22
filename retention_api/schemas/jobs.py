"""Job types and request/response models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class JobType(str, Enum):
    backfill = "backfill"
    daily = "daily"
    analyze = "analyze"
    pipeline = "pipeline"
    nuu_counts = "nuu_counts"
    nuu_retention = "nuu_retention"
    ouu_retention = "ouu_retention"
    signup_fraction = "signup_fraction"
    nuu_signup_fraction = "nuu_signup_fraction"


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class CreateJobRequest(BaseModel):
    type: JobType
    params: Dict[str, Any] = Field(default_factory=dict)


class JobArtifacts(BaseModel):
    paths: Dict[str, str] = Field(default_factory=dict)
    charts: List[str] = Field(default_factory=list)


class JobResponse(BaseModel):
    id: str
    type: JobType
    status: JobStatus
    createdAt: str
    startedAt: Optional[str] = None
    finishedAt: Optional[str] = None
    error: Optional[str] = None
    artifacts: Optional[JobArtifacts] = None
    params: Dict[str, Any] = Field(default_factory=dict)
