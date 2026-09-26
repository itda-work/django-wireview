"""wireview reads its settings when it uses them (#100).

The module used to copy ``settings.WIREVIEW`` into constants at import, so
``override_settings(WIREVIEW=...)`` changed nothing and every test had to
monkeypatch the module -- which a project's own tests had no way to know.
"""

import pytest
from django.test import override_settings

from wireview import settings as wireview_settings
from wireview.core.signing import get_signer

pytestmark = pytest.mark.unit


def test_override_settings_reaches_a_read():
    before = wireview_settings.STATE_MAX_AGE
    with override_settings(WIREVIEW={"STATE_MAX_AGE": 60}):
        assert wireview_settings.STATE_MAX_AGE == 60
    assert wireview_settings.STATE_MAX_AGE == before


def test_override_settings_reaches_a_signature():
    """The signing key is read per call: a token from one key fails the other."""
    with override_settings(WIREVIEW={"SIGNING_KEY": "one"}):
        token = get_signer("wireview.test").sign("x")
    with override_settings(WIREVIEW={"SIGNING_KEY": "two"}), pytest.raises(Exception):
        get_signer("wireview.test").unsign(token)


def test_the_refresh_window_follows_the_max_age():
    with override_settings(WIREVIEW={"STATE_MAX_AGE": 100}):
        assert wireview_settings.STATE_REFRESH_AFTER == 50
    with override_settings(WIREVIEW={"STATE_MAX_AGE": 100, "STATE_REFRESH_AFTER": 10}):
        assert wireview_settings.STATE_REFRESH_AFTER == 10


def test_an_unknown_name_is_an_attribute_error():
    with pytest.raises(AttributeError):
        wireview_settings.USE_HMIN  # noqa: B018 -- removed in #100


def test_a_setting_cannot_be_pinned_on_the_module():
    """An assignment would hide every later override, and a monkeypatch's undo pins the old value.

    One test did exactly that through an alias (``state_module.settings``), and
    the next override of the same key read the pinned value.
    testproj.wireview_setting.set_wireview is the way to change a setting in a test.
    """
    with pytest.raises(AttributeError, match="override_settings"):
        wireview_settings.STATE_MAX_AGE = 1
    assert not {n for n in vars(wireview_settings) if n.isupper() and not n.startswith("_")} - {"DEFAULT", "REMOVED"}
