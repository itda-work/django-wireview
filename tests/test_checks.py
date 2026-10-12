"""Tests for the Django system checks (wireview.checks).

Each check must fire on the trap it names and stay silent otherwise: a check
that cries wolf on a healthy project gets ignored, and then it is dead weight.
"""

import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from django.template import Context, Template, TemplateSyntaxError
from django.template.loader import get_template
from django.test import override_settings
from testproj.wireview_setting import set_wireview

from wireview import AutoBroadcast, Component
from wireview import checks as wireview_checks
from wireview import settings as wireview_settings
from wireview.checks import (
    check_async_handlers,
    check_async_lifecycle,
    check_auto_broadcast_credentials,
    check_auto_broadcast_senders,
    check_channel_layer,
    check_channel_layer_configured,
    check_client_bundle,
    check_component_name_collisions,
    check_cookie_paths,
    check_django_templates,
    check_live_sessions,
    check_presence_producer_ids,
    check_reconnect_settings,
    check_runserver_is_asgi,
    check_settings_keys,
    check_shadowed_framework_names,
    check_signing_key,
    check_upload_temp_dir,
    check_websocket_session,
    iter_component_classes,
    iter_exposed_handlers,
    reconnect_value,
)
from wireview.core import live_session as live_session_module

pytestmark = pytest.mark.unit

#: A permission-based test proves nothing when the process bypasses permissions.
needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions",
)


def make_component(
    class_name: str, module: str = "probeapp.live", meta: dict | None = None, base: type = Component, **namespace
) -> type[Component]:
    """Build a component class off the registry's beaten path.

    Registration warns on name collisions, which two of these tests want on
    purpose, so the warning is silenced here rather than in each test.
    """
    meta = meta or {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return type(
            class_name,
            (base,),
            {"__module__": module, "Meta": type("Meta", (), {"template_name": "test.html", **meta}), **namespace},
        )


@pytest.fixture
def only(monkeypatch):
    """Restrict the checks to the classes a test just built."""

    def _only(*classes):
        monkeypatch.setattr(wireview_checks, "iter_component_classes", lambda: iter(classes))

    return _only


class TestExposedHandlers:
    """iter_exposed_handlers must agree with the dispatcher, not approximate it."""

    def test_lists_user_handlers_only(self):
        async def increment(self):
            pass

        cls = make_component("ProbeExposed", increment=increment)
        names = [name for name, _ in iter_exposed_handlers(cls)]

        assert "increment" in names
        # Framework and Pydantic surface stays out (see #63)
        assert "joined" not in names
        assert "model_dump" not in names
        assert "model_post_init" not in names


class TestAsyncHandlerCheck:
    """W001: a sync handler is reachable from the client and always fails."""

    def test_async_handler_is_silent(self, only):
        async def increment(self):
            pass

        only(make_component("ProbeAsyncOk", increment=increment))
        assert check_async_handlers(None) == []

    def test_sync_handler_flagged(self, only):
        def increment(self):
            pass

        only(make_component("ProbeSyncHandler", increment=increment))
        messages = check_async_handlers(None)

        assert [m.id for m in messages] == ["wireview.W001"]
        assert "increment" in messages[0].msg
        assert "_increment" in messages[0].hint  # the rename escape hatch

    def test_private_helper_is_not_a_handler(self, only):
        def _helper(self):
            pass

        only(make_component("ProbePrivateHelper", _helper=_helper))
        assert check_async_handlers(None) == []


#: Every callback wireview awaits, written out here rather than imported: the check
#: once covered three of them and a test reading its list could not notice.
AWAITED_CALLBACKS = (
    "joined",
    "leaving",
    "update",
    "destroy",
    "mutation",
    "notification",
    "params_changed",
    "handle_async",
    "handle_hook_event",
    "on_upload_complete",
)


class TestAsyncLifecycleCheck:
    """W002: wireview awaits lifecycle callbacks, so a sync override never runs."""

    def test_async_override_is_silent(self, only):
        async def joined(self):
            pass

        only(make_component("ProbeLifecycleOk", joined=joined))
        assert check_async_lifecycle(None) == []

    def test_sync_override_flagged(self, only):
        def joined(self):
            pass

        only(make_component("ProbeLifecycleSync", joined=joined))
        messages = check_async_lifecycle(None)

        assert [m.id for m in messages] == ["wireview.W002"]
        assert "joined" in messages[0].msg

    @pytest.mark.parametrize("name", AWAITED_CALLBACKS)
    def test_every_awaited_callback_is_checked(self, only, name):
        """It checked three names; a sync ``leaving`` or ``notification`` went unreported."""

        def callback(self, *args, **kwargs):
            pass

        only(make_component(f"ProbeLifecycleSync_{name}", **{name: callback}))

        assert [m.id for m in check_async_lifecycle(None)] == ["wireview.W002"]

    @pytest.mark.parametrize("name", AWAITED_CALLBACKS)
    def test_every_checked_name_is_a_framework_coroutine(self, name):
        from wireview import Component, LiveComponent

        owner = LiveComponent if name == "update" else Component
        assert inspect.iscoroutinefunction(getattr(owner, name))

    def test_inherited_lifecycle_is_silent(self, only):
        """Not overriding it must not report the framework's own definition."""
        only(make_component("ProbeLifecycleInherited"))
        assert check_async_lifecycle(None) == []


class TestShadowedFrameworkNameCheck:
    """W018: a user method under a framework name is no handler, and nothing said so."""

    @pytest.mark.parametrize("name", ["validate", "copy", "json", "dict", "schema", "skip_render", "send_render"])
    def test_a_handler_under_a_framework_name_is_flagged(self, only, name):
        async def handler(self, **form):
            pass

        only(make_component(f"ProbeShadow_{name}", **{name: handler}))
        messages = check_shadowed_framework_names(None)

        assert [m.id for m in messages] == ["wireview.W018"]
        assert f".{name}' is not an event handler" in messages[0].msg

    def test_the_named_case_is_the_one_the_dispatcher_refuses(self, only):
        """The check states the dispatcher's rule; it is not a guess about it."""
        from wireview.core.handlers import is_client_callable

        async def validate(self, **form):
            pass

        cls = make_component("ProbeShadowRule", validate=validate)
        assert not is_client_callable(cls, "validate")

    @pytest.mark.parametrize("name", sorted({*wireview_checks.LIFECYCLE_METHODS, *wireview_checks.OVERRIDABLE_METHODS}))
    def test_an_intended_override_is_silent(self, only, name):
        async def callback(self, *args, **kwargs):
            pass

        from wireview import LiveComponent

        value = classmethod(callback) if name in {"new", "update_many"} else callback
        # LiveComponent's names are a superset of Component's: update and update_many are only its
        only(make_component(f"ProbeShadowOk_{name}", base=LiveComponent, **{name: value}))
        assert check_shadowed_framework_names(None) == []

    @staticmethod
    def _taught_overrides() -> list[str]:
        """The methods the API reference tells a component to override, from its table."""
        text = (Path(__file__).resolve().parent.parent / "docs" / "features" / "component-api.md").read_text(
            encoding="utf-8"
        )
        section = text.split("## 오버라이드하는 것", 1)[1].split("\n## ", 1)[0]
        return re.findall(r"^\| `(?:LiveComponent\.)?(\w+)\(", section, re.MULTILINE)

    def test_the_api_reference_teaches_overrides(self):
        """The table the next test reads is still there, and still has its rows."""
        assert {"joined", "get_subscriptions", "new", "update", "update_many"} <= set(self._taught_overrides())

    def test_every_override_the_api_reference_teaches_is_silent(self, only):
        """The intended list is held to the documents, not to itself: dropping a name from it fails here."""
        names = self._taught_overrides()

        async def callback(self, *args, **kwargs):
            pass

        from wireview import LiveComponent

        attrs = {name: classmethod(callback) if name in {"new", "update_many"} else callback for name in names}
        only(make_component("ProbeShadowTaught", base=LiveComponent, **attrs))
        assert check_shadowed_framework_names(None) == []

    @pytest.mark.parametrize("name", ["model_post_init", "model_dump", "model_json_schema"])
    def test_a_pydantic_model_method_override_is_silent(self, only, name):
        """Pydantic reserves ``model_``: such a method customizes the model; "rename the handler" was wrong."""

        def method(self, *args, **kwargs):
            pass

        value = classmethod(method) if name == "model_json_schema" else method
        only(make_component(f"ProbeShadowModel_{name}", **{name: value}))
        assert check_shadowed_framework_names(None) == []

    @pytest.mark.parametrize("name", wireview_checks.OVERRIDABLE_METHODS)
    def test_every_intended_override_is_a_framework_name(self, name):
        from wireview import LiveComponent

        assert hasattr(LiveComponent, name)

    def test_handlers_helpers_and_fields_are_silent(self, only):
        async def save(self):
            pass

        def _validate(self):
            pass

        only(make_component("ProbeShadowQuiet", save=save, _validate=_validate, __annotations__={"title": str}))
        assert check_shadowed_framework_names(None) == []

    def test_a_shared_user_base_is_reported_once(self, only):
        async def validate(self, **form):
            pass

        base = make_component("ProbeShadowBase", validate=validate)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            first = type("ProbeShadowA", (base,), {"__module__": "probeapp.live"})
            second = type("ProbeShadowB", (base,), {"__module__": "probeapp.live"})
        only(first, second)

        messages = check_shadowed_framework_names(None)
        assert [m.obj for m in messages] == [base]


class TestNameCollisionCheck:
    """W003: two classes under one simple name; only one resolves."""

    def test_distinct_names_silent(self, only):
        only(
            make_component("ProbeUniqueA", module="appa.live"),
            make_component("ProbeUniqueB", module="appb.live"),
        )
        assert check_component_name_collisions(None) == []

    def test_collision_flagged(self, only):
        only(
            make_component("ProbeDuplicate", module="appa.live"),
            make_component("ProbeDuplicate", module="appb.live"),
        )
        messages = check_component_name_collisions(None)

        assert [m.id for m in messages] == ["wireview.W003"]
        assert "appa.live.ProbeDuplicate" in messages[0].msg
        assert "appb.live.ProbeDuplicate" in messages[0].msg


class TestClientBundleCheck:
    """W004: without the bundle the page is silently inert."""

    def test_bundle_present_is_silent(self):
        assert check_client_bundle(None) == []

    def test_missing_bundle_flagged(self, monkeypatch):
        from django.contrib.staticfiles import finders

        monkeypatch.setattr(finders, "find", lambda *args, **kwargs: None)
        messages = check_client_bundle(None)

        assert [m.id for m in messages] == ["wireview.W004"]
        assert "make build-js" in messages[0].hint
        # The form that needs no make, which most Windows machines lack (#217)
        assert "uv run npm run build" in messages[0].hint


class TestSettingsKeysCheck:
    """W014: a WIREVIEW key wireview never reads (#100)."""

    def test_known_keys_are_silent(self, monkeypatch):
        set_wireview(monkeypatch, STATE_MAX_AGE=60)
        assert check_settings_keys(None) == []

    def test_a_typo_names_the_key_it_resembles(self, monkeypatch):
        set_wireview(monkeypatch, STATE_MAXAGE=60)
        (message,) = check_settings_keys(None)

        assert message.id == "wireview.W014"
        assert "STATE_MAXAGE" in message.msg and "STATE_MAX_AGE" in message.hint

    @pytest.mark.parametrize("key", ["USE_HMIN", "USE_HTML_DIFF", "STATE_ACCEPT_LEGACY"])
    def test_a_removed_key_says_what_replaced_it(self, monkeypatch, key):
        set_wireview(monkeypatch, **{key: True})
        (message,) = check_settings_keys(None)

        assert message.hint.startswith("Removed in #")


class TestAutoBroadcastSendersCheck:
    """W015: a broadcast flag is on and senders is empty, so nothing is broadcast."""

    @pytest.mark.parametrize("flag", ["model", "model_pk", "related", "m2m"])
    def test_a_flag_without_senders_is_flagged(self, monkeypatch, flag):
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(**{flag: True}))
        (message,) = check_auto_broadcast_senders(None)

        assert message.id == "wireview.W015"
        assert flag in message.msg and "senders" in message.hint

    def test_named_senders_are_silent(self, monkeypatch):
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("todo", "Item")}))
        assert check_auto_broadcast_senders(None) == []

    def test_everything_off_is_silent(self, monkeypatch):
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast())
        assert check_auto_broadcast_senders(None) == []

    def test_the_test_project_is_silent(self):
        assert check_auto_broadcast_senders(None) == []

    def test_the_hints_mapping_starts(self, monkeypatch):
        """Copying the example out of the hint must not fail at startup."""
        import ast

        from wireview import auto_broadcast

        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True))
        (message,) = check_auto_broadcast_senders(None)
        (example,) = re.findall(r"senders=(\{[^{}]*: [^{}]*\})", message.hint)

        assert auto_broadcast.resolve_senders(AutoBroadcast(model=True, senders=ast.literal_eval(example)))


