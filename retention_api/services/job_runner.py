"""Background job execution and persistence."""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from retention_api.jobs.handlers import run_job_handler
from retention_api.schemas.jobs import CreateJobRequest, JobArtifacts, JobResponse, JobStatus, JobType
from retention_api.services.paths import jobs_dir

logger = logging.getLogger(__name__)

_executor: Optional[ThreadPoolExecutor] = None
_lock = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _executor_instance() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        import os

        workers = int(os.environ.get("JOB_MAX_WORKERS", "2"))
        _executor = ThreadPoolExecutor(max_workers=max(1, workers))
    return _executor


def _job_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def _read_job(job_id: str) -> Optional[Dict[str, Any]]:
    path = _job_path(job_id)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_job(record: Dict[str, Any]) -> None:
    jobs_dir().mkdir(parents=True, exist_ok=True)
    _job_path(record["id"]).write_text(json.dumps(record, indent=2), encoding="utf-8")


def _to_response(record: Dict[str, Any]) -> JobResponse:
    artifacts = None
    if record.get("artifacts"):
        artifacts = JobArtifacts(**record["artifacts"])
    return JobResponse(
        id=record["id"],
        type=JobType(record["type"]),
        status=JobStatus(record["status"]),
        createdAt=record["createdAt"],
        startedAt=record.get("startedAt"),
        finishedAt=record.get("finishedAt"),
        error=record.get("error"),
        artifacts=artifacts,
        params=record.get("params") or {},
    )


def create_job(request: CreateJobRequest) -> JobResponse:
    job_id = str(uuid.uuid4())
    record: Dict[str, Any] = {
        "id": job_id,
        "type": request.type.value,
        "status": JobStatus.pending.value,
        "createdAt": _now_iso(),
        "params": request.params,
    }
    _write_job(record)

    def _run() -> None:
        with _lock:
            rec = _read_job(job_id)
            if rec is None:
                return
            rec["status"] = JobStatus.running.value
            rec["startedAt"] = _now_iso()
            _write_job(rec)
        try:
            artifacts = run_job_handler(JobType(request.type), request.params)
            with _lock:
                rec = _read_job(job_id) or record
                rec["status"] = JobStatus.succeeded.value
                rec["finishedAt"] = _now_iso()
                rec["artifacts"] = artifacts
                _write_job(rec)
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            with _lock:
                rec = _read_job(job_id) or record
                rec["status"] = JobStatus.failed.value
                rec["finishedAt"] = _now_iso()
                rec["error"] = str(exc)
                _write_job(rec)

    _executor_instance().submit(_run)
    return _to_response(record)


def get_job(job_id: str) -> Optional[JobResponse]:
    record = _read_job(job_id)
    if record is None:
        return None
    return _to_response(record)


def list_jobs(limit: int = 20) -> List[JobResponse]:
    jobs_path = jobs_dir()
    if not jobs_path.is_dir():
        return []
    files = sorted(jobs_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[JobResponse] = []
    for path in files[:limit]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            out.append(_to_response(record))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return out
