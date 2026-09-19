"""Event bindings render as data, not script, so a CSP without 'unsafe-inline' holds (#90).

``{% on %}`` used to render an inline ``onclick="wireview.send(...)"``. The
``script-src-attr`` directive takes no nonce, so enforcing a nonce-based policy
killed every component's events. Now the tag renders
``wire-on-<event>[.<modifier>...]="<json>"`` and the bundle delegates from the
root. The upload tags lose their inline handlers the same way, and the header
carries the request's CSP nonce on its <style> and <script> tags.

The upload tags also escaped their extra attributes wrongly, twice over: once
too much (``class="btn"`` became ``class=&quot;btn&quot;``) and once not at all
(``upload_preview`` marked values safe, so a file name the client chose could
close ``alt`` and add an ``onerror``). And ``upload_preview`` never found an
UploadEntry's ref.
"""

import json
import re
from html import unescape
from html.parser import HTMLParser
from unittest.mock import MagicMock

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from wireview.event_transpiler import binding
from wireview.features.uploads import UploadEntry
from wireview.js import JS
from wireview.templatetags.wireview import upload_preview

pytestmark = pytest.mark.unit


def inline_handlers(html: str) -> list[str]:
    """The ``on*`` attributes a browser would see, read by an HTML parser rather than a regex."""
    found: list[str] = []

    class Collect(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.extend(name for name, _ in attrs if name.lower().startswith("on"))

    Collect().feed(html)
    return found


def _component(**handlers) -> MagicMock:
    component = MagicMock()
    component.id = "comp-1"
    component._name = "Probe"
    for name in handlers or {"save": None}:
        setattr(component, name, MagicMock())
    return component


def _on(tag_args: str, **context) -> str:
    return Template("{% load wireview %}<b {% on " + tag_args + " %}>").render(
        Context({"this": _component(save=None, search=None), **context})
    )


def _binding_of(html: str) -> tuple[str, dict]:
    match = re.search(r'(wire-on-[^=\s]+)="([^"]*)"', html)
    assert match, html
    return match.group(1), json.loads(unescape(match.group(2)))


class TestTheOnTag:
    def test_it_renders_no_inline_handler(self):
        html = _on('"click" "save"')

        assert not inline_handlers(html), html
        assert _binding_of(html) == ("wire-on-click", {"h": "save"})

    def test_modifiers_stay_in_the_name_and_arguments_in_the_value(self):
        html = _on('"input.debounce.300" "search" q=term', term="café")

        assert _binding_of(html) == ("wire-on-input.debounce.300", {"h": "search", "a": {"q": "café"}})

    def test_the_same_event_can_be_bound_twice_on_one_element(self):
        html = Template(
            '{% load wireview %}<input {% on "keyup.enter" "save" %} {% on "keyup.esc" "search" %}>'
        ).render(Context({"this": _component(save=None, search=None)}))

        assert "wire-on-keyup.enter=" in html and "wire-on-keyup.esc=" in html

    def test_a_js_chain_is_data_too(self):
        name, value = binding("click", JS().toggle("#menu").push("save"), {})

        assert name == "wire-on-click"
        assert value == json.dumps({"js": JS().toggle("#menu").push("save")._commands}, separators=(",", ":"))

    def test_a_value_cannot_break_out_of_the_attribute(self):
        html = _on('"click" "save" note=evil', evil='" onclick="alert(1)')

        assert not inline_handlers(html), html
        assert _binding_of(html)[1]["a"]["note"] == '" onclick="alert(1)'

    @pytest.mark.parametrize("event", ['click" onclick="x', "click onmouseover", "click>", "", ".prevent"])
    def test_an_event_name_that_is_not_one_is_refused(self, event):
        with pytest.raises(ValueError):
            binding(event, "save", {})

    def test_inlinejs_is_refused_with_a_pointer_to_js(self):
        with pytest.raises(ValueError, match="JS\\(\\)"):
            binding("click.inlinejs", "save", {})


def _upload_component() -> MagicMock:
    component = MagicMock()
    config = MagicMock(accept=[".png"], max_entries=3)
    component._upload_registry.configs = {"images": config}
    return component


class TestUploadTags:
    def test_the_input_has_no_inline_handler_and_keeps_its_attributes(self):
        html = Template('{% load wireview %}{% upload_input "images" class="hidden" id="pick" %}').render(
            Context({"this": _upload_component()})
        )

        assert not inline_handlers(html), html
        assert 'wire-upload="images"' in html
        assert 'class="hidden"' in html and 'id="pick"' in html

    def test_the_button_opens_the_picker_through_an_attribute(self):
        html = Template('{% load wireview %}{% upload_button "images" class="btn btn-primary" %}').render(
            Context({"this": _upload_component()})
        )

        assert not inline_handlers(html), html
        assert 'wire-upload-select="images"' in html
        assert 'class="btn btn-primary"' in html

    def test_underscores_become_hyphens_so_data_attributes_can_be_written(self):
        html = Template('{% load wireview %}{% upload_button "images" data_testid="pick" %}').render(
            Context({"this": _upload_component()})
        )

        assert 'data-testid="pick"' in html

    def test_the_preview_finds_an_entrys_ref(self):
        entry = UploadEntry(ref="r1", upload_name="images", client_name="a.png", client_size=1, client_type="image/png")

        assert 'wire-preview="images:r1"' in upload_preview(entry)

    def test_a_file_name_cannot_add_attributes_to_the_preview(self):
        name = 'x" onerror="alert(1)'
        entry = UploadEntry(ref="r1", upload_name="images", client_name=name, client_size=1, client_type="image/png")

        html = upload_preview(entry, alt=entry.client_name, data_kind="thumb")

        assert not inline_handlers(html), html
        assert 'alt="x&quot; onerror=&quot;alert(1)"' in html
        assert 'data-kind="thumb"' in html


class TestHeaderNonce:
    def _header(self, request) -> str:
        return Template("{% load wireview %}{% wireview_header %}").render(Context({"request": request}))

    def test_the_style_and_scripts_carry_the_requests_nonce(self):
        request = RequestFactory().get("/")
        request._csp_nonce = "n0nce"

        html = self._header(request)

        assert re.search(r'<style\s+nonce="n0nce">', html), html
        assert re.search(r'src="[^"]*wireview\.min\.js\?v=2"\s+nonce="n0nce">', html), html

    def test_without_a_nonce_nothing_is_added(self):
        html = self._header(RequestFactory().get("/"))

        assert "nonce" not in html
