"""The channel names ``AUTO_BROADCAST`` publishes on are public (#119).

A component subscribes by writing these strings, so a change to how they are
built breaks it without an error: the subscription simply stops matching. Nothing
tested them before 1.0 froze them. testproj turns on all four kinds.
"""

import pytest

from examples.rating.models import Product, Rating
from wireview import ModelAction

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.fixture
def published(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "wireview.auto_broadcast.send_to",
        lambda channel, type, action, instance: sent.append((channel, action)),
    )
    return sent


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
def test_an_m2m_change_is_announced_on_both_rows_whichever_side_made_it(published, side):
    """The instance's side used to carry a trailing ``.<pk>``, so the channel depended on the manager used."""
    from django.contrib.auth.models import Group, User

    user = User.objects.create(username="alice")
    group = Group.objects.create(name="staff")
    published.clear()

    if side == "user":
        user.groups.add(group)
    else:
        group.user_set.add(user)

    added = {channel for channel, action in published if action == ModelAction.ADDED}
    assert added == {f"auth.user.{user.pk}.groups", f"auth.group.{group.pk}.user"}


def test_clearing_an_m2m_is_announced_on_the_row_that_was_cleared(published):
    from django.contrib.auth.models import Group, User

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
