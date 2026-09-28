"""Model instances in component state go into data-state as their pks, however deep (#113).

Only a field typed exactly as a model was converted; ``list[Book]``,
``dict[str, Book]`` and ``AsyncResult[Book]`` failed to sign, so a component
holding query results could not render. The examples and several tutorials had
been rewritten around dicts to avoid it. Here the whole round trip is real:
sign, unsign, and the repository's join, which loads the rows back.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from testproj.bookmarks.models import Bookmark

from wireview import AsyncResult, Component, mount
from wireview.core.meta import WireviewMeta
from wireview.core.state import sign_state, unsign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


class MsShelf(Component):
    class Meta:
        template_name = "ms/shelf.html"

    pinned: Bookmark | None = None
    rows: list[Bookmark] = []
    by_tag: dict[str, Bookmark] = {}
    loaded: AsyncResult[Bookmark] | None = None
    later: AsyncResult[list[Bookmark]] | None = None


def make(**state) -> MsShelf:
    return MsShelf(user=AnonymousUser(), wire=WireviewMeta(params={}), id="shelf", **state)


def rejoin(component: Component) -> MsShelf:
    """What a browser's join does: the signed state back through the repository."""
    state = unsign_state(sign_state(component), "MsShelf")
    return ComponentRepository(is_live=True, user=AnonymousUser()).build("MsShelf", state)  # type: ignore[return-value]


@pytest.fixture
def marks():
    return [Bookmark.objects.create(title=f"b{n}", url=f"https://x/{n}") for n in range(3)]


def test_every_place_a_model_can_sit_survives_the_round_trip(marks):
    a, b, c = marks
    shelf = make(
        pinned=a,
        rows=[c, a, b],
        by_tag={"first": b},
        loaded=AsyncResult.success(c),
        later=AsyncResult.success([b, c]),
    )

    again = rejoin(shelf)

    assert again.pinned == a
    assert again.rows == [c, a, b], "one query, and the order the component had"
    assert again.by_tag == {"first": b}
    assert again.loaded.ok and again.loaded.result == c
    assert again.later.result == [b, c]


def test_the_signed_state_holds_pks_not_rows(marks):
    a, b, _ = marks
    state = unsign_state(sign_state(make(rows=[a, b], pinned=a)), "MsShelf")

    assert state["rows"] == [a.pk, b.pk]
    assert state["pinned"] == a.pk


def test_a_list_loads_in_one_query(marks):
    shelf = make(rows=marks)
    state = unsign_state(sign_state(shelf), "MsShelf")

    with CaptureQueriesContext(connection) as queries:
        ComponentRepository(is_live=True, user=AnonymousUser()).build("MsShelf", state)

    assert len(queries) == 1


def test_a_row_deleted_meanwhile_drops_out_or_becomes_none(marks):
    a, b, c = marks
    state = unsign_state(sign_state(make(pinned=b, rows=[a, b, c])), "MsShelf")
    b.delete()

    again = ComponentRepository(is_live=True, user=AnonymousUser()).build("MsShelf", state)

    assert again.rows == [a, c]
    assert again.pinned is None


def test_a_failed_async_result_keeps_its_message(marks):
    again = rejoin(make(loaded=AsyncResult.failure(LookupError("gone"))))

    assert again.loaded.failed and again.loaded.error_message == "gone"


@pytest.mark.asyncio
async def test_a_component_holding_rows_renders():
    # What the search example could not do once it had results
    rows = [await Bookmark.objects.acreate(title=f"t{n}", url=f"https://y/{n}") for n in range(2)]
    template = (
        "{% load wireview %}<ul {% tag_header %}>{% for row in this.rows %}<li>{{ row.title }}</li>{% endfor %}</ul>"
    )
    loaders = [("django.template.loaders.locmem.Loader", {"ms/shelf.html": template})]
    with override_settings(
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "OPTIONS": {"loaders": loaders}}]
    ):
        view = await mount(MsShelf, rows=rows)
        html = view.render()

    assert "<li>t0</li><li>t1</li>" in html


@pytest.mark.django_db(transaction=True)
def test_a_list_loads_as_the_first_query_on_a_fresh_connection(marks):
    # Channels closes stale connections before a message; on Django 6.0 with SQLite,
    # in_bulk() on a connection not yet open raised in bulk_batch_size
    state = unsign_state(sign_state(make(rows=marks)), "MsShelf")
    connection.close()

    again = ComponentRepository(is_live=True, user=AnonymousUser()).build("MsShelf", state)

    assert again.rows == marks
