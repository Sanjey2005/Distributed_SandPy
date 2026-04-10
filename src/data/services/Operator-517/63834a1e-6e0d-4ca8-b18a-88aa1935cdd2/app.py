from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

app = FastAPI()

class Task(BaseModel):
    id: int
    title: str
    description: str
    done: bool

tasks = [
    {"id": 1, "title": "Task 1", "description": "This is task 1", "done": False},
    {"id": 2, "title": "Task 2", "description": "This is task 2", "done": False},
]

@app.get("/tasks/")
async def read_tasks():
    return tasks

@app.get("/tasks/{task_id}")
async def read_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            return task
    raise HTTPException(status_code=404, detail="Task not found")

@app.post("/tasks/")
async def create_task(task: Task):
    tasks.append(task.dict())
    return task

@app.put("/tasks/{task_id}")
async def update_task(task_id: int, task: Task):
    for t in tasks:
        if t["id"] == task_id:
            t["title"] = task.title
            t["description"] = task.description
            t["done"] = task.done
            return task
    raise HTTPException(status_code=404, detail="Task not found")

@app.delete("/tasks/{task_id}")
async def delete_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            tasks.remove(task)
            return {"message": "Task deleted"}
    raise HTTPException(status_code=404, detail="Task not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)