"""Background jobs."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from retention_api.schemas.jobs import CreateJobRequest, JobResponse
from retention_api.services import job_runner

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=202)
def create_job(body: CreateJobRequest) -> JobResponse:
    return job_runner.create_job(body)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str) -> JobResponse:
    job = job_runner.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("", response_model=List[JobResponse])
def list_jobs(limit: int = 20) -> List[JobResponse]:
    return job_runner.list_jobs(limit=min(max(limit, 1), 100))
