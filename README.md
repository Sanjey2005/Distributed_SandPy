# Sandboxed Jupyter Code Execution

A FastAPI-powered application that enables Python code execution in a sandboxed environment. Each user session initializes a new Jupyter notebook kernel, ensuring isolated and stateful code execution. This project is containerized using Docker, making it easy to deploy and run anywhere.

## Features

- **FastAPI API for Code Execution**: Execute Python code securely through RESTful APIs.
- **Session Isolation**: Each user gets a dedicated Jupyter kernel, ensuring isolated execution environments.
- **Dynamic Package Management**: Install additional Python packages during runtime.
- **Stateful Execution**: Code execution is stateful within each session, allowing variable persistence across API calls.
- **Resource Cleanup**: Automatic cleanup of inactive sessions to optimize resource usage.

---

## Prerequisites

- Docker installed on your system.
- Python 3.10+ (if running locally without Docker).

---

## Installation and Setup

### 1. Clone the Repository

```bash
git clone https://github.com/anukriti-ranjan/sandboxed-jupyter-code-exec.git
cd sandboxed-jupyter-code-exec
```

### 2. Build the Docker Image

```bash
docker build -t sandboxed-jupyter-code-exec .
```

### 3. Run the Docker Container

```bash
docker run -d -p 5002:5000 \
    -v $(pwd)/data:/mnt/data \
    -v $(pwd)/jupyter_sessions:/mnt/jupyter_sessions \
    sandboxed-jupyter-code-exec
```

- You can change the port according to what you prefer

### 4. Verify the API is Running

- The FastAPI server will be accessible at http://localhost:5002. You can check the interactive API documentation at http://localhost:5002/docs.

## API Endpoints

### 1. Start a New Session

#### Endpoint: /start_session
##### Method: POST
##### Parameters:
- user_id (Form Data): A unique identifier for the user.

```bash
curl -X POST http://localhost:5002/start_session -F "user_id=user_test"
```

##### Response
```json
{
    "message": "Session started successfully",
    "notebook_path": "/mnt/jupyter_sessions/user_test/notebook_user_test.ipynb"
}
```

### 2. Execute Code

#### Endpoint: /execute
##### Method: POST
##### Parameters:
- user_id (JSON): User session identifier.
- code (JSON): Python code to execute.

```bash
curl -X POST http://localhost:5002/execute \
    -H "Content-Type: application/json" \
    -d '{
        "user_id": "user_test",
        "code": "print(\"Hello, World!\")"
    }'
```

##### Response
```json
{
    "output": "Hello, World!\n"
}
```

### 3. Install a Python Package

#### Endpoint: /install_package
##### Method: POST
##### Parameters:
- user_id (JSON): User session identifier.
- package_name (JSON): Name of the package to install.

```bash
curl -X POST http://localhost:5002/install_package \
    -H "Content-Type: application/json" \
    -d '{
        "user_id": "user_test",
        "package_name": "requests"
    }'
```

##### Response
```json
{
    "message": "Successfully installed and imported requests",
    "output": "Package installation logs..."
}
```

### 4. Reset a Session

#### Endpoint: /reset
##### Method: POST
##### Parameters:
- user_id (Form Data): User session identifier.

```bash
curl -X POST http://localhost:5002/reset -F "user_id=user_test"
```

##### Response
```json
{
    "message": "Kernel reset successful"
}
```

### 5. End a Session

#### Endpoint: /end_session
##### Method: POST
##### Parameters:
- user_id (Form Data): User session identifier.

```bash
curl -X POST http://localhost:5002/end_session -F "user_id=user_test"
```

##### Response
```json
{
    "message": "Session ended successfully"
}
```

## Folder Structure
- /data: Mount this folder for input datasets. Example: Place your CSV files here.
- /jupyter_sessions: Mount this folder to store session notebooks.
- /workspace: Contains the application code.

##### Example Volumes to Mount
- Local Folder: /data
    - Mount Point: /mnt/data
- Local Folder: /jupyter_sessions
    - Mount Point: /mnt/jupyter_sessions


## Testing the API

A simple Python script (test_api.py) is included to test the API functionality. Run it as follows:

```bash
python3 test_api.py
```

Ensure the Docker container is running on port 5002 before executing the script.


## Notes
- Security: The API is designed for local or controlled environments. Add proper authentication mechanisms if deploying in production.
- Session Management: Inactive sessions are automatically cleaned up after 1 hour.
- Data Persistence: Notebooks and outputs are saved in the mounted volumes for persistence.


## Contributions


Feel free to submit pull requests or open issues for enhancements and bug fixes. 
Contributions are always welcome!

## License

This project is licensed under the MIT License.

---










