"""A marked variable prints what Django's own ``VariableNode`` prints, byte for byte (#176).

``MarkedVariableNode`` prints a plain ``int`` with ``str()`` instead of asking
Django's ``localize()``, which costs about 2 µs an int before it reaches the same
shortcut, and escapes a plain ``str`` without passing it through the localization
it does not need. That is only right where Django would not group thousands, and
for exactly those types, so the output is compared with Django's under every
setting that changes it.
"""

import decimal
import enum

import pytest
from django.template import Context, Engine
from django.test import override_settings
from django.utils import translation
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy

from wireview.core.rendered import strip_markers
from wireview.template_engine import TemplateMarker, render_value


class Level(enum.IntEnum):
    HIGH = 3


class Shown(int):
    def __str__(self) -> str:
        return f"#{int(self)}"


VALUES = [
    0,
    7,
    -1234567,
    10**21,
    True,
    False,
    Level.HIGH,
    Shown(1234),
    1234.5,
    decimal.Decimal("1234567.891"),
    None,
    "1234567",
    "<b class='x'>Tom & \"Jerry\"</b>",
    mark_safe("<i>safe</i>"),
    gettext_lazy("Yes & no"),
]

SOURCES = [
    "{{ value }}",
    "{{ value|add:1000 }}",
    "{% load l10n %}{% localize off %}{{ value }}{% endlocalize %}",
    "{% load l10n %}{% localize on %}{{ value }}{% endlocalize %}",
    "{% load l10n %}{{ value|unlocalize }}·{{ value|localize }}",
    "{% autoescape off %}{{ value }}{% endautoescape %}",
]


def _both(source: str, value: object) -> tuple[str, str]:
    engine = Engine(libraries={"l10n": "django.templatetags.l10n"})
    django_output = engine.from_string(source).render(Context({"value": value}))
    marked = TemplateMarker().render_marked(engine.from_string(source), {"value": value})
    return django_output, strip_markers(marked)


@pytest.mark.unit
@pytest.mark.parametrize("grouping", [False, True], ids=["no-grouping", "grouping"])
@pytest.mark.parametrize("language", ["en", "de", "ko"])
@pytest.mark.parametrize("source", SOURCES)
def test_a_marked_value_prints_as_django_prints_it(grouping, language, source):
    with override_settings(USE_THOUSAND_SEPARATOR=grouping, USE_I18N=True), translation.override(language):
        for value in VALUES:
            if "add:" in source and not isinstance(value, (int, float, decimal.Decimal, str)):
                continue
            django_output, marked = _both(source, value)
            assert marked == django_output, f"{value!r} in {source!r}"


@pytest.mark.unit
def test_grouping_is_still_django_s():
    """The case the shortcut must step aside for: thousands grouped, by the active language."""
    with override_settings(USE_THOUSAND_SEPARATOR=True, USE_I18N=True), translation.override("de"):
        assert _both("{{ value }}", 1234567) == ("1.234.567", "1.234.567")
    with override_settings(USE_THOUSAND_SEPARATOR=False):
        assert _both("{{ value }}", 1234567) == ("1234567", "1234567")


@pytest.mark.unit
def test_the_shortcut_takes_plain_ints_alone():
    context = Context()
    with override_settings(USE_THOUSAND_SEPARATOR=False):
        assert render_value(12, context) == "12"
        assert render_value(True, context) == "True"
        assert render_value(Shown(12), context) == "#12"
        assert render_value("<b>", context) == "&lt;b&gt;"
        assert render_value(mark_safe("<b>"), context) == "<b>"


def _undecodable() -> UnicodeDecodeError:
    return UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


class Bad:
    """Printing it fails; Django lets that out, only a failing lookup prints nothing."""

    def __str__(self) -> str:
        raise _undecodable()

    @property
    def lookup(self) -> str:
        raise _undecodable()


class BadSafe(str):
    def __html__(self) -> str:
        raise _undecodable()


@pytest.mark.unit
@pytest.mark.parametrize("value", [Bad(), BadSafe("x")], ids=["str", "html"])
def test_a_value_that_cannot_print_raises_as_in_django(value):
    engine = Engine()
    with pytest.raises(UnicodeDecodeError):
        engine.from_string("{{ value }}").render(Context({"value": value}))
    with pytest.raises(UnicodeDecodeError):
        TemplateMarker().render_marked(engine.from_string("{{ value }}"), {"value": value})


@pytest.mark.unit
def test_a_lookup_that_cannot_decode_prints_nothing_as_in_django():
    assert _both("[{{ value.lookup }}]", Bad()) == ("[]", "[]")
