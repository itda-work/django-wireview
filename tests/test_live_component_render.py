"""Render split for LiveComponents (#79, docs/design/live-component-ownership.md §3-2..3-4).

The parent's template pass only names its children; ``send_render`` then runs each child's
lifecycle and ships the children's diffs inside the parent's ``render`` frame.
"""

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview import Component, LiveComponent
from wireview.consumer import WireviewConsumer
from wireview.core.rendered import ComponentRef, Rendered, component_ref_marker, component_refs
from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "rs/board.html": (
        "{% load wireview %}<section {% tag_header %}>"
        "<h1>{{ this.title }}</h1>"
        "{% for cid in this.cards %}{% live_component 'RsCard' id=cid label=this.label %}{% endfor %}"
        "{% if this.show_extra %}{% live_component 'RsCard' id='extra' label='x' %}{% endif %}"
        "</section>"
    ),
    "rs/card.html": (
        "{% load wireview %}<article {% live_tag_header %}>"
        "<b>{{ this.label }}</b><i>{{ this.loaded }}</i><span>{{ this.count }}</span>"
        "{% if this.with_leaf %}{% live_component 'RsLeaf' id=this.leaf_id %}{% endif %}"
        "</article>"
    ),
    "rs/leaf.html": "{% load wireview %}<em {% live_tag_header %}>{{ this.loaded }}</em>",
}

CALLS: list[tuple[str, str, t.Any]] = []


class RsBoard(Component):
    _template_name = "rs/board.html"
    _subscriptions = {"rs-topic"}
    title: str = "Board"
    label: str = "a"
    cards: list[str] = ["c1", "c2"]
    show_extra: bool = False

    async def add_extra(self):
        self.show_extra = True

    async def drop_extra(self):
        self.show_extra = False

    async def relabel(self, label: str):
        self.label = label

    async def retitle(self):
        self.title = "Board!"

    async def params_changed(self, params, uri):
        if params.get("extra"):
            self.show_extra = True

    async def notification(self, channel, **kwargs):
        self.show_extra = True


class RsCard(LiveComponent):
    _template_name = "rs/card.html"
    label: str = ""
    count: int = 0
    loaded: str = ""
    with_leaf: bool = False
    leaf_id: str = ""

    async def joined(self):
        CALLS.append(("joined", self.id, self.label))
        self.loaded = "ready"

    async def update(self, **assigns):
        CALLS.append(("update", self.id, dict(assigns)))
        await super().update(**assigns)

    async def leaving(self):
        CALLS.append(("leaving", self.id, None))

    async def bump(self):
        self.count += 1

    async def grow_leaf(self):
        self.with_leaf = True
        self.leaf_id = f"{self.id}-leaf"


class RsLeaf(LiveComponent):
    _template_name = "rs/leaf.html"
    loaded: str = ""

    async def joined(self):
        CALLS.append(("joined", self.id, None))
        self.loaded = "leaf ready"


class RsFailingCard(LiveComponent):
    _template_name = "rs/card.html"
    label: str = ""
    count: int = 0
    loaded: str = ""
    with_leaf: bool = False
    leaf_id: str = ""

    async def joined(self):
        raise RuntimeError("joined blew up")


class RsFailBoard(Component):
    _template_name = "rs/failboard.html"


TEMPLATES["rs/failboard.html"] = (
    "{% load wireview %}<section {% tag_header %}>"
    "{% live_component 'RsFailingCard' id='bad' %}{% live_component 'RsCard' id='good' label='g' %}"
    "</section>"
)


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def unsubscribe(self, topic: str) -> None:
        self.unsubscribed.append(topic)

    def renders(self) -> list[dict[str, t.Any]]:
        return [payload for command, payload in self.commands if command == "render"]


@pytest.fixture(autouse=True)
def _templates_and_calls():
    CALLS.clear()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield
    CALLS.clear()


def make_consumer() -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


async def join_board(consumer: WireviewConsumer, **state) -> Component:
    board = await consumer.repo.join("RsBoard", {"id": "board", **state})
    await consumer.send_render(board)
    return board


def dumps(payload: t.Any) -> str:
    import json

    return json.dumps(payload)


# --- the diff engine ----------------------------------------------------------------------


async def test_a_ref_marker_parses_to_a_component_ref_and_round_trips():
    html = f"<main>{component_ref_marker('c1', 0)}</main>"
    rendered = Rendered.from_marked_html(html)

    assert rendered.static == ["<main>", "</main>"]
    assert rendered.dynamic == [ComponentRef("c1")]
    assert rendered.to_dict()["d"] == [{"c": "c1"}]
    assert Rendered.from_dict(rendered.to_dict()).dynamic == [ComponentRef("c1")]
    assert component_refs(rendered) == ["c1"]


