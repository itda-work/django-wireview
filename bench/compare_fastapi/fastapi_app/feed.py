"""The FastAPI side of the feed (#178): one WebSocket endpoint, the new item's HTML sent to everyone.

The item is rendered once and the message serialized once, as FastAPI's broadcast example
does with its text: the same HTML wireview's item template draws.
"""

import json
from html import escape

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from bench.compare_fastapi.store import store

app = FastAPI()
clients: set[WebSocket] = set()


def render(item: dict) -> str:
    done = "done" if item["done"] else ""
    return f'<li id="items-{item["id"]}" class="{done}">{escape(item["name"])} × {item["qty"]}</li>'


@app.websocket("/ws")
async def feed(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    await websocket.send_json({"type": "snapshot", "items": [render(item) for item in store.items]})
    try:
        while True:
            message = await websocket.receive_json()
            if message["type"] == "post":
                text = json.dumps({"type": "item", "html": render(store.insert())})
                for client in list(clients):
                    await client.send_text(text)
    except WebSocketDisconnect:
        clients.discard(websocket)
