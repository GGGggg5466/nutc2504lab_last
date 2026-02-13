from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rq.job import Job

from app.queue import queue, redis_conn
from app.tasks import run_job

app = FastAPI(title="IDP Pipeline MVP", version="0.2.0")


class CreateJobRequest(BaseModel):
    text: str


@app.get("/")
def root():
    return {"message": "OK. Try /health or POST /v1/jobs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/jobs")
def create_job(req: CreateJobRequest):
    # enqueue：把任務丟給 worker
    job = queue.enqueue(run_job, {"text": req.text})
    return {
        "job_id": job.id,
        "status": job.get_status(),
        "queue": job.origin,
    }


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        raise HTTPException(status_code=404, detail="Job not found")

    status = job.get_status()
    result = job.result if status == "finished" else None
    error = str(job.exc_info) if status == "failed" else None

    return {
        "job_id": job.id,
        "status": status,
        "result": result,
        "error": error,
    }
