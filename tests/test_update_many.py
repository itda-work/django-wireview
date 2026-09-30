"""A parent render hands every changed child of one class to ``update_many()`` at once (GAP-035, #74).

Each child's ``update()`` looking up its own row was N queries for N rows. The
hook exists so it can be one, and ``assertNumQueries`` says whether it is.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

TEMPLATES = {
    "um/list.html": (
        "{% load wireview %}<ul {% tag_header %}>"
        "{% for pk in this.user_ids %}"
        "{% live_component this.row id=forloop.counter|stringformat:'s'|add:'-row' user_id=pk %}"
        "{% endfor %}"
        "</ul>"
    ),
    "um/row.html": "{% load wireview %}<li {% live_tag_header %}>{{ this.username }}</li>",
}

CALLS: list[tuple[str, t.Any]] = []


class UmBatchedRow(LiveComponent):
    class Meta:
        template_name = "um/row.html"

    user_id: int
    username: str = ""

    @classmethod
    async def update_many(cls, updates):
        CALLS.append(("update_many", [c.id for c, _ in updates]))
        await super().update_many(updates)
        # Sync ORM on purpose: the async one runs on another thread's connection,
        # where the test's CaptureQueriesContext cannot count it.
        names = dict(User.objects.filter(pk__in=[c.user_id for c, _ in updates]).values_list("pk", "username"))
        for component, _ in updates:
            component.username = names.get(component.user_id, "")


class UmPlainRow(LiveComponent):
    class Meta:
        template_name = "um/row.html"

    user_id: int
    username: str = ""

    async def update(self, **assigns):
        CALLS.append(("update", self.id))
        if self.id == "2-row":
            raise RuntimeError("one child's update broke")
        await super().update(**assigns)


class UmList(Component):
    class Meta:
        template_name = "um/list.html"

    row: str = "UmBatchedRow"
    user_ids: list[int] = []


class FakeOutbound:
    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        pass

    async def subscribe(self, topic: str) -> None:
        pass

    async def unsubscribe(self, topic: str) -> None:
        pass


@pytest.fixture(autouse=True)
def _templates():
    CALLS.clear()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


async def page(**state) -> tuple[WireviewConsumer, Component]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser(), channel_name="c", vsn=PROTOCOL_VERSION)
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "c"
    consumer.outbound = FakeOutbound()  # type: ignore[assignment]
    component = await consumer.repo.join("UmList", {"id": "p", **state})
    await consumer.send_render(component)
    return consumer, component


async def test_every_changed_row_of_a_class_arrives_in_one_call_and_loads_in_one_query():
    first = [await User.objects.acreate(username=f"a{n}") for n in range(5)]
    second = [await User.objects.acreate(username=f"b{n}") for n in range(5)]
    consumer, component = await page(user_ids=[u.pk for u in first])
    CALLS.clear()

    # Rows are keyed by position, so a new list is a user_id change for every row.
    component.user_ids = [u.pk for u in second]
    with CaptureQueriesContext(connection) as queries:
        await consumer.send_render(component)

    assert CALLS == [("update_many", [f"{n}-row" for n in range(1, 6)])]
    assert len([q for q in queries.captured_queries if "auth_user" in q["sql"]]) == 1
    rows = sorted((c for c in consumer.repo.components.values() if isinstance(c, UmBatchedRow)), key=lambda c: c.id)
    assert [r.username for r in rows] == [f"b{n}" for n in range(5)]


async def test_a_class_without_update_many_gets_each_update_and_one_failure_stops_no_other():
    first = [await User.objects.acreate(username=f"c{n}") for n in range(3)]
    second = [await User.objects.acreate(username=f"d{n}") for n in range(3)]
    consumer, component = await page(row="UmPlainRow", user_ids=[u.pk for u in first])
    CALLS.clear()

    component.user_ids = [u.pk for u in second]
    await consumer.send_render(component)

    assert CALLS == [("update", "1-row"), ("update", "2-row"), ("update", "3-row")]
    row3 = consumer.repo.get("3-row")
    assert row3.user_id == second[2].pk, "the row after the one that raised was still updated"
