"""Who hears a page's params, and when (#170).

- A join hears the page's query before the render that answers it, as Phoenix's
  mount runs handle_params before the first render. The LiveComponents that
  render brings in hear it after their own ``joined()``: parent joined, parent
  params, child joined, child params.
- A LiveComponent a later render shows for the first time hears the params
  then too, once.
"""

from __future__ import annotations

import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from testproj.outbound import RecordingOutbound

from wireview import Component, LiveComponent, mount
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION
from wireview.core.session import SessionView
from wireview.core.state import sign_state
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db, pytest.mark.asyncio]

TEMPLATES = {
    "nav/root.html": (
        "{% load wireview %}<div {% tag_header %}>tab={{ this.tab }}"
        "{% if this.show %}{% live_component 'NavLeaf' id=this.leaf %}{% endif %}</div>"
    ),
    "nav/leaf.html": "{% load wireview %}<p {% live_tag_header %}>leaf={{ this.tab }}</p>",
}

#: What happened, in order: ("joined" | "params", component id, the tab it heard)
EVENTS: list[tuple[str, str, str]] = []


class _Recording:
    tab: str = ""

    async def joined(self):
        EVENTS.append(("joined", self.id, ""))  # type: ignore[attr-defined]

    async def params_changed(self, params, uri):
        self.tab = params.get("tab", "")
        EVENTS.append(("params", self.id, self.tab))  # type: ignore[attr-defined]


class NavRoot(_Recording, Component):
    class Meta:
        template_name = "nav/root.html"

    tab: str = ""
    show: bool = True
    leaf: str = "nav-leaf"

    async def params_changed(self, params, uri):
        await super().params_changed(params, uri)
        if "show" in params:
            self.show = params["show"] == "1"


class NavLeaf(_Recording, LiveComponent):
    class Meta:
        template_name = "nav/leaf.html"

    tab: str = ""


class NavRaisingLeaf(NavLeaf):
    async def params_changed(self, params, uri):
        raise RuntimeError("params went wrong")


class NavRaisingRoot(NavRoot):
    class Meta:
        template_name = "nav/raising.html"


TEMPLATES["nav/raising.html"] = (
    "{% load wireview %}<div {% tag_header %}>tab={{ this.tab }}{% live_component 'NavRaisingLeaf' id='bad' %}</div>"
)


@pytest.fixture(autouse=True)
def _templates():
    EVENTS.clear()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


async def started() -> tuple[WireviewSession, RecordingOutbound]:
    outbound = RecordingOutbound()
    session = WireviewSession(outbound, user=AnonymousUser(), channel_name="nav-1")
    await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
    return session, outbound


def signed(cls: type[Component] = NavRoot, **fields: t.Any) -> str:
    return sign_state(cls(user=AnonymousUser(), wire=WireviewMeta(params={}), **fields))


async def send(session: WireviewSession, command: str, **payload: t.Any) -> None:
    await session.handle_message({"command": command, "payload": payload})


async def join(session: WireviewSession, cls: type[Component] = NavRoot, **fields: t.Any) -> None:
    await send(session, "join", name=cls.__name__, state=signed(cls, **fields))


# --- a join -----------------------------------------------------------------------------


async def test_a_join_hears_the_params_before_its_first_render_and_its_child_after_joining():
    session, outbound = await started()
    await send(session, "params_changed", params={"tab": "b"}, uri="http://x/p/?tab=b")

    await join(session, id="r")

    assert EVENTS == [
        ("joined", "r", ""),
        ("params", "r", "b"),
        ("joined", "nav-leaf", ""),
        ("params", "nav-leaf", "b"),
    ]
    # One render answers the join, and it draws what the params brought
    (answer,) = outbound.renders()
    assert answer["id"] == "r" and answer["vsn"] == PROTOCOL_VERSION
    assert "b" in answer["diff"]["d"]
    assert "b" in answer["children"]["nav-leaf"]["d"]


async def test_a_join_with_no_params_hears_none():
    session, _outbound = await started()
    await send(session, "params_changed", params={}, uri="http://x/p/")

    await join(session, id="r")

    assert EVENTS == [("joined", "r", ""), ("joined", "nav-leaf", "")]


async def test_a_child_restored_from_the_page_hears_the_page_params_not_its_stored_ones():
    # The race #169's comparison E2E lost one run in six: a LiveComponent under
    # an id the previous page had came back with what it heard there
    session, outbound = await started()
    await send(session, "params_changed", params={"tab": "new"}, uri="http://x/p/?tab=new")
    stored = sign_state(NavLeaf(user=AnonymousUser(), wire=WireviewMeta(params={}), id="nav-leaf", tab="old"))

    await send(
        session,
        "join",
        name="NavRoot",
        state=signed(id="r"),
        children={"nav-leaf": ["NavLeaf", stored]},
    )

    assert session.repo.get("nav-leaf").tab == "new"  # type: ignore[union-attr]
    (answer,) = outbound.renders()
    assert "new" in answer["children"]["nav-leaf"]["d"]


async def test_a_child_a_later_render_shows_hears_the_params_once():
    session, _outbound = await started()
    await send(session, "params_changed", params={"tab": "a", "show": "0"}, uri="http://x/p/?tab=a&show=0")
    await join(session, id="r", show=False)
    EVENTS.clear()

    # A patch shows it: the parent hears the params and its render brings the
    # child in, which hears them after joining -- and not again from the patch
    await send(session, "params_changed", params={"tab": "c", "show": "1"}, uri="http://x/p/?tab=c&show=1")

    assert EVENTS == [("params", "r", "c"), ("joined", "nav-leaf", ""), ("params", "nav-leaf", "c")]


async def test_a_child_whose_params_raise_leaves_its_parent_joined():
    session, outbound = await started()
    await send(session, "params_changed", params={"tab": "b"}, uri="http://x/p/?tab=b")

    await join(session, NavRaisingRoot, id="r")

    # Logged, as the child's joined() raising is on this path
    assert [payload["id"] for payload in outbound.renders()] == ["r"]
    assert not [command for command, _payload in outbound.commands if command == "error"]
    assert session.repo.get("bad") is not None


# --- mount() ----------------------------------------------------------------------------


async def test_mount_runs_joined_then_params_for_a_live_component():
    view = await mount(NavLeaf, params={"tab": "m"}, id="leaf-only")

    assert EVENTS == [("joined", "leaf-only", ""), ("params", "leaf-only", "m")]
    assert view.component.tab == "m"