class TestAutoBroadcastCredentialsCheck:
    """W017: a password hash or session key goes onto the channel layer (#144)."""

    @pytest.mark.parametrize(
        ("senders", "what"),
        [
            ({("auth", "User")}, "password hash"),
            ({("auth", "User"): "__all__"}, "password hash"),
            ({("auth", "User"): ("username", "password")}, "password hash"),
            ({("sessions", "Session")}, "session key"),
            ({("sessions", "Session"): ()}, "session key"),
            ({("sessions", "Session"): ("expire_date",)}, "session key"),
        ],
        ids=["user-set", "user-all", "user-password-listed", "session", "session-pk-alone", "session-fields"],
    )
    def test_a_credential_the_payload_carries_is_flagged(self, monkeypatch, senders, what):
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders=senders))
        (message,) = check_auto_broadcast_credentials(None)

        assert message.id == "wireview.W017"
        assert what in message.msg

    def test_the_hint_for_a_session_model_takes_it_out_of_senders(self, monkeypatch):
        """Its pk is the session key, which every payload and a model_pk channel name carry."""
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("sessions", "Session"): ()}))
        (message,) = check_auto_broadcast_credentials(None)

        assert "primary key" in message.msg
        assert "Remove ('sessions', 'Session') from senders" in message.hint

    def test_the_hint_names_the_user_models_username_field(self, monkeypatch):
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("auth", "User")}))
        (message,) = check_auto_broadcast_credentials(None)

        assert "{('auth', 'User'): ('username',)}" in message.hint

    def test_a_custom_user_model_is_flagged(self, monkeypatch):
        from django.contrib.auth.base_user import AbstractBaseUser
        from django.db import models
        from django.test.utils import isolate_apps

        with isolate_apps("testproj.bookmarks") as isolated:

            class Account(AbstractBaseUser):
                email = models.EmailField(unique=True)
                USERNAME_FIELD = "email"

                class Meta:
                    app_label = "bookmarks"

            monkeypatch.setattr("wireview.auto_broadcast.apps", isolated)
            set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("bookmarks", "Account")}))
            (message,) = check_auto_broadcast_credentials(None)

        assert "bookmarks.account" in message.msg
        assert "{('bookmarks', 'Account'): ('email',)}" in message.hint

    def test_a_user_model_without_a_username_field_is_flagged_with_the_pk_alone(self, monkeypatch):
        """Only ``AUTH_USER_MODEL`` must set USERNAME_FIELD; another AbstractBaseUser may not."""
        from django.contrib.auth.base_user import AbstractBaseUser
        from django.db import models
        from django.test.utils import isolate_apps

        with isolate_apps("testproj.bookmarks") as isolated:

            class ServiceKey(AbstractBaseUser):
                name = models.CharField(max_length=10)

                class Meta:
                    app_label = "bookmarks"

            monkeypatch.setattr("wireview.auto_broadcast.apps", isolated)
            set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("bookmarks", "ServiceKey")}))
            (message,) = check_auto_broadcast_credentials(None)

        assert "{('bookmarks', 'ServiceKey'): ()}" in message.hint

    def test_a_child_of_the_user_model_is_silent_and_its_hint_would_start(self, monkeypatch):
        """A multi-table child sends its own columns; the password hash stays in the parent's table."""
        from django.contrib.auth.models import User
        from django.db import models
        from django.test.utils import isolate_apps

        from wireview import auto_broadcast

        with isolate_apps("testproj.bookmarks") as isolated:

            class Customer(User):
                tier = models.CharField(max_length=10, default="")

                class Meta:
                    app_label = "bookmarks"

            monkeypatch.setattr("wireview.auto_broadcast.apps", isolated)
            set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("bookmarks", "Customer")}))
            assert check_auto_broadcast_credentials(None) == []
            # The fields a hint may name are the ones the payload carries.
            assert auto_broadcast.resolve_senders(AutoBroadcast(senders={("bookmarks", "Customer"): ("tier",)}))

    def test_a_proxy_of_the_user_model_is_flagged_and_its_hint_starts(self, monkeypatch):
        """A proxy has no local fields of its own; the payload carries its concrete model's."""
        from django.contrib.auth.models import User
        from django.test.utils import isolate_apps

        from wireview import auto_broadcast

        with isolate_apps("testproj.bookmarks") as isolated:

            class Staff(User):
                class Meta:
                    app_label = "bookmarks"
                    proxy = True

            monkeypatch.setattr("wireview.auto_broadcast.apps", isolated)
            set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("bookmarks", "Staff")}))
            (message,) = check_auto_broadcast_credentials(None)
            assert "password hash of bookmarks.staff" in message.msg
            assert "{('bookmarks', 'Staff'): ('username',)}" in message.hint

            fixed = AutoBroadcast(model=True, senders={("bookmarks", "Staff"): ("username",)})
            assert auto_broadcast.resolve_senders(fixed) == {Staff: ("username",)}
            set_wireview(monkeypatch, AUTO_BROADCAST=fixed)
            assert check_auto_broadcast_credentials(None) == []

    def test_a_username_field_the_payload_cannot_carry_is_not_offered(self, monkeypatch):
        """Here USERNAME_FIELD lives in a multi-table parent; naming it would fail at startup."""
        from django.contrib.auth.base_user import AbstractBaseUser
        from django.db import models
        from django.test.utils import isolate_apps

        with isolate_apps("testproj.bookmarks") as isolated:

            class Person(models.Model):
                email = models.EmailField(unique=True)

                class Meta:
                    app_label = "bookmarks"

            class Login(Person, AbstractBaseUser):
                USERNAME_FIELD = "email"

                class Meta:
                    app_label = "bookmarks"

            monkeypatch.setattr("wireview.auto_broadcast.apps", isolated)
            set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("bookmarks", "Login")}))
            (message,) = check_auto_broadcast_credentials(None)

        assert "{('bookmarks', 'Login'): ()}" in message.hint

    def test_nothing_is_flagged_while_every_flag_is_off(self, monkeypatch):
        """connect() attaches no receiver then, so no payload goes anywhere."""
        senders = {("auth", "User"), ("sessions", "Session")}
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(senders=senders))
        assert check_auto_broadcast_credentials(None) == []

    def test_senders_startup_refuses_are_left_to_startup(self, monkeypatch):
        """connect() raises for them in ready(); the check must not raise a second time."""
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("auth", "User"): ("colour",)}))
        assert check_auto_broadcast_credentials(None) == []

    def test_listed_fields_are_silent(self, monkeypatch):
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("auth", "User"): ("username",)}))
        assert check_auto_broadcast_credentials(None) == []

    def test_an_ordinary_model_is_silent(self, monkeypatch):
        set_wireview(monkeypatch, AUTO_BROADCAST=AutoBroadcast(model=True, senders={("rating", "Product")}))
        assert check_auto_broadcast_credentials(None) == []

    def test_the_test_project_is_silent(self):
        assert check_auto_broadcast_credentials(None) == []


