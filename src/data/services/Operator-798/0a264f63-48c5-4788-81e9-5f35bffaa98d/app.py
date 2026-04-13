from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import pandas as pd

app = FastAPI()

# Initialize to-do list
todo_list = []

# HTML template for the to-do app
html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>To-Do App</title>
    <style>
        body {
            font-family: Arial, sans-serif;
        }
        .todo-list {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        .todo-item {
            padding: 10px;
            border-bottom: 1px solid #ccc;
        }
        .todo-item:last-child {
            border-bottom: none;
        }
        .done {
            text-decoration: line-through;
        }
    </style>
</head>
<body>
    <h1>To-Do App</h1>
    <form action="/" method="post">
        <input type="text" name="task" placeholder="Enter a task">
        <button type="submit">Add Task</button>
    </form>
    <ul class="todo-list">
    {}
    </ul>
    <script>
        // Add event listener to delete buttons
        const deleteButtons = document.querySelectorAll('.delete-button');
        deleteButtons.forEach(button => {
            button.addEventListener('click', event => {
                const taskId = button.dataset.taskId;
                fetch('/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    body: `action=delete&task_id=${taskId}`
                })
                .then(response => response.text())
                .then(() => window.location.reload());
            });
        });
    </script>
</body>
</html>
"""

# Route for the root URL
@app.get("/")
def read_root():
    return HTMLResponse(html_template.format("".join([f"<li class='todo-item'><input type='checkbox' id='task-{i}'><label for='task-{i}'>{task}</label><button class='delete-button' data-task-id='{i}'>Delete</button></li>" for i, task in enumerate(todo_list)])))

# Route for handling form submissions
@app.post("/")
def create_task(request: Request, task: str = Form(...), action: str = Form(None), task_id: str = Form(None)):
    if action == "delete":
        todo_list.pop(int(task_id))
    else:
        todo_list.append(task)
    return HTMLResponse(html_template.format("".join([f"<li class='todo-item'><input type='checkbox' id='task-{i}'><label for='task-{i}'>{task}</label><button class='delete-button' data-task-id='{i}'>Delete</button></li>" for i, task in enumerate(todo_list)])))

# Run the app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)