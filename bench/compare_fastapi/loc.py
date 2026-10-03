"""Lines of code each implementation needed for the same page, counted by one rule.

Counted: the files a person writes for the feature (the server's handlers, the
templates or the client, the page). A line counts when it has code on it: blank lines,
comment-only lines and Python docstrings do not.

Not counted: the store both sides share (store.py), the benchmark's own instrumentation
(serve.py, timing.py), what a tool generates (lock files, build output, the parts of
``startproject`` and ``npm init`` output nobody edits). The project scaffolding is listed
apart as ``scaffolding`` -- the wireview side's settings, urls and asgi come from the
starter template, the FastAPI side's vite config and package.json from Vite's.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

HERE = Path(__file__).resolve().parent

APP = {
    "wireview": [
        "wv/board/live.py",
        "wv/board/templates/board/board.html",
        "wv/board/templates/board/index.html",
    ],
    "fastapi-react": ["fastapi_app/main.py", "client/react/main.jsx", "client/react/index.html"],
    "fastapi-vanilla": ["fastapi_app/main.py", "client/vanilla/main.js", "client/vanilla/index.html"],
}
SCAFFOLDING = {
    "wireview": ["wv/settings.py", "wv/urls.py", "wv/asgi.py"],
    "fastapi-react": ["client/vite.config.js", "client/package.json"],
    "fastapi-vanilla": ["client/vite.config.js", "client/package.json"],
}


def _python(source: str) -> int:
    lines: set[int] = set()
    docstrings: set[int] = set()
    previous = tokenize.NEWLINE
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING and previous in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            # A string standing alone as a statement: a docstring
            docstrings.update(range(token.start[0], token.end[0] + 1))
        if token.type not in (
            tokenize.COMMENT,
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
        ):
            lines.update(range(token.start[0], token.end[0] + 1))
        if token.type not in (tokenize.COMMENT, tokenize.NL):
            previous = token.type
    return len(lines - docstrings)


_BLOCK_COMMENTS = re.compile(r"/\*.*?\*/|<!--.*?-->|\{#.*?#\}|\{/\*.*?\*/\}", re.DOTALL)


def _text(source: str) -> int:
    # Comments become blank, keeping their line breaks
    source = _BLOCK_COMMENTS.sub(lambda match: "\n" * match.group(0).count("\n"), source)
    return sum(1 for line in source.splitlines() if line.strip() and not line.strip().startswith("//"))


def lines_of_code(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    return _python(source) if path.suffix == ".py" else _text(source)


def count() -> dict:
    result = {"rule": __doc__.strip().split("\n\n")[1].replace("\n", " ")}
    for name in APP:
        files = {path: lines_of_code(HERE / path) for path in APP[name]}
        scaffolding = {path: lines_of_code(HERE / path) for path in SCAFFOLDING[name]}
        result[name] = {
            "app": sum(files.values()),
            "files": files,
            "scaffolding": sum(scaffolding.values()),
            "scaffolding_files": scaffolding,
        }
    return result
