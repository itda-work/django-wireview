"""What this example is for: one component writing a row and other components
learning about it through a subscription and a named broadcast."""

import pytest
from wireview import mount

from .live import XNotificationBell, XNotificationCreator, XNotificationList
from .models import Notification


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_creator_writes_a_row_and_clears_the_form():
    view = await mount(XNotificationCreator, title="배포 완료", message="v1.2.0")

    await view.call("create")

    assert await Notification.objects.filter(title="배포 완료").aexists()
    assert view.component.title == ""
    assert view.component.message == ""


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_an_empty_title_writes_nothing():
    view = await mount(XNotificationCreator, title="   ", message="본문")

    await view.call("create")

    assert not await Notification.objects.filter(message="본문").aexists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_dismissing_deletes_and_tells_the_bell():
    notification = await Notification.objects.acreate(title="읽고 지울 것", message="…")
    view = await mount(XNotificationList)

    await view.call("dismiss", notification_id=notification.pk)

    assert not await Notification.objects.filter(pk=notification.pk).aexists()
    assert any(b["channel"] == "notifications-refresh" for b in view.wire.broadcasts)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_bell_subscribes_to_both_the_model_and_the_refresh_channel():
    assert "notifications.notification" in XNotificationBell._subscriptions
    assert "notifications-refresh" in XNotificationBell._subscriptions

    view = await mount(XNotificationBell)
    await view.call("toggle_dropdown")
    assert view.component.is_open is True
