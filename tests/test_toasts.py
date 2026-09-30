"""Toasts: a flash sent to someone else's open pages (#116).

``toast()`` names the recipient -- a user or a session key -- and the receiver
``{% wireview_toasts %}`` puts on a page listens to its own connection's user
and session only. That subscription is the access control, so it is what most
of these pin.
"""

import asyncio

import pytest
from channels.layers import get_channel_layer
from django.contrib.auth.models import AnonymousUser, User
from django.template import Context, Template
from django.test import RequestFactory

from wireview import atoast, mount, toast, toast_channel
from wireview.features.toasts import WireviewToasts

pytestmark = [pytest.mark.unit, pytest.mark.django_db(transaction=True)]


async def _listen(group: str):
    layer = get_channel_layer()
    channel = await layer.new_channel()
    await layer.group_add(group, channel)
    return layer, channel


async def _heard(layer, channel: str) -> list[dict]:
    messages = []
    while True:
        try:
            messages.append(await asyncio.wait_for(layer.receive(channel), 0.2))
        except asyncio.TimeoutError:
            return messages


def test_a_user_and_a_session_each_have_their_own_channel():
    alice = User(pk=7, username="alice")

    assert toast_channel(alice) == "wireview.toast.user.7"
    assert toast_channel("abc123") == "wireview.toast.session.abc123"


@pytest.mark.parametrize("nobody", [User(username="unsaved"), "", None])
def test_a_toast_needs_someone_to_go_to(nobody):
    with pytest.raises(ValueError):
        toast_channel(nobody)


@pytest.mark.asyncio
async def test_atoast_reaches_the_recipients_channel_only():
    alice = await User.objects.acreate(username="alice")
    bob = await User.objects.acreate(username="bob")
    layer, bobs = await _listen(toast_channel(bob))
    _, alices = await _listen(toast_channel(alice))

    await atoast(bob, "회의 들어와요", flash_type="warning", timeout=0)

    (heard,) = await _heard(layer, bobs)
    assert heard["kwargs"] == {"flash_type": "warning", "message": "회의 들어와요", "timeout": 0, "dismissible": True}
    assert await _heard(layer, alices) == []


@pytest.mark.django_db  # inside the test's transaction, where on_commit waits
def test_toast_waits_for_the_transaction(django_capture_on_commit_callbacks):
    bob = User.objects.create(username="bob")
    with django_capture_on_commit_callbacks() as callbacks:
        toast(bob, "later")
    assert len(callbacks) == 1, "sent from on_commit, so a rollback sends nothing"


@pytest.mark.asyncio
async def test_the_receiver_listens_to_its_own_user_and_session_and_nothing_else():
    alice = await User.objects.acreate(username="alice")

    signed_in = await mount(WireviewToasts, user=alice, session_key="s-alice")
    visitor = await mount(WireviewToasts, user=AnonymousUser(), session_key="s-visitor")
    fresh = await mount(WireviewToasts, user=AnonymousUser())

    assert signed_in.component.get_subscriptions() == {toast_channel(alice), toast_channel("s-alice")}
    assert visitor.component.get_subscriptions() == {toast_channel("s-visitor")}
    assert fresh.component.get_subscriptions() == set()


@pytest.mark.asyncio
async def test_the_receiver_shows_a_toast_as_a_flash_and_renders_nothing():
    alice = await User.objects.acreate(username="alice")
    view = await mount(WireviewToasts, user=alice)

    await view.component.notification(
        toast_channel(alice), flash_type="success", message="배포 끝", timeout=0, dismissible=False
    )

    (flash,) = [m for m in view.sent_messages if m.get("type") == "flash"]
    assert (flash["flash_type"], flash["message"], flash["timeout"], flash["dismissible"]) == (
        "success",
        "배포 끝",
        0,
        False,
    )
    assert await view.render_diff() is None or view.component.wire._skip_render


@pytest.mark.asyncio
async def test_the_receiver_ignores_a_channel_that_is_not_its_own():
    alice = await User.objects.acreate(username="alice")
    bob = await User.objects.acreate(username="bob")
    view = await mount(WireviewToasts, user=alice)

    await view.component.notification(toast_channel(bob), flash_type="info", message="bob's")

    assert [m for m in view.sent_messages if m.get("type") == "flash"] == []


def test_the_tag_puts_one_hidden_receiver_on_the_page():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    html = Template("{% load wireview %}{% wireview_toasts %}").render(Context({"request": request}))

    assert 'id="wireview-toasts"' in html
    assert 'data-name="WireviewToasts"' in html
    assert "hidden" in html


def test_no_client_can_call_the_receivers_methods():
    """Its methods are the framework's, so none is an event handler (repository._is_user_defined_method)."""
    from wireview.repository import ComponentRepository

    assert not ComponentRepository._is_user_defined_method(WireviewToasts, "notification")
