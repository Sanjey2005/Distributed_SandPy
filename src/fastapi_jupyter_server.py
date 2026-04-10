from typing import Dict, Optional, List, Any
from fastapi import FastAPI, UploadFile, Form, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uuid
from pydantic import BaseModel
import os
import sys
import time
import json
import asyncio
import logging

logger = logging.getLogger(__name__)
import queue
import redis
import subprocess
import nbformat
from nbformat.v4 import new_notebook, new_code_cell
from jupyter_client import KernelManager
from llm_providers import (
    llm_client, AVAILABLE_MODELS, LLMResponse,
    CODE_GENERATION_SYSTEM_PROMPT, ERROR_EXPLANATION_SYSTEM_PROMPT, CODE_REVIEW_SYSTEM_PROMPT
)
from ai_swarm import SwarmRequest, execute_autonomous_swarm

app = FastAPI(title="SandPy Worker", version="2.0.0")

BASE_FOLDER = os.environ.get("BASE_FOLDER", os.path.join(os.getcwd(), "data"))
SESSIONS_FOLDER = os.environ.get("SESSIONS_FOLDER", os.path.join(os.getcwd(), "jupyter_sessions"))
WORKER_ID = os.getenv("WORKER_ID", "worker1")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

try:
    r = redis.from_url(REDIS_URL, decode_responses=True)
