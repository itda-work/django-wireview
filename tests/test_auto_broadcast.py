"""The channel names ``AUTO_BROADCAST`` publishes on are public (#119).

A component subscribes by writing these strings, so a change to how they are
built breaks it without an error: the subscription simply stops matching. Nothing
tested them before 1.0 froze them. testproj turns on all four kinds.

Only the models ``senders`` names are broadcast, and an empty ``senders``
broadcasts nothing. The receivers are connected when the app starts, so a test
that needs another configuration reconnects with ``auto_broadcast.connect()``.
"""

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from examples.rating.models import Product, Rating
from wireview import AutoBroadcast, ModelAction, auto_broadcast

EVERY_FLAG = dict(model=True, model_pk=True, related=True, m2m=True)

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.fixture
def published(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "wireview.auto_broadcast.send_to",
        lambda channel, type, action, instance: sent.append((channel, action)),
    )
    return sent


@pytest.fixture
def broadcasting():
    """Reconnect the receivers for one configuration, and the project's own afterwards."""
    yield auto_broadcast.connect
    auto_broadcast.connect()


def test_a_saved_row_is_announced_on_its_model_and_its_pk(published):
    product = Product.objects.create(name="lamp")

    assert ("rating.product", ModelAction.CREATED) in published
    assert (f"rating.product.{product.pk}", ModelAction.CREATED) in published


def test_a_row_is_announced_on_the_row_it_points_at_under_the_related_name(published):
    product = Product.objects.create(name="lamp")
    published.clear()

    Rating.objects.create(product=product, score=5, session_key="s")

    assert (f"rating.product.{product.pk}.ratings", ModelAction.CREATED) in published


def test_a_deleted_row_is_announced_on_the_same_channels(published):
    product = Product.objects.create(name="lamp")
    rating = Rating.objects.create(product=product, score=5, session_key="s")
    rating_pk = rating.pk
    published.clear()

    rating.delete()

    assert {channel for channel, action in published if action == ModelAction.DELETED} == {
        "rating.rating",
        f"rating.rating.{rating_pk}",
        f"rating.product.{product.pk}.ratings",
    }


@pytest.mark.parametrize("side", ["user", "group"])
def test_an_m2m_change_is_announced_on_both_rows_whichever_side_made_it(published, broadcasting, side):
    """The instance's side used to carry a trailing ``.<pk>``, so the channel depended on the manager used."""
    broadcasting(AutoBroadcast(m2m=True, senders={("auth", "User"), ("auth", "Group")}))
    user = User.objects.create(username="alice")
    group = Group.objects.create(name="staff")
    published.clear()

    if side == "user":
        user.groups.add(group)
    else:
        group.user_set.add(user)

    added = {channel for channel, action in published if action == ModelAction.ADDED}
    assert added == {f"auth.user.{user.pk}.groups", f"auth.group.{group.pk}.user"}


def test_clearing_an_m2m_is_announced_on_the_row_that_was_cleared(published, broadcasting):
    broadcasting(AutoBroadcast(m2m=True, senders={("auth", "User")}))
    user = User.objects.create(username="alice")
    user.groups.add(Group.objects.create(name="staff"))
    published.clear()

    user.groups.clear()

    assert (f"auth.user.{user.pk}.groups", ModelAction.CLEARED) in published


def test_underscores_in_a_channel_name_become_hyphens(monkeypatch):
    from wireview import auto_broadcast

    sent = []
    monkeypatch.setattr(auto_broadcast, "send_to", lambda channel, *args, **kwargs: sent.append(channel))

    auto_broadcast.notify_mutation(["shop.order.1.order_items"], ModelAction.CREATED, "{}")

    assert sent == ["shop.order.1.order-items"]


def test_the_project_broadcasts_only_the_models_it_names(published):
    """testproj turns every flag on and names its example models. auth and sessions are not among them."""
    user = User.objects.create(username="probe")
    user.groups.add(Group.objects.create(name="staff"))
    Session.objects.create(session_key="k" * 32, session_data="{}", expire_date=timezone.now())
    Product.objects.create(name="lamp")

    assert published
    assert {channel.split(".")[0] for channel, _ in published} == {"rating"}


def test_no_senders_broadcasts_nothing(published, broadcasting):
    broadcasting(AutoBroadcast(**EVERY_FLAG))

    user = User.objects.create(username="probe")
    user.groups.add(Group.objects.create(name="staff"))
    user.delete()
    Session.objects.create(session_key="k" * 32, session_data="{}", expire_date=timezone.now())
    product = Product.objects.create(name="lamp")
    Rating.objects.create(product=product, score=5, session_key="s")

    assert published == []


def test_a_named_model_is_broadcast_and_no_other(published, broadcasting):
    broadcasting(AutoBroadcast(**EVERY_FLAG, senders={("rating", "Product")}))

    product = Product.objects.create(name="lamp")
    Rating.objects.create(product=product, score=5, session_key="s")
    User.objects.create(username="probe")

    assert {channel for channel, _ in published} == {"rating.product", f"rating.product.{product.pk}"}


def test_an_m2m_change_is_broadcast_only_from_a_named_model(published, broadcasting):
    """The message carries the instance whose manager made the change, so that model must be named."""
    broadcasting(AutoBroadcast(m2m=True, senders={("auth", "User")}))
    user = User.objects.create(username="alice")
    group = Group.objects.create(name="staff")
    permission = Permission.objects.first()

    user.groups.add(group)
    assert {channel for channel, _ in published} == {f"auth.user.{user.pk}.groups", f"auth.group.{group.pk}.user"}
    published.clear()

    # Group is not named: neither its own many-to-many nor User's, changed from Group's side.
    group.permissions.add(permission)
    group.user_set.remove(user)
    assert published == []


def test_reconnecting_replaces_what_was_connected(published, broadcasting):
    broadcasting(AutoBroadcast(model=True, senders={("rating", "Product")}))
    broadcasting(AutoBroadcast(model=True, senders={("rating", "Rating")}))

    product = Product.objects.create(name="lamp")
    Rating.objects.create(product=product, score=5, session_key="s")

    assert [channel for channel, _ in published] == ["rating.rating"]


def test_a_sender_that_is_not_a_model_fails_at_startup(broadcasting):
    with pytest.raises(ImproperlyConfigured, match=r"\('rating', 'Nope'\)"):
        broadcasting(AutoBroadcast(model=True, senders={("rating", "Nope")}))