#: The client module W016 must agree with.
RECONNECT_MJS = Path(__file__).resolve().parent.parent / "wireview" / "static" / "wireview" / "reconnect.mjs"

#: Each setting's name in what ``readReconnectSettings`` returns.
RECONNECT_CLIENT_NAMES = {
    "RECONNECT_MIN_DELAY_MS": "minDelay",
    "RECONNECT_JITTER_MS": "jitter",
    "RECONNECT_MAX_DELAY_MS": "maxDelay",
    "RECONNECT_GROW_FACTOR": "growFactor",
}

#: Values an operator might write. None of the numbers equals a default, so a
#: client that returns the default has fallen back.
RECONNECT_PROBES = [
    0,
    1,
    2,
    2.5,
    750,
    30000,
    1e20,
    0.9,
    -1,
    -0.5,
    float("nan"),
    float("inf"),
    float("-inf"),
    10**400,
    True,
    False,
    None,
    "",
    "abc",
    "30000",
    "1,000",
    [30000],
]


def client_reads(cases: list[tuple[str, object]]) -> list[float]:
    """What ``readReconnectSettings`` makes of each setting, through the real header."""
    documents = []
    for key, value in cases:
        with override_settings(WIREVIEW={key: value}):
            html = Template("{% load wireview %}{% wireview_header %}").render(Context({}))
        meta = re.search(r'<meta name="wireview-reconnect"(.*?)/>', html, re.S)
        assert meta, html
        attributes = dict(re.findall(r'(data-[\w-]+)="([^"]*)"', meta.group(1)))
        documents.append({"attributes": attributes, "name": RECONNECT_CLIENT_NAMES[key]})

    script = f"""
import {{ readReconnectSettings }} from {json.dumps(RECONNECT_MJS.as_uri())};
let input = "";
for await (const chunk of process.stdin) input += chunk;
const out = JSON.parse(input).map(({{ attributes, name }}) => {{
  const doc = {{ querySelector: () => ({{ getAttribute: (a) => (a in attributes ? attributes[a] : null) }}) }};
  return readReconnectSettings(doc)[name];
}});
process.stdout.write(JSON.stringify(out));
"""
    node = shutil.which("node")
    # Not a skip, as in test_diff_roundtrip.py: node already builds the client bundle.
    assert node, "node is required: the check is compared against reconnect.mjs"
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps(documents),
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


