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
that returns a QuerySet as is stays lazy and passes. A plain helper method the
handler calls is read the same way, and so is the truth of a QuerySet (#149): in
``if``, and in a comprehension's condition. Any builtin that iterates its argument
consumes it, ``any()`` and ``max()`` as much as ``list()`` (#151).

Not caught: a foreign key followed by attribute, ``self.post.author.name``. Whether
``author`` is a relation or a column is the model's to say, not the syntax's.
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

#: Calls that iterate their argument synchronously. ``enumerate()``, ``zip()``, ``map()``,
#: ``filter()`` and ``iter()`` are lazy, but each asks its argument for an iterator at once,
#: and that is where a QuerySet runs its query.
SYNC_CONSUMERS = {
    "list",
    "tuple",
    "reversed",
    "sorted",
    "set",
    "frozenset",
    "dict",
    "len",
    "bool",
    "any",
    "all",
    "sum",
    "max",
    "min",
    "enumerate",
    "zip",
    "map",
    "filter",
    "iter",
}


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
    "values_list",
}

#: Methods a dict has too, told apart by their arguments: ``dict.values()`` takes none,
#: ``qs.values("name")`` names fields.
FIELD_QUERYSET_METHODS = {"values"}

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


def _key(func: ast.FunctionDef) -> str | None:
    """How a handler reaches ``func``: ``name`` for a property, ``name()`` for a plain
    method it calls. A decorated method is neither -- ``@database_sync_to_async``
    makes it one to await, which runs off the loop."""
    if _is_property(func):
        return func.name
    return None if func.decorator_list else f"{func.name}()"


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


def _self_call(node: ast.AST) -> str | None:
    """``name()`` for ``self.name()``, the key of a helper method."""
    if isinstance(node, ast.Call) and (name := _self_attr(node.func)):
        return f"{name}()"
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
    for a property and ``self.<name>()`` for a method that returns one as is."""
    while isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):  # qs[:10] stays lazy
        node = node.value
    if _self_attr(node) in lazy:
        return True
    if _self_call(node) in lazy:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in QUERYSET_METHODS:
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in FIELD_QUERYSET_METHODS
        and (node.args or node.keywords)
    ):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _is_orm(node.func.value, lazy) and node.func.attr not in EVALUATING_METHODS
    return _is_queryset(node, set())


def _looks_like_a_query(call: ast.Call) -> bool:
    """A query on a related manager, ``self.room.messages.count()``, whose receiver has no
    telling name. Told by its arguments: ``list.count(x)`` and ``dict.get(key)`` take one.
    A component's own attribute is not a manager unless a property makes it one, which
    ``_is_orm`` knows: ``self.history.first()`` is a list's."""
    if call.args or _self_attr(call.func.value):
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
            # ``[r for r in rows if self.items]``: each condition is a test, async for or not.
            found += [e for g in node.generators for test in g.ifs for e in _truth_tested(test, lazy)]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SYNC_CONSUMERS:
            found += [ast.unparse(node) for arg in node.args if _is_orm(arg, lazy)]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if _self_call(node) in evaluating or (
                node.func.attr in EVALUATING_METHODS and (_is_orm(node.func.value, lazy) or _looks_like_a_query(node))
            ):
                found.append(ast.unparse(node))
        # Truth and membership run the query too: ``if qs:``, ``not qs``, ``qs or []``, ``x in qs``.
        elif isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            found += _truth_tested(node.test, lazy)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            found += _truth_tested(node.operand, lazy)
        elif isinstance(node, ast.BoolOp):
            # The last operand is the value, not a test: ``self.cached or Item.objects.all()``
            # hands the QuerySet on unevaluated.
            found += [e for v in node.values[:-1] for e in _truth_tested(v, lazy)]
        elif isinstance(node, ast.Compare):
            found += [
                ast.unparse(node)
                for op, right in zip(node.ops, node.comparators, strict=True)
                if isinstance(op, (ast.In, ast.NotIn)) and _is_orm(right, lazy)
            ]
        elif isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
            if _is_orm(node.value, lazy):  # qs[0]
                found.append(ast.unparse(node))
        elif _self_attr(node) in evaluating:
            found.append(ast.unparse(node))
    # A BoolOp under ``if`` is met twice, as the test and as itself.
    return list(dict.fromkeys(found))


def _truth_tested(node: ast.expr, lazy: set[str]) -> list[str]:
    """The QuerySets asked for their truth where ``node`` is: every operand of an
    ``and``/``or`` that is itself a test, the last one included."""
    if isinstance(node, ast.BoolOp):
        return [e for v in node.values for e in _truth_tested(v, lazy)]
    return [f"bool({ast.unparse(node)})"] if _is_orm(node, lazy) else []


def _classify(funcs: list[ast.FunctionDef], lazy: set[str], evaluating: set[str]) -> None:
    """Add to ``lazy`` the properties and methods that return a QuerySet as is, and to
    ``evaluating`` those that run one, until nothing changes: one may reach the ORM
    through another, declared before or after it."""
    changed = True
    while changed:
        changed = False
        for func in funcs:
            key = _key(func)
            if key is None or key in evaluating:
                continue
            if _evaluations(func, lazy, evaluating):
                evaluating.add(key)
                lazy.discard(key)
                changed = True
            elif key not in lazy and any(
                isinstance(n, ast.Return) and n.value is not None and _is_orm(n.value, lazy) for n in _own_nodes(func)
            ):
                lazy.add(key)
                changed = True


def _property_offenders(blocks: list[str]) -> list[str]:
    """Async functions that run a query through a synchronous property or helper (#145, #149).

    A property the template reads runs off the event loop. The same property read by
    a handler runs on it, and ``SynchronousOnlyOperation`` is raised in production
    however innocent ``self.current_question`` looks; so does a plain method it
    calls. A property that only returns a QuerySet is fine -- nothing runs until
    someone evaluates it -- so a handler that evaluates *that* synchronously is
    caught too.

    ``blocks`` are one document's: a tutorial defines the class in one block and
    quotes a handler on its own further down, so a handler outside a class reads
    the names of the whole document. One inside a class reads its own class's
    first, where two classes give one name different answers. A block that does
    not parse on its own is skipped.
    """
    trees = []
    for block in blocks:
        try:
            trees.append(ast.parse(block))
        except SyntaxError:
            continue
    classes = [cls for tree in trees for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)]
    members = {cls: [n for n in cls.body if isinstance(n, ast.FunctionDef)] for cls in classes}

    lazy: set[str] = set()
    evaluating: set[str] = set()
    _classify([f for funcs in members.values() for f in funcs], lazy, evaluating)

    scope: dict[ast.AsyncFunctionDef, tuple[set[str], set[str]]] = {}
    for cls in classes:  # outer before inner, so a nested class's handlers end up with its own
        own = {_key(f) for f in members[cls]} - {None}
        cls_lazy, cls_evaluating = lazy - own, evaluating - own
        _classify(members[cls], cls_lazy, cls_evaluating)
        for node in ast.walk(cls):
            if isinstance(node, ast.AsyncFunctionDef):
                scope[node] = (cls_lazy, cls_evaluating)

    return [
        f"{func.name}: {found}"
        for tree in trees
        for func in ast.walk(tree)
        if isinstance(func, ast.AsyncFunctionDef)
        for found in _evaluations(func, *scope.get(func, (lazy, evaluating)))
    ]


