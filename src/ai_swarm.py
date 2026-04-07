import os
import json
import logging
import asyncio
import time
from pydantic import BaseModel
from typing import List, Optional, Callable

from llm_providers import llm_client

logger = logging.getLogger(__name__)

class SwarmRequest(BaseModel):
    user_id: str
    prompt: str
    model: str
    max_loops: int = 5
    enabled_agents: List[str] = ["planner", "coder", "evaluator", "debugger"]

LLM_TIMEOUT = 45

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
    
    # Step 1: PLANNER AGENT
    if "planner" in req.enabled_agents:
        log_event("planner", f"Analyzing prompt: '{req.prompt}'")
        planner_prompt = f"Analyze this request and give a one-paragraph step-by-step plan: {req.prompt}"
        
        try:
            plan_resp = await asyncio.wait_for(
                llm_client.generate(planner_prompt, req.model, system_prompt="You are a senior AI systems architect."),
                timeout=LLM_TIMEOUT
            )
        except asyncio.TimeoutError:
            log_event("planner", "Timed out generating plan.", status="failed")
            return {"status": "failed", "error": "LLM Timeout"}

        if plan_resp.error:
            log_event("planner", f"Error: {plan_resp.error}", status="failed")
            return {"status": "failed", "error": plan_resp.error}
            
        plan_content = plan_resp.content
        log_event("planner", "Execution strategy generated.", {"content": plan_content}, status="completed")
    
    # Step 2: CODER AGENT
    if "coder" in req.enabled_agents:
        log_event("coder", "Drafting script...", status="running")
        coder_sys = "You are an elite Python developer. Generate raw Python code to solve the prompt. Do not use Markdown backticks. Just output pure Python code."
        
        try:
            code_resp = await asyncio.wait_for(
                llm_client.generate(f"User Prompt: {req.prompt}\nPlan: {plan_content}\nWrite code.", req.model, system_prompt=coder_sys),
                timeout=LLM_TIMEOUT
            )
        except asyncio.TimeoutError:
            log_event("coder", "Timed out generating code.", status="failed")
            return {"status": "failed", "error": "LLM Timeout"}

        if code_resp.error:
            log_event("coder", f"Error: {code_resp.error}", status="failed")
            return {"status": "failed", "error": code_resp.error}

        current_code = extract_code(code_resp.content)
        log_event("coder", "Code generation complete.", {"code": current_code}, status="completed")
    else:
        current_code = ""

    # Step 3: EVALUATOR / DEBUGGER LOOP
    if "evaluator" in req.enabled_agents:
        log_event("evaluator", "Beginning autonomous loop...", status="running")
        
        for loop in range(req.max_loops):
            log_event("evaluator", f"Execution attempt {loop+1}/{req.max_loops}...", status="running")
            try:
                try:
                    data = await asyncio.wait_for(session_runner(current_code), timeout=30.0)
                except asyncio.TimeoutError:
                    log_event("evaluator", "Execution timed out (30s) - treating as success for long-running code.", status="completed")
                    return {"status": "success", "history": history, "final_code": current_code, "final_output": "Execution timed out (likely a web server)."}

                out = data.get("output", "")
                
                is_failed = "Traceback" in out or "Error:" in out or "Exception:" in out
                
                if is_failed and "debugger" in req.enabled_agents:
                    log_event("evaluator", "Execution failed.", {"output": out}, status="warning")
                    log_event("debugger", "Analyzing and patching...", status="running")
                    
                    debug_prompt = f"Code:\n{current_code}\nOutput:\n{out}\nFix. Output ONLY code."
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
                    log_event("debugger", "Patch complete.", {"code": current_code}, status="completed")
                    continue
                elif is_failed:
                    log_event("evaluator", "Execution failed. Debugger disabled.", {"output": out}, status="failed")
                    break
                else:
                    log_event("evaluator", "Success!", {"output": out, "images": data.get("images")}, status="completed")
                    return {"status": "success", "history": history, "final_code": current_code, "final_output": out}
            except Exception as e:
                log_event("evaluator", f"Failure: {e}", status="failed")
                break

    else:
        log_event("evaluator", "Evaluator skipped.", status="completed")

    return {"status": "finished", "history": history, "final_code": current_code}

