"""``AUTO_BROADCAST.senders`` as a mapping names the fields each model sends (#144).

A set sends every field, as before. A mapping gives each model ``"__all__"``, a
tuple of field names, or ``()`` for the pk alone. The receiving side must then
load only what came: the deserializer fills a missing field with its default, and
saving that instance wrote the default over the row. ``serializer.decode`` (since
#153) defers every field a payload does not carry, so a partial one needs no path
of its own; these tests pin that for the payloads a mapping makes.
"""

import json
import typing as t

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.core.serializers import serialize
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from pydantic import ValidationError
from testproj.outbound import RecordingOutbound

from examples.rating.models import Product, Rating
from wireview import AutoBroadcast, Component, ModelAction, auto_broadcast
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.serializer import WireviewJSONEncoder, decode, encode
from wireview.session import WireviewSession

EVERY_FLAG = dict(model=True, model_pk=True, related=True, m2m=True)

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


@pytest.fixture
def payloads(monkeypatch):
    """Every ``(channel, action, instance)`` the receivers publish."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "wireview.auto_broadcast.send_to",
        lambda channel, type, action, instance: sent.append((channel, action, instance)),
    )
    return sent


@pytest.fixture
def broadcasting():
    """Reconnect the receivers for one configuration, and the project's own afterwards."""
    yield auto_broadcast.connect
    auto_broadcast.connect()


def fields_of(instance: str) -> dict[str, t.Any]:
    (obj,) = json.loads(instance)
    return obj["fields"]


def everything(instance) -> str:
    """The payload as it was before #144: Django's serializer with no field list."""
    return serialize("json", [instance], cls=WireviewJSONEncoder)


def only(instance, *fields: str) -> str:
    """A payload with the pk and ``fields``, made by Django's serializer and not by ours."""
    return serialize("json", [instance], cls=WireviewJSONEncoder, fields=fields)


# --- what is sent ---------------------------------------------------------------------------


def test_a_user_sends_only_the_fields_listed_on_every_path(payloads, broadcasting):
    broadcasting(AutoBroadcast(**EVERY_FLAG, senders={("auth", "User"): ("username",)}))

    user = User.objects.create_user("alice", "alice@example.com", "s3cret-pass")
    user.groups.add(Group.objects.create(name="staff"))
    user.delete()

    actions = {action for _, action, _ in payloads}
    assert {ModelAction.CREATED, ModelAction.ADDED, ModelAction.DELETED} <= actions
    for channel, action, instance in payloads:
        assert fields_of(instance) == {"username": "alice"}, (channel, action)
        assert "password" not in instance and "alice@example.com" not in instance


def test_a_row_sends_the_fields_listed_on_the_related_channel_too(payloads, broadcasting):
    broadcasting(AutoBroadcast(**EVERY_FLAG, senders={("rating", "Rating"): ("product",)}))
    product = Product.objects.create(name="lamp")

    rating = Rating.objects.create(product=product, score=5, session_key="visitor-key")
    rating.delete()

    channels = {channel for channel, _, _ in payloads}
    assert f"rating.product.{product.pk}.ratings" in channels
    for _, _, instance in payloads:
        assert fields_of(instance) == {"product": product.pk}
        assert "visitor-key" not in instance


@pytest.mark.parametrize(
    "senders",
    [{("rating", "Product")}, {("rating", "Product"): "__all__"}],
    ids=["set", "__all__"],
)
def test_every_field_is_sent_as_before_for_a_set_or_all(payloads, broadcasting, senders):
    broadcasting(AutoBroadcast(**EVERY_FLAG, senders=senders))

    product = Product.objects.create(name="lamp", description="bright")

    assert payloads
    assert {instance for _, _, instance in payloads} == {everything(product)}


def test_an_empty_tuple_sends_the_pk_alone_also_on_delete(payloads, broadcasting):
    broadcasting(AutoBroadcast(**EVERY_FLAG, senders={("rating", "Product"): ()}))

    product = Product.objects.create(name="lamp")
    pk = product.pk
    product.delete()

    assert {action for _, action, _ in payloads} == {ModelAction.CREATED, ModelAction.DELETED}
    for _, _, instance in payloads:
        assert json.loads(instance) == [{"model": "rating.product", "pk": pk, "fields": {}}]


@pytest.mark.parametrize("name", ["product", "product_id"])
def test_a_foreign_key_is_named_by_field_or_attname(payloads, broadcasting, name):
    broadcasting(AutoBroadcast(model=True, senders={("rating", "Rating"): (name,)}))
    product = Product.objects.create(name="lamp")

    Rating.objects.create(product=product, score=5, session_key="s")

    ((_, _, instance),) = payloads
    assert fields_of(instance) == {"product": product.pk}


def test_an_m2m_field_left_out_costs_no_query_to_encode(broadcasting):
    user = User.objects.create_user("alice")
    user.groups.add(Group.objects.create(name="staff"))

    with CaptureQueriesContext(connection) as everything_queries:
        encode(user)
    with CaptureQueriesContext(connection) as listed_queries:
        encode(user, fields=("username",))

    assert len(everything_queries) == 2, "groups and user_permissions, one query each"
    assert len(listed_queries) == 0


def test_the_projects_bookmarks_send_a_partial_payload(payloads):
    """The bookmarks E2E carries the partial path to the browser only while created_at is left out."""
    from testproj.bookmarks.models import Bookmark

    Bookmark.objects.create(title="wireview", url="https://example.com")

    assert payloads
    for _, _, instance in payloads:
        assert set(fields_of(instance)) == {"title", "url", "is_read"}
    assert decode(payloads[0][2]).get_deferred_fields() == {"created_at"}


