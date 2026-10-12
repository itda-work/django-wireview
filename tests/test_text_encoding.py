"""Every text file and text pipe in the repository names its encoding (#217).

Without one, Python reads and writes in the locale's encoding. That is UTF-8 on macOS and Linux, so
a missing ``encoding="utf-8"`` never shows there, but a Korean Windows machine uses cp949: every
document in this repository is UTF-8 Korean, and reading one there fails with ``UnicodeDecodeError``.
On the first run of the suite on Windows that was most of five hundred failures, and the library
had the same fault in ``wireview_check_templates``, which read the template diagnostics' output
through a text pipe without one.

ruff's PLW1514 sees ``Path("x").read_text()`` but not ``ROOT / "x"`` or a path in a variable, which
is nearly every call here, so this walks the syntax tree of every tracked Python file instead.
"""

import ast
import re
import subprocess
import typing as t
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

# A mode that opens a file as text, as the first argument of ``Path.open`` or the second of ``open``
TEXT_MODE = re.compile(r"[rwax]\+?t?")
SUBPROCESS = {"run", "check_output", "Popen", "check_call", "call"}
# Modules whose open() is not a file's: tarfile.open(mode="w") writes a binary archive
NOT_TEXT = {"tarfile", "zipfile", "gzip", "bz2", "lzma", "shelve", "dbm", "webbrowser", "os"}
# What a file object is used for: a bare ``x.open()`` whose result is one of these, or a ``with``
# item, is a file. Other zero-argument open()s (a page, a slot) are left alone
FILE_METHODS = {"read", "readline", "readlines", "write", "writelines"}


class Opener(t.NamedTuple):
    """Where a callable takes its mode and its encoding, and whether it opens text without a mode."""

    mode: int | None
    encoding: int | None
    text_by_default: bool = True


# Callables, by the name they are imported under, that open text in the locale's encoding unless
# told otherwise. ``encoding`` None: there is no parameter for it at all (os.popen)
OPENERS = {
    "open": Opener(1, 3),
    "io.open": Opener(1, 3),
    "codecs.open": Opener(1, 2),
    "os.fdopen": Opener(1, 3),
    "os.popen": Opener(1, None),
    "io.TextIOWrapper": Opener(None, 1),
    "tempfile.NamedTemporaryFile": Opener(0, 2, text_by_default=False),
    "tempfile.TemporaryFile": Opener(0, 2, text_by_default=False),
    "tempfile.SpooledTemporaryFile": Opener(1, 3, text_by_default=False),
    "logging.FileHandler": Opener(None, 2),
    "logging.handlers.WatchedFileHandler": Opener(None, 2),
    "logging.handlers.RotatingFileHandler": Opener(None, 4),
    "logging.handlers.TimedRotatingFileHandler": Opener(None, 4),
}


