import os
import uuid
import time
import json
import asyncio
import logging
import psutil
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Form, BackgroundTasks, WebSocket, WebSocketDisconnect, UploadFile, File, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import redis
import httpx

from llm_providers import (
    llm_client, AVAILABLE_MODELS, LLMResponse,
    CODE_GENERATION_SYSTEM_PROMPT, ERROR_EXPLANATION_SYSTEM_PROMPT, CODE_REVIEW_SYSTEM_PROMPT
)
from auth import (
    create_token, verify_token, get_current_user, get_optional_user,
    register_user, authenticate_user, get_user_quota, scan_code
)


logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
WORKER_URLS = {
    "worker1": os.getenv("WORKER1_URL", "http://worker1:5000"),
    "worker2": os.getenv("WORKER2_URL", "http://worker2:5000"),
    "worker3": os.getenv("WORKER3_URL", "http://worker3:5000"),
}

WORKER_MAX_CAPACITY = 1

app = FastAPI(title="Distributed SandPy API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

r = redis.from_url(REDIS_URL, decode_responses=True)

# ─── WebSocket Connection Manager ───────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, List[WebSocket]] = {}

    async def connect(self, job_id: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(job_id, []).append(ws)

    def disconnect(self, job_id: str, ws: WebSocket):
        if job_id in self.active:
            self.active[job_id] = [w for w in self.active[job_id] if w != ws]

    async def broadcast(self, job_id: str, message: dict):
        if job_id in self.active:
            dead = []
            for ws in self.active[job_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.active[job_id].remove(ws)

manager = ConnectionManager()

# ─── Pydantic Models ─────────────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    user_id: str
    code: str
    priority: Optional[str] = "normal"  # high, normal, low

class Job(BaseModel):
    job_id: str
    worker_id: str
    user_id: str
    code: str
    status: str
    output: Optional[str] = None
    timestamp: float

class AIGenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = "llama-3.3-70b"
    context: Optional[str] = None  # previous session output for context
    auto_execute: Optional[bool] = False
    user_id: Optional[str] = None

class AIExplainErrorRequest(BaseModel):
    code: str
    error: str
    model: Optional[str] = "llama-3.3-70b"

class AIReviewRequest(BaseModel):
    code: str
    model: Optional[str] = "llama-3.3-70b"

class AIRaceRequest(BaseModel):
    prompt: str
    models: List[str]
    context: Optional[str] = None

# ─── Redis Lua Script ─────────────────────────────────────────────────────────

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

# ─── Worker Helpers ───────────────────────────────────────────────────────────

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

# ─── Job Execution ────────────────────────────────────────────────────────────

import uuid
DISPATCHER_ID = str(uuid.uuid4())

async def process_queue():
    while True:
        # Check if we are the elected leader
        is_leader = r.set("dispatcher_leader", DISPATCHER_ID, nx=True, ex=10)
        current_leader = r.get("dispatcher_leader")
        
        if not is_leader and current_leader != DISPATCHER_ID:
            # We are not the leader, sleep and try again
            await asyncio.sleep(2)
            continue
            
        # We are the leader, renew the lock
        r.expire("dispatcher_leader", 10)

        # Check priority queues: high -> default
        queue_to_pop = None
        if r.llen("job_queue_high") > 0:
            queue_to_pop = "job_queue_high"
        elif r.llen("job_queue") > 0:
            queue_to_pop = "job_queue"
            
        if queue_to_pop is None:
            await asyncio.sleep(1)
            continue
            
        workers = list(WORKER_URLS.keys())
        worker_id = r.eval(CLAIM_WORKER_SCRIPT, len(workers), *workers, WORKER_MAX_CAPACITY)
        if not worker_id:
            await asyncio.sleep(1)
            continue
        if not await check_worker_health(worker_id):
            safe_decr_worker_count(worker_id)
            await asyncio.sleep(1)
            continue
            
        raw_job = r.lpop(queue_to_pop)
        if raw_job is None:
            safe_decr_worker_count(worker_id)
            await asyncio.sleep(1)
            continue
            
        job_data = json.loads(raw_job)
        r.hset(f"job:{job_data['job_id']}", mapping={"worker_id": worker_id, "status": "pending"})
        # We use create_task so the loop doesn't block
        asyncio.create_task(execute_job_background(job_data["job_id"], worker_id, job_data["user_id"], job_data["code"]))

async def execute_job_background(job_id: str, worker_id: str, user_id: str, code: str):
    try:
        logger.info(f"Starting job {job_id} on {worker_id}")
        r.hset(f"job:{job_id}", mapping={"status": "running", "worker_id": worker_id})
        await manager.broadcast(job_id, {"type": "status", "status": "running", "worker_id": worker_id})

        r.set(f"session:{user_id}", worker_id, ex=3600)

        async with httpx.AsyncClient(timeout=300.0) as client:
            start_session = await client.post(f"{WORKER_URLS[worker_id]}/start_session", data={"user_id": user_id})
            logger.info(f"Start session response: {start_session.status_code}")
            await asyncio.sleep(2)

            if start_session.status_code == 409:
                r.hset(f"job:{job_id}", mapping={"status": "failed", "output": "Session exists on another worker"})
                await manager.broadcast(job_id, {"type": "error", "message": "Session conflict"})
                return

            if start_session.status_code != 200:
                r.hset(f"job:{job_id}", mapping={"status": "failed", "output": f"Session start failed: {start_session.text}"})
                await manager.broadcast(job_id, {"type": "error", "message": "Session start failed"})
                return

            execute_response = await client.post(f"{WORKER_URLS[worker_id]}/execute", json={"user_id": user_id, "code": code})
            logger.info(f"Execute response status: {execute_response.status_code}")

            if execute_response.status_code == 200:
                result = execute_response.json()
                output = result.get("output", "")
                images = result.get("images", [])
                html_outputs = result.get("html_outputs", [])
                plotly_data = result.get("plotly_data", [])
                
                r.hset(f"job:{job_id}", mapping={
                    "status": "completed",
                    "output": output,
                    "images": json.dumps(images),
                    "html_outputs": json.dumps(html_outputs),
                    "plotly_data": json.dumps(plotly_data),
                })
                await manager.broadcast(job_id, {
                    "type": "completed",
                    "output": output,
                    "images": images,
                    "html_outputs": html_outputs,
                    "plotly_data": plotly_data,
                })
            else:
                error_body = execute_response.text
                r.hset(f"job:{job_id}", mapping={"status": "failed", "output": error_body})
                await manager.broadcast(job_id, {"type": "error", "message": error_body})

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        r.hset(f"job:{job_id}", mapping={"status": "failed", "output": str(e)})
        await manager.broadcast(job_id, {"type": "error", "message": str(e)})
    finally:
        safe_decr_worker_count(worker_id)

# ─── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws/job/{job_id}")
async def websocket_job_updates(websocket: WebSocket, job_id: str):
    """Real-time job status updates via WebSocket."""
    await manager.connect(job_id, websocket)
    try:
        # Send current state immediately
        job_data = r.hgetall(f"job:{job_id}")
        if job_data:
            await websocket.send_json({
                "type": "current_state",
                "status": job_data.get("status"),
                "output": job_data.get("output", ""),
                "images": json.loads(job_data.get("images", "[]")),
            })
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        manager.disconnect(job_id, websocket)

# ─── Core Job Endpoints ───────────────────────────────────────────────────────

@app.post("/submit")
async def submit_job(request: SubmitRequest, background_tasks: BackgroundTasks, auth_user: Optional[dict] = Depends(get_optional_user)):
    job_id = str(uuid.uuid4())
    timestamp = time.time()
    
    # Identify user (fallback to request payload if no token)
    user_id = auth_user["sub"] if auth_user else request.user_id
    role = auth_user["role"] if auth_user else "user"
    priority = "high" if role in ["admin", "premium"] else "default"
    
    # Danger Scanner phase
    scan_result = scan_code(request.code)
    if scan_result["verdict"] == "BLOCKED":
        raise HTTPException(
            status_code=403, 
            detail={"error": "Dangerous code detected and blocked.", "issues": scan_result["issues"]}
        )

    # Quota check
    if r:
        quota = get_user_quota(user_id)
        user_jobs_today_key = f"user:{user_id}:jobs_today"
        jobs_today = r.incr(user_jobs_today_key)
        if jobs_today == 1:
            r.expire(user_jobs_today_key, 86400)
        
        # Max sessions / jobs basic check (simplified)
        if jobs_today > 100 and role == "user":
            raise HTTPException(status_code=429, detail="Daily quota exceeded. Upgrade to premium.")

    session_worker = r.get(f"session:{user_id}")
    worker_id = None

    if session_worker:
        worker_id = session_worker
        if not await check_worker_health(worker_id):
            r.delete(f"session:{request.user_id}")
            worker_id = None
        else:
            current_count = int(r.get(f"worker:{worker_id}:count") or 0)
            if current_count >= WORKER_MAX_CAPACITY:
                worker_id = None

    if worker_id is None:
        workers = list(WORKER_URLS.keys())
        worker_id = r.eval(CLAIM_WORKER_SCRIPT, len(workers), *workers, WORKER_MAX_CAPACITY)
        if worker_id:
            if not await check_worker_health(worker_id):
                safe_decr_worker_count(worker_id)
                remaining = [w for w in workers if w != worker_id]
                worker_id = None
                for candidate in remaining:
                    c = r.eval(CLAIM_WORKER_SCRIPT, len([candidate]), candidate, WORKER_MAX_CAPACITY)
                    if c and await check_worker_health(c):
                        worker_id = c
                        break
                    elif c:
                        safe_decr_worker_count(c)
    else:
        r.incr(f"worker:{worker_id}:count")

    if worker_id:
        job_data = {
            "job_id": job_id, "worker_id": worker_id, "user_id": request.user_id,
            "code": request.code, "status": "pending", "timestamp": str(timestamp),
            "images": "[]", "html_outputs": "[]", "plotly_data": "[]",
        }
        r.hset(f"job:{job_id}", mapping=job_data)
        r.expire(f"job:{job_id}", 86400)
        background_tasks.add_task(execute_job_background, job_id, worker_id, request.user_id, request.code)
        return {"job_id": job_id, "worker_id": worker_id, "status": "pending"}
    else:
        job_data = {
            "job_id": job_id, "worker_id": "queued", "user_id": user_id,
            "code": request.code, "status": "queued", "timestamp": str(timestamp),
            "images": "[]", "html_outputs": "[]", "plotly_data": "[]",
        }
        r.hset(f"job:{job_id}", mapping=job_data)
        r.expire(f"job:{job_id}", 86400)
        
        queue_name = "job_queue_high" if priority == "high" else "job_queue"
        r.rpush(queue_name, json.dumps({"job_id": job_id, "user_id": user_id, "code": request.code}))
        return {"job_id": job_id, "worker_id": "queued", "status": "queued", "priority": priority, "warnings": scan_result["warnings"]}

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
        "images": json.loads(job_data.get("images", "[]")),
        "html_outputs": json.loads(job_data.get("html_outputs", "[]")),
        "plotly_data": json.loads(job_data.get("plotly_data", "[]")),
        "timestamp": job_data.get("timestamp"),
    }

@app.get("/workers")
async def get_workers():
    workers = []
    for worker_id, url in WORKER_URLS.items():
        is_healthy = await check_worker_health(worker_id)
        job_count = r.get(f"worker:{worker_id}:count")
        workers.append({
            "worker_id": worker_id, "url": url,
            "healthy": is_healthy, "job_count": int(job_count) if job_count else 0,
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
                "job_id": job_data.get("job_id"), "worker_id": job_data.get("worker_id"),
                "user_id": job_data.get("user_id"), "status": job_data.get("status"),
                "timestamp": job_data.get("timestamp"),
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
                    await client.post(f"{WORKER_URLS[pinned_worker]}/end_session", data={"user_id": user_id})
                except Exception:
                    pass
            r.delete(f"session:{user_id}")
    return {"message": "All worker counts and pipeline sessions reset"}

@app.get("/health")
async def health_check():
    return {"status": "online", "redis": "connected", "version": "2.0.0"}

# ─── AI Endpoints ─────────────────────────────────────────────────────────────

@app.get("/ai/models")
async def get_available_models():
    """Get all available LLM models and their status."""
    providers = llm_client.get_available_providers()
    models = []
    for model_key, config in AVAILABLE_MODELS.items():
        provider_available = any(p["provider"] == config.provider.value for p in providers)
        models.append({
            "key": model_key,
            "display_name": config.display_name,
            "provider": config.provider.value,
            "model_id": config.model_id,
            "max_tokens": config.max_tokens,
            "available": provider_available,
        })
    return {"models": models, "providers": providers}


@app.post("/ai/generate")
async def ai_generate_code(request: AIGenerateRequest, background_tasks: BackgroundTasks):
    """Generate Python code from natural language using the selected LLM."""
    response = await llm_client.generate(
        prompt=request.prompt,
        model_key=request.model or "llama-3.3-70b",
        system_prompt=CODE_GENERATION_SYSTEM_PROMPT,
        context=request.context,
    )
    
    if response.error:
        raise HTTPException(status_code=500, detail=f"LLM error: {response.error}")
    
    # Strip markdown fences if model added them anyway
    code = response.content.strip()
    for fence in ["```python", "```py", "```"]:
        if code.startswith(fence):
            code = code[len(fence):]
    if code.endswith("```"):
        code = code[:-3]
    code = code.strip()
    
    result = {
        "code": code,
        "model": response.model,
        "provider": response.provider,
        "latency_ms": round(response.latency_ms or 0, 1),
        "tokens_used": response.tokens_used,
    }
    
    # Auto-execute if requested
    if request.auto_execute and request.user_id:
        job_id = str(uuid.uuid4())
        timestamp = time.time()
        workers = list(WORKER_URLS.keys())
        worker_id = r.eval(CLAIM_WORKER_SCRIPT, len(workers), *workers, WORKER_MAX_CAPACITY)
        
        if worker_id:
            job_data = {
                "job_id": job_id, "worker_id": worker_id, "user_id": request.user_id,
                "code": code, "status": "pending", "timestamp": str(timestamp),
                "images": "[]", "html_outputs": "[]", "plotly_data": "[]",
            }
            r.hset(f"job:{job_id}", mapping=job_data)
            r.expire(f"job:{job_id}", 86400)
            background_tasks.add_task(execute_job_background, job_id, worker_id, request.user_id, code)
            result["job_id"] = job_id
            result["job_status"] = "pending"
    
    return result


@app.post("/ai/explain-error")
async def ai_explain_error(request: AIExplainErrorRequest):
    """Explain a Python error and suggest a fix using LLM."""
    prompt = f"""Python code that caused an error:
```python
{request.code}
```

Error message:
```
{request.error}
```

Explain this error and provide a fixed version of the code."""

    response = await llm_client.generate(
        prompt=prompt,
        model_key=request.model or "llama-3.3-70b",
        system_prompt=ERROR_EXPLANATION_SYSTEM_PROMPT,
    )
    
    if response.error:
        raise HTTPException(status_code=500, detail=f"LLM error: {response.error}")
    
    # Try to parse as JSON, fallback to raw text
    try:
        import re
        json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
        if json_match:
            explanation = json.loads(json_match.group())
        else:
            explanation = {"explanation": response.content, "fix": "", "root_cause": "", "tips": []}
    except Exception:
        explanation = {"explanation": response.content, "fix": "", "root_cause": "", "tips": []}
    
    return {
        "explanation": explanation,
        "model": response.model,
        "provider": response.provider,
        "latency_ms": round(response.latency_ms or 0, 1),
    }


@app.post("/ai/review")
async def ai_review_code(request: AIReviewRequest):
    """Review code for security issues and quality with LLM."""
    prompt = f"""Review this Python code:
```python
{request.code}
```
Check for security issues, code quality problems, and suggest optimizations."""

    response = await llm_client.generate(
        prompt=prompt,
        model_key=request.model or "llama-3.3-70b",
        system_prompt=CODE_REVIEW_SYSTEM_PROMPT,
    )
    
    if response.error:
        raise HTTPException(status_code=500, detail=f"LLM error: {response.error}")
    
    try:
        import re
        json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
        review = json.loads(json_match.group()) if json_match else {"summary": response.content}
    except Exception:
        review = {"summary": response.content}
    
    return {
        "review": review,
        "model": response.model,
        "provider": response.provider,
        "latency_ms": round(response.latency_ms or 0, 1),
    }


@app.post("/ai/race")
async def ai_race_models(request: AIRaceRequest):
    """Race multiple LLM models and return all results simultaneously."""
    if len(request.models) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 models in race mode")
    
    results = await llm_client.race(
        prompt=request.prompt,
        model_keys=request.models,
        system_prompt=CODE_GENERATION_SYSTEM_PROMPT,
    )
    
    serialized = {}
    for model_key, resp in results.items():
        code = resp.content.strip()
        for fence in ["```python", "```py", "```"]:
            if code.startswith(fence):
                code = code[len(fence):]
        if code.endswith("```"):
            code = code[:-3]
        code = code.strip()
        
        serialized[model_key] = {
            "code": code,
            "provider": resp.provider,
            "model": resp.model,
            "latency_ms": round(resp.latency_ms or 0, 1),
            "tokens_used": resp.tokens_used,
            "error": resp.error,
        }
    
    # Sort by latency (fastest first)
    fastest = min(serialized.items(), key=lambda x: x[1]["latency_ms"] if not x[1]["error"] else 999999)
    return {"results": serialized, "fastest": fastest[0]}


# ─── Auth Endpoints ──────────────────────────────────────────────────────────

from ai_swarm import SwarmRequest, execute_autonomous_swarm

@app.post("/ai/swarm/run")
async def run_autonomous_swarm(req: SwarmRequest, background_tasks: BackgroundTasks, user: dict = Depends(get_current_user)):
    job_id = f"swarm-{uuid.uuid4()}"
    
    async def swarm_callback(role, message, metadata=None, status="running"):
        await manager.broadcast(job_id, {"type": "swarm_event", "role": role, "message": message, "metadata": metadata, "status": status})

    # Kick off swarm in background. We won't block the caller.
    async def bg_swarm():
        await manager.broadcast(job_id, {"type": "status", "status": "running"})
        try:
            result = await execute_autonomous_swarm(req, swarm_callback)
            await manager.broadcast(job_id, {
                "type": "swarm_complete",
                "status": result["status"],
                "final_code": result.get("final_code"),
                "final_output": result.get("final_output")
            })
        except Exception as e:
            logger.error(f"Swarm background process crashed: {e}")
            await manager.broadcast(job_id, {
                "type": "swarm_complete",
                "status": "failed",
                "final_code": "",
                "final_output": f"Swarm crashed internally: {e}"
            })

    background_tasks.add_task(bg_swarm)
    return {"job_id": job_id, "status": "running"}

class AuthRequest(BaseModel):
    user_id: str
    password: str

@app.post("/auth/register")
async def api_register(req: AuthRequest):
    if register_user(req.user_id, req.password):
        token = create_token(req.user_id)
        return {"token": token, "user": {"user_id": req.user_id, "role": "user"}}
    raise HTTPException(status_code=400, detail="User already exists or registration failed")

@app.post("/auth/login")
async def api_login(req: AuthRequest):
    user = authenticate_user(req.user_id, req.password)
    if user:
        token = create_token(req.user_id, user.get("role", "user"))
        return {"token": token, "user": {"user_id": req.user_id, "role": user.get("role", "user")}}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/auth/me")
async def api_me(user: dict = Depends(get_current_user)):
    quota = get_user_quota(user["sub"])
    return {"user": user, "quota": quota}


# ─── Cluster Status & Telemetry ──────────────────────────────────────────────

@app.get("/cluster/status")
async def get_cluster_status():
    """Detailed cluster status for admin dashboards."""
    import psutil
    workers_detailed = []
    total_jobs = 0
    
    # Try fetching real status from workers
    async with httpx.AsyncClient(timeout=3.0) as client:
        for worker_id, url in WORKER_URLS.items():
            job_count = int(r.get(f"worker:{worker_id}:count") or 0)
            total_jobs += job_count
            w_info = {"worker_id": worker_id, "url": url, "job_count": job_count, "healthy": False}
            try:
                res = await client.get(f"{url}/health")
                if res.status_code == 200:
                    data = res.json()
                    w_info["healthy"] = True
                    w_info["active_sessions"] = data.get("active_sessions", 0)
            except Exception:
                pass
            workers_detailed.append(w_info)
            
    dispatcher_info = {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "queue_depth_standard": r.llen("job_queue"),
        "queue_depth_high": r.llen("job_queue_high"),
        "total_active_jobs": total_jobs,
        "uptime": time.time() - float(r.get("dispatcher_start_time") or time.time())
    }
    
    return {"dispatcher": dispatcher_info, "workers": workers_detailed}


# ─── File Workspace Proxy ────────────────────────────────────────────────────
# The UI uploads files to /workspace, dispatcher proxies it to the correct worker

@app.post("/workspace/upload")
async def proxy_workspace_upload_sharded(file: UploadFile, user: dict = Depends(get_current_user)):
    user_id = user["sub"]
    available = [w["worker_id"] for w in (await get_workers())["workers"] if w["healthy"]]
    if not available:
        raise HTTPException(status_code=503, detail="No healthy workers for D-FS upload")

    num_shards = len(available)
    try:
        content = await file.read()
        shard_size = max(1, len(content) // num_shards)
        
        # Partition the bytes
        shards = [content[i:i + shard_size] for i in range(0, len(content), shard_size)]
        # If perfect division leaves rounding edge cases, zip handles it or we limit to workers
        
        tasks = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for idx, worker_id in enumerate(available):
                if idx < len(shards):
                    url = f"{WORKER_URLS[worker_id]}/workspace/upload_shard"
                    # Sending binary chunks
                    payload = {"user_id": user_id, "filename": file.filename, "shard_index": idx, "total_shards": len(shards)}
                    files = {'file_shard': (f"{file.filename}.part{idx}", shards[idx], 'application/octet-stream')}
                    tasks.append(client.post(url, data=payload, files=files))
                    
            resps = await asyncio.gather(*tasks, return_exceptions=True)
            for r in resps:
                if isinstance(r, Exception):
                    raise HTTPException(status_code=500, detail=f"Shard failed: {r}")
                if r.status_code != 200:
                    raise HTTPException(status_code=r.status_code, detail=f"Worker shard error: {r.text}")
                    
            # For UI metadata storage, we can post a metadata file to Redis or Worker 1
            # But the UI just lists it via proxy_workspace_list
            return {"message": f"Successfully sharded {file.filename} across {num_shards} nodes.", "filename": file.filename}
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"D-FS Sharding Error: {str(e)}")


@app.get("/workspace/files")
async def proxy_workspace_list(user: dict = Depends(get_current_user)):
    user_id = user["sub"]
    # Broad query: gather files from all healthy workers
    all_files = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for worker_id, url in WORKER_URLS.items():
            if await check_worker_health(worker_id):
                try:
                    resp = await client.get(f"{url}/workspace/files", params={"user_id": user_id})
                    if resp.status_code == 200:
                        all_files.extend(resp.json().get("files", []))
                except Exception:
                    pass
    
    # Deduplicate by name and return newest
    unique = {}
    for f in all_files:
        name = f["name"].replace(".meta", "")
        if ".part" in name:
            name = name.split(".part")[0]
            
        f["name"] = name
        if name not in unique or unique[name]["modified"] < f["modified"]:
            unique[name] = f
            
    return {"files": list(unique.values())}

@app.get("/workspace/suggest/{filename}")
async def proxy_workspace_suggest_sharded(filename: str, user: dict = Depends(get_current_user)):
    """Downloads all shards natively, reconstructs in memory, and generates code suggestions"""
    user_id = user["sub"]
    available = [w["worker_id"] for w in (await get_workers())["workers"] if w["healthy"]]
    
    # 1. Ask all healthy nodes for their shard shards
    tasks = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for worker_id in available:
            url = f"{WORKER_URLS[worker_id]}/workspace/download_shard/{filename}"
            tasks.append(client.get(url, params={"user_id": user_id}))
            
        resps = await asyncio.gather(*tasks, return_exceptions=True)
        
    # Reassemble
    shards_collected = []
    for resp in resps:
        if not isinstance(resp, Exception) and resp.status_code == 200:
             # The header will have 'X-Shard-Index' or we sort if it's there
             idx = int(resp.headers.get("X-Shard-Index", -1))
             if idx >= 0:
                 shards_collected.append((idx, resp.content))
                 
    if not shards_collected:
        raise HTTPException(status_code=404, detail="File shards not found on cluster network")
        
    shards_collected.sort(key=lambda x: x[0])
    full_content = b"".join([s[1] for s in shards_collected])
    
    # Generate mock code payload since it's reassembled
    ext = os.path.splitext(filename)[1].lower()
    sandbox_local_path = f"/mnt/data/workspace/{user_id}/{filename}"
    
    # If the user tries to run it, they'd use `sandpy_dist.read_sharded(filename)`
    # But for suggestions, we return the content snippet manually
    preview_snippet = full_content[:300].decode("utf-8", errors="ignore")
    code = f"# Assembled transparently from {len(shards_collected)} Distributed Shards\n"
    code += f"# Total Real Size: {len(full_content)} bytes\n\n"
    code += f"import sandpy_dist\ndf = sandpy_dist.read_sharded('{filename}')\nprint(df.head())\n\n"
    code += f'"""\\n--- D-FS Shard Bytes Preview ---\\n{preview_snippet}...\\n"""'
    
    return {"code": code, "filename": filename, "extension": ext}


class MapRequest(BaseModel):
    func_b64: str
    iterable: list

@app.post("/api/map")
async def orchestrate_map_reduce(req: MapRequest):
    """Orchestrates a MapReduce task across all healthy workers simultaneously."""
    available = [w["worker_id"] for w in (await get_workers())["workers"] if w["healthy"]]
    if not available:
        raise HTTPException(status_code=503, detail="No healthy workers for MapReduce")

    # Chunk the iterable manually
    num_workers = len(available)
    data = req.iterable
    if not data:
        return {"results": []}
    
    chunk_size = max(1, len(data) // num_workers)
    chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]
    
    # If the array is perfectly divisible, we might have exact chunks.
    # Otherwise, the last chunk might be larger or smaller, this matches.
    # We zip workers with chunks. Because workers might be fewer than chunks (due to slight remainder logic edge cases in slices),
    # let's just use cycle or limit it:
    import itertools
    worker_cycle = itertools.cycle(available)
    
    tasks = []
    async with httpx.AsyncClient(timeout=300.0) as client:
        for chunk in chunks:
            wid = next(worker_cycle)
            url = f"{WORKER_URLS[wid]}/api/map_chunk"
            payload = {"func_b64": req.func_b64, "chunk": chunk}
            # Add an async task
            tasks.append(client.post(url, json=payload))
            
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
    final_results = []
    for resp in responses:
        if isinstance(resp, Exception):
            raise HTTPException(status_code=500, detail=f"Worker disconnected during map: {resp}")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        chunk_res = resp.json().get("results", [])
        final_results.extend(chunk_res)
        
    return {"results": final_results, "workers_used": len(available), "chunks": len(chunks)}


# ─── Services Orchestration & Queueing ────────────────────────────────────────

class AdminServiceStart(BaseModel):
    user_id: str
    code: str
    port: Optional[int] = 8000

async def process_service_queue():
    while True:
        # Leader election for checking service queue
        is_leader = r.set("dispatcher_service_leader", DISPATCHER_ID, nx=True, ex=10)
        current_leader = r.get("dispatcher_service_leader")
        
        if not is_leader and current_leader != DISPATCHER_ID:
            await asyncio.sleep(2)
            continue
            
        r.expire("dispatcher_service_leader", 10)
        
        if r.llen("service_queue") > 0:
            for wid in list(WORKER_URLS.keys()):
                # Fast check if worker is truly free entirely. No deployed service.
                if r.get(f"worker:{wid}:service") is None and await check_worker_health(wid):
                    item = r.lpop("service_queue")
                    if item:
                        data = json.loads(item)
                        service_id = data["service_id"]
                        
                        r.set(f"worker:{wid}:service", service_id)
                        r.hset(f"service:{service_id}", mapping={"worker_id": wid, "status": "running"})
                        
                        try:
                            async with httpx.AsyncClient(timeout=10.0) as client:
                                await client.post(f"{WORKER_URLS[wid]}/services/start", json={
                                    "service_id": service_id, "user_id": data["user_id"], "code": data["code"], "port": data["port"]
                                })
                                # Optionally broadcast global ws event
                                await manager.broadcast("global", {"type": "service_deployed", "service_id": service_id, "worker_id": wid})
                        except Exception as e:
                            r.delete(f"worker:{wid}:service")
                            r.hset(f"service:{service_id}", mapping={"status": "failed"})
                            # Push back if transient error? Let's just fail it to avoid loops
                    break 
                    
        await asyncio.sleep(2)

@app.post("/services/start")
async def dispatcher_start_service(req: AdminServiceStart):
    service_id = str(uuid.uuid4())
    workers = list(WORKER_URLS.keys())
    
    for wid in workers:
        if r.get(f"worker:{wid}:service") is None:
            if not await check_worker_health(wid):
                continue
            
            r.set(f"worker:{wid}:service", service_id)
            r.hset(f"service:{service_id}", mapping={"worker_id": wid, "user_id": req.user_id, "status": "running"})
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(f"{WORKER_URLS[wid]}/services/start", json={
                        "service_id": service_id, "user_id": req.user_id, "code": req.code, "port": req.port
                    })
                    if resp.status_code == 200:
                        return {"status": "started", "service_id": service_id, "worker_id": wid}
                    else:
                        r.delete(f"worker:{wid}:service")
                        r.delete(f"service:{service_id}")
                        raise HTTPException(status_code=500, detail=resp.text)
            except Exception as e:
                r.delete(f"worker:{wid}:service")
                r.delete(f"service:{service_id}")
                raise HTTPException(status_code=500, detail=str(e))
                
    # Queue it
    queue_item = {"service_id": service_id, "user_id": req.user_id, "code": req.code, "port": req.port}
    r.rpush("service_queue", json.dumps(queue_item))
    r.hset(f"service:{service_id}", mapping={"worker_id": "queued", "user_id": req.user_id, "status": "queued"})
    return {"status": "queued", "service_id": service_id}

@app.delete("/services/stop/{service_id}")
async def dispatcher_stop_service(service_id: str):
    svc = r.hgetall(f"service:{service_id}")
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
        
    wid = svc.get("worker_id")
    if wid == "queued":
        q_len = r.llen("service_queue")
        for _ in range(q_len):
            item = r.lpop("service_queue")
            try:
                data = json.loads(item)
                if data["service_id"] != service_id:
                    r.rpush("service_queue", item)
            except:
                pass
        r.delete(f"service:{service_id}")
        return {"status": "stopped_from_queue", "service_id": service_id}
        
    if wid in WORKER_URLS:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(f"{WORKER_URLS[wid]}/services/stop/{service_id}")
        except Exception:
            pass
            
        r.delete(f"worker:{wid}:service")
        r.delete(f"service:{service_id}")
        return {"status": "stopped", "service_id": service_id}
        
    raise HTTPException(status_code=400, detail="Invalid worker state")

@app.get("/services")
async def list_services(user_id: Optional[str] = None):
    keys = r.keys("service:*")
    services = []
    for k in keys:
        svc = r.hgetall(k)
        if svc:
            svc["service_id"] = k.replace("service:", "")
            if not user_id or svc.get("user_id") == user_id:
                services.append(svc)
    return {"services": services}


@app.on_event("startup")
async def startup_event():
    logger.info("Distributed SandPy Dispatcher v2.0 starting up...")
    if r:
        r.set("dispatcher_start_time", str(time.time()))
    asyncio.create_task(process_queue())
    asyncio.create_task(process_service_queue())