async def test_an_unchanged_ref_is_not_in_the_partial_diff_and_a_swapped_one_is():
    before = Rendered.from_marked_html(f"<main><!--$0-->x<!--/$0-->{component_ref_marker('c1', 1)}</main>")
    same = Rendered.from_marked_html(f"<main><!--$0-->y<!--/$0-->{component_ref_marker('c1', 1)}</main>")
    swapped = Rendered.from_marked_html(f"<main><!--$0-->y<!--/$0-->{component_ref_marker('c9', 1)}</main>")

    assert same.get_diff(before).to_payload() == {"0": "y"}
    assert swapped.get_diff(same).to_payload() == {"1": {"c": "c9"}}


async def test_a_plain_comment_that_is_not_a_ref_stays_text():
    rendered = Rendered.from_marked_html("<main><!--$0--><!--@wv:c1--> tail<!--/$0--></main>")
    assert rendered.dynamic == ["<!--@wv:c1--> tail"]


# --- join: one frame, children first ------------------------------------------------------


async def test_join_sends_one_render_frame_with_the_children_inside():
    consumer, outbound = make_consumer()
    await join_board(consumer)

    renders = outbound.renders()
    assert len(renders) == 1, "parent and children travel in one frame"
    frame = renders[0]
    assert frame["id"] == "board"
    parent_json = dumps(frame["diff"])
    assert '"c": "c1"' in parent_json and '"c": "c2"' in parent_json
    assert "<article" not in parent_json, "the parent's diff carries no child markup"
    assert set(frame["children"]) == {"c1", "c2"}
    assert all("s" in frame["children"][cid] for cid in ("c1", "c2")), "first renders are full"


async def test_children_render_after_joined_so_their_first_html_reflects_it():
    consumer, outbound = make_consumer()
    await join_board(consumer)

    frame = outbound.renders()[0]
    for cid in ("c1", "c2"):
        assert "ready" in dumps(frame["children"][cid]), f"{cid} rendered before joined() ran"
    assert [c for c in CALLS if c[0] == "joined"] == [("joined", "c1", "a"), ("joined", "c2", "a")]


async def test_joined_runs_once_per_child_across_parent_rerenders():
    consumer, _ = make_consumer()
    board = await join_board(consumer)
    await consumer.command_user_event("board", "retitle", {}, {})
    await consumer.send_render(board)

    assert [c for c in CALLS if c[0] == "joined"] == [("joined", "c1", "a"), ("joined", "c2", "a")]


# --- parent re-renders ----------------------------------------------------------------------


async def test_a_parent_change_that_touches_no_prop_ships_no_child():
    consumer, outbound = make_consumer()
    await join_board(consumer)
    outbound.commands.clear()

    await consumer.command_user_event("board", "retitle", {}, {})

    frame = outbound.renders()[0]
    assert "children" not in frame
    assert [c for c in CALLS if c[0] == "update"] == []


async def test_a_changed_prop_updates_and_rerenders_only_the_children_it_reaches():
    consumer, outbound = make_consumer()
    await join_board(consumer)
    outbound.commands.clear()

    await consumer.command_user_event("board", "relabel", {}, {"label": "b"})

    frame = outbound.renders()[0]
    assert set(frame["children"]) == {"c1", "c2"}
    assert [c for c in CALLS if c[0] == "update"] == [
        ("update", "c1", {"label": "b"}),
        ("update", "c2", {"label": "b"}),
    ]
    for cid in ("c1", "c2"):
        assert frame["children"][cid] and "s" not in frame["children"][cid], "a re-render of a child is partial"


async def test_a_childs_own_state_survives_an_unrelated_parent_rerender():
    consumer, _ = make_consumer()
    await join_board(consumer)
    await consumer.command_user_event("c1", "bump", {}, {})
    assert consumer.repo.get("c1").count == 1

    await consumer.command_user_event("board", "retitle", {}, {})

    assert consumer.repo.get("c1").count == 1


async def test_a_child_only_event_renders_only_the_child():
    consumer, outbound = make_consumer()
    await join_board(consumer)
    outbound.commands.clear()

    await consumer.command_user_event("c1", "bump", {}, {})

    renders = outbound.renders()
    assert [r["id"] for r in renders] == ["c1"]
    assert "children" not in renders[0]


# --- children that appear and disappear -----------------------------------------------------


