"""What a page signed by one version of a component gets when it joins the next (#231).

A deploy restarts the processes and the open pages reconnect with the
``data-state`` the old code signed. The class under the same name may have
changed its fields since. The contract, one row per kind of change:

- **continues** -- the new class validates the old state: it joins from it.
- **reloads** -- it does not (a field with no default, a type the value does
  not fit, a model the stored pk would be read as another of): the page loads
  once, and the new code renders it with a state of its own.

Neither may be **silently wrong** (the join goes on from a state that means
something else now) or **dead** (``_join_failed``: the element stays as the old
page drew it, marked, until the user reloads). ``_join_failed`` is for the
application's code -- ``mount`` hooks, ``joined()`` -- that raises.

A deploy is played by signing with one class and swapping another in under the
same names in the registries (``deployed``).
"""

from __future__ import annotations

import contextlib
import typing as t
from dataclasses import dataclass

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from pydantic import field_validator
from testproj.bookmarks.models import Bookmark
from testproj.outbound import RecordingOutbound

from examples.search.models import Book
from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "fc/cart.html": "{% load wireview %}<p {% tag_header %}>cart</p>",
    "fc/shelf.html": "{% load wireview %}<main {% tag_header %}>{% live_component 'FcItem' id='item' %}</main>",
    "fc/item.html": "{% load wireview %}<p {% tag_header %}>item</p>",
}

#: What every joined() saw: (class, state)
JOINED: list[tuple[str, dict[str, t.Any]]] = []


class _Recording:
    async def joined(self) -> None:
        JOINED.append((type(self).__qualname__, self.model_dump(exclude={"user", "wire", "session"})))


# --- v1: what the open page was drawn by --------------------------------------


class FcCart(_Recording, Component):
    class Meta:
        template_name = "fc/cart.html"

    count: int = 3
    items: list[int] = []
    book: Book | None = None
    notes: list[str] = []
    secret: str = ""


class FcItem(_Recording, LiveComponent):
    class Meta:
        template_name = "fc/item.html"

    count: int = 3


class FcShelf(_Recording, Component):
    class Meta:
        template_name = "fc/shelf.html"


# --- v2: what the restarted process defines under the same names ---------------


