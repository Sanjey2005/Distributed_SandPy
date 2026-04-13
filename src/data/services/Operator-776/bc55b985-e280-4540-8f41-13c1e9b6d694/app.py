from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import pandas as pd

app = FastAPI()

# Initialize to-do list
todo_list = []

# Define HTML template for the to-do app
html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>To-Do App</title>
    <style>
        body {
            font-family: Arial, sans-serif;
        }
        .container {
            width: 80%;
            margin: 40px auto;
            text-align: center;
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
        .todo-item input[type="checkbox"] {
            margin-right: 10px;
        }
        .add-todo {
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>To-Do App</h1>
        <ul class="todo-list">
            {% for item in todo_list %}
            <li class="todo-item">
                <input type="checkbox" id="{{ item['id'] }}" {% if item['completed'] %}checked{% endif %}>
                <label for="{{ item['id'] }}">{{ item['text'] }}</label>
            </li>
            {% endfor %}
        </ul>
        <form class="add-todo" action="/" method="post">
            <input type="text" name="new_todo" placeholder="Add new to-do item">
            <button type="submit">Add</button>
        </form>
    </div>
</body>
</html>
"""

# Define a function to render the HTML template
def render_template(todo_list):
    from jinja2 import Template
    template = Template(html_template)
    return template.render(todo_list=todo_list)

# Define the root route
@app.get("/")
def read_root(request: Request):
    return HTMLResponse(render_template(todo_list))

# Define the route for adding new to-do items
@app.post("/")
def add_todo(request: Request, new_todo: str = Form(...)):
    global todo_list
    todo_list.append({"id": len(todo_list), "text": new_todo, "completed": False})
    return HTMLResponse(render_template(todo_list))

# Run the app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)