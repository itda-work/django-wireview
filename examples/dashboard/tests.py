"""What this example is for: composing several components on one page, each
loading its own slow data through AsyncResult instead of blocking the first
render."""

import asyncio
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from wireview import mount

from .live import XActivityFeed, XDashboard, XStatCard
from .models import Activity, Stat


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_dashboard_starts_from_url_parameters():
    view = await mount(XDashboard, params={"tab": "sales", "range": "30d"})

    assert view.component.active_tab == "sales"
    assert view.component.date_range == "30d"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_page_draws_its_cards_before_any_of_them_has_loaded():
    """The first response holds every nested component, each in its loading state."""
    view = await mount(XDashboard)

    html = view.render() or ""

    for card in ("stat-revenue", "stat-users", "stat-orders", "stat-conversion", "activity-feed"):
        assert f'id="{card}"' in html
    assert html.count("Initializing...") == 4


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_changing_a_tab_writes_it_back_to_the_url():
    view = await mount(XDashboard)

    await view.call("change_tab", tab="revenue")

    assert view.component.active_tab == "revenue"
    assert view.wire.params["tab"] == "revenue"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_stat_card_starts_loading_and_does_not_block_the_mount():
    await Stat.objects.acreate(name="revenue", label="매출", value=100)
    view = await mount(XStatCard, stat_name="revenue")

    # joined() hands the query to assign_async, so the card is renderable at once
    assert view.component.data is not None
    assert view.component.data.loading or view.component.data.ok


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_loaded_stat_card_renders():
    # AsyncResult[Stat]: the row is signed as its pk; it failed to sign before #113
    await Stat.objects.acreate(name="signups", label="가입", value=100, change_percent=5)
    view = await mount(XStatCard, stat_name="signups")
    await asyncio.gather(*list(view.component._assign_tasks))

    assert view.component.data.ok
    assert "가입" in view.render()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_activity_feed_subscribes_to_its_model():
    assert "dashboard.activity" in XActivityFeed._meta.subscriptions

    await Activity.objects.acreate(type="login", description="로그인")
    view = await mount(XActivityFeed)

    assert any(m.get("type") == "stream_op" for m in view.sent_messages)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_load_more_loads_the_next_page_and_stops_at_the_end():
    """It counted the page's items from a field nothing filled, so every click loaded the first page again."""
    for n in range(25):
        await Activity.objects.acreate(type="login", user_name=f"u{n}", description=f"activity {n}")
    view = await mount(XActivityFeed)
    first = [item["id"] for item in view.stream_items("activities")]

    pages = []
    for _ in range(2):
        view.clear_messages()
        await view.call("load_more")
        [op] = view.stream_ops("activities")
        assert (op["op"], op["at"]) == ("insert", -1), "one insert, under what the page shows"
        pages.append([item["id"] for item in op["items"]])

    shown = first + pages[0] + pages[1]
    assert [len(first), *map(len, pages)] == [10, 10, 5]
    assert sorted(shown) == sorted([f"activities-{a.pk}" async for a in Activity.objects.all()])
    assert view.component.has_more is False


def _seed(count: int, *, same_time: bool) -> list[Activity]:
    """``count`` activities, newest first, a second apart or all at one time."""
    now = timezone.now()
    activities = [
        Activity.objects.create(type="login", user_name=f"u{n}", description=f"activity {n}") for n in range(count)
    ]
    for n, activity in enumerate(activities):
        activity.created_at = now if same_time else now - timedelta(seconds=count - n)
        activity.save(update_fields=["created_at"])
    return sorted(activities, key=lambda a: (a.created_at, a.id), reverse=True)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_time", [False, True], ids=["a second apart", "all at one time"])
async def test_the_next_page_follows_the_oldest_shown_whatever_was_added_or_deleted(same_time):
    """An offset moved with each activity added on top or deleted: the next page repeated or skipped one.

    The cursor is (created_at, id), the page's oldest. With the time alone, the
    activities that share it are lost or shown again.
    """
    seeded = await sync_to_async(_seed)(25, same_time=same_time)
    ids = [f"activities-{a.pk}" for a in seeded]
    view = await mount(XActivityFeed)
    first = [item["id"] for item in view.stream_items("activities")]
    # Meanwhile: two new ones on top, and one of the first page deleted
    for n in range(2):
        await Activity.objects.acreate(type="login", user_name="new", description=f"new {n}")
    await seeded[3].adelete()

    pages = []
    for _ in range(2):
        view.clear_messages()
        await view.call("load_more")
        pages.append([item["id"] for item in view.stream_items("activities")])

    assert [len(first), *map(len, pages)] == [10, 10, 5]
    assert first + pages[0] + pages[1] == ids
    assert view.component.has_more is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_empty_feeds_note_is_outside_the_list_a_reset_empties():
    view = await mount(XActivityFeed)

    html = view.render() or ""

    start = html.index('wire-stream="activities"')
    assert "No activity yet" not in html[start : html.index("</ul>", start)]
    assert "No activity yet" in html
