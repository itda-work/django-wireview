"""The parent owns its LiveComponents (#78, docs/design/live-component-ownership.md §3-1).

- ``joined()`` runs once per instance: a direct join for a LiveComponent id is ignored
- a reconnect restores the child's own state from the parent's ``children`` map
- a parent re-render calls ``update()`` only for props whose value actually changed
- reusing an id with another class replaces the instance; another parent reparents it
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.state import sign_state
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

CALLS: list[tuple[str, str, t.Any]] = []


class OwnedCounter(LiveComponent):
    _template_name = "livecomp/counter.html"
    count: int = 0
    note: str = "default"

    async def joined(self):
        CALLS.append(("joined", self.id, self.count))

    async def update(self, **assigns):
        CALLS.append(("update", self.id, dict(assigns)))
        await super().update(**assigns)

    async def leaving(self):
        CALLS.append(("leaving", self.id, None))

    async def reset(self):
        self.count = 0


class OtherCounter(LiveComponent):
    _template_name = "livecomp/counter.html"
    count: int = 0


class OwnerDashboard(Component):
    _template_name = "livecomp/dashboard.html"


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()
    yield
    CALLS.clear()


def repo_with_parent() -> ComponentRepository:
    repo = ComponentRepository(is_live=True, user=AnonymousUser())
    repo.build("OwnerDashboard", {"id": "p1"})
    return repo


# --- joined() once ---------------------------------------------------------------------


async def test_a_direct_join_for_a_live_component_is_ignored_by_the_consumer():
    consumer = WireviewConsumer()
    consumer.repo = repo_with_parent()
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    child = consumer.repo.build_live_component("OwnedCounter", {"id": "c1", "count": 0}, parent_id="p1")
    await consumer.repo.flush_pending_live_components()
    assert CALLS == [("joined", "c1", 0)]

    # What a cached older client sends after the parent turned live.
    child.count = 7
    await consumer.command_join("OwnedCounter", sign_state(child), children=None)

    assert CALLS == [("joined", "c1", 0)], "joined() ran again"
    assert outbound.commands == [], "nothing was rendered for the ignored join"
    assert consumer.repo.get("c1") is child


# --- restore on reconnect ---------------------------------------------------------------


async def test_a_reconnect_restores_the_childs_own_state_under_the_parents_props():
    repo = repo_with_parent()
    # The parent's join carried the child's signed state from the previous connection.
    repo.children = {"c1": ("OwnedCounter", {"id": "c1", "count": 7, "note": "kept"})}

    child = repo.build_live_component("OwnedCounter", {"id": "c1", "count": 0}, parent_id="p1")

    assert child.count == 0, "a prop the parent passes is the parent's truth"
    assert child.note == "kept", "a field the parent does not pass is the child's own and comes back"
    assert repo.children == {}, "the restored entry is consumed"


async def test_restored_state_of_another_class_is_not_applied():
    repo = repo_with_parent()
    repo.children = {"c1": ("OtherCounter", {"id": "c1", "count": 7, "note": "kept"})}

    child = repo.build_live_component("OwnedCounter", {"id": "c1"}, parent_id="p1")

    assert (child.count, child.note) == (0, "default")


# --- update() only on a real prop change ------------------------------------------------


async def test_a_parent_rerender_with_the_same_props_does_not_reset_the_child():
    repo = repo_with_parent()
    child = repo.build_live_component("OwnedCounter", {"id": "c1", "count": 10}, parent_id="p1")
    await repo.flush_pending_live_components()

    await child.reset()  # the child changed its own state: 10 -> 0
    repo.build_live_component("OwnedCounter", {"id": "c1", "count": 10}, parent_id="p1")  # parent re-renders
    await repo.flush_pending_live_components()

    assert child.count == 0, "the parent passed the same count=10 as before, so nothing to update"
    assert [c for c in CALLS if c[0] == "update"] == []


async def test_a_changed_prop_reaches_update_with_only_the_changed_keys():
    repo = repo_with_parent()
    child = repo.build_live_component("OwnedCounter", {"id": "c1", "count": 10, "note": "a"}, parent_id="p1")
    await repo.flush_pending_live_components()

    repo.build_live_component("OwnedCounter", {"id": "c1", "count": 11, "note": "a"}, parent_id="p1")
    await repo.flush_pending_live_components()

    assert [c for c in CALLS if c[0] == "update"] == [("update", "c1", {"count": 11})]
    assert child.count == 11


async def test_a_prop_that_appears_for_the_first_time_counts_as_changed():
    repo = repo_with_parent()
    repo.build_live_component("OwnedCounter", {"id": "c1"}, parent_id="p1")
    await repo.flush_pending_live_components()

    repo.build_live_component("OwnedCounter", {"id": "c1", "note": "now"}, parent_id="p1")
    await repo.flush_pending_live_components()

    assert [c for c in CALLS if c[0] == "update"] == [("update", "c1", {"note": "now"})]


# --- id reuse ---------------------------------------------------------------------------


async def test_the_same_id_with_another_class_replaces_the_instance():
    repo = repo_with_parent()
    old = repo.build_live_component("OwnedCounter", {"id": "c1", "count": 3}, parent_id="p1")

    new = repo.build_live_component("OtherCounter", {"id": "c1"}, parent_id="p1")

    assert new is not old and isinstance(new, OtherCounter)
    assert repo.get("c1") is new
    assert repo._pending_leaving == [old], "the replaced instance owes a leaving() call"


async def test_the_same_id_under_another_parent_is_reparented():
    repo = repo_with_parent()
    repo.build("OwnerDashboard", {"id": "p2"})
    child = repo.build_live_component("OwnedCounter", {"id": "c1"}, parent_id="p1")

    same = repo.build_live_component("OwnedCounter", {"id": "c1"}, parent_id="p2")

    assert same is child
    assert child._parent_id == "p2"
    assert repo.get_live_components("p1") == [] and repo.get_live_components("p2") == [child]
