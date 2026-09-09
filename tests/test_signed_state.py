"""The signed ``data-state`` envelope (#76).

``sign_state`` signs ``{"v": 1, "n": <class FQN>, "d": <state>}`` with a
``TimestampSigner``, so a token is bound to the class it was issued for and
stops being usable after ``STATE_MAX_AGE``. These tests cover the decode rules
and what the consumer does with a state it cannot use.
"""

import time
import typing as t

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.signing import BadSignature, SignatureExpired, Signer, TimestampSigner
from django.test import override_settings

from wireview import Component
from wireview import settings as wireview_settings
from wireview.consumer import WireviewConsumer
from wireview.core.meta import WireviewMeta
from wireview.core.state import (
    ENVELOPE_VERSION,
    STATE_SALT,
    LegacyState,
    StateMismatch,
    _JSONStringSerializer,
    sign_state,
    unsign_state,
)
from wireview.repository import ComponentRepository
from wireview.testing import mount

pytestmark = [pytest.mark.unit, pytest.mark.asyncio, pytest.mark.django_db]

TEMPLATES = {
    "ss/public.html": "{% load wireview %}<p {% tag_header %}>{{ this.note }}</p>",
    "ss/protected.html": "{% load wireview %}<p {% tag_header %}>secret {{ this.note }}</p>",
    "ss/root.html": (
        "{% load wireview %}<main {% tag_header %}>"
        "{% component 'SsChild' id='c1' %}{% component 'SsChild' id='c2' %}</main>"
    ),
    "ss/child.html": "{% load wireview %}<span {% tag_header %}>{{ this.note }}</span>",
}


class SsPublic(Component):
    _template_name = "ss/public.html"
    note: str = "public"


class SsProtected(Component):
    """Field-compatible with ``SsPublic``: the issue's substitution target."""

    _template_name = "ss/protected.html"
    note: str = "protected"


class SsChild(Component):
    _template_name = "ss/child.html"
    note: str = "default"


class SsRoot(Component):
    _template_name = "ss/root.html"


class FakeOutbound:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, t.Any]]] = []

    async def send_command(self, command: str, payload: dict[str, t.Any]) -> None:
        self.commands.append((command, payload))

    async def subscribe(self, topic: str) -> None: ...

    async def unsubscribe(self, topic: str) -> None: ...


@pytest.fixture(autouse=True)
def _templates():
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", TEMPLATES)]},
            }
        ]
    ):
        yield


def make_consumer() -> tuple[WireviewConsumer, FakeOutbound]:
    consumer = WireviewConsumer()
    consumer.repo = ComponentRepository(is_live=True, user=AnonymousUser())
    consumer.subscriptions = set()
    consumer.query_string = ""
    consumer.channel_name = "test-channel"
    outbound = FakeOutbound()
    consumer.outbound = outbound  # type: ignore[assignment]
    return consumer, outbound


def build(component_class: type[Component], **state) -> Component:
    return component_class(user=AnonymousUser(), wire=WireviewMeta(params={}), **state)


def signed(component_class: type[Component], **state) -> str:
    return sign_state(build(component_class, **state))


def frozen_clock(monkeypatch, at: float) -> None:
    """Move the clock both signers read (``django.core.signing`` and ``wireview.core.state``)."""
    monkeypatch.setattr(time, "time", lambda: at)


# --- round trip -------------------------------------------------------------


async def test_round_trip_accepts_every_name_that_resolves_to_the_class():
    token = signed(SsPublic, id="p1", note="hello")

    app_alias = f"{SsPublic.__module__.split('.')[0]}:SsPublic"
    for name in ("SsPublic", app_alias, SsPublic._fqn):
        state = unsign_state(token, name)
        assert state == {"id": "p1", "note": "hello"}


# --- class binding ----------------------------------------------------------


