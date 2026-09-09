"""Tests for Upload functionality."""

import tempfile
from pathlib import Path

import pytest

from wireview import Component
from wireview.features.uploads import (
    ConsumedUpload,
    UploadConfig,
    UploadEntry,
    UploadOp,
    UploadRegistry,
    UploadStatus,
    create_temp_file,
    generate_ref,
    validate_magic_bytes,
)
from wireview.testing import mount


class TestUploadConfig:
    """Test the UploadConfig dataclass."""

    @pytest.mark.unit
    def test_config_creation(self):
        """UploadConfig should store configuration correctly."""
        config = UploadConfig(
            name="images",
            accept=[".jpg", ".png"],
            max_entries=5,
            max_file_size=5 * 1024 * 1024,
        )
        assert config.name == "images"
        assert config.accept == [".jpg", ".png"]
        assert config.max_entries == 5
        assert config.max_file_size == 5 * 1024 * 1024

    @pytest.mark.unit
    def test_config_defaults(self):
        """UploadConfig should have sensible defaults."""
        config = UploadConfig(name="files")
        assert config.accept == []
        assert config.max_entries == 1
        assert config.max_file_size == 10 * 1024 * 1024
        assert config.chunk_size == 64 * 1024
        assert config.auto_upload is True

    @pytest.mark.unit
    def test_validate_entry_valid(self):
        """Validation should pass for valid entries."""
        config = UploadConfig(
            name="images",
            accept=[".jpg", ".png"],
            max_file_size=1024 * 1024,
        )
        entry = UploadEntry(
            ref="test-1",
            upload_name="images",
            client_name="photo.jpg",
            client_size=500 * 1024,
            client_type="image/jpeg",
        )
        errors = config.validate_entry(entry)
        assert errors == []

    @pytest.mark.unit
    def test_validate_entry_wrong_type(self):
        """Validation should fail for wrong file type."""
        config = UploadConfig(name="images", accept=[".jpg"])
        entry = UploadEntry(
            ref="test-1",
            upload_name="images",
            client_name="doc.pdf",
            client_size=1024,
            client_type="application/pdf",
        )
        errors = config.validate_entry(entry)
        assert len(errors) == 1
        assert "Invalid file type" in errors[0]

    @pytest.mark.unit
    def test_validate_entry_too_large(self):
        """Validation should fail for oversized files."""
        config = UploadConfig(name="images", max_file_size=1024)
        entry = UploadEntry(
            ref="test-1",
            upload_name="images",
            client_name="large.jpg",
            client_size=2048,
            client_type="image/jpeg",
        )
        errors = config.validate_entry(entry)
        assert len(errors) == 1
        assert "too large" in errors[0].lower()

    @pytest.mark.unit
    def test_validate_entry_case_insensitive(self):
        """File extension validation should be case-insensitive."""
        config = UploadConfig(name="images", accept=[".jpg", ".PNG"])
        entry = UploadEntry(
            ref="test-1",
            upload_name="images",
            client_name="photo.JPG",
            client_size=1024,
            client_type="image/jpeg",
        )
        errors = config.validate_entry(entry)
        assert errors == []

    @pytest.mark.unit
    def test_to_client_dict(self):
        """to_client_dict should serialize correctly."""
        config = UploadConfig(
            name="images",
            accept=[".jpg"],
            max_entries=3,
        )
        data = config.to_client_dict("/upload/")
        assert data["accept"] == [".jpg"]
        assert data["max_entries"] == 3
        assert data["endpoint"] == "/upload/"


