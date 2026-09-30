"""What this example is for: notifications that belong to one user.

A stored notification (pattern A) reaches its owner's pages through
auto-broadcast; a toast (pattern B) reaches them through a named channel and is
shown with put_flash(). Neither reaches anyone else, and no handler touches a
notification that is not the caller's, whatever id the browser sends.
"""

import asyncio

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from playwright.sync_api import expect
from testproj.e2e_browser import expect_count, expect_text, wait_live
from testproj.e2e_server import serve

from wireview import ModelAction, mount, toast_channel

from .live import XNotificationBell, XNotificationCreator, XNotificationList
from .models import Notification
from .services import notifications_channel, notify, refresh_channel

# Handlers write from the executor thread, on a connection of its own, so a test
# transaction on this thread would neither see those rows nor roll them back --
# and would hold SQLite's write lock while the handler waits on it. Each test
# commits and the tables are flushed after it instead.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def alice():
    return get_user_model().objects.create(username="alice")


@pytest.fixture
def bob():
    return get_user_model().objects.create(username="bob")


async def _listen(*groups: str) -> tuple[object, str]:
    """A channel on the real layer, in ``groups``: what a connection would hear."""
    layer = get_channel_layer()
    channel = await layer.new_channel()
    for group in groups:
        await layer.group_add(group, channel)
    return layer, channel


async def _heard(layer, channel: str) -> list[dict]:
    messages = []
    while True:
        try:
            messages.append(await asyncio.wait_for(layer.receive(channel), 0.2))
        except asyncio.TimeoutError:
            return messages


# Pattern A: stored notifications


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_creator_writes_a_row_for_the_recipient_and_clears_the_form(alice, bob):
    view = await mount(XNotificationCreator, user=alice, recipient="bob", title="배포 완료", message="v1.2.0")

    await view.call("create")

    notification = await Notification.objects.aget(title="배포 완료")
    assert notification.user_id == bob.pk
    assert view.component.title == ""
    assert view.component.message == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_with_no_recipient_the_sender_gets_it(alice):
    view = await mount(XNotificationCreator, user=alice, title="메모")

    await view.call("create")

    assert await Notification.objects.filter(title="메모", user=alice).aexists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_keystroke_that_enables_the_buttons_is_rendered(alice):
    """set_title skips the render of most keystrokes; the one that ends the
    disabled state must go out, or the buttons stay disabled (#41, #117)."""
    view = await mount(XNotificationCreator, user=alice)
    await view.render_diff()  # the page as it was first drawn

    await view.call("set_title", title="첫")
    assert await view.render_diff() is not None

    await view.call("set_title", title="첫 글자")
    assert await view.render_diff() is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_empty_title_writes_nothing(alice):
    view = await mount(XNotificationCreator, user=alice, title="   ", message="본문")

    await view.call("create")

    assert not await Notification.objects.filter(message="본문").aexists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_anonymous_sender_with_no_recipient_is_told_and_writes_nothing():
    view = await mount(XNotificationCreator, user=AnonymousUser(), title="누구에게?")

    await view.call("create")

    assert not await Notification.objects.filter(title="누구에게?").aexists()
    assert [m["flash_type"] for m in view.sent_messages if m.get("type") == "flash"] == ["error"]


