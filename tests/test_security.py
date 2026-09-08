"""Security tests for wireview.

These tests verify that security measures are in place to prevent:
- Unauthorized method access via event handlers
- Pydantic internal method exposure
- HTTP header manipulation attacks
"""

import pytest
import pytest_asyncio

from wireview import Component, LiveComponent
from wireview.repository import ComponentRepository
from wireview.testing import mount

# =============================================================================
# Test Components
# =============================================================================


class SecureComponent(Component):
    """Test component with user-defined methods."""

    _template_name = "test.html"
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
    async def test_dom_blocked(self, repo_and_id):
        """Component dom method should be blocked."""
        repo, comp_id = repo_and_id
        with pytest.raises(ValueError, match="Cannot call base class method"):
            await repo.dispatch_event(comp_id, "dom", [], {})

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

        from wireview.features.uploads import UploadConfig, UploadRegistry
        from wireview.views import UploadView, register_upload_registry

        registry = UploadRegistry("test-comp")
        registry.allow_upload(UploadConfig(name="files"))
        register_upload_registry("test-comp", registry)

        try:
            factory = AsyncRequestFactory()
            request = factory.post(
                "/__wireview_upload__/test-comp/files/",
                data=b"test",
                content_type="application/octet-stream",
            )
            request.META["HTTP_X_UPLOAD_TOKEN"] = "test"
            request.META["HTTP_X_CHUNK_INDEX"] = "invalid"  # Not a number
            request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
            request.META["HTTP_X_ENTRY_REF"] = "ref-1"

            view = UploadView()
            response = await view.post(request, "test-comp", "files")

            assert response.status_code == 400
        finally:
            from wireview.views import unregister_upload_registry

            unregister_upload_registry("test-comp")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_negative_chunk_index_rejected(self):
        """Negative chunk index should be rejected."""
        from django.test import AsyncRequestFactory

        from wireview.features.uploads import UploadConfig, UploadRegistry
        from wireview.views import UploadView, register_upload_registry

        registry = UploadRegistry("test-comp-2")
        registry.allow_upload(UploadConfig(name="files"))
        register_upload_registry("test-comp-2", registry)

        try:
            factory = AsyncRequestFactory()
            request = factory.post(
                "/__wireview_upload__/test-comp-2/files/",
                data=b"test",
                content_type="application/octet-stream",
            )
            request.META["HTTP_X_UPLOAD_TOKEN"] = "test"
            request.META["HTTP_X_CHUNK_INDEX"] = "-1"  # Negative
            request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
            request.META["HTTP_X_ENTRY_REF"] = "ref-1"

            view = UploadView()
            response = await view.post(request, "test-comp-2", "files")

            assert response.status_code == 400
        finally:
            from wireview.views import unregister_upload_registry

            unregister_upload_registry("test-comp-2")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_chunk_index_exceeds_total_rejected(self):
        """Chunk index >= total_chunks should be rejected."""
        from django.test import AsyncRequestFactory

        from wireview.features.uploads import UploadConfig, UploadRegistry
        from wireview.views import UploadView, register_upload_registry

        registry = UploadRegistry("test-comp-3")
        registry.allow_upload(UploadConfig(name="files"))
        register_upload_registry("test-comp-3", registry)

        try:
            factory = AsyncRequestFactory()
            request = factory.post(
                "/__wireview_upload__/test-comp-3/files/",
                data=b"test",
                content_type="application/octet-stream",
            )
            request.META["HTTP_X_UPLOAD_TOKEN"] = "test"
            request.META["HTTP_X_CHUNK_INDEX"] = "5"  # >= total_chunks
            request.META["HTTP_X_TOTAL_CHUNKS"] = "3"
            request.META["HTTP_X_ENTRY_REF"] = "ref-1"

            view = UploadView()
            response = await view.post(request, "test-comp-3", "files")

            assert response.status_code == 400
        finally:
            from wireview.views import unregister_upload_registry

            unregister_upload_registry("test-comp-3")


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

    _template_name = "test.html"
    counter: int = 0
    joined_calls: int = 0

    async def increment(self):
        self.counter += 1

    async def joined(self):
        """Lifecycle override - still framework surface, not a client event."""
        self.joined_calls += 1


class ExposureLiveProbe(LiveComponent):
    """User LiveComponent that overrides a framework callback."""

    _template_name = "test.html"
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
