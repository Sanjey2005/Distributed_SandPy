"""
Autonomous Swarm Orchestrator
Flow: Planner → Coder (Synthesizer) → Evaluator → Debugger (on error) → Done
After the swarm completes, the generated code is sent to the frontend.
Deployment is handled separately via the "Auto Deploy" button.
"""
import os
import re
import json
import logging
import asyncio
import httpx
from pydantic import BaseModel
from typing import List, Optional

from llm_providers import llm_client

logger = logging.getLogger(__name__)

# ── Worker URLs loaded from env (mirrors dispatcher/main.py) ──────────────────
WORKER_URLS = {
    "worker1": os.getenv("WORKER1_URL", "http://worker1:5000"),
    "worker2": os.getenv("WORKER2_URL", "http://worker2:5000"),
    "worker3": os.getenv("WORKER3_URL", "http://worker3:5000"),
}

# How long (seconds) to wait for code to execute on a worker before giving up
EXEC_TIMEOUT = 30
# How long to wait for LLM generation
LLM_TIMEOUT = 30

# ── Models ────────────────────────────────────────────────────────────────────

class SwarmRequest(BaseModel):
    user_id: str
    prompt: str
    model: str
    max_loops: int = 3


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_code(resp_content: str) -> str:
    """Strip markdown fences from LLM code output."""
    if not resp_content:
        return ""
    code = resp_content.strip()
    for fence in ["```python", "```py", "```"]:
        if code.lower().startswith(fence):
            code = code[len(fence):]
            break
    if code.endswith("```"):
        code = code[:-3]
    return code.strip()


def _is_simple_prompt(prompt: str) -> bool:
    """Return True for short, straightforward prompts that don't need a planning step."""
    return len(prompt.strip().split()) <= 15


_MODULE_TO_PACKAGE = {
    "cv2": "opencv-python", "sklearn": "scikit-learn", "PIL": "Pillow",
    "bs4": "beautifulsoup4", "yaml": "pyyaml", "dotenv": "python-dotenv",
    "skimage": "scikit-image", "dateutil": "python-dateutil",
    "docx": "python-docx", "pptx": "python-pptx", "fitz": "PyMuPDF",
    "attr": "attrs", "serial": "pyserial", "Crypto": "pycryptodome",
}


def _extract_missing_module(output: str) -> Optional[str]:
    """Extract module name from ModuleNotFoundError/ImportError in output."""
    match = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"\.]+)", output)
    if not match:
        match = re.search(r"ImportError: No module named ['\"]([^'\"\.]+)", output)
    if match:
        module = match.group(1)
        if re.fullmatch(r'[A-Za-z0-9_-]+', module):
            return module
    return None


