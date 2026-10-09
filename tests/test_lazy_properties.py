"""``Meta.lazy_properties``: a sync property is read when the template reads its name (#187).

Without it a render reads every public property before the template runs,
whether the template names it or not. With it, one the template does not name
is not run, nor its SQL; one it names runs once a render, wherever the
template reads it -- through an include, an extends parent, ``{% class %}``,
a tag that takes the context. What a property raises still fails the render.
"""

import functools
import typing as t

import pytest
from asgiref.sync import sync_to_async
from django import template
from django.contrib.auth.models import User
from django.test import override_settings

from wireview import Component
from wireview.testing import mount

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "lazy/plain.html": "{% load wireview %}<div {% tag_header %}><b>{{ used }}</b><i>{{ n }}</i></div>",
    "lazy/this.html": "{% load wireview %}<div {% tag_header %}><b>{{ this.used }}</b></div>",
    "lazy/both.html": "{% load wireview %}<div {% tag_header %}><b>{{ used }}</b><s>{{ this.used }}</s></div>",
    "lazy/twice.html": "{% load wireview %}<div {% tag_header %}><b>{{ used }}</b><s>{{ used }}</s></div>",
    "lazy/users.html": "{% load wireview %}<div {% tag_header %}><b>{{ users }}</b></div>",
    "lazy/class.html": "{% load wireview %}<div {% tag_header %}><p {% class {'on': used} %}></p></div>",
    "lazy/include.html": "{% load wireview %}<div {% tag_header %}>{% include 'lazy/part.html' %}</div>",
    "lazy/part.html": "<b>{{ used }}</b>",
    "lazy/child.html": (
        "{% extends 'lazy/parent.html' %}{% load wireview %}{% block body %}<s>{{ n }}</s>{% endblock %}"
    ),
    "lazy/parent.html": (
        "{% load wireview %}<div {% tag_header %}><b>{{ used }}</b>{% block body %}{% endblock %}</div>"
    ),
    "lazy/loop.html": (
        "{% load wireview %}<div {% tag_header %}>{% with u=used %}<b>{{ u }}</b>{% endwith %}"
        "{% for x in rows %}<i>{{ x }}</i>{% endfor %}</div>"
    ),
    "lazy/tag.html": "{% load wireview lazy_tags %}<div {% tag_header %}><b>{% used_from_context %}</b></div>",
    "lazy/cached.html": "{% load wireview %}<div {% tag_header %}><b>{{ cached }}</b></div>",
    "lazy/async.html": "{% load wireview %}<div {% tag_header %}><b>{{ later }}</b></div>",
    "lazy/method.html": "{% load wireview %}<div {% tag_header %}>[{{ handler }}]</div>",
    "lazy/boom.html": "{% load wireview %}<div {% tag_header %}>[{{ boom }}]</div>",
    "lazy/boomif.html": "{% load wireview %}<div {% tag_header %}>{% if boom and n %}yes{% else %}no{% endif %}</div>",
    "lazy/boomvalue.html": "{% load wireview %}<div {% tag_header %}>[{{ fails }}]</div>",
    "lazy/watched.html": "{% load wireview %}<div {% tag_header %}><b>{{ n }}</b></div>",
}

register = template.Library()


@register.simple_tag(takes_context=True)
def used_from_context(context):
    return context["used"]


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)],
                    "libraries": {"lazy_tags": __name__},
                },
            }
        ]
    ):
        yield


#: How often each property ran, by name
CALLS: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _calls():
    CALLS.clear()
    yield


def _ran(name: str) -> None:
    CALLS[name] = CALLS.get(name, 0) + 1


class Lazy(Component):
    class Meta:
        template_name = "lazy/plain.html"
        lazy_properties = True

    n: int = 0
    rows: list[str] = ["a", "b"]

    @property
    def used(self) -> str:
        _ran("used")
        return f"used{self.n}"

    @property
    def unused(self) -> int:
        _ran("unused")
        return User.objects.count()

    @property
    def users(self) -> int:
        _ran("users")
        return User.objects.count()

    @functools.cached_property
    def cached(self) -> str:
        _ran("cached")
        return "cached"

    async def bump(self):
        self.n += 1


class Odd:
    """Properties a render handles apart: awaited up front, giving a callable, raising."""

    @property
    async def later(self) -> str:
        _ran("later")
        return "later"

    @property
    def handler(self) -> t.Callable[[], str]:
        _ran("handler")
        return lambda: "called"

    @property
    def boom(self) -> bool:
        raise AttributeError("boom is missing")

    @property
    def fails(self) -> str:
        raise ValueError("fails")


def lazy(template_name: str, eager: bool = False, odd: bool = False) -> type[Component]:
    """``Lazy`` drawn with ``template_name``; with ``eager``, as a component without the option."""
    meta = type("Meta", (), {"template_name": template_name, "lazy_properties": not eager})
    name = template_name.split("/")[1].removesuffix(".html").title()
    bases = (Odd, Lazy) if odd else (Lazy,)
    return type(f"Lazy{name}{'Eager' if eager else ''}", bases, {"Meta": meta, "__module__": __name__})


