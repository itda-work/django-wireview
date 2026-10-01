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


async def _heard(layer, channel: str, expected: int) -> list[dict]:
    """The ``expected`` messages on ``channel``, and whatever else follows them.

    Each expected message is waited for as long as it takes (#143); only the
    check that nothing follows is a short quiet window, since no message says
    that nothing more is coming.
    """
    messages = [await asyncio.wait_for(layer.receive(channel), 5) for _ in range(expected)]
    while True:
        try:
            messages.append(await asyncio.wait_for(layer.receive(channel), 0.2))
        except asyncio.TimeoutError:
            return messages


def test_a_user_and_a_session_each_have_their_own_channel():
    alice = User(pk=7, username="alice")

    assert toast_channel(alice) == "wireview.toast.user.7"
    assert toast_channel("abc123").startswith("wireview.toast.session.")
    assert toast_channel("abc123") != toast_channel("abc124")


def _signed_cookie_session_key() -> str:
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    session = SessionStore()
    session["cart"] = [1, 2]
    session.save()
    return session.session_key


@pytest.mark.parametrize(
    "to",
    [_signed_cookie_session_key(), "k" * 120, User(pk="ann@example.com", username="ann")],
    ids=["signed-cookie session key", "a long session key", "a user whose pk has an @"],
)
@pytest.mark.asyncio
async def test_every_recipient_names_a_channel_a_layer_takes(to):
    # The signed_cookies backend's key is the signed cookie: ':' in it, and over 60 characters.
    # A layer raised TypeError for it, and the receiver's join failed on that subscription.
    from channels.layers import InMemoryChannelLayer

    await InMemoryChannelLayer().group_add(toast_channel(to), "x")


def test_a_digested_pk_does_not_share_a_channel_with_a_pk_spelled_like_the_digest():
    odd = User(pk="ann@example.com", username="ann")
    digest = toast_channel(odd).rsplit(".", 1)[1]

    assert toast_channel(User(pk=digest, username="mallory")) != toast_channel(odd)


def test_a_session_key_is_not_written_into_the_channel_name():
    # The key is the session's credential; channel names reach the broker and its logs
    assert "secret-session-key" not in toast_channel("secret-session-key")


@pytest.mark.asyncio
async def test_the_receiver_joins_on_a_signed_cookie_session():
    view = await mount(WireviewToasts, session_key=_signed_cookie_session_key())

    assert len(view.component.get_subscriptions()) == 1


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

    (heard,) = await _heard(layer, bobs, 1)
    assert heard["kwargs"] == {"flash_type": "warning", "message": "회의 들어와요", "timeout": 0, "dismissible": True}
    assert await _heard(layer, alices, 0) == []


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
