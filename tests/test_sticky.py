"""A sticky component rendered without an id still sticks (#128).

A boosted navigation pairs a sticky element with the next page's by id. The
random ``rx-<uuid>`` an id-less ``{% component %}`` got never paired, so
``sticky = True`` was switched off with no signal at all. The tag now gives an
id-less sticky component an id derived from its class, the same on every page.

The browser side is tests/test_sticky_e2e.py.
"""

import logging
import re

import pytest
from django.template import Context, Template
from django.test import RequestFactory
from testproj.stickyprobe.live import PlainCounter

from wireview.repository import ComponentRepository

pytestmark = [pytest.mark.unit, pytest.mark.django_db]

#: Derived from the fully qualified name, so same-named classes in two apps do not collide
PLAYER_ID = "sticky-testproj-stickyprobe-live-StickyPlayer"


def render(source: str, **context) -> str:
    request = RequestFactory().get("/")
    return Template("{% load wireview %}" + source).render(Context({"request": request, **context}))


def ids(html: str) -> list[str]:
    return re.findall(r'\bid="([^"]+)"', html)


def test_an_id_less_sticky_component_gets_the_same_id_on_every_page():
    first = render("{% component 'StickyPlayer' %}")
    second = render("{% component 'StickyPlayer' %}")

    assert ids(first) == ids(second) == [PLAYER_ID]
    assert "wire-sticky" in first


def test_an_id_given_in_the_template_wins():
    assert ids(render("{% component 'StickyPlayer' id='player' %}")) == ["player"]


def test_a_component_that_is_not_sticky_keeps_its_random_id():
    first, second = render("{% component 'PlainCounter' %}"), render("{% component 'PlainCounter' %}")
    assert ids(first)[0].startswith("rx-")
    assert ids(first) != ids(second)
    assert PlainCounter._meta.sticky is False


def test_a_second_id_less_instance_on_one_page_is_told_and_does_not_share_the_id(caplog):
    with caplog.at_level(logging.WARNING, logger="wireview"):
        html = render("{% component 'StickyPlayer' %}{% component 'StickyPlayer' %}")

    first, second = ids(html)
    assert first == PLAYER_ID
    assert second.startswith("rx-"), "two elements with one id would be one component to the page"
    assert any("rendered more than once on this page without an id" in r.getMessage() for r in caplog.records)


def test_two_sticky_instances_with_their_own_ids_are_quiet(caplog):
    with caplog.at_level(logging.WARNING, logger="wireview"):
        html = render("{% component 'StickyPlayer' id='a' %}{% component 'StickyPlayer' id='b' %}")
    assert ids(html) == ["a", "b"]
    assert not caplog.records


def test_a_live_render_reuses_the_instance_the_connection_holds(caplog):
    """A parent's re-render names its sticky child again: the same instance, no warning."""
    repo = ComponentRepository(is_live=True, user=None, params={})
    held = repo.build("StickyPlayer", state={"id": PLAYER_ID, "count": 3})

    with caplog.at_level(logging.WARNING, logger="wireview"):
        render("{% component 'StickyPlayer' %}", wireview_repository=repo)

    assert repo.get(PLAYER_ID) is held
    assert held.count == 3
    assert not caplog.records