# --- what is refused --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sender", "fields", "words"),
    [
        (("rating", "Product"), ("colour",), "'colour' for rating.product"),
        (("rating", "Product"), ("ratings",), "a reverse relation"),
        (("rating", "Product"), ("id",), "the primary key"),
    ],
    ids=["no-such-field", "reverse-relation", "pk"],
)
def test_a_field_the_payload_cannot_carry_fails_at_startup(broadcasting, sender, fields, words):
    with pytest.raises(ImproperlyConfigured, match=words):
        broadcasting(AutoBroadcast(model=True, senders={sender: fields}))


def test_a_field_the_serializer_skips_is_named_as_such():
    from django.db import models
    from django.test.utils import isolate_apps

    with isolate_apps("testproj.bookmarks"):

        class Secretive(models.Model):
            hidden = models.CharField(max_length=10, serialize=False)

            class Meta:
                app_label = "bookmarks"

        with pytest.raises(ImproperlyConfigured, match="serialize=False"):
            auto_broadcast._sent_field_name(Secretive, "hidden")


@pytest.mark.parametrize(
    "senders",
    [
        {("auth", "User"): ("username",), ("auth", "user"): "__all__"},
        {("auth", "User"): ("username",), ("auth", "USER"): ()},
    ],
    ids=["fields-and-all", "two-lists"],
)
def test_one_model_named_twice_with_different_fields_fails_at_startup(broadcasting, senders):
    """``apps.get_model`` ignores case, so both keys are one model; one list would silently win."""
    with pytest.raises(ImproperlyConfigured, match="auth.user") as e:
        broadcasting(AutoBroadcast(model=True, senders=senders))
    for key in senders:
        assert repr(key) in str(e.value)


def test_one_model_named_twice_alike_is_one_sender():
    resolved = auto_broadcast.resolve_senders(AutoBroadcast(model=True, senders={("auth", "User"), ("auth", "user")}))
    assert resolved == {User: None}


def test_a_bare_string_is_refused_not_read_as_its_letters():
    """``("username")`` is a string, not a one-element tuple."""
    with pytest.raises(ValidationError):
        AutoBroadcast(model=True, senders={("auth", "User"): "username"})


# --- what is received -------------------------------------------------------------------------


def test_a_full_payload_decodes_as_before():
    product = Product.objects.create(name="lamp", description="bright")

    restored = decode(everything(product))

    assert restored.get_deferred_fields() == set()
    assert (restored.name, restored.description) == ("lamp", "bright")


def test_a_partial_payload_leaves_the_fields_not_sent_deferred():
    product = Product.objects.create(name="lamp", description="bright", image_url="https://example.com/i.png")

    restored = decode(only(product, "name"))

    assert restored.pk == product.pk and restored.name == "lamp"
    assert restored.get_deferred_fields() == {"description", "image_url", "created_at"}
    assert restored.description == "bright", "a deferred field reads the row, not a default"


def test_saving_a_partial_payload_writes_only_what_came():
    """The deserializer's instance had description='' and saved it over the row."""
    product = Product.objects.create(name="lamp", description="bright")

    restored = decode(only(product, "name"))
    restored.name = "desk lamp"
    restored.save()

    product.refresh_from_db()
    assert (product.name, product.description) == ("desk lamp", "bright")


def test_a_partial_payload_keeps_a_foreign_key_id():
    product = Product.objects.create(name="lamp")
    rating = Rating.objects.create(product=product, score=4, session_key="s")

    restored = decode(only(rating, "product"))

    assert restored.product_id == product.pk
    assert "score" in restored.get_deferred_fields()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_deferred_field_read_on_the_loop_is_refused_until_loaded_by_name():
    product = await Product.objects.acreate(name="lamp", description="bright")
    restored = decode(await sync_to_async(only)(product, "name"))

    with pytest.raises(SynchronousOnlyOperation):
        restored.description  # noqa: B018 -- the read is the test

    # Without fields, refresh_from_db reloads the loaded columns and leaves the
    # deferred ones deferred, so the fields to load are named.
    await restored.arefresh_from_db()
    assert "description" in restored.get_deferred_fields()
    await restored.arefresh_from_db(fields=["description"])
    assert restored.description == "bright"


# --- from the channel layer to mutation() -----------------------------------------------------

TEMPLATES = {"broadcast/seen.html": "{% load wireview %}<p {% tag_header %}>{{ this.seen }}</p>"}


class FieldsSeen(Component):
    class Meta:
        template_name = "broadcast/seen.html"
        subscriptions = {"rating.product"}

    seen: str = ""

    async def mutation(self, channel, action, instance):
        self.seen = f"{action}:{instance.pk}:{instance.name}:{sorted(instance.get_deferred_fields())}"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_published_payload_reaches_mutation_with_the_fields_sent(payloads, broadcasting):
    """No unit test joined model_mutation, decode and mutation() before: only the example E2E did."""
    await sync_to_async(broadcasting)(AutoBroadcast(model=True, senders={("rating", "Product"): ("name",)}))
    product = await Product.objects.acreate(name="lamp", description="bright")
    ((channel, action, instance),) = payloads

    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        session = WireviewSession(RecordingOutbound(), user=AnonymousUser(), channel_name="sess-1")
        await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
        state = sign_state(FieldsSeen(id="seen", user=AnonymousUser(), wire=WireviewMeta(params={})))
        await session.handle_message({"command": "join", "payload": {"name": "FieldsSeen", "state": state}})

        await session.model_mutation({"channel": channel, "action": action, "instance": instance})

    expected = f"CREATED:{product.pk}:lamp:['created_at', 'description', 'image_url']"
    assert session.repo.get("seen").seen == expected
