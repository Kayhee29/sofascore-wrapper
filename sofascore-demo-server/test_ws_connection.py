import asyncio
import websockets

async def test():
    try:
        print("Connecting to ws://127.0.0.1:8000/ws/live ...")
        async with websockets.connect("ws://127.0.0.1:8000/ws/live") as ws:
            print("WS Connected successfully!")
            await ws.send("heartbeat")
            print("Heartbeat sent.")
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
