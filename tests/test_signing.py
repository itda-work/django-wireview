"""One key behind everything wireview signs, with a lifetime of its own (#83).

Two values are signed and handed to the client: the ``data-state`` envelope and
the chunked upload token. Both used to ride on Django's ``SECRET_KEY``, which
ties them to a key rotated for other reasons -- and a stateless chunk endpoint
makes the upload token the only proof a chunk has, so its key's lifetime is the
upload's lifetime.

``WIREVIEW["SIGNING_KEY"]`` separates the two, in both directions.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.signing import BadSignature
from django.template import Template
from django.test import override_settings

from wireview import Component
from wireview import settings as wireview_settings
from wireview.core.meta import WireviewMeta
from wireview.core.signing import get_signer, signing_key, signing_key_fallbacks
from wireview.core.state import sign_state, unsign_state
from wireview.features.uploads import sign_upload_token, validate_upload_token

pytestmark = pytest.mark.unit

_template: Template | None = None


class SignProbe(Component):
    count: int = 0

    @classmethod
    def _get_template(cls, template_name=None):
        global _template
        if _template is None:
            _template = Template("{% load wireview %}<div {% tag_header %}>{{ count }}</div>")
        return _template


def make_component() -> SignProbe:
    return SignProbe(user=AnonymousUser(), wire=WireviewMeta(params={}), id="probe-1", count=1)


def upload_token() -> str:
    return sign_upload_token(
        connection_id="conn-1",
        component_id="comp-1",
        config_name="images",
        ref="upload-1",
        max_bytes=10,
    )


# --- which key -----------------------------------------------------------------------------


def test_django_s_secret_key_is_the_default():
    """A project that sets nothing keeps exactly what it had before."""
    with override_settings(SECRET_KEY="django-key"):
        assert signing_key() == "django-key"


def test_a_dedicated_key_takes_over(monkeypatch):
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "wireview-key")

    with override_settings(SECRET_KEY="django-key"):
        assert signing_key() == "wireview-key"


def test_an_empty_key_is_treated_as_unset(monkeypatch):
    """``Signer(key="")`` silently means SECRET_KEY, so wireview says the same.

    ``wireview.W009`` is what keeps that from being a surprise.
    """
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "")

    with override_settings(SECRET_KEY="django-key"):
        assert signing_key() == "django-key"


def test_fallbacks_follow_django_unless_they_are_set(monkeypatch):
    with override_settings(SECRET_KEY="new", SECRET_KEY_FALLBACKS=["old"]):
        assert signing_key_fallbacks() == ["old"]

        monkeypatch.setattr(wireview_settings, "SIGNING_KEY_FALLBACKS", ["wireview-old"])
        assert signing_key_fallbacks() == ["wireview-old"]


def test_the_key_is_read_at_call_time(monkeypatch):
    """A rotation takes effect without restarting the worker."""
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "first")
    first = get_signer("wireview.test").sign("payload")

    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "second")
    assert get_signer("wireview.test").sign("payload") != first


# --- what the separation buys ----------------------------------------------------------------


def test_rotating_secret_key_no_longer_invalidates_open_pages(monkeypatch):
    """A page's state and its uploads survive a SECRET_KEY rotation."""
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "wireview-key")

    with override_settings(SECRET_KEY="before"):
        state = sign_state(make_component())
        token = upload_token()

    with override_settings(SECRET_KEY="after", SECRET_KEY_FALLBACKS=[]):
        assert unsign_state(state, "SignProbe")["count"] == 1
        assert validate_upload_token(token) is not None


def test_rotating_the_wireview_key_leaves_sessions_alone(monkeypatch):
    """And the other direction: Django's own signing is untouched.

    Nothing here uses ``SECRET_KEY``-signed values from Django, so the check is
    that wireview reaches for its own key and only its own.
    """
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "wireview-old")
    with override_settings(SECRET_KEY="django-key"):
        token = upload_token()

        monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "wireview-new")
        monkeypatch.setattr(wireview_settings, "SIGNING_KEY_FALLBACKS", None)
        assert validate_upload_token(token) is None, "a rotation without fallbacks invalidates, as it should"
        assert signing_key() == "wireview-new"


def test_a_wireview_key_rotation_survives_with_fallbacks(monkeypatch):
    """The condition on having a dedicated key: it carries its own fallbacks."""
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "wireview-old")
    with override_settings(SECRET_KEY="django-key"):
        state = sign_state(make_component())
        token = upload_token()

        monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "wireview-new")
        monkeypatch.setattr(wireview_settings, "SIGNING_KEY_FALLBACKS", ["wireview-old"])

        assert unsign_state(state, "SignProbe")["count"] == 1
        assert validate_upload_token(token) is not None


# --- the salts still keep the two apart -------------------------------------------------------


def test_an_upload_token_cannot_be_presented_as_state(monkeypatch):
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "one-key")

    with pytest.raises(BadSignature):
        unsign_state(upload_token(), "SignProbe")


def test_a_state_token_cannot_be_presented_as_an_upload_token(monkeypatch):
    monkeypatch.setattr(wireview_settings, "SIGNING_KEY", "one-key")

    assert validate_upload_token(sign_state(make_component())) is None