class TestUploadEntry:
    """Test the UploadEntry dataclass."""

    @pytest.mark.unit
    def test_entry_creation(self):
        """UploadEntry should store data correctly."""
        entry = UploadEntry(
            ref="upload-123",
            upload_name="images",
            client_name="test.jpg",
            client_size=1024,
            client_type="image/jpeg",
        )
        assert entry.ref == "upload-123"
        assert entry.client_name == "test.jpg"
        assert entry.status == UploadStatus.PENDING
        assert entry.progress == 0

    @pytest.mark.unit
    def test_entry_defaults(self):
        """UploadEntry should have sensible defaults."""
        entry = UploadEntry(
            ref="upload-123",
            upload_name="images",
            client_name="test.jpg",
            client_size=1024,
            client_type="image/jpeg",
        )
        assert entry.errors == []
        assert entry.upload_token == ""
        assert entry.temp_path is None
        assert entry.bytes_received == 0

    @pytest.mark.unit
    def test_to_client_dict(self):
        """to_client_dict should serialize correctly."""
        entry = UploadEntry(
            ref="upload-123",
            upload_name="images",
            client_name="test.jpg",
            client_size=1024,
            client_type="image/jpeg",
            status=UploadStatus.UPLOADING,
            progress=50,
        )
        data = entry.to_client_dict()
        assert data["ref"] == "upload-123"
        assert data["name"] == "test.jpg"
        assert data["size"] == 1024
        assert data["status"] == "uploading"
        assert data["progress"] == 50

    @pytest.mark.unit
    def test_cleanup(self):
        """cleanup should remove temp file."""
        # Create a temp file
        fd, path = tempfile.mkstemp()
        import os

        os.close(fd)

        entry = UploadEntry(
            ref="test",
            upload_name="files",
            client_name="test.txt",
            client_size=100,
            client_type="text/plain",
            temp_path=Path(path),
        )
        assert entry.temp_path.exists()

        entry.cleanup()
        assert not Path(path).exists()


class TestUploadOp:
    """Test the UploadOp dataclass."""

    @pytest.mark.unit
    def test_config_op_to_payload(self):
        """Config op should serialize correctly."""
        op = UploadOp(
            op="config",
            upload="images",
            data={"accept": [".jpg"], "max_entries": 5},
        )
        payload = op.to_payload()

        assert payload["op"] == "config"
        assert payload["upload"] == "images"
        assert payload["accept"] == [".jpg"]

    @pytest.mark.unit
    def test_registered_op_to_payload(self):
        """Registered op should include ref."""
        op = UploadOp(
            op="registered",
            upload="images",
            ref="upload-123",
            data={"token": "abc123", "chunk_size": 64000},
        )
        payload = op.to_payload()

        assert payload["op"] == "registered"
        assert payload["ref"] == "upload-123"
        assert payload["token"] == "abc123"

    @pytest.mark.unit
    def test_progress_op_to_payload(self):
        """Progress op should include progress data."""
        op = UploadOp(
            op="progress",
            upload="images",
            ref="upload-123",
            data={"progress": 50, "bytes_received": 512},
        )
        payload = op.to_payload()

        assert payload["op"] == "progress"
        assert payload["ref"] == "upload-123"
        assert payload["progress"] == 50

    @pytest.mark.unit
    def test_error_op_to_payload(self):
        """Error op should include errors."""
        op = UploadOp(
            op="error",
            upload="images",
            ref="upload-123",
            data={"errors": ["File too large"]},
        )
        payload = op.to_payload()

        assert payload["op"] == "error"
        assert payload["errors"] == ["File too large"]


