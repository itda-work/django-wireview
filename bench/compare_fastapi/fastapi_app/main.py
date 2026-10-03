"""The FastAPI side: a WebSocket endpoint and the built client, as FastAPI's own guide writes them.

``BENCH_CLIENT`` picks which build the page loads: ``react`` (default) or ``vanilla``.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from bench.compare_fastapi.store import store

CLIENT = Path(__file__).resolve().parent.parent / "client" / "dist" / os.environ.get("BENCH_CLIENT", "react")

app = FastAPI()
clients: set[WebSocket] = set()


@app.websocket("/ws")
async def board(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    await websocket.send_json({"type": "snapshot", **store.snapshot()})
    try:
        while True:
            message = await websocket.receive_json()
            if message["type"] == "increment":
                await websocket.send_json({"type": "count", "count": store.increment()})
            elif message["type"] == "insert":
                await websocket.send_json({"type": "inserted", "item": store.insert()})
            elif message["type"] == "announce":
                text = json.dumps({"type": "announcement", "value": store.announce()})
                for client in list(clients):
                    await client.send_text(text)
    except WebSocketDisconnect:
        clients.discard(websocket)


app.mount("/", StaticFiles(directory=CLIENT, html=True), name="client")
