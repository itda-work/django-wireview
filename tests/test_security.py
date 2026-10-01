"""Security tests for wireview.

These tests verify that security measures are in place to prevent:
- Unauthorized method access via event handlers
- Pydantic internal method exposure
- HTTP header manipulation attacks
"""

import pytest
import pytest_asyncio
from testproj.wireview_setting import set_wireview

from wireview import Component, LiveComponent
from wireview.repository import ComponentRepository
from wireview.testing import mount

# =============================================================================
# Test Components
# =============================================================================


class SecureComponent(Component):
    """Test component with user-defined methods."""

    class Meta:
        template_name = "test.html"

    counter: int = 0

    async def increment(self):
        """User-defined event handler - should be callable."""
        self.counter += 1

    async def decrement(self):
        """Another user-defined event handler."""
        self.counter -= 1

    def sync_method(self):
        """Sync method - should also be callable."""
        return self.counter

    class Helper:
        """A nested class: callable, and not a handler."""


# =============================================================================
# Event Handler Security Tests
# =============================================================================


class TestEventHandlerSecurity:
    """Test that event handlers are properly restricted."""

    @pytest_asyncio.fixture
    async def repo_and_id(self):
        """Create a repository with a component, return both repo and component ID."""
        view = await mount(SecureComponent)
        repo = ComponentRepository(is_live=True)
        repo.register_component(view.component)
        return repo, view.component.id

    # Valid event handler tests

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_user_defined_method_allowed(self, repo_and_id):
        """User-defined methods should be callable."""
        repo, comp_id = repo_and_id
        component = await repo.dispatch_event(comp_id, "increment", [], {})
        assert component is not None
        assert component.counter == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_multiple_user_methods_allowed(self, repo_and_id):
        """Multiple user-defined methods should work."""
        repo, comp_id = repo_and_id
        await repo.dispatch_event(comp_id, "increment", [], {})
        await repo.dispatch_event(comp_id, "increment", [], {})
        await repo.dispatch_event(comp_id, "decrement", [], {})

        component = repo.get(comp_id)
        assert component.counter == 1

    # Private method blocking tests

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.parametrize("name", ["Meta", "Helper"])
    async def test_a_nested_class_is_not_a_handler(self, repo_and_id, name):
        """A class is callable, so the exposure rule let a client instantiate it (#99).

        Every component has one now: ``class Meta:``.
        """
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match=name):
            await repo.dispatch_event(comp_id, name, [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_private_method_blocked(self, repo_and_id):
        """Methods starting with _ should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Invalid event handler"):
            await repo.dispatch_event(comp_id, "_private", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_dunder_method_blocked(self, repo_and_id):
        """Methods starting with __ should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Invalid event handler"):
            await repo.dispatch_event(comp_id, "__init__", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_internal_method_blocked(self, repo_and_id):
        """Internal wireview methods should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Invalid event handler"):
            await repo.dispatch_event(comp_id, "_render", [], {})

    # Pydantic method blocking tests

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_model_validate_blocked(self, repo_and_id):
        """Pydantic model_validate should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "model_validate", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_model_dump_blocked(self, repo_and_id):
        """Pydantic model_dump should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "model_dump", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_model_validate_json_blocked(self, repo_and_id):
        """Pydantic model_validate_json should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "model_validate_json", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_dict_blocked(self, repo_and_id):
        """Pydantic dict method should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "dict", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_json_blocked(self, repo_and_id):
        """Pydantic json method should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "json", [], {})

    # Component base class method blocking tests

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_destroy_blocked(self, repo_and_id):
        """Component destroy method should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "destroy", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_allow_upload_blocked(self, repo_and_id):
        """Component allow_upload method should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "allow_upload", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_stream_blocked(self, repo_and_id):
        """Component stream method should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "stream", [], {})

    # Invalid command name tests

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_empty_command_blocked(self, repo_and_id):
        """Empty command should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Invalid event handler"):
            await repo.dispatch_event(comp_id, "", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_invalid_identifier_blocked(self, repo_and_id):
        """Invalid Python identifiers should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Invalid event handler"):
            await repo.dispatch_event(comp_id, "123invalid", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_special_chars_blocked(self, repo_and_id):
        """Commands with special characters should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Invalid event handler"):
            await repo.dispatch_event(comp_id, "method;rm -rf", [], {})

    # Nonexistent method tests

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_nonexistent_method_error(self, repo_and_id):
        """Nonexistent methods should raise error."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Unknown event handler"):
            await repo.dispatch_event(comp_id, "nonexistent", [], {})


# =============================================================================
# HTTP Upload Security Tests
# =============================================================================


class TestUploadHeaderSecurity:
    """Test HTTP upload header validation."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_invalid_chunk_index_rejected(self):
        """Invalid chunk index should be rejected."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView

        factory = AsyncRequestFactory()
        request = factory.post(
            "/__wireview_upload__/conn-1/test-comp/files/",
            data=b"test",
            content_type="application/octet-stream",
        )
        request.META["HTTP_X_UPLOAD_TOKEN"] = "test"
        request.META["HTTP_X_CHUNK_INDEX"] = "invalid"  # Not a number
        request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
        request.META["HTTP_X_ENTRY_REF"] = "ref-1"

        view = UploadView()
        response = await view.post(request, "conn-1", "test-comp", "files")

        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_negative_chunk_index_rejected(self):
        """Negative chunk index should be rejected."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView

        factory = AsyncRequestFactory()
        request = factory.post(
            "/__wireview_upload__/conn-1/test-comp-2/files/",
            data=b"test",
            content_type="application/octet-stream",
        )
        request.META["HTTP_X_UPLOAD_TOKEN"] = "test"
        request.META["HTTP_X_CHUNK_INDEX"] = "-1"  # Negative
        request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
        request.META["HTTP_X_ENTRY_REF"] = "ref-1"

        view = UploadView()
        response = await view.post(request, "conn-1", "test-comp-2", "files")

        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chunk_index_exceeds_total_rejected(self):
        """Chunk index >= total_chunks should be rejected."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView

        factory = AsyncRequestFactory()
        request = factory.post(
            "/__wireview_upload__/conn-1/test-comp-3/files/",
            data=b"test",
            content_type="application/octet-stream",
        )
        request.META["HTTP_X_UPLOAD_TOKEN"] = "test"
        request.META["HTTP_X_CHUNK_INDEX"] = "5"  # >= total_chunks
        request.META["HTTP_X_TOTAL_CHUNKS"] = "3"
        request.META["HTTP_X_ENTRY_REF"] = "ref-1"

        view = UploadView()
        response = await view.post(request, "conn-1", "test-comp-3", "files")

        assert response.status_code == 400


# =============================================================================
# Upload Ref Tests
# =============================================================================


class TestUploadRefSecurity:
    """The client picks the ref, and the ref reaches a temp filename.

    Without a check, ``prefix="wireview_x/../../victim/pwn_"`` escapes the temp
    directory whenever a ``wireview_<something>`` directory already exists there,
    which a local user can arrange on a shared host with a world-writable /tmp.
    """

    TRAVERSAL_REFS = [
        "../../etc/x",
        "x/../../y",
        "sub/dir",
        "a\\b",
        "ref\x00",
        "",
        "x" * 65,
    ]

    @pytest.mark.unit
    def test_add_entry_rejects_path_characters(self):
        """A ref with path characters never becomes an entry."""
        from wireview.features.uploads import UploadConfig, UploadEntry, UploadRegistry

        registry = UploadRegistry("comp-ref", connection_id="conn-ref")
        registry.allow_upload(UploadConfig(name="files"))

        for ref in self.TRAVERSAL_REFS:
            entry = UploadEntry(
                ref=ref,
                upload_name="files",
                client_name="photo.jpg",
                client_size=10,
                client_type="image/jpeg",
            )
            with pytest.raises(ValueError, match="Invalid upload ref"):
                registry.add_entry("files", entry)

        assert registry.get_entries("files") == []

    @pytest.mark.unit
    def test_add_entry_accepts_generated_ref(self):
        """The refs the server itself generates must still pass."""
        from wireview.features.uploads import UploadConfig, UploadEntry, UploadRegistry, generate_ref

        registry = UploadRegistry("comp-ref-ok", connection_id="conn-ref")
        registry.allow_upload(UploadConfig(name="files"))

        ref = generate_ref()
        entry = UploadEntry(
            ref=ref,
            upload_name="files",
            client_name="photo.jpg",
            client_size=10,
            client_type="image/jpeg",
        )

        assert registry.add_entry("files", entry)
        assert registry.get_entry("files", ref) is entry

    @pytest.mark.unit
    def test_a_chunk_path_stays_inside_the_store(self, monkeypatch, tmp_path):
        """The path is computed from a hash, so no ref can climb out of it."""
        from wireview.features import upload_store

        set_wireview(monkeypatch, UPLOAD_TEMP_DIR=str(tmp_path))
        expected = (tmp_path / upload_store.STORE_DIR_NAME / "conn-1").resolve()

        for ref in ["../../etc/x", "x/../../y", "sub/dir"]:
            path = upload_store.chunk_path("conn-1", "comp-1", "files", ref)

            assert path.resolve().parent == expected

    @pytest.mark.unit
    def test_a_connection_id_cannot_climb_out_of_the_store(self, monkeypatch, tmp_path):
        """The connection segment is a directory name, so it is not hashed."""
        from wireview.features import upload_store

        set_wireview(monkeypatch, UPLOAD_TEMP_DIR=str(tmp_path))

        for connection_id in ["../escape", "a/b", "", "."]:
            with pytest.raises(upload_store.InvalidConnectionId):
                upload_store.chunk_path(connection_id, "comp-1", "files", "ref-1")


# =============================================================================
# Validation Helper Tests
# =============================================================================


class TestValidationHelpers:
    """Test validation helper methods."""

    @pytest.mark.unit
    def test_is_valid_event_handler_valid(self):
        """Valid handler names should pass."""
        assert ComponentRepository._is_valid_event_handler("increment") is True
        assert ComponentRepository._is_valid_event_handler("on_click") is True
        assert ComponentRepository._is_valid_event_handler("handleSubmit") is True

    @pytest.mark.unit
    def test_is_valid_event_handler_invalid(self):
        """Invalid handler names should fail."""
        assert ComponentRepository._is_valid_event_handler("") is False
        assert ComponentRepository._is_valid_event_handler("_private") is False
        assert ComponentRepository._is_valid_event_handler("__init__") is False
        assert ComponentRepository._is_valid_event_handler("123method") is False
        assert ComponentRepository._is_valid_event_handler("method name") is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_is_user_defined_method_user_class(self):
        """Methods on user class should return True."""
        view = await mount(SecureComponent)
        component = view.component
        assert ComponentRepository._is_user_defined_method(component, "increment") is True
        assert ComponentRepository._is_user_defined_method(component, "decrement") is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_is_user_defined_method_base_class(self):
        """Methods on Component base class should return False."""
        view = await mount(SecureComponent)
        component = view.component
        assert ComponentRepository._is_user_defined_method(component, "dom") is False
        assert ComponentRepository._is_user_defined_method(component, "destroy") is False
        assert ComponentRepository._is_user_defined_method(component, "model_dump") is False


# =============================================================================
# Method Exposure Rule Tests (#63)
# =============================================================================


class ExposureProbe(Component):
    """User component that overrides framework names on purpose."""

    class Meta:
        template_name = "test.html"

    counter: int = 0
    joined_calls: int = 0

    async def increment(self):
        self.counter += 1

    async def joined(self):
        """Lifecycle override - still framework surface, not a client event."""
        self.joined_calls += 1


class ExposureLiveProbe(LiveComponent):
    """User LiveComponent that overrides a framework callback."""

    class Meta:
        template_name = "test.html"

    counter: int = 0

    async def increment(self):
        self.counter += 1

    async def update(self, **assigns):
        """Documented override point - called by the parent, not by the client."""
        await super().update(**assigns)


class TestMethodExposureRule:
    """The exposure rule must mean 'defined by user code', nothing wider."""

    async def _repo_for(self, component_class):
        view = await mount(component_class)
        repo = ComponentRepository(is_live=True)
        repo.register_component(view.component)
        return repo, view.component.id

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_user_handler_reachable(self):
        """A handler written by the user stays reachable."""
        repo, comp_id = await self._repo_for(ExposureProbe)
        component = await repo.dispatch_event(comp_id, "increment", [], {})
        assert component.counter == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_pydantic_generated_method_blocked(self):
        """model_post_init is injected into the user class by Pydantic, not written by the user."""
        repo, comp_id = await self._repo_for(ExposureProbe)
        assert "model_post_init" in type(repo.get(comp_id)).__dict__
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "model_post_init", [], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_overridden_lifecycle_method_blocked(self):
        """Overriding a framework lifecycle name does not expose it."""
        repo, comp_id = await self._repo_for(ExposureProbe)
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "joined", [], {})
        # Only the framework's own mount path called it.
        assert repo.get(comp_id).joined_calls == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_live_component_send_to_parent_blocked(self):
        """LiveComponent framework API must not be callable from the client."""
        repo, comp_id = await self._repo_for(ExposureLiveProbe)
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "send_to_parent", ["forged"], {})

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_live_component_update_blocked(self):
        """update() is a parent-driven callback, even when the user overrides it."""
        repo, comp_id = await self._repo_for(ExposureLiveProbe)
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "update", [], {"counter": 99})
        assert repo.get(comp_id).counter == 0

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_live_component_user_handler_reachable(self):
        """LiveComponent handlers written by the user stay reachable."""
        repo, comp_id = await self._repo_for(ExposureLiveProbe)
        component = await repo.dispatch_event(comp_id, "increment", [], {})
        assert component.counter == 1


# =============================================================================
# Upload Completion Callback
# =============================================================================


class UploadCallbackProbe(Component):
    """A component that takes the documented upload completion callback."""

    class Meta:
        template_name = "uploads/uploader.html"

    completed: list[str] = []

    async def joined(self):
        self.allow_upload("images", accept=[".jpg"], max_entries=1)

    async def on_upload_complete(self, name, entry):
        self.completed = [*self.completed, name]


class TestUploadCompletionCallback:
    """on_upload_complete is the server's callback, never a browser event.

    The session calls it once the bytes of an upload are on disk. When no
    framework class owned the name, a component that defined it had defined a
    handler too: a client could send ``on_upload_complete`` as an event and
    run the callback for an upload that never happened.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_client_cannot_call_the_upload_callback(self):
        view = await mount(UploadCallbackProbe)
        repo = ComponentRepository(is_live=True)
        repo.register_component(view.component)
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(view.component.id, "on_upload_complete", ["forged", None], {})
        assert view.component.completed == []

    @pytest.mark.unit
    def test_the_callback_is_not_listed_as_a_handler(self):
        from wireview.core.handlers import is_client_callable

        assert not is_client_callable(UploadCallbackProbe, "on_upload_complete")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_a_finished_upload_still_runs_the_callback(self, monkeypatch, tmp_path):
        """Owning the name does not stop the session calling the user's override."""
        from unittest.mock import AsyncMock, MagicMock

        from wireview.consumer import WireviewConsumer
        from wireview.features import upload_store
        from wireview.features.uploads import UploadEntry, UploadStatus

        set_wireview(monkeypatch, UPLOAD_TEMP_DIR=str(tmp_path))
        view = await mount(UploadCallbackProbe)
        consumer = WireviewConsumer()
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()
        consumer.repo = MagicMock()
        consumer.repo.get = MagicMock(return_value=view.component)
        # Not a component whose join failed on this connection
        consumer.repo.refused = MagicMock(return_value=False)
        consumer.send_command = AsyncMock()
        consumer.send_render = AsyncMock()

        entry = UploadEntry(
            ref="upload-1",
            upload_name="images",
            client_name="photo.jpg",
            client_size=4,
            client_type="image/jpeg",
        )
        view.component._upload_registry.add_entry("images", entry)
        entry.status = UploadStatus.UPLOADING
        assert entry.temp_path is not None
        upload_store.append_chunk(entry.temp_path, b"\xff\xd8\xff\x00")

        await consumer.command_upload_complete(id=view.component.id, name="images", ref="upload-1")

        assert view.component.completed == ["images"]


# =============================================================================
# The documented exposure rule
# =============================================================================


#: Where the guides list framework-owned names, and the words that open each list.
_NAME_LISTS = (
    ("docs/features/live-component.md", "Pydantic이 소유한 이름("),
    ("skills/wireview/SKILL.md", "Pydantic 소유 이름("),
)


def _documented_framework_names() -> list[str]:
    """Every name the guides list as framework-owned, in their own words."""
    import re
    from pathlib import Path

    names = []
    for path, opening in _NAME_LISTS:
        text = (Path(__file__).parents[1] / path).read_text()
        owned = text.split(opening, 1)[1].split(")은", 1)[0]
        names += [name for name in re.findall(r"`(\w+)`", owned) if name not in names]
    return names


class TestDocumentedFrameworkNames:
    """The guides once promised ``mount`` was framework-owned. It is not.

    Every name the guides list must be one a user can override without
    exposing it, and ``mount`` -- which no framework class defines -- must
    not be in the list.
    """

    @pytest.mark.unit
    def test_the_guide_lists_names(self):
        names = _documented_framework_names()
        assert "joined" in names and "on_upload_complete" in names
        assert "mount" not in names

    @pytest.mark.unit
    @pytest.mark.parametrize("name", _documented_framework_names())
    def test_an_override_of_a_listed_name_is_not_a_handler(self, name):
        from wireview.core.handlers import is_client_callable

        async def override(self, *args, **kwargs):
            pass

        owner = LiveComponent if name in {"update", "update_many", "send_to_parent"} else Component
        probe = type(f"ListedNameProbe_{name}", (owner,), {"__module__": __name__, name: override})
        assert not is_client_callable(probe, name)

    @pytest.mark.unit
    def test_mount_is_a_user_handler(self):
        """No framework class owns ``mount``: writing one exposes it."""
        from wireview.core.handlers import is_client_callable

        async def mount(self):
            pass

        probe = type("MountProbe", (Component,), {"__module__": __name__, "mount": mount})
        assert is_client_callable(probe, "mount")
