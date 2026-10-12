"""Guard: the commands that start the examples run as written in any shell (#217).

The example READMEs told a reader to run ``make build-js`` and ``make run-daphne``. Most
Windows machines have no make, and the recipes it runs (``cd tests && ...``) fail in
Windows PowerShell 5.1, which has no ``&&``. The READMEs also never said ``npm ci``, so
on a fresh clone the build failed for want of esbuild, Windows or not.

The commands are now one line each that PowerShell 5.1, cmd and bash all run alike:
``uv run --directory tests ...`` instead of ``cd``, ``uv run npm ...`` for the Node that
uv installs (nodejs-wheel), and make named only as a shorthand beside them. A line that
leans on one shell is the regression this file catches, in every shell block a reader
copies to run an example: the example READMEs and the root README's sections that start them.

Each rule has a line only it finds (``LINES``), so a rule taken out fails its own test: the
first version's example lines were caught by several rules at once, and six of its nine
rules could go without a test noticing. Its blind spots were the ones a regression takes --
``-m 'not e2e'`` (cmd hands pytest ``'not`` and ``e2e'``), ``a; b``, ``diff -r`` -- and they
are rules now.
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

#: name: (what is wrong, the pattern that finds it in one command line)
SHELL_SPECIFIC = {
    "and": ("'&&' (Windows PowerShell 5.1 has none)", re.compile(r"&&")),
    "or": ("'||'", re.compile(r"\|\|")),
    "cd": ("a 'cd' into tests/ (use uv run --directory tests)", re.compile(r"^cd\s+tests\b")),
    "prefix": ("an environment prefix (VAR=value command is bash's)", re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s")),
    "continuation": ("a line continuation", re.compile(r"[\\`^]$")),
    "script": ("a shell script (./x.sh runs in bash only)", re.compile(r"(^|\s)\.{0,2}/?[\w./-]*\.sh\b")),
    "comment": ("a trailing # comment (cmd passes it on as arguments)", re.compile(r"\s#")),
    "make": ("make (most Windows machines have none; name it beside the command instead)", re.compile(r"^make\b")),
    "npm": ("npm outside uv run (the Node uv installs is the one on PATH there)", re.compile(r"^(npm|npx|node)\b")),
    "quote": ("single quotes (cmd passes them on: -m 'not e2e' is two arguments)", re.compile(r"'")),
    "semicolon": ("';' (cmd runs no second command after it)", re.compile(r";")),
    "export": ("export (bash's; the variable is set differently in each shell)", re.compile(r"^export\s")),
    "set": ("set NAME=value (cmd's; PowerShell's set is another command)", re.compile(r"^set\s+\w+=", re.I)),
    "env": ("$env: (PowerShell's)", re.compile(r"\$env:", re.I)),
    "source": (
        "source or . (activating a virtualenv differs by shell; uv run needs none)",
        re.compile(r"^(source|\.)\s"),
    ),
    "subshell": ("$( ) (cmd has no command substitution)", re.compile(r"\$\(")),
    "devnull": ("/dev/null (Windows has none)", re.compile(r"/dev/null")),
    "background": (
        "a trailing & (bash's background job; cmd and PowerShell read it otherwise)",
        re.compile(r"(^|[^&])&\s*$"),
    ),
    "home": ("~/ (cmd does not expand it)", re.compile(r"(^|\s)~[/\\]")),
    "tool": (
        "a Unix tool (cmd has none; PowerShell's diff, ls or rm is another command by that name)",
        re.compile(r"^(diff|rm|cp|mv|cat|grep|sed|awk|ls|touch|chmod|which|head|tail)\b"),
    ),
    "manage": (
        "manage.py outside tests/ (uv run --directory tests python manage.py ...)",
        re.compile(r"^(?!.*--directory\s+tests\b).*\bmanage\.py\b"),
    ),
}
#: A line each rule finds and no other does, so that a rule taken out fails the test of its own line
LINES = {
    "and": "uv sync --all-extras && uv run npm ci",
    "or": "uv run npm ci || uv run npm install",
    "cd": "cd tests",
    "prefix": "WIREVIEW_TEST_LAYER=memory uv run pytest examples -m e2e",
    "continuation": "uv run pytest examples \\",
    "script": "./tests/e2e.sh examples/todo",
    "comment": "uv run npm ci          # 최초 1회",
    "make": "make run-daphne",
    "npm": "npm ci",
    "quote": "uv run pytest examples -m 'not e2e'",
    "semicolon": "uv sync --all-extras; uv run npm ci",
    "export": "export WIREVIEW_TEST_LAYER=memory",
    "set": "set WIREVIEW_TEST_LAYER=memory",
    "env": '$env:WIREVIEW_TEST_LAYER = "memory"',
    "source": "source .venv/bin/activate",
    "subshell": "uv run pytest $(git ls-files examples)",
    "devnull": "uv run npm ci > /dev/null",
    "background": "uv run --directory tests daphne testproj.asgi:application &",
    "home": "uv run --directory ~/django-wireview/tests daphne testproj.asgi:application",
    "tool": "diff -r examples/chat examples/chat_lv2",
    "manage": "uv run python manage.py migrate",
}
#: The fences a reader runs. A ```python block's ``x = 1  # note`` is no shell's comment
SHELL_FENCES = {"", "shell", "bash", "sh", "powershell", "console"}


def _blocks(text: str, origin: str, sections: tuple[str, ...] | None = None) -> list[tuple[str, list[str]]]:
    """(``origin:line``, its lines) of every shell block not under a maintainer-only heading.

    With ``sections``, only the blocks in those second-level sections.
    """
    found, heading, section, block, start, fence = [], "", "", None, 0, ""
    for number, line in enumerate(text.split("\n"), 1):
        if block is None and line.startswith("#"):
            heading = line.strip()
            if line.startswith("## "):
                section = heading
        if line.startswith("```"):
            if block is None:
                block, start, fence = [], number, line[3:].strip().lower()
            else:
                wanted = heading not in MAINTAINER_ONLY and (sections is None or section in sections)
                if wanted and fence in SHELL_FENCES:
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
        blocks += _blocks(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    return blocks + _blocks((ROOT / "README.md").read_text(encoding="utf-8"), "README.md", ROOT_SECTIONS)


def _shell_specific(lines: list[str]) -> list[tuple[str, str]]:
    return [
        (line, what)
        for line in (raw.strip() for raw in lines)
        if line
        for what, pattern in SHELL_SPECIFIC.values()
        if pattern.search(line)
    ]


def test_the_guard_sees_the_blocks():
    """An empty walk passes every check below; the guide's and every example's blocks are read."""
    origins = {origin.split(":")[0] for origin, _ in _command_blocks()}

    assert "examples/README.md" in origins
    assert "README.md" in origins
    assert {f"examples/{path.parent.name}/README.md" for path in _example_readmes()} <= origins


def test_every_rule_has_a_line_of_its_own():
    assert LINES.keys() == SHELL_SPECIFIC.keys()


@pytest.mark.parametrize("rule", LINES)
def test_each_rule_alone_finds_its_line(rule):
    """Found by its own rule and by no other: with the rule gone, nothing would find it."""
    assert rule in SHELL_SPECIFIC
    assert [what for _, what in _shell_specific([LINES[rule]])] == [SHELL_SPECIFIC[rule][0]]


@pytest.mark.parametrize(
    "line",
    [
        "uv sync --all-extras",
        "uv run --directory tests python manage.py migrate",
        'uv run pytest tests examples -m "not e2e and not slow"',
        'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
        "git diff HEAD:examples/chat HEAD:examples/chat_lv2",
        "winget install --id=astral-sh.uv -e",
    ],
)
def test_the_commands_the_guide_gives_pass(line):
    assert _shell_specific([line]) == []


def test_only_shell_blocks_are_read():
    text = "```python\nx = 1  # a note\n```\n\n```powershell\nuv sync; uv run npm ci\n```\n\n```\nmake\n```\n"
    assert _blocks(text, "x.md") == [("x.md:5", ["uv sync; uv run npm ci"]), ("x.md:9", ["make"])]


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
