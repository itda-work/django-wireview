"""Guard: the commands that start the examples run as written in any shell (#217).

The example READMEs told a reader to run ``make build-js`` and ``make run-daphne``. Most
Windows machines have no make, and the recipes it runs (``cd tests && ...``) fail in
Windows PowerShell 5.1, which has no ``&&``. The READMEs also never said ``npm ci``, so
on a fresh clone the build failed for want of esbuild, Windows or not.

The commands are now one line each that PowerShell 5.1, cmd and bash all run alike:
``uv run --directory tests ...`` instead of ``cd``, ``uv run npm ...`` for the Node that
uv installs (nodejs-wheel), and make named only as a shorthand beside them. A line that
leans on one shell is the regression this file catches, in every block a reader copies
to run an example: the example READMEs and the root README's sections that start them.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
GUIDE = EXAMPLES / "README.md"

SERVER = "uv run --directory tests daphne testproj.asgi:application"
#: The guide's first-time steps, in the order they have to run
SETUP = [
    "uv sync --all-extras",
    "uv run npm ci",
    "uv run npm run build",
    "uv run --directory tests python manage.py migrate",
    SERVER,
]
#: Headings whose blocks are not for a reader running an example. ``make example-media``
#: records the screenshots and needs brew-installed tools: a maintainer's job on macOS.
MAINTAINER_ONLY = {"### 다시 만드는 법"}
#: The root README's sections that tell a reader how to run the examples
ROOT_SECTIONS = ("## 예제", "## 개발 및 기여")

#: (what is wrong, the pattern that finds it in one command line)
SHELL_SPECIFIC = [
    ("'&&' (Windows PowerShell 5.1 has none)", re.compile(r"&&")),
    ("'||'", re.compile(r"\|\|")),
    ("a 'cd' into tests/ (use uv run --directory tests)", re.compile(r"^cd\s+tests\b")),
    ("an environment prefix (VAR=value command is bash's)", re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s")),
    ("a line continuation", re.compile(r"[\\`^]$")),
    ("a shell script (./x.sh runs in bash only)", re.compile(r"(^|\s)\.{0,2}/?[\w./-]*\.sh\b")),
    ("a trailing # comment (cmd passes it on as arguments)", re.compile(r"\s#")),
    ("make (most Windows machines have none; name it beside the command instead)", re.compile(r"^make\b")),
    ("npm outside uv run (the Node uv installs is the one on PATH there)", re.compile(r"^(npm|npx|node)\b")),
]


def _blocks(text: str, origin: str, sections: tuple[str, ...] | None = None) -> list[tuple[str, list[str]]]:
    """(``origin:line``, its lines) of every fenced block not under a maintainer-only heading.

    With ``sections``, only the blocks in those second-level sections.
    """
    found, heading, section, block, start = [], "", "", None, 0
    for number, line in enumerate(text.split("\n"), 1):
        if block is None and line.startswith("#"):
            heading = line.strip()
            if line.startswith("## "):
                section = heading
        if line.startswith("```"):
            if block is None:
                block, start = [], number
            else:
                if heading not in MAINTAINER_ONLY and (sections is None or section in sections):
                    found.append((f"{origin}:{start}", block))
                block = None
        elif block is not None:
            block.append(line)
    return found


def _example_readmes() -> list[Path]:
    return sorted(path for path in EXAMPLES.glob("*/README.md") if (path.parent / "__init__.py").is_file())


def _command_blocks() -> list[tuple[str, list[str]]]:
    blocks = []
    for path in [GUIDE, *_example_readmes()]:
        blocks += _blocks(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
    return blocks + _blocks((ROOT / "README.md").read_text(encoding="utf-8"), "README.md", ROOT_SECTIONS)


def _shell_specific(lines: list[str]) -> list[tuple[str, str]]:
    return [
        (line, what)
        for line in (raw.strip() for raw in lines)
        if line
        for what, pattern in SHELL_SPECIFIC
        if pattern.search(line)
    ]


def test_the_guard_sees_the_blocks():
    """An empty walk passes every check below; the guide's and every example's blocks are read."""
    origins = {origin.split(":")[0] for origin, _ in _command_blocks()}

    assert "examples/README.md" in origins
    assert "README.md" in origins
    assert {f"examples/{path.parent.name}/README.md" for path in _example_readmes()} <= origins


@pytest.mark.parametrize(
    "line",
    [
        "cd tests && uv run daphne testproj.asgi:application",
        "WIREVIEW_TEST_LAYER=memory ./tests/e2e.sh examples/chat_lv3",
        "make run-daphne",
        "make build-js          # 최초 1회",
        "npm ci",
        "uv run pytest examples/todo -m e2e \\",
    ],
)
def test_each_shell_specific_form_is_caught(line):
    """The forms the READMEs used before #217, each found by a rule of its own."""
    assert _shell_specific([line])


def test_no_command_needs_a_particular_shell():
    offenders = [
        f"{origin}: {line!r} uses {what}"
        for origin, lines in _command_blocks()
        for line, what in _shell_specific(lines)
    ]

    assert offenders == []


def test_the_guide_gives_every_first_time_step_in_order():
    """``npm ci`` before the build: without it a fresh clone has no esbuild to build with."""
    lines = [line.strip() for _, block in _blocks(GUIDE.read_text(encoding="utf-8"), "guide") for line in block]
    positions = [lines.index(step) if step in lines else -1 for step in SETUP]

    assert -1 not in positions, dict(zip(SETUP, positions, strict=True))
    assert positions == sorted(positions)


@pytest.mark.parametrize("readme", _example_readmes(), ids=lambda path: path.parent.name)
def test_each_example_starts_its_server_without_make_and_points_at_the_guide(readme):
    text = readme.read_text(encoding="utf-8")
    lines = [line.strip() for _, block in _blocks(text, readme.name, ("## 돌려보기",)) for line in block]

    assert SERVER in lines
    assert "](../README.md#시작하기)" in text.split("\n## 돌려보기\n", 1)[1].split("\n## ", 1)[0]
