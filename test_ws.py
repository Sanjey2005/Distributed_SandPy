import asyncio
import websockets

async def test():
    try:
        async with websockets.connect("ws://localhost:5002/ws/job/test") as ws:
            print("Connected to WebSocket successfully!")
            await ws.send("ping")
            print("Received:", await ws.recv())
    except Exception as e:
        print("Connection failed:", e)

asyncio.run(test())
