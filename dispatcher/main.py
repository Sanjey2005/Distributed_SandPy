import os
import uuid
import time
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import redis
import httpx

logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
WORKER_URLS = {
    "worker1": os.getenv("WORKER1_URL", "http://worker1:5000"),
    "worker2": os.getenv("WORKER2_URL", "http://worker2:5000"),
    "worker3": os.getenv("WORKER3_URL", "http://worker3:5000"),
}

WORKER_MAX_CAPACITY = 1  # Maximum concurrent jobs per worker

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

r = redis.from_url(REDIS_URL, decode_responses=True)

class SubmitRequest(BaseModel):
    user_id: str
    code: str

class Job(BaseModel):
    job_id: str
    worker_id: str
    user_id: str
    code: str
    status: str
    output: Optional[str] = None
    timestamp: float

# Atomically finds the least-loaded worker below capacity, increments its count, and returns it.
# Redis executes Lua scripts atomically so concurrent /submit requests are serialized
# at the Redis level — each one sees the updated count from the previous request.
# ARGV[1] = max_capacity. Returns false when all workers are at capacity.
CLAIM_WORKER_SCRIPT = """
local max_capacity = tonumber(ARGV[1])
local min_count = nil
local chosen = nil

for i = 1, #KEYS do
    local key = 'worker:' .. KEYS[i] .. ':count'
    local raw = redis.call('GET', key)
    local count = tonumber(raw) or 0
    if count < max_capacity then
        if min_count == nil or count < min_count then
            min_count = count
            chosen = KEYS[i]
        end
    end
end

if chosen then
    redis.call('INCR', 'worker:' .. chosen .. ':count')
    return chosen
else
    return false
end
"""

