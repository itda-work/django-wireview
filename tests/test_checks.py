"""Tests for the Django system checks (wireview.checks).

Each check must fire on the trap it names and stay silent otherwise: a check
that cries wolf on a healthy project gets ignored, and then it is dead weight.
"""

import os
import warnings

import pytest
from django.test import override_settings

from wireview import Component
from wireview import checks as wireview_checks
from wireview.checks import (
    check_async_handlers,
    check_async_lifecycle,
    check_channel_layer,
    check_client_bundle,
    check_component_name_collisions,
    check_hmin,
    check_signing_key,
    check_upload_temp_dir,
    iter_component_classes,
    iter_exposed_handlers,
)

pytestmark = pytest.mark.unit

#: A permission-based test proves nothing when the process bypasses permissions.
needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions",
)


def make_component(class_name: str, module: str = "probeapp.live", **namespace) -> type[Component]:
    """Build a component class off the registry's beaten path.

    Registration warns on name collisions, which two of these tests want on
    purpose, so the warning is silenced here rather than in each test.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return type(
            class_name,
            (Component,),
            {"__module__": module, "_template_name": "test.html", **namespace},
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

    def test_inherited_lifecycle_is_silent(self, only):
        """Not overriding it must not report the framework's own definition."""
        only(make_component("ProbeLifecycleInherited"))
        assert check_async_lifecycle(None) == []


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


class TestHminCheck:
    """W005: django-hmin strips the markers partial diffs are built on."""

    def test_off_by_default(self):
        assert check_hmin(None) == []

    def test_enabled_flagged(self, monkeypatch):
        from wireview import settings as wireview_settings

        monkeypatch.setattr(wireview_settings, "USE_HMIN", True)
        monkeypatch.setattr(wireview_settings, "USE_HTML_DIFF", True)
        messages = check_hmin(None)

        assert [m.id for m in messages] == ["wireview.W005"]

    def test_not_flagged_when_diffing_is_off(self, monkeypatch):
        """Without partial diffs there is nothing for hmin to degrade."""
        from wireview import settings as wireview_settings

        monkeypatch.setattr(wireview_settings, "USE_HMIN", True)
        monkeypatch.setattr(wireview_settings, "USE_HTML_DIFF", False)
        assert check_hmin(None) == []


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


class TestUploadTempDirCheck:
    """W008: the setting is only read when a chunk arrives, never at startup."""

    @pytest.fixture
    def temp_dir(self, monkeypatch):
        """Point WIREVIEW['UPLOAD_TEMP_DIR'] at a value for one test."""
        from wireview import settings as wireview_settings

        def _set(value):
            monkeypatch.setattr(wireview_settings, "UPLOAD_TEMP_DIR", value)

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
        target.write_text("x")
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
        from wireview import settings as wireview_settings

        def _set(key, fallbacks=None):
            monkeypatch.setattr(wireview_settings, "SIGNING_KEY", key)
            monkeypatch.setattr(wireview_settings, "SIGNING_KEY_FALLBACKS", fallbacks)

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
        assert check_hmin(None) == []
        assert check_upload_temp_dir(None) == []
        assert check_signing_key(None) == []
