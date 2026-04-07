import urllib.request
import json
import uuid
import time
import os

user_id = str(uuid.uuid4())
code = """
import http.server
import socketserver

PORT = 8000
Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    httpd.serve_forever()
"""

# POST /services/start
try:
    data = json.dumps({"user_id": user_id, "code": code}).encode("utf-8")
    req = urllib.request.Request("http://localhost:5002/services/start", data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    res_data = json.loads(resp.read().decode())
    print("DEPLOYED:", res_data)
except Exception as e:
    print("ERROR:", e.read().decode())
    exit(1)

# Wait 2 seconds for server to start
time.sleep(2)

# Check localhost:8001
try:
    resp = urllib.request.urlopen("http://localhost:8001")
    print("WEB SERVER RUNNING:", resp.status)
except Exception as e:
    print("WEB SERVER FAIL:", str(e))

# DELETE /services/stop
try:
    req = urllib.request.Request(f"http://localhost:5002/services/stop/{res_data['service_id']}", method="DELETE")
    resp = urllib.request.urlopen(req)
    print("STOPPED:", resp.read().decode())
except Exception as e:
    print("STOP FAIL:", str(e))

# Check list
try:
    resp = urllib.request.urlopen(f"http://localhost:5002/services?user_id={user_id}")
    print("SERVICES:", resp.read().decode())
except Exception as e:
    print("LIST FAIL:", str(e))
