import pandas as pd
import numpy as np
import matplotlib
import sys
import threading
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

tasks = {
    1: {"title": "Task 1", "due_date": "2024-03-16", "priority": "High"},
    2: {"title": "Task 2", "due_date": "2024-03-17", "priority": "Medium"},
    3: {"title": "Task 3", "due_date": "2024-03-18", "priority": "Low"}
}

users = {
    "user1": "password1",
    "user2": "password2"
}

@app.route('/tasks', methods=['POST'])
def create_task():
    data = request.json
    task_id = max(tasks.keys()) + 1 if tasks else 1
    tasks[task_id] = {
        "title": data["title"],
        "due_date": data["due_date"],
        "priority": data["priority"]
    }
    return jsonify({"task_id": task_id}), 201

@app.route('/tasks', methods=['GET'])
def get_tasks():
    return jsonify(tasks)

@app.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id):
    if task_id in tasks:
        return jsonify(tasks[task_id])
    else:
        return jsonify({"error": "Task not found"}), 404

@app.route('/tasks/<int:task_id>', methods=['PUT'])
def update_task(task_id):
    if task_id in tasks:
        data = request.json
        tasks[task_id] = {
            "title": data["title"],
            "due_date": data["due_date"],
            "priority": data["priority"]
        }
        return jsonify(tasks[task_id])
    else:
        return jsonify({"error": "Task not found"}), 404

@app.route('/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    if task_id in tasks:
        del tasks[task_id]
        return jsonify({"message": "Task deleted"})
    else:
        return jsonify({"error": "Task not found"}), 404

def send_reminders():
    current_date = datetime.now().strftime("%Y-%m-%d")
    for task_id, task in tasks.items():
        if task["due_date"] == current_date:
            print(f"Reminder: {task['title']} is due today")

def prioritize_tasks():
    prioritized_tasks = sorted(tasks.items(), key=lambda x: ["Low", "Medium", "High"].index(x[1]["priority"]))
    return prioritized_tasks

def run_app():
    app.run(host='0.0.0.0', port=8000, use_reloader=False)

if __name__ == '__main__':
    print("To-do app started. Use the API endpoints to manage tasks.")
    print("Example usage:")
    print("Create task: curl -X POST -H 'Content-Type: application/json' -d '{\"title\": \"New Task\", \"due_date\": \"2024-03-20\", \"priority\": \"High\"}' http://localhost:8000/tasks")
    print("Get all tasks: curl -X GET http://localhost:8000/tasks")
    print("Get task by id: curl -X GET http://localhost:8000/tasks/1")
    print("Update task: curl -X PUT -H 'Content-Type: application/json' -d '{\"title\": \"Updated Task\", \"due_date\": \"2024-03-21\", \"priority\": \"Medium\"}' http://localhost:8000/tasks/1")
    print("Delete task: curl -X DELETE http://localhost:8000/tasks/1")
    import threading
    threading.Thread(target=run_app).start()
    while True:
        send_reminders()
        prioritized_tasks = prioritize_tasks()
        print("Prioritized tasks:")
        for task_id, task in prioritized_tasks:
            print(f"Task ID: {task_id}, Title: {task['title']}, Due Date: {task['due_date']}, Priority: {task['priority']}")
        import time
        time.sleep(60)