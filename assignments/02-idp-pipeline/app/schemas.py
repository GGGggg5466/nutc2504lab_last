from pydantic import BaseModel
from typing import Any, Optional
from .state import JobStatus, Route

class CreateJobRequest(BaseModel):
    text: str
    route: Route = Route.auto   # auto/ocr/vlm

class CreateJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    queue: str = "default"

class JobResult(BaseModel):
    ok: bool
    echo: str
    len: int
    note: Optional[str] = None

class GetJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    result: Optional[Any] = None
    error: Optional[str] = None
