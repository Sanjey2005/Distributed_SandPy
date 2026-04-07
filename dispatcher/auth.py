"""
Auth & Security Module — Phase 3
JWT authentication, resource quotas, danger code scanner
"""
import os
import time
import json
import hmac
import hashlib
import base64
from typing import Optional, Dict
from fastapi import HTTPException, Header
import redis as redis_lib

SECRET_KEY = os.getenv("JWT_SECRET", "sandpy-super-secret-change-in-production")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

try:
    r = redis_lib.from_url(REDIS_URL, decode_responses=True)
except Exception:
    r = None

# ─── Simple JWT (no external deps) ─────────────────────────────────────────

def _b64encode(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

def _b64decode(s: str) -> dict:
    pad = 4 - len(s) % 4
    return json.loads(base64.urlsafe_b64decode(s + "=" * pad))

def create_token(user_id: str, role: str = "user") -> str:
    header = _b64encode({"alg": "HS256", "typ": "JWT"})
    payload = _b64encode({"sub": user_id, "role": role, "iat": int(time.time()), "exp": int(time.time()) + 86400 * 7})
    sig_input = f"{header}.{payload}"
    sig = hmac.new(SECRET_KEY.encode(), sig_input.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{header}.{payload}.{sig_b64}"

def verify_token(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid token format")
        header, payload_b64, sig_b64 = parts
        sig_input = f"{header}.{payload_b64}"
        expected = hmac.new(SECRET_KEY.encode(), sig_input.encode(), hashlib.sha256).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
        if not hmac.compare_digest(expected_b64, sig_b64):
            raise ValueError("Invalid signature")
        payload = _b64decode(payload_b64)
        if payload.get("exp", 0) < int(time.time()):
            raise ValueError("Token expired")
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    return verify_token(token)

def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Like get_current_user but returns None instead of raising if no token."""
    if not authorization:
        return None
    try:
        return get_current_user(authorization)
    except Exception:
        return None

# ─── User Store (Redis-backed) ───────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256((password + SECRET_KEY).encode()).hexdigest()

def register_user(user_id: str, password: str, role: str = "user") -> bool:
    if not r:
        return False
    if r.exists(f"user:{user_id}"):
        return False
    r.hset(f"user:{user_id}", mapping={
        "user_id": user_id,
        "password_hash": hash_password(password),
        "role": role,
        "created_at": str(time.time()),
        "quota_cpu_seconds": "300",
        "quota_max_sessions": "3",
        "quota_max_exec_time": "120",
    })
    return True

def authenticate_user(user_id: str, password: str) -> Optional[dict]:
    if not r:
        return {"user_id": user_id, "role": "user"}  # dev mode: allow all
    user = r.hgetall(f"user:{user_id}")
    if not user:
        return None
    if user.get("password_hash") != hash_password(password):
        return None
    return user

def get_user_quota(user_id: str) -> dict:
    if not r:
        return {"max_sessions": 3, "max_exec_time": 120, "cpu_seconds": 300}
    user = r.hgetall(f"user:{user_id}")
    return {
        "max_sessions": int(user.get("quota_max_sessions", 3)),
        "max_exec_time": int(user.get("quota_max_exec_time", 120)),
        "cpu_seconds": int(user.get("quota_cpu_seconds", 300)),
    }

# ─── Danger Code Scanner ─────────────────────────────────────────────────────

DANGEROUS_PATTERNS = [
    "os.system", "subprocess.call", "subprocess.run", "subprocess.Popen",
    "__import__('os')", "eval(", "exec(", "open('/etc/", "open('/root/",
    "shutil.rmtree", "os.remove", "os.unlink", "os.rmdir",
    "socket.socket", "urllib.request", "requests.get", "requests.post",
    "ctypes", "import ctypes", "sys.exit", "quit()", "exit()",
    "fork()", "os.fork", "threading.Thread",  # prevent fork bombs
]

WARNING_PATTERNS = [
    "while True:", "for _ in range(9999", "import socket",
    "import urllib", "import requests", "open(", "os.path",
]

def scan_code(code: str) -> dict:
    issues = []
    warnings = []
    lines = code.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pat in DANGEROUS_PATTERNS:
            if pat in line:
                issues.append({"line": i, "pattern": pat, "code": stripped})
        for pat in WARNING_PATTERNS:
            if pat in line:
                warnings.append({"line": i, "pattern": pat, "code": stripped})
    verdict = "BLOCKED" if issues else ("WARNING" if warnings else "SAFE")
    return {"verdict": verdict, "issues": issues, "warnings": warnings}
