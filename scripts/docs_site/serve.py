"""``make docs-serve``: build the site, serve it, and build again when a source changes (#159).

The same build as ``make docs-site`` writes the same files; the server adds the development
pieces on the way out, so nothing of them lands in the output. Into every HTML response it
injects one script that polls this server: when a rebuild finishes the page reloads, and while
the last build has problems they show in a band at the top of the page (and in the terminal).
A failed build does not stop the server; it keeps serving what the last build wrote.

The document guards (gate 1 of ``make docs-site``) are not run on each rebuild: they take
longer than an edit cycle should. The terminal says so once.

Standard library only: the sources are polled, not watched.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import nav
from .build import ASSETS, DEFAULT_OUT, TEMPLATES, build

DEV_PATH = "/__docs_dev__/"

#: Polls the server; reloads when a newer build is out, shows the build's problems in a band.
RELOAD_JS = """(function () {
  var seen = null;
  function band(problems) {
    var el = document.getElementById("docs-dev-problems");
    if (!problems.length) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "docs-dev-problems";
      el.setAttribute("role", "alert");
      el.style.cssText = "position:sticky;top:0;z-index:100;max-height:40vh;overflow:auto;" +
        "background:#fee2e2;color:#991b1b;font:13px/1.5 ui-monospace,Menlo,monospace;" +
        "padding:8px 16px;border-bottom:2px solid #dc2626;white-space:pre-wrap";
      document.body.prepend(el);
    }
    el.textContent = "docs-serve: the last build has " + problems.length + " problem(s)\\n" + problems.join("\\n");
  }
  function poll() {
    fetch("%(state)s", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (state) {
        if (seen !== null && state.generation !== seen) { location.reload(); return; }
        seen = state.generation;
        band(state.problems);
      })
      .catch(function () {})
      .finally(function () { setTimeout(poll, 1000); });
  }
  poll();
})();
""" % {"state": f"{DEV_PATH}state"}

INJECT = f'<script src="{DEV_PATH}reload.js"></script>'.encode()

#: The build's text files are UTF-8 and mostly Korean. Without a charset a browser reads a .txt or
#: .md as its locale's default (often windows-1252) and shows mojibake; the release's server owes
#: the same header (docs/ROADMAP.md, the release steps) (#165).
TEXT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


def watched(root: Path) -> list[Path]:
    """The files a rebuild reads: the documents, the navigation, the agent skill, and the layout."""
    files = [root / "README.md", *(root / "docs").rglob("*"), *(root / "skills").rglob("*")]
    files += [*TEMPLATES.rglob("*"), *ASSETS.rglob("*")]
    return sorted(path for path in files if path.is_file())


def snapshot(root: Path) -> tuple:
    found = []
    for path in watched(root):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        found.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(found)


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.generation = 0
        self.problems: list[str] = []

    def as_json(self) -> bytes:
        with self.lock:
            return json.dumps({"generation": self.generation, "problems": self.problems}).encode()


def rebuild(state: State, out: Path, root: Path, version: str | None) -> None:
    started = time.monotonic()
    try:
        result = build(out=out, root=root, version=version)
        problems = [str(problem) for problem in result.problems]
    except Exception:
        problems = ["the build failed:", *traceback.format_exc().splitlines()]
    with state.lock:
        state.generation += 1
        state.problems = problems
    took = time.monotonic() - started
    if problems:
        print(f"docs-serve: built with {len(problems)} problem(s) in {took:.1f}s", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
    else:
        print(f"docs-serve: built in {took:.1f}s")


class Handler(SimpleHTTPRequestHandler):
    state: State

    def __init__(self, *args, state: State, **kwargs) -> None:
        self.state = state
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # noqa: A002 - the base class's name
        pass

    def guess_type(self, path):
        return TEXT_TYPES.get(Path(str(path)).suffix.lower()) or super().guess_type(path)

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - the base class's name
        path = self.path.split("?", 1)[0]
        if path == f"{DEV_PATH}state":
            return self._send(self.state.as_json(), "application/json")
        if path == f"{DEV_PATH}reload.js":
            return self._send(RELOAD_JS.encode(), TEXT_TYPES[".js"])
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/wireview/")
            self.end_headers()
            return
        local = Path(self.translate_path(path))
        if local.is_dir():
            local = local / "index.html"
        if local.suffix == ".html" and local.is_file() and path.endswith(("/", ".html")):
            body = local.read_bytes()
            body = body.replace(b"</body>", INJECT + b"</body>", 1) if b"</body>" in body else body + INJECT
            return self._send(body, TEXT_TYPES[".html"])
        return super().do_GET()


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def serve(port: int = 8765, out: Path = DEFAULT_OUT, root: Path = nav.ROOT, version: str | None = None) -> int:
    """Serve until Ctrl-C; 0 then, 2 if the port is taken (before anything is built)."""
    # Line by line, in order with stderr, even when the terminal is a pipe or a log file.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    state = State()
    handler = partial(Handler, directory=str(out), state=state)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as error:
        print(f'docs-serve: cannot listen on 127.0.0.1:{port} ({error.strerror}); try ARGS="--port N"', file=sys.stderr)
        return 2
    server.daemon_threads = True
    print("docs-serve: the document guards (gate 1 of make docs-site) are skipped on rebuilds")
    rebuild(state, out, root, version)
    stop = threading.Event()

    def watch() -> None:
        last = snapshot(root)
        while not stop.wait(0.5):
            current = snapshot(root)
            if current != last:
                last = current
                rebuild(state, out, root, version)

    watcher = threading.Thread(target=watch, name="docs-serve-watch", daemon=True)
    watcher.start()
    print(f"docs-serve: http://127.0.0.1:{port}/wireview/ (Ctrl-C to stop)")
    # Ctrl-C reaches every process of the foreground group (make, uv, this), and uv passes it on:
    # one interrupt stops the server, and a second must not cut the cleanup short. SIGTERM ends
    # it the same way.
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("\ndocs-serve: stopped")
    finally:
        stop.set()
        server.server_close()
        watcher.join(timeout=2)
    return 0
