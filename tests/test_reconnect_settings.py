"""The client's reconnect backoff is a setting (#124).

A rolling deploy closes every socket a process holds at once, and each page that
reconnects joins all its components again. The pace is the operator's to set; the
header publishes it and ``static/wireview/reconnect.mjs`` reads it
(``tests/js/reconnect.test.mjs``).
"""

import re

import pytest
from django.template import Context, Template
from django.test import override_settings
from django.utils import translation

from wireview import settings as wireview_settings

pytestmark = pytest.mark.unit


def reconnect_meta() -> dict[str, str]:
    html = Template("{% load wireview %}{% wireview_header %}").render(Context({}))
    match = re.search(r'<meta name="wireview-reconnect"(.*?)/>', html, re.S)
    assert match, html
    return dict(re.findall(r'(data-[\w-]+)="([^"]*)"', match.group(1)))


def test_the_header_publishes_the_defaults():
    assert reconnect_meta() == {
        "data-min-delay": "1000",
        "data-jitter": "4000",
        "data-max-delay": "10000",
        "data-grow-factor": "1.3",
    }


def test_the_defaults_are_what_the_client_used_before():
    """reconnecting-websocket's own; the JS test pins these against the library."""
    assert wireview_settings.RECONNECT_MIN_DELAY_MS == 1000
    assert wireview_settings.RECONNECT_JITTER_MS == 4000
    assert wireview_settings.RECONNECT_MAX_DELAY_MS == 10000
    assert wireview_settings.RECONNECT_GROW_FACTOR == 1.3


@override_settings(
    WIREVIEW={
        "RECONNECT_MIN_DELAY_MS": 3000,
        "RECONNECT_JITTER_MS": 27000,
        "RECONNECT_MAX_DELAY_MS": 60000,
        "RECONNECT_GROW_FACTOR": 2,
    }
)
def test_the_settings_reach_the_header():
    assert reconnect_meta() == {
        "data-min-delay": "3000",
        "data-jitter": "27000",
        "data-max-delay": "60000",
        "data-grow-factor": "2",
    }


@override_settings(USE_THOUSAND_SEPARATOR=True, WIREVIEW={"RECONNECT_MAX_DELAY_MS": 60000})
def test_a_locale_does_not_reformat_the_numbers():
    """``1,3`` or ``60.000`` would read as NaN in the client and silently fall back."""
    with translation.override("de"):
        meta = reconnect_meta()

    assert meta["data-grow-factor"] == "1.3"
    assert meta["data-max-delay"] == "60000"