class TestReconnectSettingsCheck:
    """W016: a RECONNECT_* value the client falls back from, or a first wait cut at the cap (#134)."""

    def test_the_defaults_are_silent(self):
        assert check_reconnect_settings(None) == []

    def test_the_deployment_example_is_silent(self, monkeypatch):
        """docs/DEPLOYMENT.md's slower backoff for a rolling deploy."""
        set_wireview(
            monkeypatch,
            RECONNECT_MIN_DELAY_MS=1000,
            RECONNECT_JITTER_MS=10000,
            RECONNECT_GROW_FACTOR=1.5,
            RECONNECT_MAX_DELAY_MS=30000,
        )
        assert check_reconnect_settings(None) == []

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("RECONNECT_MIN_DELAY_MS", -1),
            ("RECONNECT_JITTER_MS", "4000"),
            ("RECONNECT_MAX_DELAY_MS", None),
            ("RECONNECT_GROW_FACTOR", 0.5),
            ("RECONNECT_GROW_FACTOR", True),
        ],
    )
    def test_a_value_the_client_falls_back_from_is_named(self, monkeypatch, key, value):
        set_wireview(monkeypatch, **{key: value})
        (message,) = check_reconnect_settings(None)

        assert message.id == "wireview.W016"
        assert key in message.msg and repr(wireview_settings.DEFAULT[key]) in message.msg

    def test_a_first_wait_above_the_cap_is_flagged(self, monkeypatch):
        set_wireview(monkeypatch, RECONNECT_JITTER_MS=20000)
        (message,) = check_reconnect_settings(None)

        assert message.id == "wireview.W016"
        assert "1000 to 21000 ms" in message.msg and "together" in message.msg

    def test_a_least_wait_above_the_cap_says_it_has_no_effect(self, monkeypatch):
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=20000, RECONNECT_JITTER_MS=0)
        (message,) = check_reconnect_settings(None)

        assert "every reconnect waits 10000 ms" in message.msg

    def test_a_first_wait_that_reaches_the_cap_exactly_is_silent(self, monkeypatch):
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=1000, RECONNECT_JITTER_MS=0, RECONNECT_MAX_DELAY_MS=1000)
        assert check_reconnect_settings(None) == []
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=0, RECONNECT_JITTER_MS=5000, RECONNECT_MAX_DELAY_MS=5000)
        assert check_reconnect_settings(None) == []

    @pytest.mark.parametrize(
        ("least", "jitter", "cap"),
        [(0, 0, 0), (0, 0, 5000), (1000, 4000, 0)],
    )
    def test_a_wait_of_nothing_is_flagged(self, monkeypatch, least, jitter, cap):
        """No first wait, or no cap: every retry is at once, a tight loop while the server is down."""
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=least, RECONNECT_JITTER_MS=jitter, RECONNECT_MAX_DELAY_MS=cap)
        (message,) = check_reconnect_settings(None)

        assert message.id == "wireview.W016"
        assert "tight loop" in message.msg

    @pytest.mark.parametrize(
        ("settings", "flagged"),
        [
            ({"RECONNECT_MAX_DELAY_MS": 2**31 - 1}, False),
            ({"RECONNECT_MAX_DELAY_MS": 2**31}, True),
            # With a factor of 1 the wait never grows toward the cap.
            ({"RECONNECT_MAX_DELAY_MS": 2**40, "RECONNECT_GROW_FACTOR": 1}, False),
            (
                {
                    "RECONNECT_MIN_DELAY_MS": 2**31 - 1,
                    "RECONNECT_JITTER_MS": 1,
                    "RECONNECT_MAX_DELAY_MS": 2**40,
                    "RECONNECT_GROW_FACTOR": 1,
                },
                True,
            ),
        ],
    )
    def test_a_wait_past_the_browser_timer_is_flagged(self, monkeypatch, settings, flagged):
        """A browser holds a timer's delay in 32 bits: 2**31 ms fires at once."""
        set_wireview(monkeypatch, **settings)
        messages = check_reconnect_settings(None)

        assert [m.id for m in messages] == (["wireview.W016"] if flagged else [])
        if flagged:
            assert "2147483647 ms" in messages[0].msg

    def test_a_huge_value_is_not_printed_whole(self, monkeypatch):
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=10**400)
        (message,) = check_reconnect_settings(None)

        assert len(message.msg) < 300, message.msg

    def test_the_cap_is_judged_with_what_the_client_uses(self, monkeypatch):
        """A max the client falls back from is compared as the default it uses instead."""
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=20000, RECONNECT_MAX_DELAY_MS=-1)
        messages = check_reconnect_settings(None)

        assert [m.id for m in messages] == ["wireview.W016", "wireview.W016"]
        assert "RECONNECT_MAX_DELAY_MS" in messages[0].msg and "(10000 ms)" in messages[1].msg

    def test_a_string_is_named_without_a_claim_about_the_client(self, monkeypatch):
        """``"30000"`` is read by the client as 30000, so nothing falls back and no wait is cut.

        The check still names it -- ``"1,000"`` looks as right and is dropped --
        but it cannot say what the client makes of a string, so it neither claims
        a default nor judges the waits with one.
        """
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=20000, RECONNECT_MAX_DELAY_MS="30000")
        (message,) = check_reconnect_settings(None)

        assert message.id == "wireview.W016"
        assert "RECONNECT_MAX_DELAY_MS" in message.msg and "not an int or a float" in message.msg
        assert "default" not in message.msg

    @pytest.mark.parametrize("value", [Decimal("1000"), Fraction(1000)])
    def test_another_numeric_type_is_not_called_not_a_number(self, monkeypatch, value):
        """A Decimal is a number; what the check cannot vouch for is a type other than int and float."""
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=value)
        (message,) = check_reconnect_settings(None)

        assert f"is a {type(value).__name__}, not an int or a float" in message.msg

    @pytest.mark.parametrize(
        ("settings", "shown"),
        [
            (
                {"RECONNECT_MIN_DELAY_MS": 0, "RECONNECT_JITTER_MS": 0, "RECONNECT_MAX_DELAY_MS": "30000"},
                "(RECONNECT_MIN_DELAY_MS + RECONNECT_JITTER_MS = 0)",
            ),
            (
                {"RECONNECT_MIN_DELAY_MS": 0, "RECONNECT_JITTER_MS": 0, "RECONNECT_GROW_FACTOR": "2"},
                "(RECONNECT_MIN_DELAY_MS + RECONNECT_JITTER_MS = 0, RECONNECT_MAX_DELAY_MS = 10000)",
            ),
            (
                {"RECONNECT_JITTER_MS": "4000", "RECONNECT_MAX_DELAY_MS": 0},
                "(RECONNECT_MAX_DELAY_MS = 0)",
            ),
        ],
    )
    def test_a_wait_of_nothing_is_flagged_beside_a_string(self, monkeypatch, settings, shown):
        """A string hides what the client waits, except where one side already makes every wait 0."""
        set_wireview(monkeypatch, **settings)
        messages = check_reconnect_settings(None)

        assert [m.id for m in messages] == ["wireview.W016", "wireview.W016"]
        assert "not an int or a float" in messages[0].msg
        assert "tight loop" in messages[1].msg and shown in messages[1].msg

    def test_a_wait_the_string_could_make_nonzero_is_not_flagged(self, monkeypatch):
        """``RECONNECT_JITTER_MS="4000"`` may be read as 4000, so a 0 minimum says nothing yet."""
        set_wireview(monkeypatch, RECONNECT_MIN_DELAY_MS=0, RECONNECT_JITTER_MS="4000")
        (message,) = check_reconnect_settings(None)

        assert "not an int or a float" in message.msg

    def test_the_check_and_the_client_agree(self):
        """For numbers, None and bools the check and the client agree both ways.

        The check passes a value exactly when the client uses it, as the same
        number. Anything else (a string, a list) the check names without
        judging it like the client: a string the client parses (``"30000"``)
        is still named, because ``"1,000"`` looks as right and is not, but the
        warning must not say the client falls back from it.
        """
        cases = [(key, value) for key in RECONNECT_CLIENT_NAMES for value in RECONNECT_PROBES]
        reads = client_reads(cases)

        for (key, value), read in zip(cases, reads, strict=True):
            client_fell_back = read == wireview_settings.DEFAULT[key]
            if value is None or isinstance(value, (int, float)):
                number = reconnect_value(value, wireview_checks.RECONNECT_FLOORS[key])
                if number is not None:
                    assert read == number, (key, value, read)
                assert (number is None) == client_fell_back, (key, value, read)
                continue
            with override_settings(WIREVIEW={key: value}):
                messages = check_reconnect_settings(None)
            assert [m.id for m in messages] == ["wireview.W016"], (key, value, messages)
            if not client_fell_back:
                assert "default" not in messages[0].msg, (key, value, read)


