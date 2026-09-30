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

The same query also hides behind a property. A template reads ``self.questions``
off the loop, so ``list(self.quiz.questions.all())`` is fine there; a handler that
reads it runs the query on the loop. Tutorial 13 taught that after the example was
fixed (#145), so properties that evaluate the ORM, directly or through another
property, are collected and every async function that reads one fails. A property
that returns a QuerySet as is stays lazy and passes.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

#: Records of the past, not examples: design notes quote code as it was, legacy is 0.x.
NOT_EXAMPLES = {"legacy", "design"}

#: What a reader copies from, as in test_public_api.py, plus the test project
#: whose fixtures the skill is verified against.
SOURCES = [
    ROOT / "README.md",
    *sorted(p for p in (ROOT / "docs").rglob("*.md") if not NOT_EXAMPLES & set(p.relative_to(ROOT / "docs").parts)),
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


#: Methods that return a new QuerySet whatever manager they are called on:
#: ``self.quiz.questions.all()`` is one. Names a dict or a set also has are left out.
QUERYSET_METHODS = {
    "all",
    "filter",
    "exclude",
    "order_by",
    "select_related",
    "prefetch_related",
    "annotate",
    "distinct",
    "only",
    "defer",
}

#: Methods that run the query when called on a QuerySet or a manager.
EVALUATING_METHODS = {
    "get",
    "count",
    "exists",
    "first",
    "last",
    "earliest",
    "latest",
    "aggregate",
    "in_bulk",
    "contains",
    "iterator",
    "create",
    "get_or_create",
    "update_or_create",
    "update",
    "delete",
    "bulk_create",
}


#: Evaluating methods that a list, a dict or a str never calls without an argument.
ZERO_ARGUMENT_QUERIES = {"count", "exists", "first", "last", "iterator"}


def _is_property(func: ast.FunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id in {"property", "cached_property"})
        or (isinstance(d, ast.Attribute) and d.attr == "cached_property")
        for d in func.decorator_list
    )


def _self_attr(node: ast.AST) -> str | None:
    """``name`` for ``self.name``."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return node.attr
    return None


def _own_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    """The nodes of a function's body, without those of the functions and lambdas it defines:
    a nested synchronous function is what gets handed to ``sync_to_async``."""
    nodes, stack = [], list(func.body)
    while stack:
        node = stack.pop()
        nodes.append(node)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))
    return nodes


def _is_orm(node: ast.expr, lazy: set[str]) -> bool:
    """Whether ``node`` is an unevaluated QuerySet or manager, counting ``self.<name>``
    for a property that returns one as is."""
    while isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):  # qs[:10] stays lazy
        node = node.value
    if _self_attr(node) in lazy:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in QUERYSET_METHODS:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _is_orm(node.func.value, lazy) and node.func.attr not in EVALUATING_METHODS
    return _is_queryset(node, set())


def _looks_like_a_query(call: ast.Call) -> bool:
    """A query on a related manager, ``self.room.messages.count()``, whose receiver has no
    telling name. Told by its arguments: ``list.count(x)`` and ``dict.get(key)`` take one."""
    if call.args:
        return False
    return call.func.attr in ZERO_ARGUMENT_QUERIES or (call.func.attr == "get" and bool(call.keywords))


def _evaluations(func: ast.FunctionDef | ast.AsyncFunctionDef, lazy: set[str], evaluating: set[str]) -> list[str]:
    """Where ``func`` runs a query itself, or reads a property that does."""
    found = []
    for node in _own_nodes(func):
        if isinstance(node, ast.For) and _is_orm(node.iter, lazy):
            found.append(f"for {ast.unparse(node.target)} in {ast.unparse(node.iter)}")
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            found += [ast.unparse(g.iter) for g in node.generators if not g.is_async and _is_orm(g.iter, lazy)]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SYNC_CONSUMERS:
            found += [ast.unparse(node) for arg in node.args if _is_orm(arg, lazy)]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in EVALUATING_METHODS and (_is_orm(node.func.value, lazy) or _looks_like_a_query(node)):
                found.append(ast.unparse(node))
        elif isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
            if _is_orm(node.value, lazy):  # qs[0]
                found.append(ast.unparse(node))
        elif _self_attr(node) in evaluating:
            found.append(ast.unparse(node))
    return found


def _property_offenders(blocks: list[str]) -> list[str]:
    """Async functions that run a query through a synchronous property (#145).

    A property the template reads runs off the event loop. The same property read by
    a handler runs on it, and ``SynchronousOnlyOperation`` is raised in production
    however innocent ``self.current_question`` looks. A property that only returns a
    QuerySet is fine -- nothing runs until someone evaluates it -- so a handler
    that evaluates *that* synchronously is caught too.

    ``blocks`` are one document's: a tutorial defines the class in one block and
    quotes a handler on its own further down, so properties are told by name
    across all of them. A block that does not parse on its own is skipped.
    """
    trees = []
    for block in blocks:
        try:
            trees.append(ast.parse(block))
        except SyntaxError:
            continue
    props = [
        node
        for tree in trees
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef)
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and _is_property(node)
    ]
    lazy: set[str] = set()
    evaluating: set[str] = set()
    changed = True
    while changed:  # A property may reach the ORM through another property.
        changed = False
        for func in props:
            if func.name in evaluating:
                continue
            if _evaluations(func, lazy, evaluating):
                evaluating.add(func.name)
                lazy.discard(func.name)
                changed = True
            elif func.name not in lazy and any(
                isinstance(n, ast.Return) and n.value is not None and _is_orm(n.value, lazy) for n in _own_nodes(func)
            ):
                lazy.add(func.name)
                changed = True
    return [
        f"{func.name}: {found}"
        for tree in trees
        for func in ast.walk(tree)
        if isinstance(func, ast.AsyncFunctionDef)
        for found in _evaluations(func, lazy, evaluating)
    ]


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_handler_runs_a_query_through_a_property(path):
    offenders = _property_offenders(_blocks(path))
    assert not offenders, (
        f"{path.relative_to(ROOT)} has an async method that runs a query through a synchronous property: "
        f"{offenders}. Give the handler an async helper (await qs.afirst(), await qs.acount()) and leave "
        "the property to the template, which reads it off the event loop"
    )


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


_QUESTIONS = (
    "class Quiz(Component):\n    @property\n    def questions(self):\n        return list(self.quiz.questions.all())\n"
)


@pytest.mark.parametrize(
    "source",
    [
        # Tutorial 13 before #145: a property the handler reads lists the questions.
        _QUESTIONS
        + "    async def next_question(self):\n        if self.index >= len(self.questions):\n            pass",
        # ... or reaches that property through another one.
        _QUESTIONS + "    @property\n    def current(self):\n        return self.questions[self.index]\n"
        "    async def answer(self, choice_id: int):\n        question = self.current",
        "class A(Component):\n    @property\n    def total(self):\n        return self.room.messages.count()\n"
        "    async def joined(self):\n        self.seen = self.total",
        "class A(Component):\n    @property\n    def owner(self):\n        return User.objects.get(pk=self.owner_id)\n"
        "    async def rename(self, name: str):\n        self.owner.name = name",
        "class A(Component):\n"
        "    @property\n    def author(self):\n        return self.post.authors.get(pk=self.author_id)\n"
        "    async def joined(self):\n        self.name = self.author.name",
        "class A(Component):\n"
        "    @cached_property\n    def latest(self):\n        return self.room.messages.order_by('-id')[0]\n"
        "    async def reply(self):\n        await Message.objects.acreate(parent=self.latest)",
        "class A(Component):\n"
        "    @property\n    def names(self):\n        return [u.name for u in self.team.members.all()]\n"
        "    async def joined(self):\n        self.count = len(self.names)",
        # A property that returns a QuerySet as is, evaluated synchronously by the handler.
        "class A(Component):\n    @property\n    def items(self):\n        return Item.objects.filter(done=False)\n"
        "    async def clear(self):\n        for item in self.items:\n            await item.adelete()",
        "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
        "    async def joined(self):\n        self.n = self.items.count()",
        # The property that runs the query declared after the one that reads it: one pass
        # over the properties in order is not enough, for an evaluation or a lazy chain.
        # (Not named ``queryset``: that name alone reads as a QuerySet, pass or no pass.)
        "class Quiz(Component):\n    @property\n    def current(self):\n        return self.questions[self.index]\n"
        "    @property\n    def questions(self):\n        return list(self.quiz.questions.all())\n"
        "    async def answer(self, choice_id: int):\n        question = self.current",
        "class A(Component):\n    @property\n    def items(self):\n        return self.pending\n"
        "    @property\n    def pending(self):\n        return Item.objects.filter(done=False)\n"
        "    async def joined(self):\n        for item in self.items:\n            await item.adelete()",
        # No property at all: the handler runs the query itself.
        "class A(Component):\n    async def joined(self):\n        self.owner = User.objects.get(pk=1)",
        "class A(Component):\n    async def joined(self):\n        self.seen = self.room.messages.count()",
    ],
)
def test_the_property_scan_catches_a_query_on_the_event_loop(source):
    assert _property_offenders([source])


@pytest.mark.parametrize(
    "source",
    [
        # The fixed tutorial: the template reads the property, the handler an async helper.
        _QUESTIONS + "    async def _aquestion_count(self) -> int:\n        return await self.quiz.questions.acount()\n"
        "    async def next_question(self):\n        if self.index >= await self._aquestion_count():\n            pass",
        # The todo example: a property that returns a QuerySet and handlers that await it.
        "class A(Component):\n    @property\n    def queryset(self):\n        return Item.objects.filter(done=False)\n"
        "    @property\n    def items(self):\n        return self.queryset\n"
        "    async def toggle_all(self):\n        await self.items.aupdate(done=True)\n"
        "    async def all_done(self):\n        return not await self.items.filter(done=False).aexists()\n"
        "    async def joined(self):\n        await self.stream('i', self.items[:20])",
        "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
        "    async def joined(self):\n        self.names = [i.name async for i in self.items]",
        # A property that queries nothing: a dict's get() and values() are not the ORM's.
        "class A(Component):\n"
        "    @property\n    def current_answer(self):\n        return self.answers.get(self.index)\n"
        "    @property\n    def picked(self):\n        return list(self.answers.values())\n"
        "    async def answer(self):\n        if self.current_answer or self.picked:\n            pass",
        "class A(Component):\n"
        "    @property\n    def twos(self):\n        return self.rolls.count(2) + len(self.tags.get('x', []))\n"
        "    async def roll(self):\n        self.last = self.twos",
        # A synchronous method may read it: it runs off the loop, like the template.
        _QUESTIONS + "    def get_context_data(self):\n        return {'n': len(self.questions)}",
        # So may a nested function handed to sync_to_async.
        _QUESTIONS + "    async def joined(self):\n"
        "        self.n = await sync_to_async(lambda: len(self.questions))()\n"
        "        def count():\n            return len(self.questions)\n"
        "        self.n = await sync_to_async(count)()",
    ],
)
def test_the_property_scan_passes_what_does_not_block(source):
    assert not _property_offenders([source])


def test_the_property_scan_reads_a_document_as_a_whole():
    """Tutorial 13 quotes ``answer()`` again on its own, away from the class."""
    handler = "async def answer(self, choice_id):\n    question = self.current_question"
    prop = _QUESTIONS + "    @property\n    def current_question(self):\n        return self.questions[self.index]\n"
    assert _property_offenders([prop, "not python {", handler]) == ["answer: self.current_question"]
