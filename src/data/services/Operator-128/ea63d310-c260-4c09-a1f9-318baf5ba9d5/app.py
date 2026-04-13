from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List

app = FastAPI()

class Todo(BaseModel):
    id: int
    title: str
    description: str
    done: bool

todos = [
    {"id": 1, "title": "Buy groceries", "description": "Buy milk, eggs, and bread", "done": False},
    {"id": 2, "title": "Do laundry", "description": "Wash, dry, and fold clothes", "done": False},
    {"id": 3, "title": "Clean the house", "description": "Vacuum, mop, and dust", "done": False},
]

@app.get("/todos/")
async def read_todos():
    return todos

@app.get("/todos/{todo_id}")
async def read_todo(todo_id: int):
    for todo in todos:
        if todo["id"] == todo_id:
            return todo
    raise HTTPException(status_code=404, detail="Todo not found")

@app.post("/todos/")
async def create_todo(todo: Todo):
    todos.append(todo.dict())
    return todo

@app.put("/todos/{todo_id}")
async def update_todo(todo_id: int, todo: Todo):
    for i, t in enumerate(todos):
        if t["id"] == todo_id:
            todos[i] = todo.dict()
            return todo
    raise HTTPException(status_code=404, detail="Todo not found")

@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: int):
    for i, t in enumerate(todos):
        if t["id"] == todo_id:
            del todos[i]
            return {"message": "Todo deleted"}
    raise HTTPException(status_code=404, detail="Todo not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)