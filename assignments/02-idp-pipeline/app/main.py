from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rq import Retry
import os

from app.queue import queue, redis_conn
from app.tasks import run_job
from app.router import decide_route
from app.state import Route

from .schemas import CreateJobRequest, CreateJobResponse, GetJobResponse
from .state import JobStatus, Route
from .queue import set_status, get_status, get_result, get_error

app = FastAPI(title="IDP Pipeline MVP", version="0.2.0")
DEFAULT_JOB_TIMEOUT_SEC = int(os.getenv("DEFAULT_TIMEOUT_SEC", "20"))

@app.get("/")
def root():
    return {"message": "OK. Try /health or POST /v1/jobs"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/v1/jobs")
def create_job(req: CreateJobRequest):
    # 使用者要求的 route（auto/ocr/vlm）
    route_request = req.route.value

    # route_hint：只有展示用（A 先保留），真正 chosen_route 以 worker 結果為準
    if req.route == Route.auto:
        hint_route, hint_conf, hint_reason = decide_route(req.text)
        route_hint = {"route": hint_route.value, "confidence": hint_conf, "reason": hint_reason}
        route_for_worker = Route.auto.value   # ✅ 建議：仍傳 auto，讓 worker 統一決策
    else:
        route_hint = {"route": route_request, "confidence": 1.0, "reason": "Route forced by request"}
        route_for_worker = route_request

    job = queue.enqueue(
            "app.tasks.run_job", 
            req.text, 
            route_for_worker,
            req.input_type.value,                 # ✅ 新增：傳 input_type
            job_timeout=DEFAULT_JOB_TIMEOUT_SEC,  # ✅ 新增：RQ timeout
            retry=Retry(max=2, interval=[1, 3])   # ✅ 新增：最小 retry
        )

    return {
        "job_id": job.id,
        "status": "queued",
        "queue": job.origin,
        "route_request": route_request,
        "route_hint": route_hint,
        "input_type": req.input_type.value,
    }



@app.get("/v1/jobs/{job_id}", response_model=GetJobResponse)
def get_job(job_id: str):
    status = get_status(redis_conn, job_id)
    if not status:
        # 代表 job_id 根本不存在/過期/被清掉
        raise HTTPException(status_code=404, detail="Job not found")

    result = get_result(redis_conn, job_id) if status == JobStatus.finished.value else None
    error = get_error(redis_conn, job_id) if status == JobStatus.failed.value else None

    return GetJobResponse(job_id=job_id, status=status, result=result, error=error)