"""A mounted component's page, held as a connection holds it (GAP-056, #252).

Once a DOM helper or ``child()`` reads the page, the mount keeps what the
component draws: the nested components the page joins and the LiveComponents
their parents own. An event goes to the component whose element sent it, that
component renders as a session renders it, and the lifecycle the session runs
-- ``joined()``, ``update()``, ``leaving()`` -- runs. ``FAMILY`` is the same
table ``tests/test_dom_events_e2e.py`` walks in a browser.
"""

from __future__ import annotations

import asyncio

import pytest
from testproj.domprobe.live import (
    CANCELLED,
    HEARD,
    JOINED_ROWS,
    DomFamily,
    HeldDeepRoot,
    HeldGuard,
    HeldRows,
    HeldShelter,
)
from testproj.domprobe.scenarios import FAMILY, FAMILY_JOINED, family_heard
from testproj.identityprobe.live_sessions import inside

from wireview import mount
from wireview.testing_dom import Selector, normalized_text, parse

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _clear_heard():
    HEARD.clear()
    yield
    HEARD.clear()


def shows(html: str | None, testid: str) -> str | None:
    """The text of the element ``data-testid=testid`` in ``html``, or ``None`` when there is none."""
    found = Selector(f"[data-testid={testid}]").select(parse(html or ""))
    return normalized_text(found[0]) if found else None


# ============================================================================
# The table a browser walks too
# ============================================================================


async def test_the_page_is_joined_as_a_browser_joins_it():
    view = await mount(DomFamily, id="family")
    # mount() runs the mounted component's joined(); the page's components join once a helper reads it
    assert family_heard(HEARD) == []
    await view.child("kid")
    assert sorted(family_heard(HEARD)) == sorted(FAMILY_JOINED)


async def test_each_step_runs_what_the_session_runs_and_the_page_shows_what_the_browser_does():
    view = await mount(DomFamily, id="family")
    await view.child("kid")
    for step in FAMILY:
        HEARD.clear()
        html = await view.click(f"[data-testid={step.testid}]")
        assert family_heard(HEARD) == step.heard, step.name
        for testid, text in step.shows.items():
            assert shows(html, testid) == text, (step.name, testid)


async def test_a_child_is_reached_by_id_and_its_handler_runs_as_an_event():
    view = await mount(DomFamily, id="family")
    pet = await view.child("pet")
    await pet.call("pet")
    assert pet.component.pets == 1
    assert shows(view.render(), "pet-text") == "a:1"
    # The nested one too
    kid = await view.child("kid")
    await kid.call("bump")
    assert shows(view.render(), "kid-text") == "a:1"
    with pytest.raises(AssertionError, match=r"holds no component under 'nope'.*'kid' \(DomKid\)"):
        await view.child("nope")


async def test_call_on_the_parent_settles_its_children_once_the_page_is_held():
    view = await mount(DomFamily, id="family")
    await view.child("pet")
    HEARD.clear()
    await view.call("rename")
    assert family_heard(HEARD) == [("DomFamily", "rename", {}), ("DomPet", "update", {"title": "ab"})]
    assert shows(view.render(), "pet-text") == "ab:0"


async def test_until_a_helper_reads_the_page_render_draws_it_anew_as_before():
    view = await mount(DomFamily, id="family")
    view.render()
    await view.call("rename")
    # No child is joined, none is updated: the first response's drawing, with the parent's props
    assert family_heard(HEARD) == [("DomFamily", "rename", {})]
    assert shows(view.render(), "pet-text") == "ab:0"


async def test_a_child_left_off_the_page_is_named_no_more():
    view = await mount(DomFamily, id="family")
    pet = await view.child("pet")
    await view.click("[data-testid=toggle]")
    with pytest.raises(AssertionError, match="no component under 'pet' any more"):
        pet.component  # noqa: B018
    with pytest.raises(AssertionError, match="No element matches"):
        await view.click("[data-testid=pet-pet]")


# ============================================================================
# The mail of an instance a render replaced (#233)
# ============================================================================


async def test_the_mail_of_a_replaced_instance_reaches_nothing_of_the_new_one():
    view = await mount(DomFamily, id="family")
    old = (await view.child("pet")).component
    await view.click("[data-testid=pet-pet]")
    await view.click("[data-testid=toggle]")
    await view.click("[data-testid=toggle]")
    new = (await view.child("pet")).component
    assert new is not old and new.pets == 0
    HEARD.clear()

    # A deferred handler of the old one, a render it asks for, what it tells its parent
    await old.defer(old.pet)
    await old.send_render()
    await old.send_to_parent("told_me", n=99)
    for _ in range(10):
        await asyncio.sleep(0)
    assert new.pets == 0
    # The parent is the same instance: what the old child tells it still reaches it, as on a socket
    assert family_heard(HEARD) == [("DomFamily", "told_me", {"n": 99})]

    # The parent's send_update named the instance it drew: the new one
    family = view.component
    await family.send_update("pet", title="zz")
    for _ in range(10):
        await asyncio.sleep(0)
    assert new.title == "zz" and old.title != "zz"


