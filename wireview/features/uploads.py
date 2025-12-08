"""Uploads module for file upload handling.

This module provides Phoenix LiveView-style file uploads for wireview components.
Supports chunked uploads, progress tracking, and drag-and-drop.
"""

from __future__ import annotations

import secrets
import tempfile
import typing as t
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from django.core.signing import TimestampSigner

# Magic bytes for file type validation
MAGIC_BYTES: dict[str, list[bytes]] = {
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".pdf": [b"%PDF"],
    ".webp": [b"RIFF"],  # RIFF....WEBP
    ".bmp": [b"BM"],
    ".ico": [b"\x00\x00\x01\x00"],
    ".svg": [b"<?xml", b"<svg"],
    ".mp3": [b"ID3", b"\xff\xfb", b"\xff\xfa"],
    ".mp4": [b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x20ftyp", b"ftyp"],
    ".zip": [b"PK\x03\x04"],
}


def validate_magic_bytes(file_path: Path, extension: str) -> bool:
    """Validate file signature matches extension.

    Args:
        file_path: Path to the file to validate
        extension: Expected file extension (e.g., ".jpg")

    Returns:
        True if file signature matches or extension is unknown
    """
    signatures = MAGIC_BYTES.get(extension.lower(), [])
    if not signatures:
        return True  # Unknown type, skip validation

    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
    except (OSError, IOError):
        return False

    return any(header.startswith(sig) for sig in signatures)


class UploadStatus(str, Enum):
    """Status of an upload entry."""

    PENDING = "pending"  # Registered, not yet started
    UPLOADING = "uploading"  # Currently uploading chunks
    COMPLETED = "completed"  # All chunks received
    CONSUMED = "consumed"  # Saved by application
    CANCELLED = "cancelled"  # Cancelled by user
    ERROR = "error"  # Failed


@dataclass
class UploadConfig:
    """Configuration for an upload field.

    Follows Phoenix LiveView allow_upload pattern.

    Attributes:
        name: Upload field name
        accept: List of accepted file extensions (e.g., [".jpg", ".png"])
        max_entries: Maximum number of concurrent uploads
        max_file_size: Maximum file size in bytes (default 10MB)
        chunk_size: Chunk size for uploads (default 64KB)
        auto_upload: Start upload immediately when files are selected
    """

    name: str
    accept: list[str] = field(default_factory=list)
    max_entries: int = 1
    max_file_size: int = 10 * 1024 * 1024  # 10MB default
    chunk_size: int = 64 * 1024  # 64KB chunks
    auto_upload: bool = True

    def validate_entry(self, entry: UploadEntry) -> list[str]:
        """Validate an entry against this config.

        Args:
            entry: The upload entry to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors: list[str] = []

        # Check file extension
        if self.accept:
            ext = Path(entry.client_name).suffix.lower()
            accepted_lower = [a.lower() for a in self.accept]
            if ext not in accepted_lower:
                errors.append(f"Invalid file type: {ext}")

        # Check file size
        if entry.client_size > self.max_file_size:
            size_mb = entry.client_size / (1024 * 1024)
            max_mb = self.max_file_size / (1024 * 1024)
            errors.append(f"File too large: {size_mb:.1f}MB > {max_mb:.1f}MB")

        return errors

    def to_client_dict(self, endpoint: str) -> dict[str, t.Any]:
        """Convert to dict for sending to client.

        Args:
            endpoint: The upload endpoint URL

        Returns:
            Configuration dict for client
        """
        return {
            "accept": self.accept,
            "max_entries": self.max_entries,
            "max_file_size": self.max_file_size,
            "chunk_size": self.chunk_size,
            "auto_upload": self.auto_upload,
            "endpoint": endpoint,
        }


@dataclass
class UploadEntry:
    """Represents a single file upload entry.

    Tracks upload state, progress, and metadata.

    Attributes:
        ref: Unique reference for this entry
        upload_name: Name of the upload config
        client_name: Original filename from client
        client_size: File size in bytes
        client_type: MIME type
        status: Current upload status
        progress: Upload progress (0-100)
        errors: List of error messages
        upload_token: Signed token for HTTP upload
        temp_path: Temporary file path
        bytes_received: Number of bytes received
        chunk_count: Number of chunks received
        created_at: Entry creation timestamp
    """

    ref: str
    upload_name: str
    client_name: str
    client_size: int
    client_type: str

    status: UploadStatus = UploadStatus.PENDING
    progress: int = 0
    errors: list[str] = field(default_factory=list)

    # Server-side tracking
    upload_token: str = ""
    temp_path: Path | None = None
    bytes_received: int = 0
    chunk_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)

    def to_client_dict(self) -> dict[str, t.Any]:
        """Serialize for sending to client.

        Returns:
            Entry data suitable for JSON serialization
        """
        return {
            "ref": self.ref,
            "name": self.client_name,
            "size": self.client_size,
            "type": self.client_type,
            "status": self.status.value,
            "progress": self.progress,
            "errors": self.errors,
        }

    def cleanup(self) -> None:
        """Clean up temporary file if it exists."""
        if self.temp_path and self.temp_path.exists():
            try:
                self.temp_path.unlink()
            except OSError:
                pass


@dataclass
class UploadOp:
    """Upload operation to send to client.

    Similar to StreamOp pattern.

    Attributes:
        op: Operation type (config, registered, progress, complete, error, cancel)
        upload: Upload config name
        ref: Entry reference (optional for config op)
        data: Additional operation data
    """

    op: t.Literal["config", "registered", "progress", "complete", "error", "cancel"]
    upload: str
    ref: str | None = None
    data: dict[str, t.Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, t.Any]:
        """Convert to payload dict for WebSocket transmission.

        Returns:
            Payload dict suitable for JSON serialization
        """
        payload: dict[str, t.Any] = {"op": self.op, "upload": self.upload}
        if self.ref:
            payload["ref"] = self.ref
        if self.data:
            payload.update(self.data)
        return payload


class UploadRegistry:
    """Per-component upload configuration and entry registry.

    Manages upload configurations and entries for a single component.
    Handles token generation and validation for secure uploads.

    Attributes:
        component_id: ID of the component this registry belongs to
        configs: Mapping of config names to UploadConfig objects
        entries: Mapping of config names to entry dicts (ref -> UploadEntry)
    """

    def __init__(self, component_id: str) -> None:
        """Initialize the registry.

        Args:
            component_id: ID of the owning component
        """
        self.component_id = component_id
        self.configs: dict[str, UploadConfig] = {}
        self.entries: dict[str, dict[str, UploadEntry]] = {}
        self._signer = TimestampSigner()

    def allow_upload(self, config: UploadConfig) -> None:
        """Register an upload configuration.

        Args:
            config: Upload configuration to register
        """
        self.configs[config.name] = config
        self.entries[config.name] = {}

    def add_entry(self, config_name: str, entry: UploadEntry) -> str:
        """Add an upload entry and generate a token.

        Args:
            config_name: Name of the upload config
            entry: Entry to add

        Returns:
            Signed upload token

        Raises:
            ValueError: If config is unknown or max entries reached
        """
        if config_name not in self.configs:
            raise ValueError(f"Unknown upload config: {config_name}")

        config = self.configs[config_name]

        # Check max entries
        active_entries = [
            e
            for e in self.entries[config_name].values()
            if e.status not in (UploadStatus.CONSUMED, UploadStatus.CANCELLED, UploadStatus.ERROR)
        ]
        if len(active_entries) >= config.max_entries:
            raise ValueError(f"Maximum entries reached: {config.max_entries}")

        # Validate entry
        errors = config.validate_entry(entry)
        if errors:
            entry.errors = errors
            entry.status = UploadStatus.ERROR

        # Generate signed token
        token_data = f"{self.component_id}:{config_name}:{entry.ref}"
        entry.upload_token = self._signer.sign(token_data)

        self.entries[config_name][entry.ref] = entry
        return entry.upload_token

    def get_entry(self, config_name: str, ref: str) -> UploadEntry | None:
        """Get an entry by config name and ref.

        Args:
            config_name: Name of the upload config
            ref: Entry reference

        Returns:
            The entry if found, None otherwise
        """
        return self.entries.get(config_name, {}).get(ref)

    def cancel_entry(self, config_name: str, ref: str) -> UploadEntry | None:
        """Cancel an upload entry and clean up temp file.

        Args:
            config_name: Name of the upload config
            ref: Entry reference

        Returns:
            The cancelled entry if found, None otherwise
        """
        entry = self.get_entry(config_name, ref)
        if entry:
            entry.status = UploadStatus.CANCELLED
            entry.cleanup()
        return entry

    def validate_token(self, token: str, max_age: int = 3600) -> tuple[str, str, str] | None:
        """Validate upload token.

        Args:
            token: Signed token to validate
            max_age: Maximum token age in seconds (default 1 hour)

        Returns:
            Tuple of (component_id, config_name, ref) if valid, None otherwise
        """
        try:
            data = self._signer.unsign(token, max_age=max_age)
            parts = data.split(":")
            if len(parts) == 3:
                return (parts[0], parts[1], parts[2])
        except Exception:
            pass
        return None

    def get_entries(self, config_name: str) -> list[UploadEntry]:
        """Get all entries for a config.

        Args:
            config_name: Name of the upload config

        Returns:
            List of all entries for this config
        """
        return list(self.entries.get(config_name, {}).values())

    def get_completed_entries(self, config_name: str) -> list[UploadEntry]:
        """Get all completed (ready to consume) entries.

        Args:
            config_name: Name of the upload config

        Returns:
            List of entries in COMPLETED status
        """
        return [e for e in self.get_entries(config_name) if e.status == UploadStatus.COMPLETED]

    def cleanup_all(self) -> None:
        """Clean up all temporary files."""
        for entries in self.entries.values():
            for entry in entries.values():
                entry.cleanup()


class ConsumedUpload:
    """Wrapper for consuming a completed upload.

    Provides convenient methods to read and save uploaded files.

    Attributes:
        entry: The underlying UploadEntry
    """

    def __init__(self, entry: UploadEntry) -> None:
        """Initialize the wrapper.

        Args:
            entry: The upload entry to wrap
        """
        self.entry = entry
        self._consumed = False

    @property
    def name(self) -> str:
        """Original filename."""
        return self.entry.client_name

    @property
    def size(self) -> int:
        """File size in bytes."""
        return self.entry.client_size

    @property
    def content_type(self) -> str:
        """MIME type."""
        return self.entry.client_type

    @property
    def ref(self) -> str:
        """Entry reference."""
        return self.entry.ref

    def read(self) -> bytes:
        """Read the entire file into memory.

        Returns:
            File contents as bytes

        Raises:
            FileNotFoundError: If upload file doesn't exist
        """
        if not self.entry.temp_path or not self.entry.temp_path.exists():
            raise FileNotFoundError("Upload file not found")
        return self.entry.temp_path.read_bytes()

    def open(self, mode: str = "rb") -> t.IO[bytes]:
        """Open the temp file for reading.

        Args:
            mode: File open mode (default "rb")

        Returns:
            File handle

        Raises:
            FileNotFoundError: If upload file doesn't exist
        """
        if not self.entry.temp_path or not self.entry.temp_path.exists():
            raise FileNotFoundError("Upload file not found")
        return open(self.entry.temp_path, mode)

    async def save_to(self, directory: str | Path, filename: str | None = None) -> Path:
        """Save the upload to a directory using Django storage.

        Args:
            directory: Target directory path
            filename: Optional filename override (defaults to original name)

        Returns:
            Path where the file was saved

        Raises:
            RuntimeError: If upload was already consumed
        """
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        if self._consumed:
            raise RuntimeError("Upload already consumed")

        directory = Path(directory)
        filename = filename or self.entry.client_name

        # Validate magic bytes before saving
        if self.entry.temp_path:
            ext = Path(self.entry.client_name).suffix.lower()
            if not validate_magic_bytes(self.entry.temp_path, ext):
                raise ValueError(f"File content doesn't match extension: {ext}")

        # Use Django's storage backend
        final_path = str(directory / filename)

        with self.open("rb") as src:
            content = ContentFile(src.read())
            saved_path = default_storage.save(final_path, content)

        # Mark as consumed and cleanup
        self.entry.status = UploadStatus.CONSUMED
        self._consumed = True
        self.entry.cleanup()

        return Path(saved_path)

    def __enter__(self) -> "ConsumedUpload":
        """Context manager entry."""
        return self

    def __exit__(self, *args: t.Any) -> None:
        """Context manager exit - cleanup if not consumed."""
        if not self._consumed:
            self.entry.cleanup()


def generate_ref() -> str:
    """Generate a unique upload reference.

    Returns:
        Unique reference string
    """
    return f"upload-{secrets.token_urlsafe(12)}"


def create_temp_file(ref: str) -> Path:
    """Create a temporary file for upload.

    Args:
        ref: Upload reference (used in filename)

    Returns:
        Path to the created temp file
    """
    fd, path = tempfile.mkstemp(prefix=f"wireview_{ref}_", suffix=".upload")
    import os

    os.close(fd)
    return Path(path)
