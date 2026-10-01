"""The instance ``mutation()`` receives saves like any model instance (#153).

``AUTO_BROADCAST`` carries the row as Django's JSON serializer writes it, and the
session restores it with ``serializer.decode``. That used to hand the receiver the
deserializer's ``save``: ``save_base(raw=True)``, which skips the model's own
``save()`` and sends ``pre_save``/``post_save`` with ``raw=True``. A receiver that
saved the instance got a fixture load, not a save.

The end-to-end test drives the whole path -- a real payload published by the
signal receiver, ``WireviewSession.model_mutation``, a joined component's
``mutation()`` -- with nothing but an ``Outbound``. Nothing tested that path
before.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.exceptions import SynchronousOnlyOperation
from django.db import connection
from django.db.models.signals import post_save
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from testproj.inheritprobe.models import Restaurant

from examples.rating.models import Product
from examples.todo.models import Item
from wireview import Component, ModelAction, serializer
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.session import WireviewSession

TEMPLATES = {"mutsave/product.html": "{% load wireview %}<p {% tag_header %}>{{ this.name }}</p>"}


@pytest.fixture
def published(monkeypatch) -> list[dict[str, t.Any]]:
    """Every message the signal receivers publish, as the session would receive it."""
    sent: list[dict[str, t.Any]] = []
    monkeypatch.setattr(
        "wireview.auto_broadcast.send_to",
        lambda channel, type, **kwargs: sent.append(dict(type=type, channel=channel, **kwargs)),
    )
    return sent


@pytest.fixture
def saves(monkeypatch) -> list[t.Any]:
    """The pks ``Product.save()`` ran for: the model's own ``save``, not ``save_base``."""
    ran: list[t.Any] = []
    original = Product.save

    def save(self, *args, **kwargs):
        ran.append(self.pk)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Product, "save", save)
    return ran


@pytest.fixture
def raw_flags() -> t.Iterator[list[bool]]:
    """The ``raw`` of every ``post_save`` a ``Product`` sends."""
    flags: list[bool] = []

    def receiver(sender, raw=False, **kwargs):
        flags.append(raw)

    post_save.connect(receiver, sender=Product, dispatch_uid="test_mutation_instance")
    yield flags
    post_save.disconnect(sender=Product, dispatch_uid="test_mutation_instance")


def payload_of(published: list[dict[str, t.Any]], channel: str) -> dict[str, t.Any]:
    (message,) = [m for m in published if m["channel"] == channel]
    return message


@pytest.mark.unit
@pytest.mark.django_db
class TestDecodedInstance:
    def test_save_runs_the_models_save_and_sends_a_signal_that_is_not_raw(self, published, saves, raw_flags):
        product = Product.objects.create(name="lamp")
        instance = serializer.decode(payload_of(published, f"rating.product.{product.pk}")["instance"])
        saves.clear()
        raw_flags.clear()

        instance.name = "desk lamp"
        instance.save()

        assert saves == [product.pk], "the model's save() ran"
        assert raw_flags == [False]
        assert Product.objects.get(pk=product.pk).name == "desk lamp"

    def test_it_is_an_existing_row_so_a_pk_with_a_default_updates_instead_of_inserting(self, published):
        # Item's pk is a UUIDField with a default. A fresh Model(**data) is "adding",
        # and Django inserts outright when adding such a row -- an IntegrityError.
        item = Item.objects.create(text="milk")
        instance = serializer.decode(payload_of(published, f"todo.item.{item.pk}")["instance"])

        assert instance._state.adding is False
        instance.completed = True
        instance.save()

        assert Item.objects.filter(pk=item.pk).values_list("completed", flat=True).get() is True
        assert Item.objects.count() == 1

    def test_save_writes_every_column_from_the_payload_over_what_the_row_has_now(self, published):
        # The payload is the row as it was when the signal fired. Saving it writes all
        # of it back: a change that reached the row afterwards is overwritten.
        product = Product.objects.create(name="lamp", description="old")
        instance = serializer.decode(payload_of(published, f"rating.product.{product.pk}")["instance"])
        Product.objects.filter(pk=product.pk).update(description="changed since")

        instance.name = "desk lamp"
        instance.save()

        assert Product.objects.values_list("name", "description").get(pk=product.pk) == ("desk lamp", "old")

    def test_save_leaves_the_m2m_as_it_is_now(self):
        # The payload lists the m2m pks. Saving does not set them back, as no save() does.
        user = User.objects.create(username="alice")
        staff, admins = Group.objects.create(name="staff"), Group.objects.create(name="admins")
        user.groups.set([staff])
        payload = serializer.encode(user)
        user.groups.add(admins)

        serializer.decode(payload).save()

        assert set(user.groups.all()) == {staff, admins}

    def test_update_fields_writes_only_those_columns(self, published):
        product = Product.objects.create(name="lamp", description="old")
        instance = serializer.decode(payload_of(published, f"rating.product.{product.pk}")["instance"])
        Product.objects.filter(pk=product.pk).update(description="changed since")

        instance.name = "desk lamp"
        instance.save(update_fields=["name"])

        assert Product.objects.values_list("name", "description").get(pk=product.pk) == ("desk lamp", "changed since")