class TestUploadRegistry:
    """Test the UploadRegistry class."""

    @pytest.mark.unit
    def test_registry_creation(self):
        """Registry should initialize correctly."""
        registry = UploadRegistry("comp-1")
        assert registry.component_id == "comp-1"
        assert registry.configs == {}

    @pytest.mark.unit
    def test_allow_upload(self):
        """allow_upload should register config."""
        registry = UploadRegistry("comp-1")
        config = UploadConfig(name="files", max_entries=3)
        registry.allow_upload(config)

        assert "files" in registry.configs
        assert registry.configs["files"] == config
        assert "files" in registry.entries

    @pytest.mark.unit
    def test_add_entry_generates_token(self):
        """add_entry should generate a signed token."""
        registry = UploadRegistry("comp-1")
        registry.allow_upload(UploadConfig(name="files"))

        entry = UploadEntry(
            ref="entry-1",
            upload_name="files",
            client_name="test.txt",
            client_size=100,
            client_type="text/plain",
        )
        token = registry.add_entry("files", entry)

        assert token
        assert entry.upload_token == token

    @pytest.mark.unit
    def test_validate_token(self):
        """Token validation should work correctly."""
        registry = UploadRegistry("comp-1")
        registry.allow_upload(UploadConfig(name="files"))

        entry = UploadEntry(
            ref="entry-1",
            upload_name="files",
            client_name="test.txt",
            client_size=100,
            client_type="text/plain",
        )
        token = registry.add_entry("files", entry)

        result = registry.validate_token(token)
        assert result == ("", "comp-1", "files", "entry-1")

    @pytest.mark.unit
    def test_validate_token_carries_the_owner(self):
        """The connection that owns the registry is part of the signed token."""
        registry = UploadRegistry("comp-1", connection_id="conn-a")
        registry.allow_upload(UploadConfig(name="files"))

        entry = UploadEntry(
            ref="entry-1",
            upload_name="files",
            client_name="test.txt",
            client_size=100,
            client_type="text/plain",
        )
        token = registry.add_entry("files", entry)

        assert registry.validate_token(token) == ("conn-a", "comp-1", "files", "entry-1")

    @pytest.mark.unit
    def test_validate_token_invalid(self):
        """Invalid token should return None."""
        registry = UploadRegistry("comp-1")
        result = registry.validate_token("invalid-token")
        assert result is None

    @pytest.mark.unit
    def test_max_entries_enforced(self):
        """Should reject entries when max is reached."""
        registry = UploadRegistry("comp-1")
        registry.allow_upload(UploadConfig(name="files", max_entries=1))

        entry1 = UploadEntry(
            ref="e1",
            upload_name="files",
            client_name="a.txt",
            client_size=100,
            client_type="text/plain",
        )
        registry.add_entry("files", entry1)

        entry2 = UploadEntry(
            ref="e2",
            upload_name="files",
            client_name="b.txt",
            client_size=100,
            client_type="text/plain",
        )

        with pytest.raises(ValueError, match="Maximum entries"):
            registry.add_entry("files", entry2)

    @pytest.mark.unit
    def test_get_entry(self):
        """get_entry should return correct entry."""
        registry = UploadRegistry("comp-1")
        registry.allow_upload(UploadConfig(name="files"))

        entry = UploadEntry(
            ref="entry-1",
            upload_name="files",
            client_name="test.txt",
            client_size=100,
            client_type="text/plain",
        )
        registry.add_entry("files", entry)

        result = registry.get_entry("files", "entry-1")
        assert result == entry

    @pytest.mark.unit
    def test_get_entry_not_found(self):
        """get_entry should return None for missing entry."""
        registry = UploadRegistry("comp-1")
        result = registry.get_entry("files", "nonexistent")
        assert result is None

    @pytest.mark.unit
    def test_cancel_entry(self):
        """cancel_entry should mark entry as cancelled."""
        registry = UploadRegistry("comp-1")
        registry.allow_upload(UploadConfig(name="files"))

        entry = UploadEntry(
            ref="entry-1",
            upload_name="files",
            client_name="test.txt",
            client_size=100,
            client_type="text/plain",
        )
        registry.add_entry("files", entry)
        result = registry.cancel_entry("files", "entry-1")

        assert result == entry
        assert entry.status == UploadStatus.CANCELLED

    @pytest.mark.unit
    def test_get_completed_entries(self):
        """get_completed_entries should return only completed entries."""
        registry = UploadRegistry("comp-1")
        registry.allow_upload(UploadConfig(name="files", max_entries=5))

        for i, status in enumerate([UploadStatus.PENDING, UploadStatus.COMPLETED, UploadStatus.COMPLETED]):
            entry = UploadEntry(
                ref=f"e{i}",
                upload_name="files",
                client_name=f"file{i}.txt",
                client_size=100,
                client_type="text/plain",
                status=status,
            )
            registry.entries["files"][entry.ref] = entry

        completed = registry.get_completed_entries("files")
        assert len(completed) == 2