async def check_worker_health(worker_id: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{WORKER_URLS[worker_id]}/health")
            return response.status_code == 200
    except Exception:
        return False

def safe_decr_worker_count(worker_id: str):
    count = r.decr(f"worker:{worker_id}:count")
    if count < 0:
        r.set(f"worker:{worker_id}:count", 0)

async def process_queue():
    """Attempt to dispatch queued jobs to free workers."""
    while True:
        if r.llen("job_queue") == 0:
            return

        workers = list(WORKER_URLS.keys())
        worker_id = r.eval(CLAIM_WORKER_SCRIPT, len(workers), *workers, WORKER_MAX_CAPACITY)

        if not worker_id:
            return

        if not await check_worker_health(worker_id):
            safe_decr_worker_count(worker_id)
            return

        raw_job = r.lpop("job_queue")
        if raw_job is None:
            # Queue drained between llen check and lpop (race with another process_queue)
            safe_decr_worker_count(worker_id)
            return

        job_data = json.loads(raw_job)
        job_id = job_data["job_id"]
        user_id = job_data["user_id"]
        code = job_data["code"]

        r.hset(f"job:{job_id}", mapping={
            "worker_id": worker_id,
            "status": "pending"
        })

        asyncio.create_task(
            execute_job_background(job_id, worker_id, user_id, code)
        )

async def execute_job_background(job_id: str, worker_id: str, user_id: str, code: str):
    try:
        logger.info(f"Starting job {job_id} on {worker_id}")
        r.hset(f"job:{job_id}", mapping={
            "status": "running",
            "worker_id": worker_id
        })

        # Pin the session to this worker before calling start_session,
        # so the worker's Redis check (session:{user_id}) sees the correct worker_id
        r.set(f"session:{user_id}", worker_id, ex=3600)

        async with httpx.AsyncClient(timeout=300.0) as client:
            start_session_response = await client.post(
                f"{WORKER_URLS[worker_id]}/start_session",
                data={"user_id": user_id}
            )
            logger.info(f"Start session response: {start_session_response.status_code}")

            await asyncio.sleep(2)

            if start_session_response.status_code == 409:
                r.hset(f"job:{job_id}", "status", "failed")
                r.hset(f"job:{job_id}", "output", "Session exists on another worker")
                return

            if start_session_response.status_code != 200:
                r.hset(f"job:{job_id}", "status", "failed")
                r.hset(f"job:{job_id}", "output", f"Failed to start session: {start_session_response.text}")
                return

            execute_response = await client.post(
                f"{WORKER_URLS[worker_id]}/execute",
                json={"user_id": user_id, "code": code}
            )
            logger.info(f"Execute response status: {execute_response.status_code}")

            if execute_response.status_code == 200:
                result = execute_response.json()
                logger.info(f"Execute result: {result}")
                r.hset(f"job:{job_id}", "status", "completed")
                r.hset(f"job:{job_id}", "output", result.get("output", ""))
            else:
                r.hset(f"job:{job_id}", "status", "failed")
                r.hset(f"job:{job_id}", "output", execute_response.text)

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        r.hset(f"job:{job_id}", "status", "failed")
        r.hset(f"job:{job_id}", "output", str(e))

    finally:
        safe_decr_worker_count(worker_id)
        await process_queue()

@app.post("/submit")
async def submit_job(request: SubmitRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    timestamp = time.time()

    session_worker = r.get(f"session:{request.user_id}")

    worker_id = None

    if session_worker:
        worker_id = session_worker
        if not await check_worker_health(worker_id):
            r.delete(f"session:{request.user_id}")
            worker_id = None
        else:
            # Check if pinned worker has capacity
            current_count = int(r.get(f"worker:{worker_id}:count") or 0)
            if current_count >= WORKER_MAX_CAPACITY:
                worker_id = None  # At capacity — will enqueue below

    if worker_id is None:
        # Try to atomically claim a free worker (capacity-aware)
        workers = list(WORKER_URLS.keys())
        worker_id = r.eval(CLAIM_WORKER_SCRIPT, len(workers), *workers, WORKER_MAX_CAPACITY)

        if worker_id:
            # Health check — if unhealthy, undo the incr and try remaining workers
            if not await check_worker_health(worker_id):
                safe_decr_worker_count(worker_id)
                remaining = [w for w in workers if w != worker_id]
                worker_id = None
                for _ in range(len(remaining)):
                    if not remaining:
                        break
                    candidate = r.eval(CLAIM_WORKER_SCRIPT, len(remaining), *remaining, WORKER_MAX_CAPACITY)
                    if not candidate:
                        break
                    if await check_worker_health(candidate):
                        worker_id = candidate
                        break
                    else:
                        safe_decr_worker_count(candidate)
                        remaining = [w for w in remaining if w != candidate]
    else:
        # Pinned session path with capacity — increment (worker already chosen)
        r.incr(f"worker:{worker_id}:count")

    if worker_id:
        # Worker claimed — dispatch immediately
        job_data = {
            "job_id": job_id,
            "worker_id": worker_id,
            "user_id": request.user_id,
            "code": request.code,
            "status": "pending",
            "timestamp": str(timestamp)
        }
        r.hset(f"job:{job_id}", mapping=job_data)
        r.expire(f"job:{job_id}", 86400)

        background_tasks.add_task(execute_job_background, job_id, worker_id, request.user_id, request.code)

        return {"job_id": job_id, "worker_id": worker_id, "status": "pending"}
    else:
        # All workers at capacity — enqueue the job
        job_data = {
            "job_id": job_id,
            "worker_id": "queued",
            "user_id": request.user_id,
            "code": request.code,
            "status": "queued",
            "timestamp": str(timestamp)
        }
        r.hset(f"job:{job_id}", mapping=job_data)
        r.expire(f"job:{job_id}", 86400)

        queue_entry = json.dumps({
            "job_id": job_id,
            "user_id": request.user_id,
            "code": request.code,
        })
        r.rpush("job_queue", queue_entry)

        return {"job_id": job_id, "worker_id": "queued", "status": "queued"}

@app.get("/job/{job_id}")
async def get_job(job_id: str):
    job_data = r.hgetall(f"job:{job_id}")
    
    if not job_data:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {
        "job_id": job_data.get("job_id"),
        "worker_id": job_data.get("worker_id"),
        "user_id": job_data.get("user_id"),
        "code": job_data.get("code"),
        "status": job_data.get("status"),
        "output": job_data.get("output"),
        "timestamp": job_data.get("timestamp")
    }

@app.get("/workers")
async def get_workers():
    workers = []
    for worker_id, url in WORKER_URLS.items():
        is_healthy = await check_worker_health(worker_id)
        job_count = r.get(f"worker:{worker_id}:count")
        
        workers.append({
            "worker_id": worker_id,
            "url": url,
            "healthy": is_healthy,
            "job_count": int(job_count) if job_count else 0
        })
    
    queue_depth = r.llen("job_queue")

    return {"workers": workers, "queue_depth": queue_depth}

@app.get("/jobs")
async def get_all_jobs():
    job_keys = r.keys("job:*")
    jobs = []
    
    for key in job_keys:
        job_data = r.hgetall(key)
        if job_data:
            jobs.append({
                "job_id": job_data.get("job_id"),
                "worker_id": job_data.get("worker_id"),
                "user_id": job_data.get("user_id"),
                "status": job_data.get("status"),
                "timestamp": job_data.get("timestamp")
            })
    
    jobs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return {"jobs": jobs, "total": len(jobs)}

@app.post("/reset-counts")
async def reset_counts():
    for worker_id in WORKER_URLS:
        r.set(f"worker:{worker_id}:count", 0)

    r.delete("job_queue")

    pipeline_users = [f"pipeline-test-{i}" for i in range(1, 5)]

    async with httpx.AsyncClient(timeout=5.0) as client:
        for user_id in pipeline_users:
            pinned_worker = r.get(f"session:{user_id}")
            if pinned_worker and pinned_worker in WORKER_URLS:
                try:
                    await client.post(
                        f"{WORKER_URLS[pinned_worker]}/end_session",
                        data={"user_id": user_id}
                    )
                except Exception:
                    pass
            r.delete(f"session:{user_id}")

    return {"message": "All worker counts and pipeline sessions reset"}

@app.get("/health")
async def health_check():
    return {"status": "online", "redis": "connected"}
