"""``mount()`` renders children the way the page's first response does (#115).

A component whose template holds ``{% component %}`` or ``{% live_component %}``
used to break ``view.render()`` with an ``AttributeError`` from the stand-in
repository, so its tests asserted on state and never saw the HTML.
"""

import typing as t

import pytest
from django.test import override_settings

from wireview import Component, LiveComponent, mount

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "mr/parent.html": (
        "{% load wireview %}<div {% tag_header %}>"
        "<p>{{ this.wire.params.q }}</p>"
        "{% component 'MrChild' id='c' label=this.label %}"
        "</div>"
    ),
    "mr/child.html": "{% load wireview %}<span {% tag_header %}>{{ this.label }}:{{ this.wire.params.q }}</span>",
    "mr/lparent.html": (
        "{% load wireview %}<div {% tag_header %}>{% live_component 'MrLive' id='l' label=this.label %}</div>"
    ),
    "mr/guarded.html": (
        "{% load wireview %}<div {% tag_header %}>{% component 'MrMembersOnly' id='m' %}<i>after</i></div>"
    ),
    "mr/members.html": "{% load wireview %}<b {% tag_header %}>secret</b>",
    "mr/live.html": "{% load wireview %}<em {% live_tag_header %}>{{ this.label }}/{{ this.loaded }}</em>",
}

CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _templates():
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


class MrChild(Component):
    class Meta:
        template_name = "mr/child.html"

    label: str = ""


class MrParent(Component):
    class Meta:
        template_name = "mr/parent.html"

    label: str = "first"

    async def rename(self, label: str):
        self.label = label

    async def search(self, q: str):
        await self.wire.push_to(f"?q={q}")


class MembersOnly:
    @staticmethod
    async def on_mount(component, params, session):
        CALLS.append("on_mount")
        if not component.user.is_authenticated:
            return {"halt": True}
        return {"cont": True}


class MrMembersOnly(Component):
    class Meta:
        template_name = "mr/members.html"
        on_mount = [MembersOnly]


class MrGuarded(Component):
    class Meta:
        template_name = "mr/guarded.html"


class MrLive(LiveComponent):
    class Meta:
        template_name = "mr/live.html"

    label: str = ""
    loaded: bool = False

    async def joined(self):
        CALLS.append("joined")
        self.loaded = True

    async def update(self, props: dict[str, t.Any]):
        CALLS.append("update")
        await super().update(props)


class MrLParent(Component):
    class Meta:
        template_name = "mr/lparent.html"

    label: str = "first"

    async def rename(self, label: str):
        self.label = label


async def test_a_nested_component_renders_inside_its_parent():
    view = await mount(MrParent)

    html = view.render()

    assert html is not None
    assert ">first:</span>" in html


async def test_the_nested_component_follows_the_parent_across_renders():
    view = await mount(MrParent)
    view.render()

    await view.call("rename", label="second")

    assert ">second:</span>" in (view.render() or "")


async def test_a_live_component_renders_inline_without_its_socket_lifecycle():
    """The first response draws the child and runs no ``joined()``; the consumer does that."""
    view = await mount(MrLParent)

    html = view.render() or ""
    await view.call("rename", label="second")
    again = view.render() or ""

    assert ">first/False</em>" in html
    assert ">second/False</em>" in again
    assert CALLS == []


async def test_children_read_the_params_the_parent_navigated_to():
    """The repository shares the mounted component's params dict, even an empty one."""
    view = await mount(MrParent)

    await view.call("search", q="wire")
    await view.follow_push()

    html = view.render() or ""
    assert "<p>wire</p>" in html
    assert ">first:wire</span>" in html


async def test_a_child_that_its_mount_hook_refuses_is_left_out():
    """The hook runs on the child as it would on the page; the parent still renders."""
    view = await mount(MrGuarded)

    html = view.render() or ""

    assert CALLS == ["on_mount"]
    assert "secret" not in html
    assert "<i>after</i>" in html