async def test_a_send_update_for_an_instance_since_replaced_is_dropped():
    view = await mount(DomFamily, id="family")
    await view.child("pet")
    family = view.component
    stale = family.wire.live_instances["pet"]
    await view.click("[data-testid=toggle]")
    await view.click("[data-testid=toggle]")
    new = (await view.child("pet")).component
    assert new.wire.instance != stale
    family.wire.live_instances["pet"] = stale  # what a mail sent before the redraw named
    await family.send_update("pet", title="late")
    for _ in range(10):
        await asyncio.sleep(0)
    assert new.title == "a"


# ============================================================================
# A child that leaves: its tasks and its subscriptions go with it
# ============================================================================


async def test_a_child_that_leaves_takes_its_tasks_and_subscriptions_with_it():
    CANCELLED.clear()
    view = await mount(HeldShelter, id="shelter")
    await view.child("busy")
    layer = view.wire._mock_channel_layer
    assert layer.groups.get("held-busy") and layer.groups.get("held-busykid")
    await view.click("[data-testid=busy-run]")
    await view.click("[data-testid=busykid-run]")

    await view.click("[data-testid=toggle]")
    await asyncio.sleep(0)
    assert sorted(name for name, what, _ in HEARD if what == "leaving") == ["HeldBusyKid", "HeldBusyPet"]
    assert sorted(CANCELLED) == ["busy", "busykid"]
    assert not layer.groups.get("held-busy") and not layer.groups.get("held-busykid")

    # Drawn again: new instances, from their first state, subscribed again
    await view.click("[data-testid=toggle]")
    assert shows(view.render(), "busy-runs") == "0" and shows(view.render(), "busykid-runs") == "0"
    assert layer.groups.get("held-busy") and layer.groups.get("held-busykid")


async def test_a_notification_reaches_the_child_the_page_holds_that_listens():
    view = await mount(HeldShelter, id="shelter")
    await view.child("busykid")
    HEARD.clear()
    await view.component.broadcast("held-busykid", hello=1)
    for _ in range(10):
        await asyncio.sleep(0)
    assert HEARD == [("HeldBusyKid", "notification", {"hello": 1})]


# ============================================================================
# A nested component its mount hooks refuse: as the first response refuses it
# ============================================================================


async def test_a_refused_nested_component_is_drawn_as_the_first_response_draws_it():
    view = await mount(HeldGuard, id="guard", live_session=inside)
    first = view.render()
    assert shows(first, "refused-secret") is None and shows(first, "refused-pet-secret") is None
    assert shows(first, "allowed-secret") == "secret"

    await view.click("[data-testid=allowed-hit]")
    held = view.render()
    # Holding the page refuses what the first response refused, and nothing of it is reachable
    assert shows(held, "refused-secret") is None and shows(held, "refused-pet-secret") is None
    for refused in ("refused", "refused-pet"):
        with pytest.raises(AssertionError, match=f"holds no component under '{refused}'"):
            await view.child(refused)
    assert [name for name, *_ in HEARD] == ["HeldAllowed"]


# ============================================================================
# How far a page goes: the session's depth, not a count of components
# ============================================================================


async def test_a_page_of_many_siblings_is_held_whole():
    """A list of LiveComponents is not a loop: no count of siblings stops it."""
    JOINED_ROWS.clear()
    rows = [f"r{n}" for n in range(1500)]
    view = await mount(HeldRows, id="rows", rows=rows)
    await view.child("r0")
    assert JOINED_ROWS == rows


@pytest.mark.parametrize("until", [7, 8])
async def test_nesting_goes_as_deep_as_the_session_renders_and_no_deeper(until):
    from wireview.session import WireviewSession

    assert WireviewSession.MAX_LIVE_COMPONENT_DEPTH == 8
    view = await mount(HeldDeepRoot, id="deep", until=until)
    if until < WireviewSession.MAX_LIVE_COMPONENT_DEPTH:
        assert (await view.child(f"deep-{until}")).component.level == until
    else:
        with pytest.raises(AssertionError, match="nest deeper than 8 under HeldDeepRoot, at HeldDeep 'deep-8'"):
            await view.child("deep-1")