@pytest.mark.unit
@pytest.mark.django_db
class TestInheritedModel:
    """A child of multi-table inheritance: the payload carries its own table, not its parents'."""

    def test_saving_it_writes_its_own_columns_and_keeps_the_parents(self):
        restaurant = Restaurant.objects.create(name="Pizzeria", city="Seoul")
        instance = serializer.decode(serializer.encode(restaurant))

        instance.serves_pizza = True
        with CaptureQueriesContext(connection) as queries:
            instance.save()

        assert [query["sql"].split(" SET ")[0] for query in queries] == ['UPDATE "inheritprobe_restaurant"']
        assert Restaurant.objects.values_list("name", "city", "serves_pizza").get(pk=restaurant.pk) == (
            "Pizzeria",
            "Seoul",
            True,
        )

    def test_a_parent_field_is_deferred_and_reads_the_row(self):
        restaurant = Restaurant.objects.create(name="Pizzeria", city="Seoul")
        instance = serializer.decode(serializer.encode(restaurant))

        assert instance.get_deferred_fields() == {"name", "city"}
        assert instance.name == "Pizzeria"


@pytest.mark.unit
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reading_a_parent_field_of_an_inherited_model_on_the_event_loop_fails_loudly():
    # It is not in the payload. Read without a query it would be the field's default,
    # a wrong value that raises nothing.
    restaurant = await Restaurant.objects.acreate(name="Pizzeria", city="Seoul")
    instance = serializer.decode(serializer.encode(restaurant))

    with pytest.raises(SynchronousOnlyOperation):
        instance.name

    await instance.arefresh_from_db(fields=["name"])
    assert instance.name == "Pizzeria"


class ProductSaver(Component):
    """Saves the product it hears about, once: a receiver that writes back."""

    class Meta:
        template_name = "mutsave/product.html"

    product_id: int = 0
    name: str = ""

    def get_subscriptions(self) -> set[str]:
        return {f"rating.product.{self.product_id}"}

    async def mutation(self, channel: str, action: ModelAction, instance: t.Any) -> None:
        HEARD.append((channel, action, instance))
        if instance.name != "seen":
            instance.name = "seen"
            await instance.asave()
        self.name = instance.name


HEARD: list[tuple[str, ModelAction, t.Any]] = []


class RecordingOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...

    async def close(self, code: int | None = None) -> None: ...


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_published_mutation_reaches_mutation_and_its_instance_saves_like_a_model(published, saves, raw_flags):
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
        }
    ]
    with override_settings(TEMPLATES=templates):
        product = await Product.objects.acreate(name="lamp")
        channel = f"rating.product.{product.pk}"
        message = payload_of(published, channel)
        published.clear()
        saves.clear()
        raw_flags.clear()
        HEARD.clear()

        outbound = RecordingOutbound()
        session = WireviewSession(outbound, user=AnonymousUser(), channel_name="mutsave-1")
        await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
        saver = ProductSaver(id="p", product_id=product.pk, user=AnonymousUser(), wire=WireviewMeta(params={}))
        state = sign_state(saver)
        await session.handle_message({"command": "join", "payload": {"name": "ProductSaver", "state": state}})
        outbound.commands.clear()

        await session.model_mutation(message)

        ((heard_channel, action, instance),) = HEARD
        assert (heard_channel, action) == (channel, ModelAction.CREATED)
        assert isinstance(instance, Product) and instance.pk == product.pk
        assert saves == [product.pk], "asave() ran the model's save()"
        assert raw_flags == [False]
        assert (await Product.objects.aget(pk=product.pk)).name == "seen"
        assert session.repo.get("p").name == "seen"
        assert [command for command, _ in outbound.commands] == ["render"]

        # The save is a save like any other, so it is announced on the channel this
        # component listens on. Delivered, the receiver hears its own write: one that
        # saves unconditionally would loop. This one saves only when the value differs.
        (echo,) = [m for m in published if m["channel"] == channel]
        assert echo["action"] == ModelAction.UPDATED
        published.clear()

        await session.model_mutation(echo)

        assert len(HEARD) == 2
        assert saves == [product.pk], "the echo did not save again"
        assert published == []
        await session.stop()


class ProductRenamer(Component):
    """Renames the instance it hears about, without saving: what it holds is its own."""

    class Meta:
        template_name = "mutsave/product.html"

    product_id: int = 0
    name: str = ""

    def get_subscriptions(self) -> set[str]:
        return {f"rating.product.{self.product_id}"}

    async def mutation(self, channel: str, action: ModelAction, instance: t.Any) -> None:
        RENAMED.append((self.id, instance, instance.name))
        instance.name = f"renamed by {self.id}"


RENAMED: list[tuple[str, t.Any, str]] = []


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_each_component_that_hears_a_mutation_gets_an_instance_of_its_own(published):
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
        }
    ]
    with override_settings(TEMPLATES=templates):
        product = await Product.objects.acreate(name="lamp")
        message = payload_of(published, f"rating.product.{product.pk}")
        RENAMED.clear()

        session = WireviewSession(RecordingOutbound(), user=AnonymousUser(), channel_name="mutsave-2")
        await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
        for id in ("a", "b"):
            renamer = ProductRenamer(id=id, product_id=product.pk, user=AnonymousUser(), wire=WireviewMeta(params={}))
            await session.handle_message(
                {"command": "join", "payload": {"name": "ProductRenamer", "state": sign_state(renamer)}}
            )

        await session.model_mutation(message)

        assert sorted(id for id, _, _ in RENAMED) == ["a", "b"]
        assert [name for _, _, name in RENAMED] == ["lamp", "lamp"], "each heard the payload, not the other's edit"
        (_, first, _), (_, second, _) = RENAMED
        assert first is not second
        await session.stop()