async def test_a_state_signed_for_one_class_cannot_be_presented_as_another():
    token = signed(SsPublic, id="p1", note="hello")

    with pytest.raises(StateMismatch) as excinfo:
        unsign_state(token, "SsProtected")
    assert excinfo.value.component_id == "p1"
    assert excinfo.value.signed_name.endswith("SsPublic")
    assert excinfo.value.asked_name.endswith("SsProtected")


async def test_a_class_substitution_join_mounts_nothing_and_asks_for_a_reload():
    consumer, outbound = make_consumer()

    await consumer.command_join("SsProtected", signed(SsPublic, id="p1", note="hello"))

    assert consumer.repo.components == {}
    assert outbound.commands == [("reload", {"id": "p1", "reason": "invalid"})]


async def test_an_unknown_class_name_is_a_mismatch_not_a_crash():
    token = signed(SsPublic, id="p1")
    with pytest.raises(StateMismatch):
        unsign_state(token, "NoSuchComponent")


# --- tampering --------------------------------------------------------------


async def test_a_tampered_token_fails_the_signature():
    token = signed(SsPublic, id="p1", note="hello")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

    with pytest.raises(BadSignature):
        unsign_state(tampered, "SsPublic")


async def test_a_tampered_join_mounts_nothing_and_asks_for_a_reload():
    consumer, outbound = make_consumer()
    token = signed(SsPublic, id="p1", note="hello")

    await consumer.command_join("SsPublic", token[:-1] + ("a" if token[-1] != "a" else "b"))

    assert consumer.repo.components == {}
    assert outbound.commands == [("reload", {"id": None, "reason": "invalid"})]


# --- expiry -----------------------------------------------------------------


async def test_a_token_expires_after_state_max_age(monkeypatch):
    issued_at = 1_000_000.0
    frozen_clock(monkeypatch, issued_at)
    token = signed(SsPublic, id="p1", note="hello")

    frozen_clock(monkeypatch, issued_at + wireview_settings.STATE_MAX_AGE - 10)
    assert unsign_state(token, "SsPublic")["note"] == "hello"

    frozen_clock(monkeypatch, issued_at + wireview_settings.STATE_MAX_AGE + 10)
    with pytest.raises(SignatureExpired):
        unsign_state(token, "SsPublic")


async def test_an_expired_join_mounts_nothing_and_asks_for_a_reload(monkeypatch):
    issued_at = 1_000_000.0
    frozen_clock(monkeypatch, issued_at)
    token = signed(SsPublic, id="p1", note="hello")
    frozen_clock(monkeypatch, issued_at + wireview_settings.STATE_MAX_AGE + 10)

    consumer, outbound = make_consumer()
    await consumer.command_join("SsPublic", token)

    assert consumer.repo.components == {}
    assert outbound.commands == [("reload", {"id": None, "reason": "expired"})]


# --- legacy formats ---------------------------------------------------------


def legacy_json_format(component: Component) -> str:
    return Signer().sign(component.model_dump_json(exclude=component._exclude_fields))


def legacy_compact_format(component: Component) -> str:
    return Signer().sign_object(
        component.model_dump_json(exclude=component._exclude_fields),
        serializer=_JSONStringSerializer,
        compress=True,
    )


@pytest.mark.parametrize("make_legacy", [legacy_json_format, legacy_compact_format])
async def test_legacy_formats_are_rejected_by_default(make_legacy):
    component = build(SsPublic, id="p1", note="hello")

    with pytest.raises(LegacyState):
        unsign_state(make_legacy(component), "SsPublic")


@pytest.mark.parametrize("make_legacy", [legacy_json_format, legacy_compact_format])
async def test_legacy_formats_are_accepted_with_the_flag(monkeypatch, make_legacy):
    # wireview.settings binds its names at import time, so the module attribute
    # is what the decoder reads; override_settings(WIREVIEW=...) would not reach it.
    monkeypatch.setattr(wireview_settings, "STATE_ACCEPT_LEGACY", True)
    component = build(SsPublic, id="p1", note="hello")

    assert unsign_state(make_legacy(component), "SsPublic")["note"] == "hello"