@pytest.mark.unit
def test_a_saved_notification_is_announced_on_its_owners_channel_only(alice, bob, django_capture_on_commit_callbacks):
    layer, alices = async_to_sync(_listen)(notifications_channel(alice))
    _, bobs = async_to_sync(_listen)(notifications_channel(bob))

    with django_capture_on_commit_callbacks(execute=True):
        notification = notify(alice, "alice 것")

    (heard,) = async_to_sync(_heard)(layer, alices)
    assert heard["type"] == "model_mutation"
    assert heard["action"] == ModelAction.CREATED
    assert str(notification.pk) in heard["instance"]
    assert async_to_sync(_heard)(layer, bobs) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_list_shows_only_the_users_own_notifications(alice, bob):
    await Notification.objects.acreate(user=alice, title="alice 것", message="")
    await Notification.objects.acreate(user=bob, title="bob 것", message="")

    view = await mount(XNotificationList, user=alice)

    assert "alice 것" in view.stream_html("notifications")
    assert "bob 것" not in view.stream_html("notifications")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_components_listen_on_the_users_channels_and_an_anonymous_visitor_on_none(alice):
    bell = await mount(XNotificationBell, user=alice)
    listing = await mount(XNotificationList, user=alice)

    assert bell.component.get_subscriptions() == {
        notifications_channel(alice),
        refresh_channel(alice),
    }
    assert listing.component.get_subscriptions() == {notifications_channel(alice)}

    assert (await mount(XNotificationBell, user=AnonymousUser())).component.get_subscriptions() == set()
    assert (await mount(XNotificationList, user=AnonymousUser())).component.get_subscriptions() == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dismissing_deletes_only_the_users_own(alice, bob):
    mine = await Notification.objects.acreate(user=alice, title="내 것", message="")
    theirs = await Notification.objects.acreate(user=bob, title="남의 것", message="")
    view = await mount(XNotificationList, user=alice)

    await view.call("dismiss", notification_id=theirs.pk)
    assert await Notification.objects.filter(pk=theirs.pk).aexists()

    await view.call("dismiss", notification_id=mine.pk)
    assert not await Notification.objects.filter(pk=mine.pk).aexists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_marking_read_touches_only_the_users_own_and_tells_their_bell(alice, bob):
    mine = await Notification.objects.acreate(user=alice, title="내 것", message="")
    theirs = await Notification.objects.acreate(user=bob, title="남의 것", message="")
    view = await mount(XNotificationList, user=alice)

    await view.call("mark_as_read", notification_id=theirs.pk)
    assert not (await Notification.objects.aget(pk=theirs.pk)).is_read
    assert view.broadcasts == []

    await view.call("mark_as_read", notification_id=mine.pk)
    assert (await Notification.objects.aget(pk=mine.pk)).is_read
    assert [b["channel"] for b in view.broadcasts] == [refresh_channel(alice)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clearing_all_leaves_other_users_alone(alice, bob):
    await Notification.objects.acreate(user=alice, title="내 것", message="")
    await Notification.objects.acreate(user=bob, title="남의 것", message="")
    view = await mount(XNotificationList, user=alice)

    await view.call("clear_all")

    assert await Notification.objects.filter(user=alice).acount() == 0
    assert await Notification.objects.filter(user=bob).acount() == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_new_notification_is_streamed_in_and_pulses(alice):
    # The path auto-broadcast takes when a row is created anywhere
    view = await mount(XNotificationList, user=alice)
    notification = await Notification.objects.acreate(user=alice, title="새 알림", message="…")

    await view.component.mutation(notifications_channel(alice), ModelAction.CREATED, notification)

    assert "새 알림" in view.stream_html("notifications")
    (js,) = [m for m in view.sent_messages if m.get("type") == "exec_js"]
    assert js["commands"][0]["transition"] == "pulse"


# Pattern B: toasts


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_toast_goes_to_the_recipients_channel_and_stores_nothing(alice, bob):
    layer, bobs = await _listen(toast_channel(bob))
    _, alices = await _listen(toast_channel(alice))
    view = await mount(XNotificationCreator, user=alice, recipient="bob", title="잠깐 볼래?", type="warning")

    await view.call("send_toast")

    (heard,) = await _heard(layer, bobs)
    assert heard["type"] == "notification"
    assert heard["kwargs"] == {"flash_type": "warning", "message": "잠깐 볼래?", "timeout": 5000, "dismissible": True}
    assert await _heard(layer, alices) == []
    assert not await Notification.objects.aexists()


# In a browser: two people, two pages, two sets of cookies


@pytest.fixture
def server():
    with serve() as base_url:
        yield base_url


def _signed_in(browser, server: str, username: str):
    """A page of its own -- ``new_page()`` opens a new context, so its own cookies."""
    page = browser.new_page()
    page.goto(f"{server}/notifications/")
    page.get_by_role("button", name=f"Sign in as {username}").click()
    expect_text(page.get_by_test_id("signed-in-as"), username)
    wait_live(page, '[data-name=XNotificationList][data-is-live="true"]')
    wait_live(page, '[data-name=XNotificationBell][data-is-live="true"]')
    # Hidden by design, so counted rather than waited on as visible
    expect_count(page.locator('[data-name=WireviewToasts][data-is-live="true"]'), 1)
    return page


def _send(page, to: str, title: str, button: str) -> None:
    page.locator("select[name=recipient]").select_option(to)
    page.locator("input[name=title]").fill(title)
    # set_title skips most renders; the one that enables the buttons has to go out
    expect(page.get_by_test_id(button)).to_be_enabled()
    page.get_by_test_id(button).click()


@pytest.mark.e2e
def test_a_notification_reaches_its_recipient_and_no_one_else(browser, server):
    alice = _signed_in(browser, server, "alice")
    bob = _signed_in(browser, server, "bob")
    alice.reload()  # so bob is in alice's recipient list
    wait_live(alice, '[data-name=XNotificationCreator][data-is-live="true"]')

    _send(alice, "bob", "리뷰 부탁해요", "send-notification")

    expect_count(bob.locator(".notification-item"), 1)
    expect_text(bob.locator(".notification-title"), "리뷰 부탁해요")
    expect_text(bob.locator(".bell-badge"), "1")
    # Alice's pages heard nothing: her list is still empty after bob's filled
    expect_count(alice.locator(".notification-item"), 0)
    expect_count(alice.locator(".bell-badge"), 0)


@pytest.mark.e2e
def test_a_toast_shows_on_the_recipients_open_page_and_is_not_kept(browser, server):
    alice = _signed_in(browser, server, "alice")
    bob = _signed_in(browser, server, "bob")
    alice.reload()
    wait_live(alice, '[data-name=XNotificationCreator][data-is-live="true"]')

    _send(alice, "bob", "지금 회의 들어와요", "send-toast")

    expect_text(bob.locator("[wire-flash] .wireview-flash-message"), "지금 회의 들어와요")
    expect_count(alice.locator("[wire-flash] .wireview-flash"), 0)
    # Not stored: bob's list stays empty, and a page opened now shows no toast
    expect_count(bob.locator(".notification-item"), 0)
    bob.reload()
    wait_live(bob, '[data-name=XNotificationBell][data-is-live="true"]')
    expect_count(bob.locator("[wire-flash] .wireview-flash"), 0)
