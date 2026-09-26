"""A component's configuration lives in ``class Meta:`` (#99).

It used to be underscore class attributes (``_template_name``,
``_on_mount`` ...), which read as private and sat next to the registry's
own underscore names. The rules pinned here:

- each key a subclass's Meta does not set is inherited, so a base that guards
  itself with ``on_mount`` or ``live_sessions`` keeps guarding;
- an unknown key and an old underscore name are errors, not silence;
- ``exclude_fields`` adds to user, wire and session and cannot drop them;
- subscriptions that depend on state come from ``get_subscriptions()``.
"""

import pytest
from django.contrib.auth.models import AnonymousUser

from wireview import Component, LiveComponent
from wireview.core.component import ComponentOptions
from wireview.core.meta import WireviewMeta

pytestmark = pytest.mark.unit


class Guarded(Component, public=False):
    class Meta:
        template_name = "meta/guarded.html"
        on_mount = ["guard"]
        live_sessions = {"admin"}


class GuardedChild(Guarded, public=False):
    class Meta:
        template_name = "meta/child.html"


class GuardedGrandchild(GuardedChild, public=False):
    pass


def test_a_meta_sets_the_options():
    assert Guarded._meta.template_name == "meta/guarded.html"
    assert Guarded._meta.on_mount == ("guard",)
    assert Guarded._meta.live_sessions == frozenset({"admin"})


def test_a_subclass_inherits_each_key_it_does_not_set():
    assert GuardedChild._meta.template_name == "meta/child.html"
    assert GuardedChild._meta.on_mount == ("guard",)
    assert GuardedChild._meta.live_sessions == frozenset({"admin"})


def test_a_subclass_without_a_meta_inherits_all_of_it():
    assert GuardedGrandchild._meta == GuardedChild._meta


def test_a_live_component_reads_its_meta():
    """LiveComponent registers apart from Component and used to skip its Meta."""

    class Card(LiveComponent, public=False):
        class Meta:
            template_name = "meta/card.html"

    assert Card._meta.template_name == "meta/card.html"


def test_an_unknown_key_is_an_error():
    with pytest.raises(TypeError, match="has no option 'templte_name'"):

        class Typo(Component, public=False):
            class Meta:
                templte_name = "meta/typo.html"


@pytest.mark.parametrize(
    "old, new",
    [
        ("_template_name", "template_name"),
        ("_subscriptions", "subscriptions"),
        ("_on_mount", "on_mount"),
        ("_live_sessions", "live_sessions"),
        ("_presence_config", "presence"),
    ],
)
def test_an_old_underscore_name_says_where_it_went(old, new):
    with pytest.raises(TypeError, match=rf"{old} -> Meta\.{new}"):
        type("Old", (Component,), {"__module__": "meta.live", old: "x"}, public=False)


def test_exclude_fields_adds_to_what_is_always_left_out():
    class Secretive(Component, public=False):
        class Meta:
            exclude_fields = {"token"}

    assert Secretive._meta.exclude_fields == frozenset({"token", "user", "wire", "session"})


def test_a_shared_meta_can_be_assigned():
    class Shared:
        template_name = "meta/shared.html"

    class One(Component, public=False):
        Meta = Shared

    assert One._meta.template_name == "meta/shared.html"
    assert "Meta" not in One.model_fields


def test_the_options_are_frozen():
    with pytest.raises(AttributeError):
        Guarded._meta.template_name = "other.html"  # type: ignore[misc]
    assert isinstance(Guarded._meta, ComponentOptions)


def _instance(cls, **fields):
    return cls(user=AnonymousUser(), wire=WireviewMeta(params={}), **fields)


def test_get_subscriptions_defaults_to_the_meta():
    class Listener(Component, public=False):
        class Meta:
            subscriptions = {"news"}

    assert _instance(Listener).get_subscriptions() == {"news"}


def test_get_subscriptions_can_depend_on_state():
    class Room(Component, public=False):
        room_id: int = 0

        def get_subscriptions(self) -> set[str]:
            return {f"room.{self.room_id}"}

    assert _instance(Room, room_id=7).get_subscriptions() == {"room.7"}


@pytest.mark.asyncio
async def test_defer_queues_the_handler_on_the_connection():
    """``defer`` (was ``deffer``, #99) sends the call through the connection."""
    from wireview import mount

    class Later(Component, public=False):
        class Meta:
            template_name = "todo/counter.html"

        async def start(self, **_):
            await self.defer(self.finish, 5)

        async def finish(self, n: int = 0, **_):
            pass

    view = await mount(Later)
    view.clear_messages()
    await view.call("start")

    deferred = [m for m in view.sent_messages if m.get("type") == "dispatch_event"]
    assert [(m["command"], tuple(m["args"])) for m in deferred] == [("finish", (5,))]