class TestChannelLayerCheck:
    """W006: deploy-only, because in-memory is right for single-process dev."""

    @override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
    def test_in_memory_flagged(self):
        messages = check_channel_layer(None)

        assert [m.id for m in messages] == ["wireview.W006"]
        assert "multi-process" in messages[0].hint

    @override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}})
    def test_broker_backed_layer_silent(self):
        assert check_channel_layer(None) == []


class TestChannelLayerConfiguredCheck:
    """W012: Channels has no default layer, and without one no connection survives (#87)."""

    @override_settings()
    def test_unset_flagged(self):
        from django.conf import settings

        del settings.CHANNEL_LAYERS
        messages = check_channel_layer_configured(None)

        assert [m.id for m in messages] == ["wireview.W012"]
        assert "InMemoryChannelLayer" in messages[0].hint

    @override_settings(CHANNEL_LAYERS={})
    def test_empty_flagged(self):
        assert [m.id for m in check_channel_layer_configured(None)] == ["wireview.W012"]

    @override_settings(CHANNEL_LAYERS={"other": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
    def test_only_a_non_default_alias_flagged(self):
        """The consumer asks for ``default``; another alias does not answer it."""
        assert [m.id for m in check_channel_layer_configured(None)] == ["wireview.W012"]

    @override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
    def test_in_memory_silent(self):
        """In-memory is the right answer for one process. That case is W006's, on deploy."""
        assert check_channel_layer_configured(None) == []

    def test_runs_without_deploy(self):
        """Unlike W006 this is wrong on a single process too, so plain ``check`` reports it."""
        from django.core.checks.registry import registry

        assert check_channel_layer_configured in registry.get_checks(include_deployment_checks=False)
        assert check_channel_layer not in registry.get_checks(include_deployment_checks=False)


class TestWebsocketSessionCheck:
    """W020: no SessionMiddleware above wireview's consumer, so a handshake says nobody (#209)."""

    @pytest.mark.parametrize("app", ["BARE", "NESTED_BARE"])
    def test_a_stack_without_a_session_is_flagged(self, app):
        with override_settings(ASGI_APPLICATION=f"testproj.asgi_variants.{app}"):
            messages = check_websocket_session(None)
        assert [m.id for m in messages] == ["wireview.W020"]
        assert "AuthMiddlewareStack" in messages[0].hint

    def test_a_base_middleware_of_the_projects_own_is_walked_through(self):
        """It may fill scope["session"] itself; the check cannot know, and the hint says to silence it."""
        with override_settings(ASGI_APPLICATION="testproj.asgi_variants.OWN_BASE_MIDDLEWARE"):
            messages = check_websocket_session(None)
        assert [m.id for m in messages] == ["wireview.W020"]
        assert "SILENCED_SYSTEM_CHECKS" in messages[0].hint

    @pytest.mark.parametrize("app", ["AUTH", "SESSION_ONLY", "OPAQUE", "HTTP_ONLY", "NOT_THERE"])
    def test_otherwise_silent(self, app):
        """A session on the way, a layer of the project's own, no WebSocket, an unimportable path."""
        with override_settings(ASGI_APPLICATION=f"testproj.asgi_variants.{app}"):
            assert check_websocket_session(None) == []

    def test_the_test_projects_own_stack_is_silent(self):
        assert check_websocket_session(None) == []

    @override_settings()
    def test_no_asgi_application_is_silent(self):
        from django.conf import settings

        del settings.ASGI_APPLICATION
        assert check_websocket_session(None) == []

    def test_runs_without_deploy(self):
        from django.core.checks.registry import registry

        assert check_websocket_session in registry.get_checks(include_deployment_checks=False)


class TestCookiePathsCheck:
    """W021: a session or CSRF cookie the browser does not send to the socket (#209)."""

    @pytest.mark.parametrize("path", ["/", "/__wireview__"])
    def test_a_path_that_covers_the_socket_is_silent(self, path):
        with override_settings(SESSION_COOKIE_PATH=path, CSRF_COOKIE_PATH=path):
            assert check_cookie_paths(None) == []

    @pytest.mark.parametrize("path", ["/app", "/app/", "/__wireview", "/__wireview__/"])
    def test_a_path_that_does_not_is_flagged(self, path):
        with override_settings(SESSION_COOKIE_PATH=path, CSRF_COOKIE_PATH=path):
            messages = check_cookie_paths(None)
        assert [m.id for m in messages] == ["wireview.W021"] * 2
        session, csrf = messages
        assert "SESSION_COOKIE_PATH" in session.msg and "not mounted" in session.hint and "1.6" in session.hint
        assert "CSRF_COOKIE_PATH" in csrf.msg and "one more socket" in csrf.hint
        for message in messages:
            assert "every app on this host" in message.hint, "what '/' costs"
            assert "sub-path is not supported" in message.hint and "COMPATIBILITY" in message.hint

    def test_the_csrf_cookie_is_not_judged_when_the_secret_is_in_the_session(self):
        with override_settings(SESSION_COOKIE_PATH="/", CSRF_COOKIE_PATH="/app/", CSRF_USE_SESSIONS=True):
            assert check_cookie_paths(None) == []
        with override_settings(SESSION_COOKIE_PATH="/app/", CSRF_COOKIE_PATH="/app/", CSRF_USE_SESSIONS=True):
            assert [m.msg.split(" ")[0] for m in check_cookie_paths(None)] == ["SESSION_COOKIE_PATH"]

    def test_force_script_name_does_not_move_the_socket(self):
        """The client opens the socket at the host's root; a cookie under the script name misses it."""
        with override_settings(FORCE_SCRIPT_NAME="/app", SESSION_COOKIE_PATH="/app/", CSRF_COOKIE_PATH="/"):
            assert [m.id for m in check_cookie_paths(None)] == ["wireview.W021"]

    def test_the_socket_path_is_the_clients(self):
        """The path the check holds the cookies to is where wireview.js opens the socket."""
        from wireview.urls import SOCKET_PATH, websocket_urlpatterns

        static = Path(__file__).resolve().parent.parent / "wireview" / "static" / "wireview"
        assert f'open(path = "{SOCKET_PATH}")' in (static / "wireview.js").read_text(encoding="utf-8")
        assert "${location.host}/${path}?" in (static / "tab.mjs").read_text(encoding="utf-8"), "at the host's root"
        assert [str(pattern.pattern) for pattern in websocket_urlpatterns] == [SOCKET_PATH]

    def test_runs_without_deploy(self):
        from django.core.checks.registry import registry

        assert check_cookie_paths in registry.get_checks(include_deployment_checks=False)


class TestRunserverIsAsgiCheck:
    """W013: Django's own runserver is WSGI and never accepts the WebSocket."""

    @pytest.fixture
    def started_as(self, monkeypatch):
        """Pretend the process was started as ``manage.py <argv>``, with ``runserver`` from ``provider``."""

        def _started_as(*argv: str, provider: str | None = None):
            monkeypatch.setattr(sys, "argv", ["manage.py", *argv])
            if provider is not None:
                monkeypatch.setattr("django.core.management.get_commands", lambda: {"runserver": provider})

        return _started_as

    @pytest.mark.parametrize(
        "provider",
        [
            "django.core",
            # Both wrap the stock command -- still WSGI.
            "django.contrib.staticfiles",
            "whitenoise.runserver_nostatic",
        ],
    )
    def test_a_wsgi_runserver_flagged(self, started_as, provider):
        started_as("runserver", provider=provider)
        messages = check_runserver_is_asgi(None)

        assert [m.id for m in messages] == ["wireview.W013"]
        assert provider in messages[0].msg
        assert "'daphne' at the top of INSTALLED_APPS" in messages[0].hint
        # daphne has to outrank the app that provides the command, which Django itself is not one of.
        assert (f"above '{provider}'" in messages[0].hint) is (provider != "django.core")

    def test_daphne_silent(self, started_as):
        started_as("runserver", "0.0.0.0:8000", provider="daphne")
        assert check_runserver_is_asgi(None) == []

    @pytest.mark.parametrize("argv", [("check",), ("migrate",), ()])
    def test_other_commands_silent(self, started_as, argv):
        """uvicorn and friends never run the command, so INSTALLED_APPS says nothing about them."""
        started_as(*argv, provider="django.core")
        assert check_runserver_is_asgi(None) == []

    def test_testproj_runserver_is_daphne(self, started_as):
        """testproj lists daphne above whitenoise and staticfiles, so its real registry is clean."""
        started_as("runserver")
        assert check_runserver_is_asgi(None) == []

    def test_registered_without_deploy(self):
        from django.core.checks.registry import registry

        assert check_runserver_is_asgi in registry.get_checks(include_deployment_checks=False)


class TestUploadTempDirCheck:
    """W008: the setting is only read when a chunk arrives, never at startup."""

    @pytest.fixture
    def temp_dir(self, monkeypatch):
        """Point WIREVIEW['UPLOAD_TEMP_DIR'] at a value for one test."""

        def _set(value):
            set_wireview(monkeypatch, UPLOAD_TEMP_DIR=value)

        return _set

    def test_unset_is_silent(self, temp_dir):
        temp_dir(None)

        assert check_upload_temp_dir(None) == []

    def test_empty_string_flagged(self, temp_dir):
        """An unset environment variable reads as '', and nobody means the cwd."""
        temp_dir("")

        messages = check_upload_temp_dir(None)

        assert [m.id for m in messages] == ["wireview.W008"]
        assert "is empty" in messages[0].msg

    def test_writable_directory_silent(self, temp_dir, tmp_path):
        temp_dir(str(tmp_path))

        assert check_upload_temp_dir(None) == []

    def test_file_flagged(self, temp_dir, tmp_path):
        target = tmp_path / "not-a-dir"
        target.write_text("x", encoding="utf-8")
        temp_dir(str(target))

        messages = check_upload_temp_dir(None)

        assert [m.id for m in messages] == ["wireview.W008"]
        assert "not a directory" in messages[0].msg

    @needs_permissions
    def test_unwritable_directory_flagged(self, temp_dir, tmp_path):
        target = tmp_path / "locked"
        target.mkdir(mode=0o500)
        temp_dir(str(target))

        try:
            messages = check_upload_temp_dir(None)
        finally:
            target.chmod(0o700)

        assert [m.id for m in messages] == ["wireview.W008"]
        assert "cannot write" in messages[0].msg

    def test_missing_but_creatable_is_silent(self, temp_dir, tmp_path):
        """The directory is created on first upload, so this is not a problem."""
        temp_dir(str(tmp_path / "uploads" / "chunks"))

        assert check_upload_temp_dir(None) == []

    @needs_permissions
    def test_missing_and_uncreatable_flagged(self, temp_dir, tmp_path):
        blocker = tmp_path / "locked"
        blocker.mkdir(mode=0o500)
        temp_dir(str(blocker / "uploads"))

        try:
            messages = check_upload_temp_dir(None)
        finally:
            blocker.chmod(0o700)

        assert [m.id for m in messages] == ["wireview.W008"]
        assert "does not exist" in messages[0].msg

    def test_has_no_side_effects(self, temp_dir, tmp_path):
        """A check must not create what it is checking for."""
        target = tmp_path / "uploads"
        temp_dir(str(target))

        check_upload_temp_dir(None)

        assert not target.exists()


class TestSigningKeyCheck:
    """W009: a key that reads as set but silently is not (#83)."""

    @pytest.fixture
    def signing(self, monkeypatch):
        """Set WIREVIEW['SIGNING_KEY'] and its fallbacks for one test."""

        def _set(key, fallbacks=None):
            set_wireview(monkeypatch, SIGNING_KEY=key)
            set_wireview(monkeypatch, SIGNING_KEY_FALLBACKS=fallbacks)

        return _set

    def test_unset_is_silent(self, signing):
        """Riding on SECRET_KEY is the default, not a mistake."""
        signing(None)

        assert check_signing_key(None) == []

    def test_a_real_key_is_silent(self, signing):
        signing("a-dedicated-key", ["an-older-key"])

        assert check_signing_key(None) == []

    def test_an_empty_key_is_flagged(self, signing):
        """Django's signers read a falsy key as absent, so nothing would break."""
        signing("")

        messages = check_signing_key(None)

        assert [m.id for m in messages] == ["wireview.W009"]
        assert "SECRET_KEY" in messages[0].msg

    def test_fallbacks_without_a_key_are_flagged(self, signing):
        signing(None, ["an-older-key"])

        messages = check_signing_key(None)

        assert [m.id for m in messages] == ["wireview.W009"]
        assert "SIGNING_KEY_FALLBACKS" in messages[0].msg


class TestLiveSessions:
    """W010: ``_live_sessions`` and the declared boundaries have to line up."""

    @pytest.fixture
    def registry(self):
        """Each test sees only the boundaries it declares."""
        saved = dict(live_session_module._REGISTRY)
        live_session_module._REGISTRY.clear()
        yield live_session_module._REGISTRY
        live_session_module._REGISTRY.clear()
        live_session_module._REGISTRY.update(saved)

    def test_a_project_without_boundaries_is_silent(self, only, registry):
        only(make_component("W10Plain", meta={"on_mount": [object()]}))

        assert check_live_sessions(None) == []

    def test_a_name_no_session_declares_is_flagged(self, only, registry):
        only(make_component("W10Typo", meta={"live_sessions": {"admn"}}))

        messages = check_live_sessions(None)

        assert [m.id for m in messages] == ["wireview.W010"]
        assert "'admn'" in messages[0].msg

    def test_a_declared_name_is_silent(self, only, registry):
        live_session_module.live_session("admin")
        only(make_component("W10Bound", meta={"live_sessions": {"admin"}}))

        assert check_live_sessions(None) == []

    def test_a_guarded_component_without_a_boundary_is_flagged(self, only, registry):
        live_session_module.live_session("admin")
        only(make_component("W10Unbound", meta={"on_mount": [object()]}))

        messages = check_live_sessions(None)

        assert [m.id for m in messages] == ["wireview.W010"]
        assert "Meta.live_sessions" in messages[0].msg

    def test_a_boundary_without_the_request_context_processor_is_flagged(self, only, registry):
        """The trap: everything still renders, and the boundary is simply not there."""
        live_session_module.live_session("admin")
        only(make_component("W10NoProcessor"))
        engine = {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "NAME": "main",
            "OPTIONS": {"context_processors": ["django.contrib.auth.context_processors.auth"]},
        }

        with override_settings(TEMPLATES=[engine]):
            messages = check_live_sessions(None)

        assert [m.id for m in messages] == ["wireview.W010"]
        assert "context_processors.request" in messages[0].msg

    def test_the_processor_being_present_is_silent(self, only, registry):
        live_session_module.live_session("admin")
        only(make_component("W10WithProcessor"))
        engine = {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
        }

        with override_settings(TEMPLATES=[engine]):
            assert check_live_sessions(None) == []

    def test_a_project_without_boundaries_is_not_told_about_the_processor(self, only, registry):
        only(make_component("W10NoBoundaryNoProcessor"))
        engine = {"BACKEND": "django.template.backends.django.DjangoTemplates", "OPTIONS": {}}

        with override_settings(TEMPLATES=[engine]):
            assert check_live_sessions(None) == []

    def test_an_unguarded_component_without_a_boundary_is_silent(self, only, registry):
        """Most components are not guards. The nudge is for the ones that are."""
        live_session_module.live_session("admin")
        only(make_component("W10Ordinary"))

        assert check_live_sessions(None) == []


class TestDjangoTemplatesCheck:
    """W022: a component's or function component's template is found by another engine than Django's (#210)."""

    ENGINES = [
        {"BACKEND": "testproj.other_backend.OtherTemplates", "DIRS": [], "OPTIONS": {}},
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [
                    (
                        "django.template.loaders.locmem.Loader",
                        {"other/count.html": "<div></div>", "dj/count.html": "<div></div>", "dj/fn.html": "<p></p>"},
                    )
                ]
            },
        },
    ]

    @pytest.fixture
    def functions(self, monkeypatch):
        from wireview import function_components

        def _functions(**templates):
            registry = {
                name: function_components.FunctionComponent(func=lambda: {}, name=name, template=template)
                for name, template in templates.items()
            }
            monkeypatch.setattr(function_components, "_registry", registry)

        return _functions

    def test_a_template_another_engine_finds_first_is_flagged(self, only, functions):
        only(make_component("OtherCount", meta={"template_name": "other/count.html"}))
        functions(other_fn="other/fn.html")
        with override_settings(TEMPLATES=self.ENGINES):
            messages = check_django_templates(None)
        assert [m.id for m in messages] == ["wireview.W022", "wireview.W022"]
        assert (
            "probeapp.live.OtherCount's template 'other/count.html' was found by the 'other_backend'" in messages[0].msg
        )
        assert "Function component 'other_fn''s template 'other/fn.html'" in messages[1].msg
        assert "DjangoTemplates" in messages[0].hint

    def test_a_template_another_engine_finds_first_and_cannot_compile_is_flagged(self, only, functions):
        """Jinja2 raises its own syntax error on Django's tags, and Django's loader asks no other engine."""
        only(make_component("StrictCount", meta={"template_name": "dj/count.html"}))
        functions(strict_fn="dj/fn.html")
        engines = [{**self.ENGINES[0], "BACKEND": "testproj.other_backend.StrictTemplates"}, self.ENGINES[1]]
        with override_settings(TEMPLATES=engines):
            messages = check_django_templates(None)
        assert [m.id for m in messages] == ["wireview.W022", "wireview.W022"]
        assert (
            "probeapp.live.StrictCount's template 'dj/count.html' was found, and could not compile, by the "
            "'other_backend' template engine (testproj.other_backend.StrictTemplates)" in messages[0].msg
        )
        assert "Function component 'strict_fn''s template 'dj/fn.html' was found, and could not" in messages[1].msg

    def test_a_django_template_that_does_not_compile_is_the_renders_to_tell(self, only, functions):
        only(make_component("BrokenCount", meta={"template_name": "dj/broken.html"}))
        functions()
        engines = [
            self.ENGINES[0],
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", {"dj/broken.html": "{% nope %}"})]},
            },
        ]
        with override_settings(TEMPLATES=engines):
            assert check_django_templates(None) == []

    def test_django_templates_and_missing_ones_are_silent(self, only, functions):
        """A template no engine finds is the render's to tell: not this check's."""
        only(
            make_component("DjCount", meta={"template_name": "dj/count.html"}),
            make_component("Nowhere", meta={"template_name": "nowhere.html"}),
        )
        functions(dj_fn="dj/fn.html", nowhere_fn="nowhere.html")
        with override_settings(TEMPLATES=self.ENGINES):
            assert check_django_templates(None) == []

    def test_django_ahead_of_the_other_engine_is_silent(self, only, functions):
        only(make_component("OtherCount", meta={"template_name": "other/count.html"}))
        functions()
        with override_settings(TEMPLATES=list(reversed(self.ENGINES))):
            assert check_django_templates(None) == []