class TestMagicBytesValidation:
    """Test magic bytes validation."""

    @pytest.mark.unit
    def test_validate_jpeg(self):
        """JPEG file should validate correctly."""
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF")
            path = Path(f.name)

        try:
            assert validate_magic_bytes(path, ".jpg") is True
            assert validate_magic_bytes(path, ".png") is False
        finally:
            path.unlink()

    @pytest.mark.unit
    def test_validate_png(self):
        """PNG file should validate correctly."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00")
            path = Path(f.name)

        try:
            assert validate_magic_bytes(path, ".png") is True
            assert validate_magic_bytes(path, ".jpg") is False
        finally:
            path.unlink()

    @pytest.mark.unit
    def test_validate_unknown_type(self):
        """Unknown file types should pass validation."""
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"random content")
            path = Path(f.name)

        try:
            assert validate_magic_bytes(path, ".xyz") is True
        finally:
            path.unlink()

    @pytest.mark.unit
    def test_validate_missing_file(self):
        """Missing file should fail validation."""
        result = validate_magic_bytes(Path("/nonexistent/file.jpg"), ".jpg")
        assert result is False


class TestHelperFunctions:
    """Test helper functions."""

    @pytest.mark.unit
    def test_generate_ref(self):
        """generate_ref should create unique references."""
        ref1 = generate_ref()
        ref2 = generate_ref()

        assert ref1.startswith("upload-")
        assert ref2.startswith("upload-")
        assert ref1 != ref2

    @pytest.mark.unit
    def test_create_temp_file(self):
        """create_temp_file should create a file."""
        path = create_temp_file("test-ref")

        try:
            assert path.exists()
            assert "wireview_test-ref_" in path.name
            assert path.suffix == ".upload"
        finally:
            path.unlink()


class TestConsumedUpload:
    """Test the ConsumedUpload class."""

    @pytest.mark.unit
    def test_properties(self):
        """ConsumedUpload should expose entry properties."""
        entry = UploadEntry(
            ref="test-1",
            upload_name="images",
            client_name="photo.jpg",
            client_size=1024,
            client_type="image/jpeg",
        )
        upload = ConsumedUpload(entry)

        assert upload.name == "photo.jpg"
        assert upload.size == 1024
        assert upload.content_type == "image/jpeg"
        assert upload.ref == "test-1"

    @pytest.mark.unit
    def test_read(self):
        """read should return file contents."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = Path(f.name)

        entry = UploadEntry(
            ref="test-1",
            upload_name="files",
            client_name="test.txt",
            client_size=12,
            client_type="text/plain",
            temp_path=path,
        )
        upload = ConsumedUpload(entry)

        try:
            content = upload.read()
            assert content == b"test content"
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.unit
    def test_read_missing_file(self):
        """read should raise FileNotFoundError for missing file."""
        entry = UploadEntry(
            ref="test-1",
            upload_name="files",
            client_name="test.txt",
            client_size=12,
            client_type="text/plain",
            temp_path=None,
        )
        upload = ConsumedUpload(entry)

        with pytest.raises(FileNotFoundError):
            upload.read()

    @pytest.mark.unit
    def test_context_manager_cleanup(self):
        """Context manager should cleanup on exit."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = Path(f.name)

        entry = UploadEntry(
            ref="test-1",
            upload_name="files",
            client_name="test.txt",
            client_size=12,
            client_type="text/plain",
            temp_path=path,
        )

        with ConsumedUpload(entry) as upload:
            assert upload.read() == b"test content"
            # Not consumed, so cleanup should happen on exit

        assert not path.exists()


# =============================================================================
# Component Upload Method Tests
# =============================================================================


class UploadComponent(Component):
    """Test component with upload functionality."""

    _template_name = "uploads/uploader.html"

    async def joined(self):
        """Configure uploads on join."""
        self.allow_upload(
            "images",
            accept=[".jpg", ".png"],
            max_entries=5,
            max_file_size=5 * 1024 * 1024,
        )

    async def save_images(self):
        """Consume and save uploaded images."""
        saved = []
        async for upload in self.consume_uploads("images"):
            saved.append(upload.name)
        return saved

    async def cancel_image(self, ref: str):
        """Cancel an upload."""
        await self.cancel_upload("images", ref)


class UploadComponentNoJoined(Component):
    """Test component without auto-upload in joined."""

    _template_name = "uploads/uploader.html"


class TestComponentUploadMethods:
    """Test Component upload methods."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_allow_upload_creates_registry(self):
        """allow_upload should create an upload registry."""
        view = await mount(UploadComponent)

        assert hasattr(view.component, "_upload_registry")
        assert view.component._upload_registry is not None
        assert "images" in view.component._upload_registry.configs

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_allow_upload_config_values(self):
        """allow_upload should store correct config values."""
        view = await mount(UploadComponent)

        config = view.component._upload_registry.configs["images"]
        assert config.accept == [".jpg", ".png"]
        assert config.max_entries == 5
        assert config.max_file_size == 5 * 1024 * 1024

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_allow_upload_sends_config_message(self):
        """allow_upload should send config to client."""
        view = await mount(UploadComponent)

        # Wait a bit for async task to complete
        import asyncio

        await asyncio.sleep(0.01)

        # Check that upload_op config message was sent
        upload_messages = [m for m in view.sent_messages if m.get("type") == "upload_op"]
        assert len(upload_messages) >= 1

        config_msg = upload_messages[0]
        assert config_msg["op"] == "config"
        assert config_msg["upload"] == "images"
        assert ".jpg" in config_msg["accept"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_uploads_property_empty(self):
        """uploads property should return empty dict when no registry."""
        view = await mount(UploadComponentNoJoined)

        uploads = view.component.uploads
        assert uploads == {}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_uploads_property_with_registry(self):
        """uploads property should return entries grouped by name."""
        view = await mount(UploadComponent)

        uploads = view.component.uploads
        assert isinstance(uploads, dict)
        assert "images" in uploads
        assert isinstance(uploads["images"], list)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_uploads_property_with_entries(self):
        """uploads property should include registered entries."""
        view = await mount(UploadComponent)

        # Manually add an entry
        entry = UploadEntry(
            ref="test-entry",
            upload_name="images",
            client_name="photo.jpg",
            client_size=1024,
            client_type="image/jpeg",
        )
        view.component._upload_registry.add_entry("images", entry)

        uploads = view.component.uploads
        assert len(uploads["images"]) == 1
        assert uploads["images"][0].ref == "test-entry"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_consume_uploads_empty(self):
        """consume_uploads should yield nothing when no completed uploads."""
        view = await mount(UploadComponent)

        consumed = []
        async for upload in view.component.consume_uploads("images"):
            consumed.append(upload)

        assert consumed == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_consume_uploads_with_completed(self):
        """consume_uploads should yield completed entries."""
        view = await mount(UploadComponent)

        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test image data")
            temp_path = Path(f.name)

        try:
            # Add a completed entry
            entry = UploadEntry(
                ref="test-entry",
                upload_name="images",
                client_name="photo.jpg",
                client_size=15,
                client_type="image/jpeg",
                status=UploadStatus.COMPLETED,
                temp_path=temp_path,
            )
            view.component._upload_registry.entries["images"]["test-entry"] = entry

            consumed = []
            async for upload in view.component.consume_uploads("images"):
                consumed.append(upload.name)

            assert consumed == ["photo.jpg"]
        finally:
            temp_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_consume_uploads_no_registry(self):
        """consume_uploads should handle missing registry gracefully."""
        view = await mount(UploadComponentNoJoined)

        consumed = []
        async for upload in view.component.consume_uploads("images"):
            consumed.append(upload)

        assert consumed == []

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cancel_upload(self):
        """cancel_upload should mark entry as cancelled."""
        view = await mount(UploadComponent)
        view.clear_messages()

        # Add an entry
        entry = UploadEntry(
            ref="test-entry",
            upload_name="images",
            client_name="photo.jpg",
            client_size=1024,
            client_type="image/jpeg",
        )
        view.component._upload_registry.add_entry("images", entry)

        # Cancel it
        await view.call("cancel_image", ref="test-entry")

        # Check status
        cancelled_entry = view.component._upload_registry.get_entry("images", "test-entry")
        assert cancelled_entry.status == UploadStatus.CANCELLED

        # Check that cancel message was sent
        cancel_messages = [m for m in view.sent_messages if m.get("type") == "upload_op" and m.get("op") == "cancel"]
        assert len(cancel_messages) == 1

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cancel_upload_no_registry(self):
        """cancel_upload should handle missing registry gracefully."""
        view = await mount(UploadComponentNoJoined)

        # Should not raise
        await view.component.cancel_upload("images", "nonexistent")


# =============================================================================
# Consumer Upload Handler Tests
# =============================================================================


class TestConsumerUploadHandlers:
    """Test consumer upload command handlers."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_register_creates_entries(self):
        """command_upload_register should create entries and return tokens."""
        from unittest.mock import AsyncMock, MagicMock

        from wireview.consumer import WireviewConsumer

        # Create mock consumer
        consumer = WireviewConsumer()
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()
        consumer.subscriptions = set()

        # Create a component with upload registry
        component = await mount(UploadComponent)
        consumer.repo = MagicMock()
        consumer.repo.get = MagicMock(return_value=component.component)

        # Mock send_command and send_render
        consumer.send_command = AsyncMock()
        consumer.send_render = AsyncMock()

        # Register uploads
        await consumer.command_upload_register(
            id=component.component.id,
            name="images",
            entries=[
                {
                    "ref": "upload-1",
                    "name": "photo.jpg",
                    "size": 1024,
                    "type": "image/jpeg",
                }
            ],
        )

        # Check entry was added
        entry = component.component._upload_registry.get_entry("images", "upload-1")
        assert entry is not None
        assert entry.client_name == "photo.jpg"
        assert entry.upload_token != ""

        # Check registered message was sent
        consumer.send_command.assert_called()
        call_args = consumer.send_command.call_args_list
        upload_calls = [c for c in call_args if c[0][0] == "upload_op"]
        assert len(upload_calls) >= 1

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_register_validation_error(self):
        """command_upload_register should handle validation errors."""
        from unittest.mock import AsyncMock, MagicMock

        from wireview.consumer import WireviewConsumer

        consumer = WireviewConsumer()
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()

        component = await mount(UploadComponent)
        consumer.repo = MagicMock()
        consumer.repo.get = MagicMock(return_value=component.component)
        consumer.send_command = AsyncMock()
        consumer.send_render = AsyncMock()

        # Register with invalid file type
        await consumer.command_upload_register(
            id=component.component.id,
            name="images",
            entries=[
                {
                    "ref": "upload-1",
                    "name": "document.pdf",  # Invalid type
                    "size": 1024,
                    "type": "application/pdf",
                }
            ],
        )

        # Entry should have error status
        entry = component.component._upload_registry.get_entry("images", "upload-1")
        assert entry.status == UploadStatus.ERROR
        assert len(entry.errors) > 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_cancel_handler(self):
        """command_upload_cancel should cancel the upload."""
        from unittest.mock import AsyncMock, MagicMock

        from wireview.consumer import WireviewConsumer

        consumer = WireviewConsumer()
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()

        component = await mount(UploadComponent)
        consumer.repo = MagicMock()
        consumer.repo.get = MagicMock(return_value=component.component)
        consumer.send_command = AsyncMock()
        consumer.send_render = AsyncMock()

        # Add an entry first
        entry = UploadEntry(
            ref="upload-1",
            upload_name="images",
            client_name="photo.jpg",
            client_size=1024,
            client_type="image/jpeg",
        )
        component.component._upload_registry.add_entry("images", entry)

        # Cancel it
        await consumer.command_upload_cancel(id=component.component.id, name="images", ref="upload-1")

        # Check status
        cancelled = component.component._upload_registry.get_entry("images", "upload-1")
        assert cancelled.status == UploadStatus.CANCELLED

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_complete_handler(self):
        """command_upload_complete should mark upload as completed."""
        from unittest.mock import AsyncMock, MagicMock

        from wireview.consumer import WireviewConsumer

        consumer = WireviewConsumer()
        consumer.channel_name = "test-channel"
        consumer.channel_layer = MagicMock()

        component = await mount(UploadComponent)
        consumer.repo = MagicMock()
        consumer.repo.get = MagicMock(return_value=component.component)
        consumer.send_command = AsyncMock()
        consumer.send_render = AsyncMock()

        # Add an uploading entry
        entry = UploadEntry(
            ref="upload-1",
            upload_name="images",
            client_name="photo.jpg",
            client_size=1024,
            client_type="image/jpeg",
            status=UploadStatus.UPLOADING,
        )
        component.component._upload_registry.entries["images"]["upload-1"] = entry

        # Complete it
        await consumer.command_upload_complete(id=component.component.id, name="images", ref="upload-1")

        # Check status
        completed = component.component._upload_registry.get_entry("images", "upload-1")
        assert completed.status == UploadStatus.COMPLETED
        assert completed.progress == 100


# =============================================================================
# HTTP UploadView Tests
# =============================================================================


class TestUploadView:
    """Test HTTP upload endpoint."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_view_missing_registry(self):
        """Upload view should return 404 for missing component."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView

        factory = AsyncRequestFactory()
        request = factory.post(
            "/__wireview_upload__/conn-1/nonexistent/images/",
            data=b"chunk data",
            content_type="application/octet-stream",
        )
        request.META["HTTP_X_UPLOAD_TOKEN"] = "invalid"
        request.META["HTTP_X_CHUNK_INDEX"] = "0"
        request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
        request.META["HTTP_X_ENTRY_REF"] = "upload-1"

        view = UploadView()
        response = await view.post(request, "conn-1", "nonexistent", "images")

        assert response.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_view_invalid_token(self):
        """Upload view should return 403 for invalid token."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView, register_upload_registry

        # Register a valid registry
        registry = UploadRegistry("comp-123", connection_id="conn-1")
        registry.allow_upload(UploadConfig(name="images"))
        register_upload_registry("conn-1", "comp-123", registry)

        try:
            factory = AsyncRequestFactory()
            request = factory.post(
                "/__wireview_upload__/conn-1/comp-123/images/",
                data=b"chunk data",
                content_type="application/octet-stream",
            )
            request.META["HTTP_X_UPLOAD_TOKEN"] = "invalid-token"
            request.META["HTTP_X_CHUNK_INDEX"] = "0"
            request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
            request.META["HTTP_X_ENTRY_REF"] = "upload-1"

            view = UploadView()
            response = await view.post(request, "conn-1", "comp-123", "images")

            assert response.status_code == 403
        finally:
            from wireview.views import unregister_upload_registry

            unregister_upload_registry("conn-1", "comp-123")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_view_valid_chunk(self):
        """Upload view should accept valid chunks."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView, register_upload_registry

        # Create registry with entry
        registry = UploadRegistry("comp-456", connection_id="conn-1")
        registry.allow_upload(UploadConfig(name="images", accept=[".jpg"]))

        entry = UploadEntry(
            ref="upload-1",
            upload_name="images",
            client_name="photo.jpg",
            client_size=100,
            client_type="image/jpeg",
        )
        token = registry.add_entry("images", entry)
        register_upload_registry("conn-1", "comp-456", registry)

        try:
            factory = AsyncRequestFactory()
            request = factory.post(
                "/__wireview_upload__/conn-1/comp-456/images/",
                data=b"x" * 50,  # First chunk
                content_type="application/octet-stream",
            )
            request.META["HTTP_X_UPLOAD_TOKEN"] = token
            request.META["HTTP_X_CHUNK_INDEX"] = "0"
            request.META["HTTP_X_TOTAL_CHUNKS"] = "2"
            request.META["HTTP_X_ENTRY_REF"] = "upload-1"

            view = UploadView()
            response = await view.post(request, "conn-1", "comp-456", "images")

            assert response.status_code == 200

            import json

            data = json.loads(response.content)
            assert data["status"] == "ok"
            assert data["bytes_received"] == 50
            assert entry.temp_path is not None
            assert entry.temp_path.exists()
        finally:
            from wireview.views import unregister_upload_registry

            unregister_upload_registry("conn-1", "comp-456")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_view_complete_upload(self):
        """Upload view should mark upload as complete when all chunks received."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView, register_upload_registry

        # Create registry with entry
        registry = UploadRegistry("comp-789", connection_id="conn-1")
        registry.allow_upload(UploadConfig(name="images", accept=[".txt"]))

        entry = UploadEntry(
            ref="upload-1",
            upload_name="images",
            client_name="test.txt",
            client_size=10,
            client_type="text/plain",
        )
        token = registry.add_entry("images", entry)
        register_upload_registry("conn-1", "comp-789", registry)

        try:
            factory = AsyncRequestFactory()
            request = factory.post(
                "/__wireview_upload__/conn-1/comp-789/images/",
                data=b"0123456789",  # All data in one chunk
                content_type="application/octet-stream",
            )
            request.META["HTTP_X_UPLOAD_TOKEN"] = token
            request.META["HTTP_X_CHUNK_INDEX"] = "0"
            request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
            request.META["HTTP_X_ENTRY_REF"] = "upload-1"

            view = UploadView()
            response = await view.post(request, "conn-1", "comp-789", "images")

            assert response.status_code == 200

            import json

            data = json.loads(response.content)
            assert data["complete"] is True
            assert entry.status == UploadStatus.COMPLETED
        finally:
            from wireview.views import unregister_upload_registry

            unregister_upload_registry("conn-1", "comp-789")
            if entry.temp_path:
                entry.cleanup()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_view_cancelled_entry(self):
        """Upload view should return 410 for cancelled uploads."""
        from django.test import AsyncRequestFactory

        from wireview.views import UploadView, register_upload_registry

        # Create registry with cancelled entry
        registry = UploadRegistry("comp-cancelled", connection_id="conn-1")
        registry.allow_upload(UploadConfig(name="images"))

        entry = UploadEntry(
            ref="upload-1",
            upload_name="images",
            client_name="photo.jpg",
            client_size=100,
            client_type="image/jpeg",
            status=UploadStatus.CANCELLED,
        )
        token = registry.add_entry("images", entry)
        # Manually set status after adding (since add_entry validates)
        entry.status = UploadStatus.CANCELLED
        register_upload_registry("conn-1", "comp-cancelled", registry)

        try:
            factory = AsyncRequestFactory()
            request = factory.post(
                "/__wireview_upload__/conn-1/comp-cancelled/images/",
                data=b"chunk",
                content_type="application/octet-stream",
            )
            request.META["HTTP_X_UPLOAD_TOKEN"] = token
            request.META["HTTP_X_CHUNK_INDEX"] = "0"
            request.META["HTTP_X_TOTAL_CHUNKS"] = "1"
            request.META["HTTP_X_ENTRY_REF"] = "upload-1"

            view = UploadView()
            response = await view.post(request, "conn-1", "comp-cancelled", "images")

            assert response.status_code == 410
        finally:
            from wireview.views import unregister_upload_registry

            unregister_upload_registry("conn-1", "comp-cancelled")
