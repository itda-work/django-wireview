"""Every text file and text pipe in the repository names its encoding (#217).

Without one, Python reads and writes in the locale's encoding. That is UTF-8 on macOS and Linux, so
a missing ``encoding="utf-8"`` never shows there, but a Korean Windows machine uses cp949: every
document in this repository is UTF-8 Korean, and reading one there fails with ``UnicodeDecodeError``.
On the first run of the suite on Windows that was most of five hundred failures, and the library
had the same fault in what it writes for the editor and reads from the template diagnostics.

ruff's PLW1514 sees ``Path("x").read_text()`` but not ``ROOT / "x"`` or a path in a variable, which
is nearly every call here, so this walks the syntax tree of every tracked Python file instead.
"""

import ast
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

# A mode that opens a file as text, as the first argument of ``Path.open`` or the second of ``open``
TEXT_MODE = re.compile(r"[rwax]\+?t?")
SUBPROCESS = {"run", "check_output", "Popen", "check_call", "call"}
# Modules whose open() is not a file's: tarfile.open(mode="w") writes a binary archive
NOT_TEXT = {"tarfile", "zipfile", "gzip", "bz2", "lzma", "shelve", "dbm", "webbrowser", "os"}


def _argument(call: ast.Call, index: int, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return call.args[index] if len(call.args) > index else None


def _text_mode(mode: ast.expr | None) -> bool:
    """Whether ``mode`` certainly opens text; a mode only the running code knows is left alone."""
    if mode is None:
        return True
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" not in mode.value


def _unencoded(call: ast.Call) -> str | None:
    """What ``call`` opens as text without naming an encoding, or None."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in ("self", "cls"):
        return None  # a method of the class at hand (a command's write_text writes a report)
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
    if name == "read_text" and isinstance(func, ast.Attribute):
        return None if _argument(call, 0, "encoding") else "read_text"
    if name == "write_text" and isinstance(func, ast.Attribute):
        return None if _argument(call, 1, "encoding") else "write_text"
    if name == "open" and isinstance(func, ast.Name):
        if _text_mode(_argument(call, 1, "mode")) and not _argument(call, 3, "encoding"):
            return "open"
        return None
    if name == "open" and isinstance(func, ast.Attribute):
        # Path.open(mode); other objects' open (a page, a link) take no text mode first
        if isinstance(func.value, ast.Name) and func.value.id in NOT_TEXT:
            return None
        mode = _argument(call, 0, "mode")
        if (
            isinstance(mode, ast.Constant)
            and isinstance(mode.value, str)
            and TEXT_MODE.fullmatch(mode.value)
            and not _argument(call, 2, "encoding")
        ):
            return ".open"
        return None
    if (
        name in SUBPROCESS
        and isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
    ):
        text = any(
            k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value
            for k in call.keywords
        )
        if text and not _argument(call, 99, "encoding"):
            return f"subprocess.{name}(text=True)"
    return None


def _offenders(source: str, where: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and (what := _unencoded(node)):
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
    ],
)
def test_what_names_its_encoding_or_is_bytes_passes(line):
    assert not _offenders(line, "x.py"), line