class TestPresenceProducerIdsCheck:
    """W023: a template draws a presence producer without an id (#212)."""

    @pytest.fixture
    def producer(self, only):
        from wireview import PresenceMixin

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            base = type("W23Base", (PresenceMixin, Component), {"__module__": __name__})
        made = make_component("W23Typing", base=base)
        only(made)
        return made

    @staticmethod
    def engines(directory: Path, locmem: dict[str, str] | None = None) -> list[dict]:
        engines: list[dict] = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [directory]}]
        if locmem is not None:
            engines.append(
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "NAME": "locmem",
                    "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", locmem)]},
                }
            )
        return engines

    def test_a_template_that_draws_one_without_an_id_is_flagged(self, producer, tmp_path):
        page = tmp_path / "rooms" / "room.html"
        page.parent.mkdir()
        page.write_text(
            "{% load wireview %}\n<main>\n"
            "{% if room %}{% component 'W23Typing' room=room %}{% endif %}\n"
            '{% component_block "W23Typing" %}{% endcomponent %}\n'
            "{% component 'W23Typing' id='composer' %}\n"
            "{% component 'XNotAProducer' %}{% component name %}\n</main>\n",
            encoding="utf-8",
        )
        with override_settings(TEMPLATES=self.engines(tmp_path)):
            messages = check_presence_producer_ids(None)
        assert [m.id for m in messages] == ["wireview.W023", "wireview.W023"]
        assert messages[0].msg == f"{page}:3 draws {producer._fqn}, a presence producer, without an id."
        assert messages[1].msg.startswith(f"{page}:4 ")
        assert "{% component 'W23Typing' id='...' %}" in messages[0].hint

    def test_a_components_own_template_is_read_wherever_its_loader_finds_it(self, only, producer, tmp_path):
        """A loader that names no directory (locmem here) still hands the component's template over."""
        drawer = make_component("W23Drawer", meta={"template_name": "w23/drawer.html"})
        only(producer, drawer)
        locmem = {"w23/drawer.html": "{% load wireview %}<div>{% component 'W23Typing' %}</div>"}
        with override_settings(TEMPLATES=self.engines(tmp_path, locmem)):
            [message] = check_presence_producer_ids(None)
        assert message.msg.startswith("w23/drawer.html:1 draws ")

    def test_ids_trackers_and_templates_that_do_not_compile_are_silent(self, producer, tmp_path):
        (tmp_path / "ok.html").write_text(
            "{% load wireview %}{% component 'W23Typing' id=\"room-\"|concat:1 %}", encoding="utf-8"
        )
        (tmp_path / "broken.html").write_text(
            "{% load wireview %}{% component 'W23Typing' %}{% nope %}", encoding="utf-8"
        )
        (tmp_path / "plain.html").write_text("{% component 'W23Typing' %}", encoding="utf-8")  # no {% load %}: not ours
        with override_settings(TEMPLATES=self.engines(tmp_path)):
            assert check_presence_producer_ids(None) == []

    def test_an_empty_id_is_flagged_as_the_tag_refuses_it(self, producer, tmp_path):
        page = tmp_path / "empty.html"
        page.write_text(
            "{% load wireview %}\n{% component 'W23Typing' id='' %}\n{% component 'W23Typing' id=None %}",
            encoding="utf-8",
        )
        with override_settings(TEMPLATES=self.engines(tmp_path)):
            messages = check_presence_producer_ids(None)
        assert [m.msg.split(" draws ")[0] for m in messages] == [f"{page}:2", f"{page}:3"]

    @staticmethod
    def overridden(tmp_path: Path, project: str) -> tuple[Path, Path]:
        """A project directory ahead of a vendor one, both with ``room.html``; the vendor's draws without an id."""
        ours, vendor = tmp_path / "project", tmp_path / "vendor"
        ours.mkdir()
        vendor.mkdir()
        (ours / "room.html").write_text(project, encoding="utf-8")
        (vendor / "room.html").write_text(
            "{% load wireview %}\n{% block body %}{% component 'W23Typing' %}{% endblock %}", encoding="utf-8"
        )
        return ours, vendor

    def test_a_file_another_directory_overrides_is_silent(self, producer, tmp_path):
        """The loader picks the project's room.html: the vendor's is never rendered (#212 review)."""
        ours, vendor = self.overridden(tmp_path, "safe override")
        engines = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [ours, vendor]}]
        with override_settings(TEMPLATES=engines):
            assert check_presence_producer_ids(None) == []

    def test_the_parent_an_override_extends_by_its_own_name_is_read(self, producer, tmp_path):
        """``{% extends 'room.html' %}`` in the override reaches the vendor's file, skipping itself."""
        ours, vendor = self.overridden(tmp_path, "{% extends 'room.html' %}")
        engines = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [ours, vendor]}]
        with override_settings(TEMPLATES=engines):
            [message] = check_presence_producer_ids(None)
        assert message.msg.startswith(f"{vendor / 'room.html'}:2 draws ")

    def test_templates_reached_where_no_directory_lists_them_are_read(self, producer, tmp_path, monkeypatch):
        """A function component's template and what it includes, in a loader that names no directory."""
        from wireview import function_components

        function = function_components.FunctionComponent(func=lambda: {}, name="w23_fn", template="w23/fn.html")
        monkeypatch.setattr(function_components, "_registry", {"w23_fn": function})
        locmem = {
            "w23/fn.html": "{% load wireview %}{% include 'w23/inner.html' %}",
            "w23/inner.html": "{% load wireview %}\n{% component 'W23Typing' %}",
            "w23/unused.html": "{% load wireview %}{% component 'W23Typing' %}",
        }
        engines = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", locmem)]},
            }
        ]
        with override_settings(TEMPLATES=engines):
            [message] = check_presence_producer_ids(None)
        assert message.msg.startswith("w23/inner.html:2 draws ")

    @staticmethod
    def choosing(monkeypatch, tag: str) -> list[dict]:
        """A function component whose template uses ``tag``: an inclusion tag with a list of candidates."""
        from wireview import function_components

        function = function_components.FunctionComponent(func=lambda: {}, name="w23_fn", template="w23/root.html")
        monkeypatch.setattr(function_components, "_registry", {"w23_fn": function})
        locmem = {
            "w23/root.html": f"{{% load sh_tags %}}{{% {tag} %}}",
            "w23/safe.inc": "safe",
            "w23/bad.inc": "{% load wireview %}{% component 'W23Typing' %}",
        }
        return [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [("django.template.loaders.locmem.Loader", locmem)],
                    "libraries": {"sh_tags": "testproj.inclusion_tags"},
                },
            }
        ]

    def test_a_candidate_after_the_one_an_inclusion_tag_takes_is_silent(self, producer, monkeypatch):
        """``['safe.inc', 'bad.inc']``: the render takes safe.inc and never loads bad.inc (#212 review)."""
        with override_settings(TEMPLATES=self.choosing(monkeypatch, "sh_first_choice")):
            assert get_template("w23/root.html").render({}) == "safe"
            assert check_presence_producer_ids(None) == []

    def test_the_candidate_after_a_missing_one_is_read(self, producer, monkeypatch):
        """``['missing.inc', 'bad.inc']``: the render takes bad.inc, and its tag raises (#212 review)."""
        with override_settings(TEMPLATES=self.choosing(monkeypatch, "sh_fallback_choice")):
            with pytest.raises(TemplateSyntaxError, match="without an id"):
                get_template("w23/root.html").render({})
            [message] = check_presence_producer_ids(None)
        assert message.msg.startswith("w23/bad.inc:1 draws ")


