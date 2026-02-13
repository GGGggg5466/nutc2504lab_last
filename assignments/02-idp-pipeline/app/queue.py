import os
from redis import Redis
from rq import Queue

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

redis_conn = Redis(host=REDIS_HOST, port=REDIS_PORT)  # ← 名字用 redis_conn
queue = Queue(connection=redis_conn)