async def test_a_legacy_join_asks_for_a_reload_with_its_own_reason():
    consumer, outbound = make_consumer()

    await consumer.command_join("SsPublic", legacy_json_format(build(SsPublic, id="p1")))

    assert consumer.repo.components == {}
    assert outbound.commands == [("reload", {"id": None, "reason": "legacy"})]


async def test_a_legacy_join_mounts_when_the_flag_is_on(monkeypatch):
    monkeypatch.setattr(wireview_settings, "STATE_ACCEPT_LEGACY", True)
    consumer, outbound = make_consumer()

    await consumer.command_join("SsPublic", legacy_json_format(build(SsPublic, id="p1", note="hello")))

    assert consumer.repo.get("p1") is not None
    assert [command for command, _ in outbound.commands] == ["render"]


# --- token reuse ------------------------------------------------------------


async def test_an_unchanged_state_reuses_the_token():
    view = await mount(SsPublic, note="hello")
    component = view.component

    first = sign_state(component)
    assert sign_state(component) == first


async def test_a_changed_state_issues_a_new_token():
    view = await mount(SsPublic, note="hello")
    component = view.component

    first = sign_state(component)
    component.note = "changed"
    second = sign_state(component)

    assert second != first
    assert unsign_state(second, "SsPublic")["note"] == "changed"


async def test_an_unchanged_state_is_reissued_after_the_refresh_window(monkeypatch):
    issued_at = 1_000_000.0
    frozen_clock(monkeypatch, issued_at)
    view = await mount(SsPublic, note="hello")
    component = view.component
    first = sign_state(component)

    frozen_clock(monkeypatch, issued_at + wireview_settings.STATE_REFRESH_AFTER - 10)
    assert sign_state(component) == first

    frozen_clock(monkeypatch, issued_at + wireview_settings.STATE_REFRESH_AFTER + 10)
    refreshed = sign_state(component)
    assert refreshed != first
    assert unsign_state(refreshed, "SsPublic")["note"] == "hello"


# --- children ---------------------------------------------------------------


async def test_a_bad_child_envelope_is_dropped_and_the_rest_still_restores(caplog):
    consumer, _ = make_consumer()
    good = signed(SsChild, id="c1", note="kept")
    bad = signed(SsPublic, id="c2", note="stolen")  # signed for another class

    with caplog.at_level("WARNING", logger="wireview"):
        await consumer.command_join(
            "SsRoot",
            signed(SsRoot, id="root"),
            children={"c1": ("SsChild", good), "c2": ("SsChild", bad)},
        )

    assert consumer.repo.get("root") is not None
    assert consumer.repo.get("c1").note == "kept"
    assert consumer.repo.get("c2").note == "default"
    assert any("dropping child c2" in record.getMessage() for record in caplog.records)


# --- repository -------------------------------------------------------------


async def test_build_refuses_to_reuse_an_instance_of_another_class():
    repo = ComponentRepository(is_live=True, user=AnonymousUser())
    repo.register_component(build(SsPublic, id="p1", note="hello"))

    with pytest.raises(StateMismatch):
        repo.build("SsProtected", {"id": "p1", "note": "stolen"})


async def test_build_still_reuses_an_instance_of_the_same_class():
    repo = ComponentRepository(is_live=True, user=AnonymousUser())
    existing = repo.register_component(build(SsPublic, id="p1", note="hello"))

    assert repo.build("SsPublic", {"id": "p1", "note": "updated"}) is existing
    assert existing.note == "updated"


# --- envelope shape ---------------------------------------------------------


async def test_the_signed_payload_is_a_v1_envelope_naming_the_class():
    token = signed(SsPublic, id="p1", note="hello")

    envelope = TimestampSigner(salt=STATE_SALT).unsign_object(token, serializer=_JSONStringSerializer)

    assert envelope["v"] == ENVELOPE_VERSION
    assert envelope["n"] == SsPublic._fqn
    assert envelope["d"] == {"id": "p1", "note": "hello"}
