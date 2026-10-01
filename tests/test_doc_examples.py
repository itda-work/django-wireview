"""Guard: the code the documentation shows is code that can run (#113).

Running every Python block of the user-facing docs found about seventy that
could not: an awaited QuerySet in eight places, ``self.abroadcast`` (there is no
such method), a plain ``for`` over the ``consume_uploads()`` async generator, an
upload API that never existed, and templates calling ``JS()`` with arguments,
which Django cannot parse. ``tests/test_public_api.py`` read only the imports,
so none of it showed.

Running every block again on each test run is not possible -- most are
fragments that live inside a class the reader already has. So this guard keeps
what can be kept mechanically: every Python block parses, and the mistakes that
recurred are named and refused. A new kind of mistake still needs a person, or
another run like #113's.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

#: The same reader-facing set tests/test_public_api.py guards; docs/design,
#: docs/implementation and docs/legacy are records of how the library was built.
DOCS = [
    ROOT / "README.md",
    *sorted((ROOT / "docs" / "features").glob("*.md")),
    *sorted((ROOT / "docs" / "tutorials").glob("*.md")),
    *sorted((ROOT / "skills").rglob("*.md")),
    *sorted((ROOT / "examples").rglob("*.md")),
]

FENCE = re.compile(r"^\s*```(\w*)\s*$")

#: Django's async QuerySet, manager and related-manager methods: the only ones that can be awaited.
#: ``all`` begins with an "a" too, which is why this is a list and not a pattern.
ASYNC_ORM = {
    "aget",
    "acreate",
    "aget_or_create",
    "aupdate_or_create",
    "abulk_create",
    "abulk_update",
    "acount",
    "ain_bulk",
    "aiterator",
    "alatest",
    "aearliest",
    "afirst",
    "alast",
    "aaggregate",
    "aexists",
    "acontains",
    "aupdate",
    "adelete",
    "aexplain",
    "aadd",
    "aremove",
    "aclear",
    "aset",
}


def _blocks(language: str) -> list[tuple[str, str]]:
    """(``file:line``, dedented code) for every fenced block in ``language``."""
    found = []
    for path in DOCS:
        lines = path.read_text(encoding="utf-8").split("\n")
        i = 0
        while i < len(lines):
            match = FENCE.match(lines[i])
            if match and match.group(1):
                start, body = i + 1, []
                i += 1
                while i < len(lines) and not FENCE.match(lines[i]):
                    body.append(lines[i])
                    i += 1
                if match.group(1) == language:
                    found.append((f"{path.relative_to(ROOT)}:{start + 1}", textwrap.dedent("\n".join(body))))
            i += 1
    return found


PYTHON = _blocks("python")
HTML = _blocks("html")


def _parse(code: str) -> ast.Module:
    return ast.parse(code)


def _chain(node: ast.AST) -> list[str]:
    """``Model.objects.filter(x)[:5]`` → ["Model", "objects", "filter"]; calls and slices are looked through."""
    names: list[str] = []
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Subscript):
            node = node.value
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Name):
            names.append(node.id)
            return names[::-1]
        else:
            return names[::-1]


def _builds_markup(node: ast.JoinedStr) -> bool:
    """An f-string with a tag in its text and a value formatted into it."""
    text = "".join(part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str))
    return "<" in text and any(isinstance(part, ast.FormattedValue) for part in node.values)


def _mistakes(tree: ast.Module) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            chain = _chain(node.value)
            if "objects" in chain and chain[-1] not in ASYNC_ORM and not isinstance(node.value, ast.Call):
                found.append(f"awaits a QuerySet ({'.'.join(chain)}[...]); iterate it: [x async for x in qs]")
            elif "objects" in chain and isinstance(node.value, ast.Call) and chain[-1] not in ASYNC_ORM:
                found.append(f"awaits {'.'.join(chain)}(), which is not an async ORM method")
            if chain[-2:] in (["self", "skip_render"], ["self", "force_render"]):
                found.append(f"awaits {chain[-1]}(), which is synchronous")
        if isinstance(node, ast.Attribute) and node.attr == "abroadcast" and _chain(node) == ["self", "abroadcast"]:
            found.append("self.abroadcast does not exist: await self.broadcast(...)")
        if isinstance(node, ast.For) and _chain(node.iter)[-1:] == ["consume_uploads"]:
            found.append("consume_uploads() is an async generator: async for")
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            _chain(d)[-1:] == ["function_component"] for d in node.decorator_list
        ):
            # The returned string is output as it is (mark_safe), so an f-string puts the caller's value in raw
            for inner in ast.walk(node):
                if isinstance(inner, ast.JoinedStr) and _builds_markup(inner):
                    found.append(f"{node.name}() formats markup with an f-string, unescaped: format_html(...)")
                    break
        if isinstance(node, ast.Call) and _chain(node.func)[-1:] == ["allow_upload"]:
            if node.args and isinstance(node.args[0], ast.Call) and _chain(node.args[0].func)[-1:] == ["UploadConfig"]:
                found.append("allow_upload takes the name and keywords, not an UploadConfig")
    return found


@pytest.mark.parametrize(("where", "code"), PYTHON, ids=[where for where, _ in PYTHON])
def test_every_python_block_parses(where, code):
    try:
        _parse(code)
    except SyntaxError as error:
        pytest.fail(f"{where}: {error.msg} at block line {error.lineno}. A signature listing ends in ': ...'.")


@pytest.mark.parametrize(("where", "code"), PYTHON, ids=[where for where, _ in PYTHON])
def test_no_python_block_repeats_a_known_mistake(where, code):
    try:
        tree = _parse(code)
    except SyntaxError:
        pytest.skip("reported by test_every_python_block_parses")
    assert not _mistakes(tree), f"{where}: {_mistakes(tree)}"


@pytest.mark.parametrize(("where", "code"), HTML, ids=[where for where, _ in HTML])
def test_no_template_calls_js_with_arguments(where, code):
    # A template cannot call with arguments; the chain is a component property (docs/features/optimistic-ui.md)
    assert not re.search(r"\{%[^%]*\bJS\(", code), f"{where}: build the JS() chain in a @property"


def _js_chains() -> list[tuple[str, str]]:
    """(``file:line``, source) for every ``JS().name(...)...`` chain the docs show, in a block or inline."""
    found = []
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        for start in (m.start() for m in re.finditer(r"\bJS\(\)\.", text)):
            end = start + len("JS()")
            while (link := re.match(r"\s*\.\s*\w+\(", text[end:])) is not None:
                depth, i = 0, end + link.end() - 1
                while i < len(text):
                    depth += {"(": 1, ")": -1}.get(text[i], 0)
                    i += 1
                    if depth == 0:
                        break
                end = i
            line = text.count("\n", 0, start) + 1
            found.append((f"{path.relative_to(ROOT)}:{line}", text[start:end]))
    return found


JS_CHAINS = _js_chains()


def _js_mistakes(source: str) -> list[str]:
    """Each link of a JS() chain must be a JS method its arguments bind to."""
    import inspect

    from wireview import JS

    try:
        node = ast.parse(textwrap.dedent(source).replace("\n", " "), mode="eval").body
    except SyntaxError:
        return []  # an elided signature (``set_value(...)``) or a fragment of prose
    links = []
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        links.append(node)
        node = node.func.value
    found = []
    for call in links:
        name = call.func.attr  # type: ignore[union-attr]
        method = getattr(JS, name, None)
        if method is None or name.startswith("_"):
            found.append(f"JS has no method {name}()")
            continue
        args = [None] * sum(not isinstance(a, ast.Starred) for a in call.args)
        kwargs = {k.arg: None for k in call.keywords if k.arg is not None}
        try:
            inspect.signature(method).bind(None, *args, **kwargs)
        except TypeError as error:
            found.append(f"JS.{name}{inspect.signature(method)}: {error}")
    return found


@pytest.mark.parametrize(("where", "source"), JS_CHAINS, ids=[where for where, _ in JS_CHAINS])
def test_every_js_chain_binds_to_the_builder(where, source):
    # The skill's JS().add_class("shake", to="#row") was a TypeError; prose and tables are read too
    assert not _js_mistakes(source), f"{where}: {source} -> {_js_mistakes(source)}"


@pytest.mark.parametrize(
    "source",
    ['JS().add_class("shake", to="#row")', 'JS().show("#a").toggle(show="x", hide="y")', "JS().explode()"],
)
def test_the_js_rule_catches_its_mistake(source):
    assert _js_mistakes(source)


@pytest.mark.parametrize(
    "source",
    ['JS().add_class("#row", "shake")', 'JS().hide("#a", transition=("fade", 300)).push("saved", value={"id": 1})'],
)
def test_the_js_rule_refuses_nothing_right(source):
    assert not _js_mistakes(source)


def test_the_docs_are_read():
    assert len(PYTHON) > 200 and len(HTML) > 50 and len(JS_CHAINS) > 20


@pytest.mark.parametrize(
    "code",
    [
        "async def f(self):\n    rows = await Row.objects.all()[:10]",
        "async def f(self):\n    rows = await Row.objects.filter(a=1)",
        "async def f(self):\n    await self.skip_render()",
        "async def f(self):\n    await self.abroadcast('x')",
        "async def f(self):\n    for u in self.consume_uploads('a'):\n        pass",
        "async def f(self):\n    self.allow_upload(UploadConfig(name='a'))",
        "@function_component\ndef b(text):\n    return f'<b>{text}</b>'",
        "@function_component(name='x')\ndef b(text):\n    inner = f'<i>{text}</i>'\n    return inner",
    ],
)
def test_each_rule_catches_its_mistake(code):
    assert _mistakes(_parse(code))


@pytest.mark.parametrize(
    "code",
    [
        "async def f(self):\n    rows = [r async for r in Row.objects.all()[:10]]",
        "async def f(self):\n    row = await Row.objects.aget(pk=1)",
        "async def f(self):\n    n = await Row.objects.filter(a=1).acount()",
        "async def f(self):\n    self.skip_render()\n    await self.broadcast('x')",
        "async def f(self):\n    async for u in self.consume_uploads('a'):\n        pass",
        "async def f(self):\n    self.allow_upload('a', accept=['.png'])",
        "@function_component\ndef b(text):\n    return format_html('<b>{}</b>', text)",
        "@function_component\ndef b(size):\n    return format_html('<i>{}</i>', f'{size}px')",
    ],
)
def test_no_rule_refuses_the_right_way(code):
    assert not _mistakes(_parse(code))
