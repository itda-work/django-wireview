"""No example evaluates a QuerySet synchronously on the event loop (#121).

``stream()`` consumes a QuerySet with ``async for`` (#120), so handing it one as is
-- sliced or not -- is the documented pattern. What still fails is evaluating
the QuerySet before it gets there, or anywhere else in async code: ``list(qs)``,
``reversed(qs)``, ``[x for x in qs]``, ``for x in qs:``. On the event loop Django
refuses each with ``SynchronousOnlyOperation``, and the join or the handler fails
in production. ``stream_insert()`` takes one item, so a QuerySet there is wrong
whatever the loop.

The skill's example taught one of these for a release while every test passed:
the suite ran with ``DJANGO_ALLOW_ASYNC_UNSAFE``, which turns the refusal off.
Every example a reader or an agent copies from is scanned: the Python blocks of
the user-facing documentation, the skill installed into projects, the examples,
and the test project's fixtures (bookmarks is the skill's baseline).
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

#: What a reader copies from, as in test_public_api.py, plus the test project
#: whose fixtures the skill is verified against.
SOURCES = [
    ROOT / "README.md",
    *sorted((ROOT / "docs" / "features").glob("*.md")),
    *sorted((ROOT / "docs" / "tutorials").glob("*.md")),
    *sorted((ROOT / "skills").rglob("*.md")),
    *sorted((ROOT / "examples").rglob("*.py")),
    *sorted((ROOT / "examples").rglob("*.md")),
    *sorted((ROOT / "tests" / "testproj").rglob("*.py")),
]

_FENCE = re.compile(r"```(?:python|py)\n(.*?)```", re.S)

#: Calls that iterate their argument synchronously.
SYNC_CONSUMERS = {"list", "tuple", "reversed", "sorted", "set", "len"}


def _blocks(path: Path) -> list[str]:
    text = path.read_text()
    return [text] if path.suffix == ".py" else [textwrap.dedent(b) for b in _FENCE.findall(text)]


def _is_queryset(node: ast.expr, bound: set[str]) -> bool:
    """Whether ``node`` is, as far as a reader can tell, an unevaluated QuerySet."""
    while isinstance(node, ast.Subscript):  # qs[:50]
        node = node.value
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if "queryset" in func.attr:  # self.get_queryset()
                return True
            return _is_queryset(func.value, bound)  # Model.objects.filter(...).order_by(...)
        return False
    if isinstance(node, ast.Attribute):
        return node.attr == "objects" or "queryset" in node.attr or _is_queryset(node.value, bound)
    if isinstance(node, ast.Name):
        return node.id in bound or "queryset" in node.id
    return False


def _scopes(tree: ast.Module):
    """Each async function body, and the module's own statements (a snippet's
    top-level ``await`` is written as if inside a handler), with their nodes in order."""
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.AsyncFunctionDef)):
            continue
        nodes = []
        stack = list(ast.iter_child_nodes(scope))
        while stack:
            node = stack.pop()
            nodes.append(node)
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                stack.extend(ast.iter_child_nodes(node))
        yield sorted((n for n in nodes if hasattr(n, "lineno")), key=lambda n: (n.lineno, n.col_offset))


def _offenders(source: str) -> list[str]:
    found = []
    for nodes in _scopes(ast.parse(source)):
        bound: set[str] = set()
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if _is_queryset(node.value, bound):
                    bound.add(name)
                else:
                    bound.discard(name)
            elif isinstance(node, ast.For) and _is_queryset(node.iter, bound):
                found.append(f"for {ast.unparse(node.target)} in {ast.unparse(node.iter)}")
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for gen in node.generators:
                    if not gen.is_async and _is_queryset(gen.iter, bound):
                        found.append(f"for {ast.unparse(gen.target)} in {ast.unparse(gen.iter)}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SYNC_CONSUMERS:
                if any(_is_queryset(arg, bound) for arg in node.args):
                    found.append(ast.unparse(node))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "stream_insert"
            ):
                item = node.args[1] if len(node.args) > 1 else None
                item = next((k.value for k in node.keywords if k.arg == "item"), item)
                if item is not None and _is_queryset(item, bound):
                    found.append(ast.unparse(node))
    return found


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_example_evaluates_a_queryset_on_the_event_loop(path):
    offenders = []
    for block in _blocks(path):
        try:
            offenders += _offenders(block)
        except SyntaxError:
            # A fragment that is not Python on its own. The regex still catches the
            # plain form, a manager chain handed to a synchronous consumer.
            offenders += re.findall(rf"\b(?:{'|'.join(SYNC_CONSUMERS)})\(\s*[\w.]*\.objects\.[^\n]*", block)
    assert not offenders, (
        f"{path.relative_to(ROOT)} evaluates a QuerySet synchronously in async code: {offenders}. "
        "Pass it to stream() as is, or collect it with [x async for x in qs]"
    )


@pytest.mark.parametrize(
    "source",
    [
        'await self.stream("i", list(Item.objects.all()))',
        'await self.stream("i", reversed(Item.objects.order_by("-id")[:10]))',
        'await self.stream("i", [i for i in Item.objects.all()])',
        "qs = Item.objects.all()\nqs = qs.filter(done=False)\nitems = list(qs)",
        "async def joined(self):\n    for item in self.get_queryset():\n        await self.stream_insert('i', item)",
        "async def joined(self):\n    n = len(Item.objects.all())",
        'await self.stream_insert("i", Item.objects.filter(pk=1))',
        'await self.stream_insert(name="i", item=self.queryset)',
    ],
)
def test_the_scan_catches_a_synchronous_evaluation(source):
    assert _offenders(source)


@pytest.mark.parametrize(
    "source",
    [
        # A QuerySet handed over as is: stream() consumes it with async for (#120).
        'await self.stream("m", Message.objects.filter(room=1)[:50], limit=50)',
        'qs = Item.objects.all()\nqs = qs.filter(done=False)\nawait self.stream("i", qs, limit=100)',
        'await self.stream("i", self.get_queryset())',
        'await self.stream("m", [m async for m in Message.objects.filter(room=1)[:50]])',
        'messages = [m async for m in Message.objects.all()]\nawait self.stream("m", reversed(messages))',
        "async def joined(self):\n"
        "    async for row in Row.objects.all()[:20]:\n"
        "        await self.stream_insert('r', row)",
        'item = await Item.objects.acreate(text="x")\nawait self.stream_insert("i", item, at=0)',
        # A synchronous function runs off the loop: a property the render reads, a view.
        "def items(self):\n    return list(Item.objects.all())",
        # A name bound to a QuerySet in one function is a list in another.
        "async def _load(self):\n    messages = Message.objects.all()\n"
        "async def joined(self):\n    messages = await self._load()\n    await self.stream('m', reversed(messages))",
    ],
)
def test_the_scan_passes_what_does_not_block(source):
    assert not _offenders(source)
