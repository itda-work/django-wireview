import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

const FEED_SIZE = 50;

function reduce(board, message) {
  switch (message.type) {
    case "snapshot":
      return message;
    case "count":
      return { ...board, count: message.count };
    case "announcement":
      return { ...board, announcement: message.value };
    case "inserted":
      return { ...board, items: [message.item, ...board.items].slice(0, FEED_SIZE) };
    default:
      return board;
  }
}

function Board() {
  const [board, setBoard] = useState(null);
  const socket = useRef(null);

  useEffect(() => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      setBoard((board) => reduce(board, message));
    };
    socket.current = ws;
    return () => ws.close();
  }, []);

  if (!board) return null;
  const send = (type) => socket.current.send(JSON.stringify({ type }));
  return (
    <main data-is-live="true">
      <p>
        Count <span id="count">{board.count}</span>
        <button id="increment" onClick={() => send("increment")}>+1</button>
      </p>
      <p>
        Announcement <span id="announcement">{board.announcement}</span>
        <button id="announce" onClick={() => send("announce")}>Announce</button>
      </p>
      <button id="insert" onClick={() => send("insert")}>Insert</button>
      <ul id="items">
        {board.items.map((item) => (
          <li key={item.id} className={item.done ? "done" : ""}>
            {item.name} × {item.qty}
          </li>
        ))}
      </ul>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<Board />);