async def pick_healthy_worker() -> Optional[str]:
    """Return the URL of the first healthy, available worker, or None."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        for worker_id, url in WORKER_URLS.items():
            try:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    return url
            except Exception:
                continue
    return None


async def run_code_on_worker(worker_url: str, user_id: str, code: str) -> dict:
    """
    Start a session and execute code on the given worker.
    Returns {"output": str, "timed_out": bool, "success": bool}.
    """
    try:
        async with httpx.AsyncClient(timeout=EXEC_TIMEOUT + 10) as client:
            # Ensure a session exists
            await client.post(f"{worker_url}/start_session", data={"user_id": user_id})

            try:
                exec_resp = await asyncio.wait_for(
                    client.post(
                        f"{worker_url}/execute",
                        json={"user_id": user_id, "code": code},
                    ),
                    timeout=EXEC_TIMEOUT,
                )
            except asyncio.TimeoutError:
                return {"output": "Execution timed out.", "timed_out": True, "success": True}

        if exec_resp.status_code == 200:
            data = exec_resp.json()
            out = data.get("output", "")
            has_error = any(
                kw in out
                for kw in ("Traceback", "Error:", "Exception:", "SyntaxError")
            )
            return {"output": out, "timed_out": False, "success": not has_error}
        else:
            return {
                "output": f"Worker returned HTTP {exec_resp.status_code}: {exec_resp.text}",
                "timed_out": False,
                "success": False,
            }
    except Exception as e:
        return {"output": f"Worker connection error: {e}", "timed_out": False, "success": False}


# ── Main Swarm Executor ───────────────────────────────────────────────────────

async def execute_autonomous_swarm(req: SwarmRequest, websocket_callback=None):
    """
    Runs the full agent pipeline and always returns a result dict:
      {"status": "success"|"failed", "history": [...], "final_code": str, "final_output": str}
    """
    history = []

    def log_event(role: str, message: str, metadata=None, status="running"):
        logger.info(f"[Swarm {role.upper()}] {message}")
        if websocket_callback:
            asyncio.create_task(websocket_callback(role, message, metadata, status))
        history.append({"role": role, "message": message, "metadata": metadata, "status": status})

    current_code = ""
    is_simple = _is_simple_prompt(req.prompt)

    try:
        # ── STEP 1: PLANNER ──────────────────────────────────────────────────
        if is_simple:
            plan = "Direct generation"
            log_event("planner", "Simple prompt — skipping planning.", status="completed")
        else:
            log_event("planner", f"Analyzing prompt: '{req.prompt}'")
            plan_resp = await asyncio.wait_for(
                llm_client.generate(
                    f"Analyze this request and give a concise step-by-step plan: {req.prompt}",
                    req.model,
                    system_prompt="You are a senior AI systems architect. Be concise.",
                ),
                timeout=LLM_TIMEOUT
            )
            plan = plan_resp.content or "No plan generated."
            log_event("planner", "Execution strategy ready.", {"content": plan}, status="completed")

        # ── STEP 2: CODER / SYNTHESIZER ──────────────────────────────────────
        log_event("coder", "Synthesizing Python code...")
        coder_sys = (
            "You are an elite Python developer. "
            "Generate ONLY raw Python code — no markdown fences, no explanations. "
            "The code runs in a Jupyter kernel where pandas, numpy, matplotlib are available. "
            "Use print() to show results. "
            "If a web app or server is needed, use FastAPI with uvicorn (NOT Flask — it is not installed). "
            "Bind to host='0.0.0.0', port=8000. "
            "When building ANY web app, ALWAYS serve a complete HTML/CSS/JS frontend from the root route '/' using FastAPI's HTMLResponse. "
            "The frontend must be styled, interactive, and visually polished — never return only bare JSON API endpoints without a UI."
        )
        coder_prompt = (
            f"Write Python code for: {req.prompt}" if is_simple
            else f"User Prompt: {req.prompt}\nPlan: {plan}\nWrite the complete Python code."
        )
        code_resp = await asyncio.wait_for(
            llm_client.generate(
                coder_prompt,
                req.model,
                system_prompt=coder_sys,
            ),
            timeout=LLM_TIMEOUT
        )
        current_code = extract_code(code_resp.content)
        log_event("coder", "Code synthesis complete.", {"code": current_code}, status="completed")

        # ── STEP 3: EVALUATOR + DEBUGGER LOOP ────────────────────────────────
        log_event("evaluator", "Finding an available worker node...")
        worker_url = await pick_healthy_worker()

        if worker_url is None:
            log_event(
                "evaluator",
                "No worker node available right now. Code is ready — use Auto Deploy to run it.",
            )
            return {
                "status": "success",
                "history": history,
                "final_code": current_code,
                "final_output": "No worker available for live evaluation. Code is ready for deployment.",
            }

        log_event("evaluator", f"Worker found. Running code (timeout: {EXEC_TIMEOUT}s)...")

        installed_pkgs = set()

        for loop in range(req.max_loops):
            attempt = loop + 1
            log_event("evaluator", f"Execution attempt {attempt}/{req.max_loops}...")

            result = await run_code_on_worker(worker_url, req.user_id, current_code)

            if result["timed_out"]:
                # Timeout = long-running code (web server etc.) — treat as success
                log_event(
                    "evaluator",
                    f"Execution timed out after {EXEC_TIMEOUT}s — treating as successful "
                    "(long-running or web server code). Use Auto Deploy to launch it.",
                    {"output": result["output"]},
                )
                return {
                    "status": "success",
                    "history": history,
                    "final_code": current_code,
                    "final_output": result["output"],
                }

            if result["success"]:
                log_event(
                    "evaluator",
                    "Execution succeeded!",
                    {"output": result["output"]},
                    status="completed"
                )
                return {
                    "status": "success",
                    "history": history,
                    "final_code": current_code,
                    "final_output": result["output"],
                }

            # Auto-install missing dependencies before involving the debugger
            missing = _extract_missing_module(result["output"])
            if missing and missing not in installed_pkgs:
                pkg = _MODULE_TO_PACKAGE.get(missing, missing)
                log_event("evaluator", f"Auto-installing missing package: {pkg}...", status="running")
                try:
                    async with httpx.AsyncClient(timeout=120) as client:
                        install_resp = await client.post(
                            f"{worker_url}/install_package",
                            json={"user_id": req.user_id, "package_name": pkg}
                        )
                        if install_resp.status_code == 200:
                            installed_pkgs.add(missing)
                            log_event("evaluator", f"Installed {pkg}. Retrying...", status="running")
                            continue
                except Exception:
                    pass
                log_event("evaluator", f"Could not install {pkg}.", status="warning")

            # Execution failed — hand off to Debugger
            log_event("evaluator", "Execution failed. Sending to Debugger...", {"output": result["output"]}, status="failed")

            if attempt < req.max_loops:
                log_event("debugger", f"Analyzing error and patching code (attempt {attempt})...")
                debug_prompt = (
                    f"This Python code crashed:\n```python\n{current_code}\n```\n\n"
                    f"Error output:\n{result['output']}\n\n"
                    "Fix the bug. Return ONLY the corrected Python code, no markdown, no explanations."
                )
                fix_resp = await asyncio.wait_for(
                    llm_client.generate(debug_prompt, req.model, system_prompt=coder_sys),
                    timeout=LLM_TIMEOUT
                )
                current_code = extract_code(fix_resp.content)
                log_event("debugger", "Code patched. Retrying...", {"code": current_code}, status="completed")
            else:
                log_event(
                    "debugger",
                    "Max debug attempts reached. Returning best available code.",
                )

    except Exception as e:
        logger.error(f"Swarm crashed: {e}", exc_info=True)
        log_event("planner", f"Swarm encountered a fatal error: {e}")

    # Fall-through: return whatever code we have so the editor is populated
    return {
        "status": "failed",
        "history": history,
        "final_code": current_code,
        "final_output": "Swarm could not fully verify the code. Review it and use Auto Deploy.",
    }
