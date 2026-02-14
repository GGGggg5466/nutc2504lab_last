import os
import json
from redis import Redis
from rq import Queue

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)  # ← 名字用 redis_conn
queue = Queue(connection=redis_conn)

def job_status_key(job_id: str) -> str:
    return f"job:{job_id}:status"

def job_result_key(job_id: str) -> str:
    return f"job:{job_id}:result"

def job_error_key(job_id: str) -> str:
    return f"job:{job_id}:error"

def set_status(r: Redis, job_id: str, status: str):
    r.set(job_status_key(job_id), status)

def set_result(r: Redis, job_id: str, result: dict):
    r.set(job_result_key(job_id), json.dumps(result, ensure_ascii=False))

def set_error(r: Redis, job_id: str, err: str):
    r.set(job_error_key(job_id), err)

def get_status(r: Redis, job_id: str):
    v = r.get(job_status_key(job_id))
    return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v

def get_result(r: Redis, job_id: str):
    v = r.get(job_result_key(job_id))
    if not v:
        return None
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8")
    return json.loads(v)

def get_error(r: Redis, job_id: str):
    v = r.get(job_error_key(job_id))
    if not v:
        return None
    return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v