def _fragment_offenders(block: str) -> list[str]:
    """A fragment that is not Python on its own: the plain form still shows, a manager
    chain handed to a synchronous consumer. Not after a dot -- ``.filter(`` and ``.all(``
    are the manager's own."""
    return re.findall(rf"(?<![.\w])(?:{'|'.join(SYNC_CONSUMERS)})\(\s*[\w.]*\.objects\.[^\n]*", block)


def test_a_fragment_is_scanned_for_the_plain_form():
    block = "x = {\nlist(Item.objects.all())\nqs = Item.objects.filter(pk__in=Other.objects.all())\n"
    block += "rows = Item.objects.filter(Other.objects.all())"
    assert _fragment_offenders(block) == ["list(Item.objects.all())"]


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
            offenders += _fragment_offenders(block)
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
        "async def joined(self):\n    self.any = bool(Item.objects.filter(done=False))",
        "async def joined(self):\n    self.names = dict(Item.objects.values_list('id', 'name'))",
        "async def joined(self):\n    self.top = max(Item.objects.filter(done=False))",
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
        # Through a synchronous helper method rather than a property (#149).
        "class Quiz(Component):\n    def _current(self):\n        return self.quiz.questions.all()[self.index]\n"
        "    async def answer(self, choice_id: int):\n        question = self._current()",
        "class A(Component):\n    def _pending(self):\n        return Item.objects.filter(done=False)\n"
        "    async def clear(self):\n        for item in self._pending():\n            await item.adelete()",
        # values_list() is the ORM's alone; values() is when it names fields (#149).
        "class A(Component):\n    async def joined(self):\n"
        "        self.tags = list(self.post.tags.values_list('name', flat=True))",
        "class A(Component):\n    async def joined(self):\n        self.rows = list(self.post.tags.values('name'))",
        # Truth and membership evaluate a QuerySet as much as a loop does (#149).
        "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
        "    async def joined(self):\n        if self.items:\n            pass",
        "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
        "    async def pick(self, item):\n        self.ok = item in self.items",
        "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
        "    async def joined(self):\n        self.empty = not self.items",
        "class A(Component):\n    async def joined(self):\n        self.any = Item.objects.filter(done=False) or []",
        # The last operand is tested too when the whole and/or is the test.
        "class A(Component):\n    async def joined(self):\n"
        "        if self.ready and Item.objects.filter(done=False):\n            pass",
        "class A(Component):\n    async def joined(self):\n"
        "        self.none = not (self.ready and Item.objects.filter(done=False))",
        # One document, two classes, one name: each handler is judged by its own class (#149).
        "class A(Component):\n    @property\n    def items(self):\n        return list(Item.objects.all())\n"
        "    async def joined(self):\n        self.n = len(self.items)\n"
        "class B(Component):\n    @property\n    def items(self):\n        return self.cached\n",
        # A comprehension's condition is a truth test like ``if`` (#151).
        "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
        "    async def joined(self):\n        self.rows = [r async for r in Row.objects.all() if self.items]",
        "class A(Component):\n    async def joined(self):\n"
        "        self.rows = [r for r in self.cached if r.ok and Item.objects.filter(done=False)]",
        # Builtins that iterate their argument as list() does (#151).
        *(
            "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
            f"    async def joined(self):\n        self.x = {call}"
            for call in [
                "any(self.items)",
                "all(self.items)",
                "sum(self.items)",
                "max(self.items)",
                "min(self.items)",
                "frozenset(self.items)",
                "dict(self.items.values_list('id', 'name'))",
                "next(iter(self.items))",
                "list(enumerate(self.items))",
                "zip(self.names, self.items)",
                "map(str, self.items)",
                "filter(None, self.items)",
            ]
        ),
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
        # A helper method handed to sync_to_async rather than called, or one decorated to be awaited (#149).
        "class A(Component):\n    def _count(self):\n        return len(list(Item.objects.all()))\n"
        "    async def joined(self):\n        self.n = await sync_to_async(self._count)()",
        "class A(Component):\n    @database_sync_to_async\n    def _count(self):\n        return Item.objects.count()\n"
        "    async def joined(self):\n        self.n = await self._count()",
        # A component's own list or dict, not a manager: its first() and get() are not queries (#149).
        "class A(Component):\n    async def undo(self):\n        self.last = self.history.first()\n"
        "        self.page = self.params.get(default=1)",
        # Truth of a list the handler built, and of a property that queries nothing (#149).
        "class A(Component):\n    @property\n    def picked(self):\n        return list(self.answers.values())\n"
        "    async def joined(self):\n        rows = [r async for r in Item.objects.all()]\n"
        "        if rows and not self.picked:\n            pass",
        # The last operand of and/or is the value, handed on unevaluated.
        "class A(Component):\n    async def joined(self):\n        rows = self.cached or Item.objects.all()\n"
        "        self.n = await rows.acount()",
        "class A(Component):\n    async def joined(self):\n"
        "        rows = self.show and Item.objects.filter(done=False)\n"
        "        self.names = [r.name async for r in rows]",
        # One document, two classes, one name: B's handler reads B's property, which queries nothing (#149).
        "class A(Component):\n    @property\n    def items(self):\n        return list(Item.objects.all())\n"
        "class B(Component):\n    @property\n    def items(self):\n        return self.cached\n"
        "    async def joined(self):\n        self.n = len(self.items)",
        # A comprehension's condition that queries nothing, and builtins over what is not a QuerySet (#151).
        "class A(Component):\n    @property\n    def items(self):\n        return self.list.items.all()\n"
        "    async def joined(self):\n        self.rows = [r async for r in self.items if r.done and self.ready]\n"
        "        self.top = max(self.scores)\n        self.ok = any(r.done for r in self.rows)\n"
        "        self.pairs = dict(zip(self.keys, self.values))",
    ],
)
def test_the_property_scan_passes_what_does_not_block(source):
    assert not _property_offenders([source])


def test_the_property_scan_reads_a_document_as_a_whole():
    """Tutorial 13 quotes ``answer()`` again on its own, away from the class."""
    handler = "async def answer(self, choice_id):\n    question = self.current_question"
    prop = _QUESTIONS + "    @property\n    def current_question(self):\n        return self.questions[self.index]\n"
    assert _property_offenders([prop, "not python {", handler]) == ["answer: self.current_question"]
