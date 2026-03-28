from fastapi import FastAPI, UploadFile, Form, HTTPException
import subprocess
from pydantic import BaseModel
import os
import queue
import asyncio
from jupyter_client import KernelManager
import nbformat
from nbformat.v4 import new_notebook, new_code_cell
import time
from typing import Dict, Optional
import redis
import json

WORKER_ID = os.getenv("WORKER_ID", "worker1")
WORKER_PORT = os.getenv("WORKER_PORT", "5000")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

app = FastAPI()

BASE_FOLDER = f"/mnt/data/{WORKER_ID}"
SESSIONS_FOLDER = f"/mnt/jupyter_sessions/{WORKER_ID}"

r = None

def get_redis():
    global r
    if r is None:
        r = redis.from_url(REDIS_URL, decode_responses=True)
    return r

class JupyterController:
    def __init__(self, folder_path):
        self.folder_path = folder_path
        self.notebook_path = None
        self.kernel_manager = None
        self.kernel_client = None
        self._kernel_ready = False

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
                pass
            
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

    async def execute_code(self, code):
        if not self._kernel_ready:
            raise RuntimeError("Kernel not ready. Please wait for initialization or restart session.")

        if not self.kernel_manager.is_alive():
            self._kernel_ready = False
            raise RuntimeError("Kernel died. Please restart session.")

        self._clear_output_queue()

        msg_id = self.kernel_client.execute(code)
        outputs = []
        error_encountered = False

        while True:
            try:
                msg = self.kernel_client.get_iopub_msg(timeout=120)
                msg_type = msg['header']['msg_type']
                content = msg['content']

                if msg_type == 'stream':
                    outputs.append(content['text'])
                elif msg_type == 'execute_result':
                    outputs.append(str(content['data'].get('text/plain', '')))
                elif msg_type == 'display_data':
                    text_data = content['data'].get('text/plain', '')
                    if text_data:
                        outputs.append(str(text_data))
                elif msg_type == 'error':
                    error_encountered = True
                    error_msg = '\n'.join(content['traceback'])
                    raise HTTPException(
                        status_code=400,
                        detail={"error": "Execution error", "traceback": content['traceback']}
                    )
                elif msg_type == 'status' and content['execution_state'] == 'idle':
                    if not error_encountered:
                        break

            except queue.Empty:
                raise HTTPException(
                    status_code=408,
                    detail="Code execution timed out"
                )

        return '\n'.join(outputs) if outputs else ""

    async def reset_kernel(self):
        if self.kernel_manager:
            self._kernel_ready = False
            self.kernel_manager.restart_kernel()
            await self._wait_for_kernel_ready()
            self._clear_output_queue()

    def cleanup(self):
        if self.kernel_client:
            self.kernel_client.stop_channels()
        if self.kernel_manager:
            self.kernel_manager.shutdown_kernel(now=True)
        if self.notebook_path and os.path.exists(self.notebook_path):
            os.remove(self.notebook_path)

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

async def cleanup_inactive_sessions():
    while True:
        current_time = time.time()
        to_remove = []
        
        for user_id, session_info in sessions.items():
            if current_time - session_info.last_activity > 3600:
                to_remove.append(user_id)
        
        for user_id in to_remove:
            session_info = sessions.pop(user_id)
            session_info.controller.cleanup()
            try:
                redis_conn = get_redis()
                redis_conn.delete(f"session:{user_id}")
            except Exception as e:
                print(f"Failed to cleanup Redis session: {e}")
        
        await asyncio.sleep(300)

async def register_worker():
    try:
        redis_conn = get_redis()
        worker_data = json.dumps({
            "worker_id": WORKER_ID,
            "port": WORKER_PORT,
            "status": "online"
        })
        redis_conn.set(f"worker:{WORKER_ID}", worker_data, ex=3600)
        print(f"Worker {WORKER_ID} registered in Redis")
    except Exception as e:
        print(f"Failed to register worker in Redis: {e}")

async def unregister_worker():
    try:
        redis_conn = get_redis()
        redis_conn.delete(f"worker:{WORKER_ID}")
        print(f"Worker {WORKER_ID} unregistered from Redis")
    except Exception as e:
        print(f"Failed to unregister worker: {e}")

@app.on_event("startup")
async def startup_event():
    os.makedirs(BASE_FOLDER, exist_ok=True)
    os.makedirs(SESSIONS_FOLDER, exist_ok=True)
    asyncio.create_task(cleanup_inactive_sessions())
    await register_worker()

@app.on_event("shutdown")
async def shutdown_event():
    await unregister_worker()
    for user_id, session_info in list(sessions.items()):
        session_info.controller.cleanup()

@app.get("/health")
async def health_check():
    return {
        "worker_id": WORKER_ID,
        "status": "online",
        "sessions": len(sessions)
    }

async def get_session(user_id: str) -> SessionInfo:
    if user_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found. Please start a new session.")
    
    session_info = sessions[user_id]
    session_info.last_activity = time.time()
    
    if not session_info.controller._kernel_ready:
        try:
            await session_info.controller._wait_for_kernel_ready(timeout=10)
        except TimeoutError:
            await session_info.controller.reset_kernel()
    
    return session_info

@app.post("/start_session")
async def start_session(user_id: str = Form(...)):
    redis_conn = get_redis()
    
    existing_worker = redis_conn.get(f"session:{user_id}")
    if existing_worker and existing_worker != WORKER_ID:
        raise HTTPException(
            status_code=409,
            detail=f"Session for user {user_id} exists on {existing_worker}"
        )
    
    if user_id in sessions:
        sessions[user_id].controller.cleanup()
    
    session_folder = os.path.join(SESSIONS_FOLDER, user_id)
    controller = JupyterController(session_folder)
    
    try:
        notebook_path = await controller.create_notebook(f"notebook_{user_id}")
        sessions[user_id] = SessionInfo(controller, time.time())
        
        setup_code = """
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
"""
        await controller.execute_code(setup_code)
        
        redis_conn.set(f"session:{user_id}", WORKER_ID, ex=3600)
        
        return {
            "message": "Session started successfully",
            "notebook_path": notebook_path
        }
    except Exception as e:
        controller.cleanup()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/execute")
async def execute_code(request: ExecuteRequest):
    session_info = await get_session(request.user_id)
    
    try:
        output = await session_info.controller.execute_code(request.code)
        return {"output": output}
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300
        )
        
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to install {request.package_name}: {result.stderr}"
            )
        
        import_code = f"import {request.package_name.split('[')[0]}"
        await session_info.controller.execute_code(import_code)
        
        return {
            "message": f"Successfully installed and imported {request.package_name}",
            "output": result.stdout
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=500,
            detail=f"Package installation timed out for {request.package_name}"
        )
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
import matplotlib.pyplot as plt
"""
        await session_info.controller.execute_code(setup_code)
        
        return {"message": "Kernel reset successful"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/end_session")
async def end_session(user_id: str = Form(...)):
    if user_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session_info = sessions.pop(user_id)
    session_info.controller.cleanup()
    
    try:
        redis_conn = get_redis()
        redis_conn.delete(f"session:{user_id}")
    except Exception as e:
        print(f"Failed to cleanup Redis session: {e}")
    
    return {"message": "Session ended successfully"}
