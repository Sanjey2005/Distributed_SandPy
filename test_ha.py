import asyncio
import httpx

API_URL = "http://localhost:8000"

async def test_all():
    async with httpx.AsyncClient() as client:
        # 1. Test Nginx Proxy to Dispatcher Health
        print("[*] Testing HA Dispatchers...")
        resp = await client.get(f"{API_URL}/cluster/status", headers={"Authorization": "Bearer TEST_SKIP_TOKEN"})
        if resp.status_code in [200, 401, 403]: # Unauthenticated or ok is fine, we just care Nginx routes it
            print(f" [+] NGINX Load Balancer active. Route returned {resp.status_code}")
        else:
            print(f" [-] NGINX Failed: {resp.status_code}")

test_all()