async def test_a_child_that_appears_through_an_event_is_joined_and_shipped():
    consumer, outbound = make_consumer()
    await join_board(consumer)
    outbound.commands.clear()

    await consumer.command_user_event("board", "add_extra", {}, {})

    frame = outbound.renders()[0]
    assert set(frame["children"]) == {"extra"}
    assert ("joined", "extra", "x") in CALLS
    assert "ready" in dumps(frame["children"]["extra"])


async def test_a_child_that_appears_through_params_changed_is_joined():
    consumer, outbound = make_consumer()
    await join_board(consumer)
    outbound.commands.clear()

    await consumer.command_params_changed({"extra": "1"}, "?extra=1")

    assert ("joined", "extra", "x") in CALLS
    assert any("extra" in (r.get("children") or {}) for r in outbound.renders())


async def test_a_child_that_appears_through_a_broadcast_is_joined():
    consumer, outbound = make_consumer()
    await join_board(consumer)
    outbound.commands.clear()

    await consumer.notification({"channel": "rs-topic", "kwargs": {}})

    assert ("joined", "extra", "x") in CALLS
    assert any("extra" in (r.get("children") or {}) for r in outbound.renders())


async def test_a_child_the_parent_stops_naming_gets_leaving_and_is_removed():
    consumer, _ = make_consumer()
    await join_board(consumer)
    await consumer.command_user_event("board", "add_extra", {}, {})
    assert consumer.repo.get("extra") is not None

    await consumer.command_user_event("board", "drop_extra", {}, {})

    assert ("leaving", "extra", None) in CALLS
    assert consumer.repo.get("extra") is None
    assert {c.id for c in consumer.repo.get_live_components("board")} == {"c1", "c2"}


async def test_a_skipped_parent_render_does_not_retire_its_children():
    consumer, _ = make_consumer()
    board = await join_board(consumer)

    board.skip_render()
    await consumer.send_render(board)

    assert {c.id for c in consumer.repo.get_live_components("board")} == {"c1", "c2"}
    assert [c for c in CALLS if c[0] == "leaving"] == []


# --- nesting and failure ----------------------------------------------------------------------


async def test_a_grandchild_is_joined_and_travels_in_the_same_frame():
    consumer, outbound = make_consumer()
    await join_board(consumer)
    outbound.commands.clear()

    await consumer.command_user_event("c1", "grow_leaf", {}, {})

    frame = outbound.renders()[0]
    assert frame["id"] == "c1"
    assert set(frame["children"]) == {"c1-leaf"}
    assert ("joined", "c1-leaf", None) in CALLS
    assert "leaf ready" in dumps(frame["children"]["c1-leaf"])
    assert consumer.repo.get("c1-leaf")._parent_id == "c1"


async def test_a_failing_joined_is_logged_and_the_other_children_still_render():
    consumer, outbound = make_consumer()
    board = await consumer.repo.join("RsFailBoard", {"id": "fb"})

    await consumer.send_render(board)

    frame = outbound.renders()[0]
    assert set(frame["children"]) == {"bad", "good"}
    assert ("joined", "good", "g") in CALLS
    assert consumer.repo.get("bad").wire._pending_mode is False, "pending mode was released"
    assert consumer.repo.get("good").wire._pending_mode is False


async def test_subscriptions_are_synced_before_the_childrens_queued_operations_flush(monkeypatch):
    """A broadcast queued in a child's joined() must not leave before this connection subscribed."""
    from wireview.core.meta import WireviewMeta

    order: list[str] = []

    class RsSubCard(LiveComponent):
        _template_name = "rs/leaf.html"
        _subscriptions = {"rs-sub-topic"}
        loaded: str = ""

        async def joined(self):
            await self.broadcast("rs-sub-topic", hello=1)

    class RsSubBoard(Component):
        _template_name = "rs/subboard.html"

    TEMPLATES["rs/subboard.html"] = (
        "{% load wireview %}<section {% tag_header %}>{% live_component 'RsSubCard' id='s1' %}</section>"
    )

    async def send_broadcast(self, channel, **kwargs):
        order.append(f"publish:{channel}")

    monkeypatch.setattr(WireviewMeta, "_send_broadcast", send_broadcast)
    consumer, outbound = make_consumer()

    async def subscribe(topic):
        order.append(f"subscribe:{topic}")

    outbound.subscribe = subscribe  # type: ignore[method-assign]
    board = await consumer.repo.join("RsSubBoard", {"id": "sb"})

    await consumer.send_render(board)

    assert order == ["subscribe:rs-sub-topic", "publish:rs-sub-topic"]