def _argument(call: ast.Call, index: int | None, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return call.args[index] if index is not None and len(call.args) > index else None


def _text_mode(mode: ast.expr | None, by_default: bool = True) -> bool:
    """Whether ``mode`` certainly opens text; a mode only the running code knows is left alone."""
    if mode is None:
        return by_default
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" not in mode.value


def _dotted(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute) and (inner := _dotted(expr.value)):
        return f"{inner}.{expr.attr}"
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    """What each imported name stands for: ``sp`` for subprocess, ``run`` for subprocess.run."""
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.partition(".")[0]] = (
                    alias.name if alias.asname else alias.name.partition(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _qualified(func: ast.expr, aliases: dict[str, str]) -> str | None:
    dotted = _dotted(func)
    if dotted is None:
        return None
    head, dot, rest = dotted.partition(".")
    return aliases.get(head, head) + dot + rest


def _unencoded(call: ast.Call, aliases: dict[str, str], used_as_file: bool) -> str | None:
    """What ``call`` opens as text without naming an encoding, or None."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in ("self", "cls"):
        return None  # a method of the class at hand (a command's write_text writes a report)
    qualified = _qualified(func, aliases)
    if qualified in OPENERS:
        opener = OPENERS[qualified]
        if opener.mode is not None and not _text_mode(_argument(call, opener.mode, "mode"), opener.text_by_default):
            return None
        if opener.encoding is None:
            return f"{qualified} (it takes no encoding: use subprocess)"
        return None if _argument(call, opener.encoding, "encoding") else qualified
    module, _, name = (qualified or "").rpartition(".")
    if module == "subprocess" and name in SUBPROCESS:
        text = any(
            k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value
            for k in call.keywords
        )
        if text and not _argument(call, None, "encoding"):
            return f"subprocess.{name}(text=True)"
        return None
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == "read_text":
        return None if _argument(call, 0, "encoding") else "read_text"
    if func.attr == "write_text":
        return None if _argument(call, 1, "encoding") else "write_text"
    if func.attr == "open":
        # Path.open(mode); other objects' open (a page, a link) take no text mode first
        if isinstance(func.value, ast.Name) and aliases.get(func.value.id, func.value.id) in NOT_TEXT:
            return None
        if _argument(call, 2, "encoding"):
            return None
        mode = _argument(call, 0, "mode")
        if mode is None:
            # Path.open() opens text; a zero-argument open of something else is not a file's
            return ".open()" if used_as_file and not call.args else None
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and TEXT_MODE.fullmatch(mode.value):
            return ".open"
    return None


def _offenders(source: str, where: str) -> list[str]:
    tree = ast.parse(source)
    aliases = _aliases(tree)
    files = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.withitem):
            files.add(id(node.context_expr))
        elif isinstance(node, ast.Attribute) and node.attr in FILE_METHODS:
            files.add(id(node.value))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (what := _unencoded(node, aliases, id(node) in files)):
            found.append(f"{where}:{node.lineno}: {what} without encoding=")
    return found


def _tracked_python() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=ROOT, capture_output=True, check=True).stdout.decode(
        "utf-8"
    )
    return [ROOT / name for name in listed.split("\0") if name]


def test_every_text_file_and_pipe_names_its_encoding():
    files = _tracked_python()
    assert len(files) > 300, "git ls-files found too few files to be this checkout"
    offenders = []
    for path in files:
        offenders += _offenders(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize(
    "line",
    [
        "(ROOT / 'README.md').read_text()",
        "path.write_text(body)",
        "open(path)",
        "open(path, 'w')",
        "open(path, mode='a')",
        "log.open('w')",
        "subprocess.run(['git'], capture_output=True, text=True)",
        "subprocess.check_output(['node'], universal_newlines=True)",
        "with path.open() as f: pass",
        "path.open().read()",
        "io.open(path)",
        "codecs.open(path, 'w')",
        "os.fdopen(fd, 'w')",
        "os.popen('git status')",
        "os.popen('git status', 'r')",
        "tempfile.NamedTemporaryFile('w')",
        "tempfile.TemporaryFile(mode='w+')",
        "tempfile.SpooledTemporaryFile(100, 'w+')",
        "logging.FileHandler(path)",
        "logging.handlers.RotatingFileHandler(path, 'a', 100, 2)",
        "import logging.handlers\nlogging.handlers.WatchedFileHandler(path)",
        "io.TextIOWrapper(raw)",
        "from io import TextIOWrapper\nTextIOWrapper(raw)",
        "from subprocess import run\nrun(['git'], text=True)",
        "from subprocess import run as go\ngo(['git'], text=True)",
        "import subprocess as sp\nsp.run(['git'], text=True)",
        "from tempfile import NamedTemporaryFile\nNamedTemporaryFile(mode='w')",
        "from os import fdopen\nfdopen(fd)",
    ],
)
def test_each_unencoded_form_is_caught(line):
    assert _offenders(line, "x.py"), line


@pytest.mark.parametrize(
    "line",
    [
        "path.read_text(encoding='utf-8')",
        "path.read_text('utf-8')",
        "path.write_text(body, encoding='utf-8')",
        "path.read_bytes()",
        "open(path, 'rb')",
        "open(path, mode)",
        "open(path, 'w', encoding='utf-8')",
        "tarfile.open(target, 'r:gz')",
        "tarfile.open(fileobj=data, mode='w')",
        "link.open(url)",
        "self.write_text(report)",
        "subprocess.run(['git'], capture_output=True)",
        "subprocess.run(['git'], text=True, encoding='utf-8')",
        "with path.open('rb') as f: pass",
        "with path.open(encoding='utf-8') as f: pass",
        "slot = reads.open()",
        "page = await Page.open()",
        "io.open(path, 'rb')",
        "io.open(path, encoding='utf-8')",
        "codecs.open(path, 'w', 'utf-8')",
        "os.fdopen(fd, 'wb')",
        "os.fdopen(fd, 'w', encoding='utf-8')",
        "tempfile.NamedTemporaryFile()",
        "tempfile.NamedTemporaryFile(delete=False)",
        "tempfile.NamedTemporaryFile('w', encoding='utf-8')",
        "tempfile.TemporaryFile('wb')",
        "logging.FileHandler(path, encoding='utf-8')",
        "logging.FileHandler(path, 'a', 'utf-8')",
        "io.TextIOWrapper(raw, encoding='utf-8')",
        "io.TextIOWrapper(raw, 'utf-8')",
        "from subprocess import run\nrun(['git'], capture_output=True)",
        "import subprocess as sp\nsp.run(['git'], text=True, encoding='utf-8')",
        "from asyncio import run\nrun(main())",
        "import os\nos.open(path, os.O_RDONLY)",
    ],
)
def test_what_names_its_encoding_or_is_bytes_passes(line):
    assert not _offenders(line, "x.py"), line