class AddedWithDefault(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"

    count: int = 3
    items: list[int] = []
    currency: str = "KRW"


class AddedRequired(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"

    count: int = 3
    items: list[int] = []
    currency: str


class Removed(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"

    items: list[int] = []


class WidenedType(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"

    count: float = 3.0


class ChangedType(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"

    count: list[int] = []


class RenamedWithDefault(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"

    quantity: int = 1


class RenamedRequired(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"

    quantity: int


class OtherModel(_Recording, Component, public=False, name="FcCart"):
    """``book`` names another model now: the pk the page holds is a Book's."""

    class Meta:
        template_name = "fc/cart.html"

    book: Bookmark | None = None


class SameModel(_Recording, Component, public=False, name="FcCart"):
    """``Book | None`` written as ``Optional[Book]``: the same model, nothing to refuse."""

    class Meta:
        template_name = "fc/cart.html"

    book: t.Optional[Book] = None


class NowTemporary(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"
        temporary_assigns = ["notes"]

    notes: list[str] = []


class NowExcluded(_Recording, Component, public=False, name="FcCart"):
    class Meta:
        template_name = "fc/cart.html"
        exclude_fields = ["secret"]

    secret: str = ""


class ItemAddedRequired(_Recording, LiveComponent, public=False, name="FcItem"):
    class Meta:
        template_name = "fc/item.html"

    count: int = 3
    currency: str


class ItemChangedType(_Recording, LiveComponent, public=False, name="FcItem"):
    class Meta:
        template_name = "fc/item.html"

    count: list[int] = []


class ItemOtherModel(_Recording, LiveComponent, public=False, name="FcItem"):
    class Meta:
        template_name = "fc/item.html"

    count: int = 3
    book: Bookmark | None = None


class Gone(Component, public=False, name="FcGone"):
    class Meta:
        template_name = "fc/cart.html"


@contextlib.contextmanager
def deployed(new: type[Component]):
    """Put ``new`` where the registries hold the class it shares names with, as a restart would."""
    registries = [Component._all, Component._by_fqn, Component._by_app]
    if issubclass(new, LiveComponent):
        registries += [LiveComponent._live_all, LiveComponent._live_by_fqn]
    saved = [dict(registry) for registry in registries]
    old = Component._by_fqn[new._fqn]
    try:
        for registry in registries:
            for key, value in list(registry.items()):
                if value is old:
                    registry[key] = new
        yield
    finally:
        for registry, before in zip(registries, saved):
            registry.clear()
            registry.update(before)


@contextlib.contextmanager
def removed(cls: type[Component]):
    """No class answers to ``cls``'s names any more: renamed or deleted."""
    registries = [Component._all, Component._by_fqn, Component._by_app]
    saved = [dict(registry) for registry in registries]
    try:
        for registry in registries:
            for key, value in list(registry.items()):
                if value is cls:
                    del registry[key]
        yield
    finally:
        for registry, before in zip(registries, saved):
            registry.clear()
            registry.update(before)


@pytest.fixture(autouse=True)
def _templates():
    JOINED.clear()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


def build(cls: type[Component], **state: t.Any) -> Component:
    return cls(user=AnonymousUser(), wire=WireviewMeta(params={}), **state)


def make_session() -> tuple[WireviewConsumer, RecordingOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), vsn=PROTOCOL_VERSION)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = RecordingOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def outcome(outbound: RecordingOutbound) -> str:
    """``continues``, ``reloads`` or ``dead``, from what the page heard."""
    commands = [command for command, _ in outbound.commands]
    if "reload" in commands:
        return "reloads"
    if any(command == "error" and payload.get("during") == "join" for command, payload in outbound.commands):
        return "dead"
    assert "render" in commands, outbound.commands
    return "continues"


@dataclass
class Row:
    change: str
    new: type[Component]
    expected: str
    #: What joined() sees when it continues: a subset of its fields
    state: dict[str, t.Any] | None = None


async def _book() -> Book:
    from asgiref.sync import sync_to_async

    return await sync_to_async(Book.objects.create)(title="Dune", author="Herbert")


async def _bookmark(pk: int) -> None:
    from asgiref.sync import sync_to_async

    await sync_to_async(Bookmark.objects.create)(pk=pk, title="not the book", url="https://example.com")


ROOT_ROWS = [
    Row("added with a default", AddedWithDefault, "continues", {"count": 5, "items": [1, 2], "currency": "KRW"}),
    Row("added without a default", AddedRequired, "reloads"),
    Row("removed", Removed, "continues", {"items": [1, 2]}),
    Row("widened type (int -> float)", WidenedType, "continues", {"count": 5.0}),
    Row("incompatible type (int -> list[int])", ChangedType, "reloads"),
    Row("renamed, with a default", RenamedWithDefault, "continues", {"quantity": 1}),
    Row("renamed, without a default", RenamedRequired, "reloads"),
    Row("model annotation names another model", OtherModel, "reloads"),
    Row("model annotation respelled, same model", SameModel, "continues"),
    Row("field becomes a temporary assign", NowTemporary, "continues", {"notes": []}),
    Row("field becomes excluded", NowExcluded, "continues", {"secret": ""}),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("row", ROOT_ROWS, ids=lambda row: row.change)
async def test_a_root_joins_after_its_class_changed(row: Row):
    book = await _book()
    # A Bookmark under the Book's pk: what OtherModel would read the stored pk as
    await _bookmark(book.pk)
    token = sign_state(build(FcCart, id="cart", count=5, items=[1, 2], book=book, notes=["old"], secret="s3"))
    session, outbound = make_session()

    with deployed(row.new):
        await session.command_join("FcCart", token)

    assert outcome(outbound) == row.expected
    if row.expected == "continues":
        assert JOINED and JOINED[-1][0] == row.new.__qualname__
        seen = JOINED[-1][1]
        if row.state is not None:
            assert {key: seen[key] for key in row.state} == row.state
        if row.new is SameModel:
            assert seen["book"] == book.pk  # still the Book
    else:
        assert not JOINED, "the new class ran joined() over a state it refused"


# --- what a parent's join carries for the components under it -------------------


class ItemAddedWithDefault(_Recording, LiveComponent, public=False, name="FcItem"):
    class Meta:
        template_name = "fc/item.html"

    count: int = 3
    currency: str = "KRW"


@dataclass
class ChildRow:
    change: str
    new: type[Component]
    #: The child's ``count`` after the parent's join: 5 from the page's state, or what a fresh one starts with
    count: t.Any


LIVE_ROWS = [
    ChildRow("unchanged", FcItem, 5),
    ChildRow("added with a default", ItemAddedWithDefault, 5),
    ChildRow("incompatible type (int -> list[int])", ItemChangedType, []),
    ChildRow("model annotation names another model", ItemOtherModel, 3),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("row", LIVE_ROWS, ids=lambda row: row.change)
async def test_a_live_component_whose_class_changed_starts_anew_and_its_parent_continues(row: ChildRow):
    """A child's state the new class refuses is dropped, as one that does not verify is: the parent goes on."""
    book = await _book()
    await _bookmark(book.pk)
    item = build(FcItem, id="item", count=5)
    # ItemOtherModel's field: what the old class signed for it is a Book's pk
    item_token = sign_state(item)
    if row.new is ItemOtherModel:
        item_token = sign_state(build(_ItemWithBook, id="item", count=5, book=book))
    session, outbound = make_session()

    with deployed(row.new):
        await session.command_join(
            "FcShelf", sign_state(build(FcShelf, id="shelf")), children={"item": ["FcItem", item_token]}
        )

    assert outcome(outbound) == "continues"
    child = session.repo.get("item")
    assert type(child) is row.new
    assert child.count == row.count  # type: ignore[attr-defined]
    if row.new is ItemOtherModel:
        assert child.book is None  # type: ignore[attr-defined]


class _ItemWithBook(_Recording, LiveComponent, public=False, name="FcItem"):
    """FcItem as a version that stored a Book: signs under FcItem's names."""

    class Meta:
        template_name = "fc/item.html"

    count: int = 3
    book: Book | None = None


TEMPLATES["fc/nest.html"] = "{% load wireview %}<main {% tag_header %}>{% component 'FcCart' id='inner' %}</main>"


class FcNest(Component):
    class Meta:
        template_name = "fc/nest.html"


@pytest.mark.parametrize(
    ("new", "count"),
    [(AddedWithDefault, 5), (ChangedType, []), (WidenedType, 5.0)],
    ids=["added with a default", "incompatible type", "widened type"],
)
async def test_a_nested_component_whose_class_changed_starts_anew_and_its_drawer_continues(new, count):
    session, outbound = make_session()
    inner = sign_state(build(FcCart, id="inner", count=5))

    with deployed(new):
        await session.command_join(
            "FcNest", sign_state(build(FcNest, id="nest")), children={"inner": ["FcCart", inner]}
        )

    assert outcome(outbound) == "continues"
    assert session.repo.get("inner").count == count  # type: ignore[union-attr]


# --- the class itself ------------------------------------------------------------


async def test_a_class_renamed_or_deleted_reloads():
    session, outbound = make_session()
    token = sign_state(build(FcCart, id="cart"))

    with removed(FcCart):
        await session.command_join("FcCart", token)

    assert [command for command, _ in outbound.commands] == ["reload"]
    assert outbound.commands[0][1]["reason"] == "invalid"


# --- where the line is -----------------------------------------------------------


class RefusesOld(_Recording, Component, public=False, name="FcCart"):
    """The application's own validator: a deploy may refuse a state its fields would take."""

    class Meta:
        template_name = "fc/cart.html"

    count: int = 3

    @field_validator("count")
    @classmethod
    def _at_most_four(cls, value: int) -> int:
        if value > 4:
            raise ValueError("at most four now")
        return value


class FailsInJoined(Component, public=False, name="FcCart"):
    """Takes the state; its own code raises: that is a join failure, not a stale page."""

    class Meta:
        template_name = "fc/cart.html"

    count: int = 3

    async def joined(self) -> None:
        raise RuntimeError("the application's code")


async def test_a_validator_of_the_application_refusing_the_state_reloads():
    session, outbound = make_session()
    with deployed(RefusesOld):
        await session.command_join("FcCart", sign_state(build(FcCart, id="cart", count=5)))
    assert outcome(outbound) == "reloads"
    assert outbound.commands[-1][1] == {"id": "cart", "reason": "stale"}


async def test_the_application_raising_in_joined_is_still_a_join_failure():
    session, outbound = make_session()
    with deployed(FailsInJoined):
        await session.command_join("FcCart", sign_state(build(FcCart, id="cart", count=5)))
    assert outcome(outbound) == "dead"
    assert "reload" not in [command for command, _ in outbound.commands]


async def test_the_reload_answers_the_join_and_names_the_component():
    session, outbound = make_session()
    with deployed(AddedRequired):
        await session.command_join("FcCart", sign_state(build(FcCart, id="cart")), ref=7)
    assert outbound.commands == [("reload", {"id": "cart", "reason": "stale", "ref": 7})]
    assert session.repo.get("cart") is None


async def test_the_page_the_new_class_draws_joins():
    """No loop: what the reload brings is signed by the new class, and it joins."""
    session, outbound = make_session()
    with deployed(AddedRequired):
        fresh = sign_state(build(AddedRequired, id="cart", currency="USD"))
        await session.command_join("FcCart", fresh)
    assert outcome(outbound) == "continues"


@pytest.mark.django_db(transaction=True)
async def test_a_token_from_a_server_that_carried_no_model_labels_is_not_checked():
    """A rolling deploy: the older server's tokens have no ``m``, and join as they did."""
    from wireview.core import state as state_module

    book = await _book()
    component = build(FcCart, id="cart", book=book)
    original = state_module.model_fields_of
    state_module._MODEL_FIELDS.pop(FcCart, None)
    try:
        state_module.model_fields_of = lambda cls: None  # type: ignore[assignment]
        token = sign_state(component)
    finally:
        state_module.model_fields_of = original
    session, outbound = make_session()
    await session.command_join("FcCart", token)
    assert outcome(outbound) == "continues"
    assert session.repo.get("cart").book == book  # type: ignore[union-attr]