except Exception:
    r = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── WebSocket Connection Manager ───────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, List[WebSocket]] = {}
        self.buffer: Dict[str, List[dict]] = {}

    async def connect(self, job_id: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(job_id, []).append(ws)
        # Replay any events that fired before the WebSocket connected
        for message in self.buffer.get(job_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                break

    def disconnect(self, job_id: str, ws: WebSocket):
        if job_id in self.active:
            self.active[job_id] = [w for w in self.active[job_id] if w != ws]

    async def broadcast(self, job_id: str, message: dict):
        self.buffer.setdefault(job_id, []).append(message)
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


class JupyterController:
    def __init__(self, folder_path):
        self.folder_path = folder_path
        self.notebook_path = None
        self.kernel_manager = None
        self.kernel_client = None
        self._kernel_ready = False
        self.execution_history: List[Dict] = []

    async def _wait_for_kernel_ready(self, timeout=30):
        start_time = time.time()
        while not self._kernel_ready:
            if time.time() - start_time > timeout:
                raise TimeoutError("Kernel failed to start within timeout period")
            try:
                if self.kernel_manager and self.kernel_manager.is_alive():
                    self.kernel_client.execute("1+1")
                    while True:
                        try:
                            msg = self.kernel_client.get_iopub_msg(timeout=0.1)
                            if msg['header']['msg_type'] == 'status' and \
                               msg['content']['execution_state'] == 'idle':
                                break
                        except queue.Empty:
                            break
                    self._kernel_ready = True
                    break
            except Exception as e:
                print(f"Kernel init error: {str(e)}")
            await asyncio.sleep(0.1)

    async def create_notebook(self, notebook_name):
        os.makedirs(self.folder_path, exist_ok=True)
        self.notebook_path = os.path.join(self.folder_path, f"{notebook_name}.ipynb")
        nb = new_notebook()
        with open(self.notebook_path, "w") as f:
            nbformat.write(nb, f)
        self.kernel_manager = KernelManager()
        self.kernel_manager.start_kernel()
        self.kernel_client = self.kernel_manager.client()
        self.kernel_client.start_channels()
        await self._wait_for_kernel_ready()
        self._clear_output_queue()
        return self.notebook_path

    def _clear_output_queue(self):
        while True:
            try:
                self.kernel_client.get_iopub_msg(timeout=0.1)
            except queue.Empty:
                break

    async def execute_code(self, code: str, raise_http: bool = True) -> Dict:
        """Execute code and capture ALL output types: text, images, HTML, Plotly."""
        if not self._kernel_ready:
            raise RuntimeError("Kernel not ready. Please wait or restart session.")
        if not self.kernel_manager.is_alive():
            self._kernel_ready = False
            raise RuntimeError("Kernel died. Please restart session.")

        await asyncio.to_thread(self._clear_output_queue)

        # Inject matplotlib backend for non-interactive PNG capture
        setup_code = """
import matplotlib
matplotlib.use('Agg')
import io as _io
import base64 as _base64
from IPython.display import display
"""
        # Wrap code to intercept plt.show() and capture figures
        wrapped_code = f"""
{setup_code}
_captured_images = []
_captured_html = []
_captured_plotly = []

import matplotlib.pyplot as _plt_orig
_orig_show = _plt_orig.show

def _capture_show():
    for fig_num in _plt_orig.get_fignums():
        fig = _plt_orig.figure(fig_num)
        buf = _io.BytesIO()
        fig.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor=fig.get_facecolor())
        buf.seek(0)
        img_b64 = _base64.b64encode(buf.read()).decode('utf-8')
        _captured_images.append(img_b64)
    _plt_orig.close('all')

_plt_orig.show = _capture_show

# ─── USER CODE ────────────────────────────────────────────────────────────────
{code}
# ─── END USER CODE ────────────────────────────────────────────────────────────

# Auto-capture any remaining figures
for _fig_num in _plt_orig.get_fignums():
    _fig = _plt_orig.figure(_fig_num)
    _buf = _io.BytesIO()
    _fig.savefig(_buf, format='png', dpi=120, bbox_inches='tight', facecolor=_fig.get_facecolor())
    _buf.seek(0)
    _captured_images.append(_base64.b64encode(_buf.read()).decode('utf-8'))
_plt_orig.close('all')
_plt_orig.show = _orig_show

# Output capture markers
if _captured_images:
    print(f"__IMAGES__:{{','.join(_captured_images)}}")
"""
        
        msg_id = self.kernel_client.execute(wrapped_code)
        text_outputs = []
        images = []
        html_outputs = []
        plotly_data = []
        
        while True:
            try:
                msg = await asyncio.to_thread(self.kernel_client.get_iopub_msg, timeout=10)
                parent_msg_id = msg.get('parent_header', {}).get('msg_id')
                if parent_msg_id and parent_msg_id != msg_id:
                    continue  # Ignore delayed messages from previous executions
                    
                msg_type = msg['header']['msg_type']
                content = msg['content']
                
                if msg_type == 'stream':
                    text = content['text']
                    # Extract our special image marker
                    if '__IMAGES__:' in text:
                        for line in text.splitlines():
                            if line.startswith('__IMAGES__:'):
                                img_data = line[len('__IMAGES__:'):]
                                images.extend([i for i in img_data.split(',') if i])
                            else:
                                if line.strip():
                                    text_outputs.append(line)
                    else:
                        text_outputs.append(text)
                
                elif msg_type == 'execute_result':
                    data = content.get('data', {})
                    if 'text/html' in data:
                        html_outputs.append(data['text/html'])
                    elif 'application/json' in data:
                        # Plotly or other JSON viz
                        plotly_data.append(data['application/json'])
                    elif 'image/png' in data:
                        images.append(data['image/png'])
                    elif 'text/plain' in data:
                        text_outputs.append(str(data['text/plain']))
                
                elif msg_type == 'display_data':
                    data = content.get('data', {})
                    if 'image/png' in data:
                        images.append(data['image/png'])
                    elif 'text/html' in data:
                        html_outputs.append(data['text/html'])
                    elif 'application/json' in data:
                        plotly_data.append(data['application/json'])
                    elif 'text/plain' in data and data['text/plain']:
                        text_outputs.append(str(data['text/plain']))
                
                elif msg_type == 'error':
                    if raise_http:
                        raise HTTPException(
                            status_code=400,
                            detail={"error": "Execution error", "traceback": content['traceback']}
                        )
                    else:
                        tb = '\n'.join(content.get('traceback', []))
                        error_text = f"Error: {content.get('ename', 'Unknown')}: {content.get('evalue', '')}\n{tb}"
                        text_outputs.append(error_text)
                
                elif msg_type == 'status' and content['execution_state'] == 'idle':
                    break

            except queue.Empty:
                if raise_http:
                    raise HTTPException(status_code=408, detail="Code execution timed out")
                else:
                    text_outputs.append("Error: Code execution timed out (no response from kernel)")
                    break

        output_text = '\n'.join(text_outputs) if text_outputs else ""
        
        # Record in history
        self.execution_history.append({
            "code": code,
            "output": output_text,
            "images": len(images),
            "timestamp": time.time(),
        })
        
        return {
            "output": output_text,
            "images": images,
            "html_outputs": html_outputs,
            "plotly_data": plotly_data,
        }

    async def reset_kernel(self):
        if self.kernel_manager:
            self._kernel_ready = False
            self.kernel_manager.restart_kernel()
            await self._wait_for_kernel_ready()
            await asyncio.to_thread(self._clear_output_queue)
            self.execution_history = []

    def cleanup(self):
        if self.kernel_client:
            self.kernel_client.stop_channels()
        if self.kernel_manager:
            self.kernel_manager.shutdown_kernel(now=True)
        if self.notebook_path and os.path.exists(self.notebook_path):
            os.remove(self.notebook_path)

    def export_notebook(self) -> dict:
        """Export session as a Jupyter notebook dict."""
        nb = new_notebook()
        cells = []
        for item in self.execution_history:
            cell = new_code_cell(source=item["code"])
            cell.outputs = [{"output_type": "stream", "name": "stdout", "text": item["output"]}]
            cells.append(cell)
        nb.cells = cells
        return nbformat.writes(nb)


class SessionInfo:
    def __init__(self, controller, created_at: float):
        self.controller = controller
        self.created_at = created_at
        self.last_activity = created_at


sessions: Dict[str, SessionInfo] = {}


class ExecuteRequest(BaseModel):
    user_id: str
    code: str


class InstallPackageRequest(BaseModel):
    user_id: str
    package_name: str

class AIGenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = "llama-3.3-70b"
    context: Optional[str] = None
    user_id: str

class AIExplainErrorRequest(BaseModel):
    code: str
    error: str
    model: Optional[str] = "llama-3.3-70b"

class AIReviewRequest(BaseModel):
    code: str
    model: Optional[str] = "llama-3.3-70b"

class LockRequest(BaseModel):
    user_id: str
    action: str # "acquire" or "release"


async def cleanup_inactive_sessions():
    while True:
        current_time = time.time()
        to_remove = [uid for uid, si in sessions.items() if current_time - si.last_activity > 3600]
        for user_id in to_remove:
            sessions.pop(user_id).controller.cleanup()
        await asyncio.sleep(300)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_inactive_sessions())


async def get_session(user_id: str) -> SessionInfo:
    if user_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    si = sessions[user_id]
    si.last_activity = time.time()
    if not si.controller._kernel_ready:
        try:
            await si.controller._wait_for_kernel_ready(timeout=10)
        except TimeoutError:
            await si.controller.reset_kernel()
    return si


@app.post("/start_session")
async def start_session(user_id: str = Form(...)):
    # Check if user is pinned to this worker via Redis
    if r:
        pinned_worker = r.get(f"session:{user_id}")
        if pinned_worker and pinned_worker != WORKER_ID:
            raise HTTPException(status_code=409, detail=f"Session pinned to {pinned_worker}")

    if user_id in sessions:
        sessions[user_id].controller.cleanup()

    session_folder = os.path.join(SESSIONS_FOLDER, user_id)
    controller = JupyterController(session_folder)
    try:
        notebook_path = await controller.create_notebook(f"notebook_{user_id}")
        sessions[user_id] = SessionInfo(controller, time.time())
        # Pre-import common libraries
        setup_code = """
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json, os, sys, math, re, itertools, collections
from datetime import datetime, timedelta
print("Environment ready.")
"""
        await controller.execute_code(setup_code)
        return {"message": "Session started successfully", "notebook_path": notebook_path, "worker_id": WORKER_ID}
    except Exception as e:
        controller.cleanup()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute")
async def execute_code(request: ExecuteRequest):
    session_info = await get_session(request.user_id)
    try:
        result = await session_info.controller.execute_code(request.code)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/install_package")
async def install_package(request: InstallPackageRequest):
    session_info = await get_session(request.user_id)
    try:
        result = subprocess.run(
            ["pip", "install", request.package_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to install {request.package_name}: {result.stderr}")
        import_name = request.package_name.split('[')[0].split('==')[0].split('>=')[0]
        await session_info.controller.execute_code(f"import {import_name}")
        return {"message": f"Successfully installed and imported {request.package_name}", "output": result.stdout}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail=f"Package installation timed out for {request.package_name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset")
async def reset_session(user_id: str = Form(...)):
    session_info = await get_session(user_id)
    try:
        await session_info.controller.reset_kernel()
        setup_code = """
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json, os, sys, math, re, itertools, collections
from datetime import datetime, timedelta
print("Environment reset and ready.")
"""
        await session_info.controller.execute_code(setup_code)
        return {"message": "Kernel reset successful"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/end_session")
async def end_session(user_id: str = Form(...)):
    if user_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    sessions.pop(user_id).controller.cleanup()
    return {"message": "Session ended successfully"}


@app.get("/session/{user_id}/history")
async def get_session_history(user_id: str):
    session_info = await get_session(user_id)
    return {"history": session_info.controller.execution_history}


@app.get("/session/{user_id}/export")
async def export_notebook(user_id: str):
    session_info = await get_session(user_id)
    notebook_json = session_info.controller.export_notebook()
    return {"notebook": notebook_json, "filename": f"session_{user_id}.ipynb"}


@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "worker_id": WORKER_ID,
        "active_sessions": len(sessions),
        "version": "2.0.0",
    }


# ─── File Workspace Endpoints ────────────────────────────────────────────────

WORKSPACE_FOLDER = os.path.join(BASE_FOLDER, "workspace")
ALLOWED_EXTENSIONS = {".csv", ".json", ".txt", ".parquet", ".xlsx", ".png", ".jpg", ".py", ".md"}
MAX_FILE_SIZE_MB = 100


@app.post("/workspace/upload_shard")
async def upload_shard(file_shard: UploadFile, user_id: str = Form(...), filename: str = Form(...), shard_index: int = Form(...)):
    user_workspace = os.path.join(WORKSPACE_FOLDER, user_id)
    os.makedirs(user_workspace, exist_ok=True)
    # Save the part file with its index mapping
    dest_path = os.path.join(user_workspace, f"{filename}.part{shard_index}")
    content = await file_shard.read()
    with open(dest_path, "wb") as f:
        f.write(content)
    
    # Write a tiny metadata file so `list_files` knows the original file exists in D-FS
    meta_path = os.path.join(user_workspace, f"{filename}.meta")
    with open(meta_path, "w") as f:
        f.write(str(len(content)))
        
    return {"message": "Shard saved", "shard_index": shard_index}

from fastapi.responses import Response

@app.get("/workspace/download_shard/{filename}")
async def download_shard(filename: str, user_id: str):
    user_workspace = os.path.join(WORKSPACE_FOLDER, user_id)
    # Give back whichever shard we have
    for f in os.listdir(user_workspace):
        if f.startswith(f"{filename}.part"):
            shard_index = f.replace(f"{filename}.part", "")
            fpath = os.path.join(user_workspace, f)
            with open(fpath, "rb") as bf:
                content = bf.read()
            return Response(content=content, media_type="application/octet-stream", headers={"X-Shard-Index": shard_index})
    raise HTTPException(status_code=404, detail="Shard not found on this node")


@app.get("/workspace/files")
async def list_files(user_id: str):
    """List files in a user's workspace."""
    user_workspace = os.path.join(WORKSPACE_FOLDER, user_id)
    os.makedirs(user_workspace, exist_ok=True)

    files = []
    for fname in os.listdir(user_workspace):
        fpath = os.path.join(user_workspace, fname)
        if os.path.isfile(fpath):
            stat = os.stat(fpath)
            files.append({
                "name": fname,
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
                "extension": os.path.splitext(fname)[1].lower(),
                "workspace_path": os.path.join(WORKSPACE_FOLDER, user_id, fname),
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files, "user_id": user_id}


@app.delete("/workspace/files/{filename}")
async def delete_file(filename: str, user_id: str):
    """Delete a file from user's workspace."""
    safe_name = os.path.basename(filename)
    fpath = os.path.join(WORKSPACE_FOLDER, user_id, safe_name)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="File not found")
    os.remove(fpath)
    return {"message": f"Deleted {safe_name}"}


@app.get("/workspace/suggest/{filename}")
async def suggest_analysis(filename: str, user_id: str):
    """Get suggested analysis code for an uploaded file."""
    ext = os.path.splitext(filename)[1].lower()
    workspace_path = os.path.join(WORKSPACE_FOLDER, user_id, filename)
    suggestions = {
        ".csv": f"""import pandas as pd
df = pd.read_csv('{workspace_path}')
print(f"Shape: {{df.shape}}")
print(f"Columns: {{list(df.columns)}}")
print("\\nFirst 5 rows:")
print(df.head().to_string())
print("\\nData types:")
print(df.dtypes)
print("\\nBasic stats:")
print(df.describe())""",
        ".json": f"""import json, pandas as pd
with open('{workspace_path}') as f:
    data = json.load(f)
print(f"Type: {{type(data).__name__}}")
if isinstance(data, list):
    print(f"Records: {{len(data)}}")
    df = pd.DataFrame(data)
    print(df.head().to_string())
else:
    print(f"Keys: {{list(data.keys())}}")""",
        ".parquet": f"""import pandas as pd
df = pd.read_parquet('{workspace_path}')
print(f"Shape: {{df.shape}}")
print(df.head().to_string())
print(df.dtypes)""",
        ".xlsx": f"""import pandas as pd
df = pd.read_excel('{workspace_path}')
print(f"Shape: {{df.shape}}")
print(df.head().to_string())""",
        ".txt": f"""with open('{workspace_path}') as f:
    content = f.read()
print(f"Length: {{len(content)}} chars")
print("First 500 chars:")
print(content[:500])""",
    }
    code = suggestions.get(ext, f"# No template for {ext}\nprint('File at: {workspace_path}')")
    return {"code": code, "filename": filename, "extension": ext}


# ─── Locking Mechanism ──────────────────────────────────────────────────────

@app.post("/lock")
async def handle_lock(req: LockRequest):
    if not r:
        return {"status": "unsupported", "message": "Redis not available"}
    
    lock_key = "lock:code_editor"
    if req.action == "acquire":
        success = r.set(lock_key, req.user_id, nx=True, ex=30)
        if success:
            return {"status": "acquired", "user_id": req.user_id}
        else:
            current_owner = r.get(lock_key)
            return {"status": "denied", "owner": current_owner}
    else:
        owner = r.get(lock_key)
        if owner == req.user_id:
            r.delete(lock_key)
            return {"status": "released"}
        return {"status": "not_owner", "owner": owner}

# ─── AI Endpoints ─────────────────────────────────────────────────────────────

@app.get("/ai/models")
async def get_available_models():
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
async def ai_generate_code(request: AIGenerateRequest):
    response = await llm_client.generate(
        prompt=request.prompt,
        model_key=request.model or "llama-3.3-70b",
        system_prompt=CODE_GENERATION_SYSTEM_PROMPT,
        context=request.context,
    )
    if response.error:
        raise HTTPException(status_code=500, detail=f"LLM error: {response.error}")
    
    code = response.content.strip()
    for fence in ["```python", "```py", "```"]:
        if code.startswith(fence):
            code = code[len(fence):]
    if code.endswith("```"):
        code = code[:-3]
    
    return {
        "code": code.strip(),
        "model": response.model,
        "provider": response.provider,
        "latency_ms": round(response.latency_ms or 0, 1),
    }

@app.post("/ai/swarm/run")
async def run_autonomous_swarm(req: SwarmRequest, background_tasks: BackgroundTasks):
    job_id = f"swarm-{uuid.uuid4()}"
    
    async def swarm_callback(role, message, metadata=None, status="running"):
        await manager.broadcast(job_id, {"type": "swarm_event", "role": role, "message": message, "metadata": metadata, "status": status})

    async def session_runner(code: str):
        # Ensure session exists
        if req.user_id not in sessions:
            await start_session(req.user_id)
        si = await get_session(req.user_id)
        return await si.controller.execute_code(code, raise_http=False)

    async def bg_swarm():
        try:
            await manager.broadcast(job_id, {"type": "status", "status": "running"})
            result = await execute_autonomous_swarm(req, session_runner, swarm_callback)
            await manager.broadcast(job_id, {
                "type": "swarm_complete",
                "status": result["status"],
                "final_code": result.get("final_code"),
                "final_output": result.get("final_output")
            })
        except Exception as e:
            logger.exception(f"Swarm {job_id} crashed: {e}")
            await manager.broadcast(job_id, {
                "type": "swarm_complete",
                "status": "failed",
                "final_code": None,
                "final_output": str(e)
            })

    background_tasks.add_task(bg_swarm)
    return {"job_id": job_id, "status": "running"}

@app.websocket("/ws/job/{job_id}")
async def websocket_job_updates(websocket: WebSocket, job_id: str):
    await manager.connect(job_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(job_id, websocket)

@app.post("/ai/explain-error")
async def ai_explain_error(request: AIExplainErrorRequest):
    prompt = f"Code:\n{request.code}\nError:\n{request.error}\nExplain and fix."
    response = await llm_client.generate(
        prompt=prompt,
        model_key=request.model or "llama-3.3-70b",
        system_prompt=ERROR_EXPLANATION_SYSTEM_PROMPT,
    )
    if response.error: raise HTTPException(status_code=500, detail=response.error)
    try:
        import re
        json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
        explanation = json.loads(json_match.group()) if json_match else {"explanation": response.content}
    except Exception: explanation = {"explanation": response.content}
    return {"explanation": explanation, "provider": response.provider, "latency_ms": response.latency_ms}

@app.post("/ai/review")
async def ai_review_code(request: AIReviewRequest):
    response = await llm_client.generate(prompt=request.code, model_key=request.model, system_prompt=CODE_REVIEW_SYSTEM_PROMPT)
    if response.error: raise HTTPException(status_code=500, detail=response.error)
    try:
        import re
        json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
        review = json.loads(json_match.group()) if json_match else {"summary": response.content}
    except Exception: review = {"summary": response.content}
    return {"review": review, "provider": response.provider, "latency_ms": response.latency_ms}


# ─── Services Deployment ─────────────────────────────────────────────────────

import subprocess

class StartServiceRequest(BaseModel):
    service_id: str
    user_id: str
    code: str
    port: Optional[int] = 8000

active_services = {}


def _code_has_server(code: str) -> bool:
    """Check if code already contains a real web server by looking for import statements."""
    import re
    # Only match actual import lines, not random keyword mentions like app.run()
    patterns = [
        r'^\s*from\s+flask\s+import',
        r'^\s*import\s+flask',
        r'^\s*from\s+fastapi\s+import',
        r'^\s*import\s+fastapi',
        r'^\s*from\s+http\.server\s+import',
        r'^\s*import\s+http\.server',
        r'^\s*from\s+bottle\s+import',
        r'^\s*import\s+bottle',
    ]
    for line in code.splitlines():
        for pat in patterns:
            if re.match(pat, line, re.IGNORECASE):
                return True
    return False


def wrap_code_with_server(code: str, port: int) -> str:
    """If code doesn't already contain a web server, wrap it in one that serves stdout as HTML."""
    if _code_has_server(code):
        return code

    indented = "\n".join("    " + line for line in code.splitlines())
    return f'''import sys, io
from http.server import HTTPServer, BaseHTTPRequestHandler

_buf = io.StringIO()
_old_stdout = sys.stdout
sys.stdout = _buf
try:
{indented}
except Exception as _e:
    print(f"Error: {{_e}}")
sys.stdout = _old_stdout
_output = _buf.getvalue()

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        html = (
            "<!DOCTYPE html><html><head><title>SandPy App</title>"
            "<style>body{{font-family:monospace;background:#0a0a0a;color:#e0e0e0;padding:2rem;}}"
            "pre{{background:#111;padding:1rem;border-radius:8px;border:1px solid #333;white-space:pre-wrap;}}</style>"
            "</head><body><h2>SandPy Deployment Output</h2><pre>"
            + _output.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            + "</pre></body></html>"
        )
        self.wfile.write(html.encode())
    def log_message(self, *args):
        pass

print(f"Serving on 0.0.0.0:{port}")
HTTPServer(("0.0.0.0", {port}), _Handler).serve_forever()
'''


@app.post("/services/start")
async def start_service(req: StartServiceRequest):
    if req.service_id in active_services:
        raise HTTPException(status_code=400, detail="Service ID already running")

    # Enforce 1 service per worker to avoid port conflict simply.
    if active_services:
        raise HTTPException(status_code=409, detail="Worker already has an active service. Please queue.")

    deploy_code = wrap_code_with_server(req.code, req.port)

    service_dir = os.path.join(BASE_FOLDER, "services", req.user_id, req.service_id)
    os.makedirs(service_dir, exist_ok=True)
    file_path = os.path.join(service_dir, "app.py")
    with open(file_path, "w") as f:
        f.write(deploy_code)

    log_path = os.path.join(service_dir, "output.log")
    try:
        log_file = open(log_path, "w")
        process = subprocess.Popen(
            ["python", "app.py"],
            cwd=service_dir,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

        # Brief check — give the process a moment to crash on import/syntax errors
        import time
        time.sleep(0.5)
        if process.poll() is not None:
            log_file.close()
            with open(log_path) as f:
                error_output = f.read()
            raise HTTPException(status_code=500, detail=f"Service crashed on startup: {error_output[:500]}")

        active_services[req.service_id] = {
            "process": process,
            "port": req.port,
            "dir": service_dir,
            "user_id": req.user_id,
            "log_file": log_file,
        }
        return {"status": "started", "service_id": req.service_id, "port": req.port}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/services/stop/{service_id}")
async def stop_service(service_id: str):
    if service_id not in active_services:
        raise HTTPException(status_code=404, detail="Service not found")
        
    svc = active_services[service_id]
    svc["process"].terminate()
    try:
        svc["process"].wait(timeout=5)
    except subprocess.TimeoutExpired:
        svc["process"].kill()
    if "log_file" in svc:
        try:
            svc["log_file"].close()
        except Exception:
            pass
    del active_services[service_id]
    return {"status": "stopped", "service_id": service_id}

@app.get("/services")
async def list_services_on_worker(user_id: Optional[str] = None):
    result = []
    for sid, svc in active_services.items():
        if not user_id or svc["user_id"] == user_id:
            result.append({
                "service_id": sid,
                "port": svc["port"],
                "worker_id": WORKER_ID,
                "user_id": svc["user_id"],
                "status": "running" if svc["process"].poll() is None else "stopped",
            })
    return {"services": result}
