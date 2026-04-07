import os

file_path = r"X:\Package\distributed\Distributed_SandPy\src\ai_swarm.py"
with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "def log_event(role" in line:
        line = line.replace("def log_event", "async def log_event")
    elif "asyncio.create_task(websocket_callback" in line:
        line = line.replace("asyncio.create_task(websocket_callback", "await websocket_callback")
    elif "log_event(" in line and "def " not in line:
        line = line.replace("log_event(", "await log_event(")
    new_lines.append(line)

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
