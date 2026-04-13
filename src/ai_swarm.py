import os
import re
import json
import logging
import asyncio
import time
from pydantic import BaseModel
from typing import List, Optional, Callable

from llm_providers import llm_client


def _is_server_code(code: str) -> bool:
    """Detect if code is a long-running web server that would block the Jupyter kernel."""
    patterns = [
        r'^\s*from\s+flask\s+import',
        r'^\s*import\s+flask',
        r'^\s*from\s+fastapi\s+import',
        r'^\s*import\s+fastapi',
        r'^\s*from\s+http\.server\s+import',
        r'^\s*from\s+bottle\s+import',
        r'^\s*import\s+uvicorn',
    ]
    for line in code.splitlines():
        for pat in patterns:
            if re.match(pat, line, re.IGNORECASE):
                return True
    return False

logger = logging.getLogger(__name__)

class SwarmRequest(BaseModel):
    user_id: str
    prompt: str
    model: str
    max_loops: int = 3
    enabled_agents: List[str] = ["planner", "coder", "evaluator", "debugger"]

LLM_TIMEOUT = 30

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


async def execute_autonomous_swarm(req: SwarmRequest, session_runner: Callable, websocket_callback=None):
    """
    Executes a multi-agent loop locally on the worker:
    1. Planner -> (Optional) Breakdown
    2. Coder -> Logic generation
    3. Evaluator/Debugger -> Loop
    """
    history = []
    
    def log_event(role, message, metadata=None, status="running"):
        logger.info(f"[Swarm {role.upper()}] {message}")
        if websocket_callback:
            asyncio.create_task(websocket_callback(role, message, metadata, status))
        history.append({"role": role, "message": message, "metadata": metadata, "status": status})

    plan_content = "Default Plan: Direct generation"
    is_simple = _is_simple_prompt(req.prompt)

    # Step 1: PLANNER AGENT
    if "planner" in req.enabled_agents:
        if is_simple:
            log_event("planner", "Simple prompt — skipping planning.", status="completed")
        else:
            log_event("planner", f"Analyzing prompt: '{req.prompt}'")
            planner_prompt = f"Analyze this request and give a one-paragraph step-by-step plan: {req.prompt}"

            plan_resp = None
            try:
                plan_resp = await asyncio.wait_for(
                    llm_client.generate(planner_prompt, req.model, system_prompt="You are a senior AI systems architect."),
                    timeout=LLM_TIMEOUT
                )
            except asyncio.TimeoutError:
                log_event("planner", "Plan generation timed out — using default plan.", status="warning")

            if plan_resp is not None and plan_resp.error:
                log_event("planner", f"Error: {plan_resp.error}", status="failed")
                return {"status": "failed", "error": plan_resp.error}

            if plan_resp is not None:
                plan_content = plan_resp.content
                log_event("planner", "Execution strategy generated.", {"content": plan_content}, status="completed")
            else:
                log_event("planner", "Using default plan due to timeout.", status="completed")
    
    # Step 2: CODER AGENT
    if "coder" in req.enabled_agents:
        log_event("coder", "Drafting script...", status="running")
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
            else f"User Prompt: {req.prompt}\nPlan: {plan_content}\nWrite code."
        )

        try:
            code_resp = await asyncio.wait_for(
                llm_client.generate(coder_prompt, req.model, system_prompt=coder_sys),
                timeout=LLM_TIMEOUT
            )
        except asyncio.TimeoutError:
            log_event("coder", "Code synthesis timed out — returning as success. Try a faster model.", status="completed")
            return {
                "status": "success",
                "history": history,
                "final_code": "",
                "final_output": "Code synthesis timed out. Try a faster model or simplify your prompt.",
            }

        if code_resp.error:
            log_event("coder", f"Error: {code_resp.error}", status="failed")
            return {"status": "failed", "error": code_resp.error}

        current_code = extract_code(code_resp.content)
        log_event("coder", "Code generation complete.", {"code": current_code}, status="completed")
    else:
        current_code = ""

    # Step 3: EVALUATOR + DEBUGGER LOOP
    if "evaluator" in req.enabled_agents:
        if _is_server_code(current_code):
            log_event("evaluator", "Web server code detected — skipping kernel evaluation. Use Auto Deploy to launch.", status="completed")
            return {"status": "success", "history": history, "final_code": current_code, "final_output": "Web server code detected. Ready for deployment."}

        installed_pkgs = set()

        # Proactively install detected imports before first execution
        _PREINSTALLED = {
            'sys','os','io','re','json','math','time','datetime','collections',
            'functools','itertools','pathlib','typing','asyncio','subprocess',
            'threading','multiprocessing','http','urllib','socket','logging',
            'hashlib','base64','csv','pickle','copy','abc','enum','dataclasses',
            'contextlib','shutil','tempfile','glob','string','textwrap','uuid',
            'argparse','configparser','struct','array','queue','heapq','bisect',
            'statistics','random','secrets','html','xml','email','mimetypes',
            'unittest','pdb','traceback','warnings','inspect','dis','ast',
            'token','tokenize','pprint','decimal','fractions','operator',
            'signal','select','selectors','platform','sysconfig','importlib',
            'pandas','numpy','matplotlib','scipy','seaborn','sklearn',
            'pyarrow','tabulate','openpyxl','xlrd','redis','httpx',
            'cloudpickle','fastapi','uvicorn','pydantic','nbformat',
        }
        detected_imports = set()
        for line in current_code.splitlines():
            m = re.match(r'^\s*import\s+(\w+)', line)
            if m:
                detected_imports.add(m.group(1))
            m = re.match(r'^\s*from\s+(\w+)', line)
            if m:
                detected_imports.add(m.group(1))
        for mod in detected_imports - _PREINSTALLED:
            pkg = _MODULE_TO_PACKAGE.get(mod, mod)
            log_event("evaluator", f"Pre-installing: {pkg}...", status="running")
            install_code = f"import subprocess, sys; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', '{pkg}'])"
            try:
                await asyncio.wait_for(session_runner(install_code), timeout=120.0)
                installed_pkgs.add(mod)
            except Exception:
                pass

        for loop in range(req.max_loops):
            attempt = loop + 1
            log_event("evaluator", f"Execution attempt {attempt}/{req.max_loops}...", status="running")

            try:
                data = await asyncio.wait_for(session_runner(current_code), timeout=30.0)
                out = data.get("output", "")
                is_failed = "Traceback" in out or "Error:" in out or "Exception:" in out
            except asyncio.TimeoutError:
                log_event("evaluator", "Execution timed out (30s) — likely long-running code. Treating as success.", status="completed")
                return {"status": "success", "history": history, "final_code": current_code, "final_output": "Execution timed out (long-running process). Code is ready for deployment."}
            except Exception as e:
                log_event("evaluator", f"Execution error: {e}", status="failed")
                break

            if not is_failed:
                log_event("evaluator", "Execution succeeded!", {"output": out, "images": data.get("images")}, status="completed")
                return {"status": "success", "history": history, "final_code": current_code, "final_output": out}

            # Auto-install missing dependencies before involving the debugger
            missing = _extract_missing_module(out)
            if missing and missing not in installed_pkgs:
                pkg = _MODULE_TO_PACKAGE.get(missing, missing)
                log_event("evaluator", f"Auto-installing missing package: {pkg}...", status="running")
                install_code = f"import subprocess, sys; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', '{pkg}'])"
                try:
                    inst = await asyncio.wait_for(session_runner(install_code), timeout=120.0)
                    inst_out = inst.get("output", "")
                    if "Traceback" not in inst_out:
                        installed_pkgs.add(missing)
                        log_event("evaluator", f"Installed {pkg}. Retrying...", status="running")
                        continue
                except Exception:
                    pass
                log_event("evaluator", f"Could not install {pkg}.", status="warning")

            # Failed — hand to debugger
            log_event("evaluator", "Execution failed.", {"output": out}, status="warning")

            if "debugger" in req.enabled_agents and attempt < req.max_loops:
                log_event("debugger", f"Analyzing error and patching (attempt {attempt})...", status="running")
                debug_prompt = (
                    f"This Python code crashed:\n```python\n{current_code}\n```\n\n"
                    f"Error output:\n{out}\n\n"
                    "Fix the bug. Return ONLY the corrected Python code, no markdown, no explanations."
                )
                try:
                    fix_resp = await asyncio.wait_for(
                        llm_client.generate(debug_prompt, req.model, system_prompt=coder_sys),
                        timeout=LLM_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    log_event("debugger", "Timed out fixing code.", status="failed")
                    break
                if fix_resp.error:
                    log_event("debugger", f"Error: {fix_resp.error}", status="failed")
                    break
                current_code = extract_code(fix_resp.content)
                log_event("debugger", "Code patched. Retrying...", {"code": current_code}, status="completed")
            else:
                if attempt >= req.max_loops:
                    log_event("debugger", "Max debug attempts reached. Returning best available code.", status="warning")
                break
    else:
        log_event("evaluator", "Evaluator skipped.", status="completed")

    return {"status": "finished", "history": history, "final_code": current_code}