LazyPlain = lazy("lazy/plain.html")
LazyPlainEager = lazy("lazy/plain.html", eager=True)
LazyThis = lazy("lazy/this.html")
LazyBoth = lazy("lazy/both.html")
LazyTwice = lazy("lazy/twice.html")
LazyUsers = lazy("lazy/users.html")
LazyUsersEager = lazy("lazy/users.html", eager=True)
LazyClass = lazy("lazy/class.html")
LazyInclude = lazy("lazy/include.html")
LazyChild = lazy("lazy/child.html")
LazyLoop = lazy("lazy/loop.html")
LazyTag = lazy("lazy/tag.html")
LazyCached = lazy("lazy/cached.html")
LazyAsync = lazy("lazy/async.html", odd=True)
LazyMethod = lazy("lazy/method.html", odd=True)
LazyBoom = lazy("lazy/boom.html", odd=True)
LazyBoomIf = lazy("lazy/boomif.html", odd=True)
LazyBoomValue = lazy("lazy/boomvalue.html", odd=True)


async def live(cls: type[Component]) -> str:
    """The live render after an event, as the page has it."""
    view = await mount(cls)
    await view.render_diff()
    CALLS.clear()
    await view.call("bump")
    await view.render_diff()
    return view.component.wire._last_rendered.to_html()  # type: ignore[union-attr]


async def http(cls: type[Component]) -> str:
    view = await mount(cls)
    CALLS.clear()
    return await sync_to_async(view.render)() or ""


RENDERS = [pytest.param(live, id="live"), pytest.param(http, id="http")]


@pytest.mark.parametrize("render", RENDERS)
async def test_a_property_the_template_does_not_name_is_not_run(render):
    html = await render(LazyPlain)

    assert "<b>used" in html
    assert CALLS == {"used": 1}


@pytest.mark.parametrize("render", RENDERS)
async def test_without_the_option_every_property_is_run(render):
    """The control: the render reads every public property up front, as it always has."""
    await render(LazyPlainEager)

    # The cached property was computed by the live render before the event
    assert {"used": 1, "unused": 1, "users": 1}.items() <= CALLS.items(), CALLS


async def test_the_sql_of_a_property_the_template_does_not_name_does_not_run():
    view = await mount(LazyPlain)
    eager = await mount(LazyPlainEager)

    async with view.queries() as lazily:
        await view.render_diff()
    async with eager.queries() as up_front:
        await eager.render_diff()

    assert lazily.count == 0
    assert up_front.count == 2  # unused and users, neither named by the template


async def test_the_sql_of_a_named_property_is_its_own_in_the_record():
    view = await mount(LazyUsers)

    async with view.queries() as q:
        await view.render_diff()

    assert q.count == 1
    assert q.rows[0].prop == "users", q.rows


@pytest.mark.parametrize(
    ("cls", "calls"),
    [
        pytest.param(LazyThis, 1, id="this-only"),
        pytest.param(LazyTwice, 1, id="name-twice"),
        pytest.param(LazyBoth, 2, id="name-and-this"),
    ],
)
async def test_a_named_property_runs_once_a_render_through_its_name(cls, calls):
    """``this.used`` reads the component, not the context: only a template that also names it runs it twice."""
    await live(cls)

    assert CALLS == {"used": calls}


@pytest.mark.parametrize("render", RENDERS)
@pytest.mark.parametrize(
    ("cls", "drawn"),
    [
        pytest.param(LazyClass, 'class="on"', id="class"),
        pytest.param(LazyInclude, "<b>used", id="include"),
        pytest.param(LazyChild, "<b>used", id="extends"),
        pytest.param(LazyLoop, "<b>used", id="with"),
        pytest.param(LazyTag, "<b>used", id="context-tag"),
    ],
)
async def test_a_property_read_where_the_variables_do_not_show_it_is_run(render, cls, drawn):
    html = await render(cls)

    assert drawn in html, html
    assert CALLS == {"used": 1}


async def test_a_cached_property_is_read_when_named_and_kept():
    view = await mount(LazyCached)
    await view.render_diff()
    await view.call("bump")
    await view.render_diff()

    assert "<b>cached</b>" in view.component.wire._last_rendered.to_html()  # type: ignore[union-attr]
    assert CALLS == {"cached": 1}


@pytest.mark.parametrize("render", RENDERS)
async def test_an_async_property_is_still_awaited_before_the_template(render):
    html = await render(LazyAsync)

    assert "<b>later</b>" in html
    assert CALLS == {"later": 1}


@pytest.mark.parametrize("render", RENDERS)
async def test_a_property_that_gives_a_callable_is_not_in_the_context(render):
    html = await render(LazyMethod)

    assert "[]" in html, html  # as when it is read up front


@pytest.mark.parametrize("render", RENDERS)
@pytest.mark.parametrize(
    ("cls", "error"),
    [
        pytest.param(LazyBoom, AttributeError, id="attribute-error"),
        pytest.param(LazyBoomIf, AttributeError, id="under-an-if-operator"),
        pytest.param(LazyBoomValue, ValueError, id="value-error"),
    ],
)
async def test_what_a_property_raises_fails_the_render(render, cls, error):
    """Django's lookup swallows an AttributeError, and anything under an ``{% if %}`` operator."""
    with pytest.raises(error):
        await render(cls)


async def test_a_subclass_keeps_the_option():
    class LazierPlain(LazyPlain):
        pass

    assert LazierPlain._meta.lazy_properties
    assert not Component._meta.lazy_properties
