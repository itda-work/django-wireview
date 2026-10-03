const FEED_SIZE = 50;

const root = document.getElementById("root");
const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
const send = (type) => ws.send(JSON.stringify({ type }));

function itemElement(item) {
  const li = document.createElement("li");
  li.className = item.done ? "done" : "";
  li.textContent = `${item.name} × ${item.qty}`;
  return li;
}

function draw(board) {
  root.innerHTML = `
    <main data-is-live="true">
      <p>Count <span id="count"></span> <button id="increment">+1</button></p>
      <p>Announcement <span id="announcement"></span> <button id="announce">Announce</button></p>
      <button id="insert">Insert</button>
      <ul id="items"></ul>
    </main>`;
  root.querySelector("#count").textContent = board.count;
  root.querySelector("#announcement").textContent = board.announcement;
  root.querySelector("#items").append(...board.items.map(itemElement));
  for (const type of ["increment", "announce", "insert"]) {
    root.querySelector(`#${type}`).addEventListener("click", () => send(type));
  }
}

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.type === "snapshot") {
    draw(message);
  } else if (message.type === "count") {
    root.querySelector("#count").textContent = message.count;
  } else if (message.type === "announcement") {
    root.querySelector("#announcement").textContent = message.value;
  } else if (message.type === "inserted") {
    const list = root.querySelector("#items");
    list.prepend(itemElement(message.item));
    while (list.children.length > FEED_SIZE) list.lastElementChild.remove();
  }
};