class TestTestprojIsClean:
    """AC3: zero false positives on the project we actually ship tests for."""

    def test_no_findings_for_testproj_components(self, monkeypatch):
        testproj = [cls for cls in iter_component_classes() if cls.__module__.startswith("testproj.")]
        assert testproj, "testproj components should be registered by now"

        monkeypatch.setattr(wireview_checks, "iter_component_classes", lambda: iter(testproj))
        assert check_async_handlers(None) == []
        assert check_async_lifecycle(None) == []
        assert check_component_name_collisions(None) == []
        assert check_client_bundle(None) == []
        assert check_settings_keys(None) == []
        assert check_reconnect_settings(None) == []
        assert check_upload_temp_dir(None) == []
        assert check_signing_key(None) == []
        assert check_live_sessions(None) == []
        assert check_django_templates(None) == []
        assert check_presence_producer_ids(None) == []

    def test_the_settings_checks_run_with_plain_check(self):
        """A check nobody registers is silent too: the calls above cannot tell."""
        from django.core.checks.registry import registry

        assert check_reconnect_settings in registry.get_checks(include_deployment_checks=False)

    def test_every_check_is_registered(self):
        """Each ``check_*`` in wireview.checks runs under ``manage.py check``.

        The tests above call the functions directly, so a ``register()`` line
        dropped from ``register_checks()`` passed them all -- W017 among them,
        which is what stands between a password hash and the broker.
        """
        from django.core.checks.registry import registry

        defined = {
            func
            for name, func in inspect.getmembers(wireview_checks, inspect.isfunction)
            if name.startswith("check_") and func.__module__ == wireview_checks.__name__
        }
        plain = set(registry.get_checks(include_deployment_checks=False))
        deploy = set(registry.get_checks(include_deployment_checks=True)) - plain

        assert defined - plain - deploy == set()
        assert defined & deploy == {check_channel_layer}
