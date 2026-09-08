"""What this example is for: composing several components on one page, each
loading its own slow data through AsyncResult instead of blocking the first
render."""

import pytest
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
async def test_changing_a_tab_writes_it_back_to_the_url():
    view = await mount(XDashboard)

    await view.call("change_tab", tab="revenue")

    assert view.component.active_tab == "revenue"
    assert view.wire.params["tab"] == "revenue"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_stat_card_starts_loading_and_does_not_block_the_mount():
    await Stat.objects.acreate(name="revenue", label="매출", value=100)
    view = await mount(XStatCard, stat_name="revenue")

    # joined() hands the query to assign_async, so the card is renderable at once
    assert view.component.data is not None
    assert view.component.data.loading or view.component.data.ok


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_activity_feed_subscribes_to_its_model():
    assert "dashboard.activity" in XActivityFeed._subscriptions

    await Activity.objects.acreate(type="login", description="로그인")
    view = await mount(XActivityFeed)

    assert any(m.get("type") == "stream_op" for m in view.sent_messages)
