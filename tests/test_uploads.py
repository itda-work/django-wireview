"""Tests for Upload functionality."""

import tempfile
from pathlib import Path

import pytest

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
        assert result == ("comp-1", "files", "entry-1")

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
