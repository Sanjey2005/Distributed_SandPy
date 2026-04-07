import urllib.request
import json
import uuid

user_id = str(uuid.uuid4())

# Start Session
try:
    req = urllib.request.Request("http://localhost:5002/start_session", data=f"user_id={user_id}".encode("utf-8"))
    resp = urllib.request.urlopen(req)
    print("SESSION:", resp.read().decode())
except Exception as e:
    print("SESSION ERROR:", e.read().decode())

# Execute Code
try:
    data = json.dumps({"user_id": user_id, "code": "print('Hello World')"}).encode("utf-8")
    req = urllib.request.Request("http://localhost:5002/execute", data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    print("EXECUTE:", resp.read().decode())
except Exception as e:
    print("EXECUTE ERROR:", e.read().decode())
