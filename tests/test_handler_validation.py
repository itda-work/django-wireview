"""Which methods ``validate_call`` wraps: the handlers a client may call, and nothing else (#127).

``_validate_handlers`` used to wrap every lowercase public method a class
defined, the framework's own included. ``LiveComponent.update_many`` is
annotated with ``t.Self``, which pydantic refuses to validate; the refusal was
swallowed as a ``TypeError`` until pydantic 2.13 made ``PydanticUserError`` a
``RuntimeError``, and from then on ``import wireview`` failed. The wrapping now
follows the dispatcher's rule (``wireview.core.handlers``), and an annotation
pydantic cannot validate costs that handler its validation, not the import.
"""

import logging
import typing as t

import pytest
from pydantic import validate_call

from wireview import Component, LiveComponent
from wireview.checks import iter_exposed_handlers
from wireview.core.component import _validate_handlers
from wireview.core.handlers import is_client_callable

pytestmark = pytest.mark.unit


def _is_wrapped(cls: type, name: str) -> bool:
    raw = vars(cls)[name]
    func = raw.__func__ if isinstance(raw, (classmethod, staticmethod)) else raw
    # validate_call's wrapper keeps the function it wraps as raw_function.
    return hasattr(func, "raw_function")


def _pydantic_validates_self() -> bool:
    """pydantic before 2.10 builds a validator for ``t.Self``; from 2.10 it refuses (#132)."""

    def pick(self, other: "t.Self") -> None:
        pass

    try:
        validate_call(config={"arbitrary_types_allowed": True})(pick)
    except Exception:
        return False
    return True


class Picker(Component, public=False):
    count: int = 0

    async def increment(self, by: int = 1) -> None:
        self.count += by

    async def pick(self, other: "t.Self") -> None:
        pass

    async def joined(self) -> None:
        await super().joined()

    async def handle_async(self, name: str, result: t.Any) -> None:
        pass

    def _helper(self, value: int) -> int:
        return value


class Row(LiveComponent, public=False):
    @classmethod
    async def update_many(cls, updates: "list[tuple[t.Self, dict[str, t.Any]]]") -> None:
        await super().update_many(updates)

    async def update(self, **assigns: t.Any) -> None:
        await super().update(**assigns)

    async def select(self, index: int) -> None:
        pass


def test_a_handler_annotated_with_self_does_not_break_the_class():
    # Defining Picker above is the test; it failed at import on pydantic 2.13.
    # Whether it is wrapped is pydantic's call, and the answer changed in 2.10.
    assert _is_wrapped(Picker, "pick") == _pydantic_validates_self()
    assert is_client_callable(Picker, "pick")


@pytest.mark.parametrize(
    "annotation",
    [
        # An unresolvable forward reference: refused by every pydantic in range.
        "NoSuchType",
        pytest.param(
            "t.Self",
            marks=pytest.mark.skipif(_pydantic_validates_self(), reason="pydantic before 2.10 validates t.Self"),
        ),
    ],
)
def test_a_handler_pydantic_cannot_validate_is_logged(caplog, annotation):
    class Doubtful(Component, public=False):
        async def pick(self, other: annotation) -> None:  # the string, as if written in quotes
            pass

    with caplog.at_level(logging.WARNING, logger="wireview"):
        _validate_handlers(Doubtful)

    assert "Doubtful.pick is not validated" in caplog.text


def test_user_handlers_are_validated():
    assert _is_wrapped(Picker, "increment")
    assert _is_wrapped(Row, "select")


@pytest.mark.parametrize(
    ("cls", "name"),
    [
        (Component, "handle_async"),
        (Component, "joined"),
        (LiveComponent, "update"),
        (LiveComponent, "update_many"),
        (LiveComponent, "send_to_parent"),
    ],
)
def test_framework_methods_are_not_wrapped(cls, name):
    assert not _is_wrapped(cls, name)


@pytest.mark.parametrize(
    ("cls", "name"),
    [(Picker, "joined"), (Picker, "handle_async"), (Row, "update"), (Row, "update_many")],
)
def test_an_overridden_lifecycle_method_is_not_wrapped(cls, name):
    # A client cannot call it, so there is nothing for validation to guard.
    assert not is_client_callable(cls, name)
    assert not _is_wrapped(cls, name)


@pytest.mark.parametrize("cls", [Picker, Row])
def test_what_is_wrapped_is_what_a_client_may_call(cls):
    own = {name for name in vars(cls) if callable(getattr(cls, name, None))}
    exposed = {name for name, _ in iter_exposed_handlers(cls)} & own
    wrapped = {name for name in own if _is_wrapped(cls, name)}

    # Everything wrapped is exposed; the exposed ones left out are the ones
    # pydantic cannot validate.
    assert wrapped <= exposed
    assert exposed - wrapped <= {"pick"}
