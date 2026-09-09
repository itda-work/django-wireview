"""Uploads module for file upload handling.

This module provides Phoenix LiveView-style file uploads for wireview components.
Supports chunked uploads, progress tracking, and drag-and-drop.
"""

from __future__ import annotations

import re
import secrets
import typing as t
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from ..core.signing import get_signer
from . import upload_store

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


def upload_group_name(connection_id: str) -> str:
    """Name of the progress group for one connection.

    One group per connection, not per component: the consumer subscribes once in
    ``connect()`` and the client routes each update by the upload name and ref
    carried in the payload (#77).

    Args:
        connection_id: ID of the connection

    Returns:
        The channel-layer group name
    """
    return f"wireview_upload_{connection_id}"


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


#: Salt for the upload token. Separate from the state salt, so neither token can
#: be presented as the other even though both ride on the same key.
UPLOAD_SALT = "wireview.upload"

#: Version stored in the token. v2 carries what the stateless HTTP endpoint needs
#: to judge a chunk without consulting any registry (#83).
TOKEN_VERSION = 2


@dataclass(frozen=True)
class UploadToken:
    """What a signed upload token says, once it verifies.

    This is the whole authority behind a chunk. The HTTP endpoint holds no
    per-upload state -- that is what lets any worker serve the upload -- so
    everything it has to decide comes from here: which upload the chunk belongs
    to (and therefore which file it lands in), how many bytes may arrive, and
    which file type the finished bytes have to look like.

    Attributes:
        connection_id: Connection that owns the upload
        component_id: Component that declared it
        config_name: Name given to ``allow_upload()``
        ref: Entry reference
        max_bytes: Size the client announced at registration. The endpoint
            refuses to write past it and calls the upload complete on reaching it
        extension: Lowercased suffix of the client filename, for the magic-byte
            check on the last chunk
    """

    connection_id: str
    component_id: str
    config_name: str
    ref: str
    max_bytes: int
    extension: str = ""


def sign_upload_token(
    *,
    connection_id: str,
    component_id: str,
    config_name: str,
    ref: str,
    max_bytes: int,
    extension: str = "",
) -> str:
    """Sign one upload's identity for the HTTP endpoint.

    Signed as an object rather than a delimited string: component ids and upload
    names come from application templates, and a colon in either would make a
    packed string ambiguous exactly where it decides who may write what.

    Returns:
        The token the client sends back in ``X-Upload-Token``
    """
    return get_signer(UPLOAD_SALT).sign_object(
        {
            "v": TOKEN_VERSION,
            "conn": connection_id,
            "comp": component_id,
            "cfg": config_name,
            "ref": ref,
            "max": int(max_bytes),
            "ext": extension,
        },
        compress=True,
    )


def validate_upload_token(token: str, max_age: int | None = None) -> UploadToken | None:
    """Verify an upload token, on any worker.

    Verification needs the signing key and nothing else, which is the point: the
    worker holding the WebSocket and the worker receiving the chunk reach the
    same verdict without sharing state.

    Args:
        token: Value of the ``X-Upload-Token`` header
        max_age: Maximum token age in seconds (default ``UPLOAD_TOKEN_MAX_AGE``)

    Returns:
        The token's contents, or None if it does not verify
    """
    from .. import settings as wireview_settings

    if max_age is None:
        max_age = wireview_settings.UPLOAD_TOKEN_MAX_AGE
    signer = get_signer(UPLOAD_SALT)
    try:
        payload = signer.unsign_object(token, max_age=max_age)
    except Exception:
        return _validate_legacy_token(signer, token, max_age)

    if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
        return None
    try:
        return UploadToken(
            connection_id=str(payload["conn"]),
            component_id=str(payload["comp"]),
            config_name=str(payload["cfg"]),
            ref=str(payload["ref"]),
            max_bytes=int(payload["max"]),
            extension=str(payload.get("ext", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _validate_legacy_token(signer: t.Any, token: str, max_age: int) -> UploadToken | None:
    """Accept the pre-#83 ``conn:comp:config:ref`` token during a rolling deploy.

    A page rendered by an older worker holds tokens in the old shape, and its
    uploads should finish rather than fail on the way to the new one. The old
    token says nothing about size, so the global limit applies, and nothing about
    the filename, so the magic-byte check has no extension to go on -- both
    weaker than v2, which is why this is a transition, not a supported format.
    """
    from .. import settings as wireview_settings

    try:
        data = signer.unsign(token, max_age=max_age)
    except Exception:
        return None
    parts = data.split(":")
    if len(parts) != 4:
        return None
    return UploadToken(
        connection_id=parts[0],
        component_id=parts[1],
        config_name=parts[2],
        ref=parts[3],
        max_bytes=wireview_settings.UPLOAD_MAX_FILE_SIZE,
    )


class UploadStatus(str, Enum):
    """Status of an upload entry."""

    PENDING = "pending"  # Registered, not yet started
    UPLOADING = "uploading"  # Currently uploading chunks
    COMPLETED = "completed"  # All chunks received
    CONSUMED = "consumed"  # Saved by application
    CANCELLED = "cancelled"  # Cancelled by user
    ERROR = "error"  # Failed


# Type alias for external upload callback
# Signature: (entry: UploadEntry, component: Component) -> ExternalUploadMeta
ExternalUploadCallback = t.Callable[["UploadEntry", t.Any], "ExternalUploadMeta"]


@dataclass
class ExternalUploadMeta:
    """Metadata for external uploads (S3, GCS, etc.).

    Returned by the external upload callback to configure direct upload.

    Attributes:
        uploader: Name of the client-side uploader (e.g., "S3", "GCS")
        url: Presigned URL for direct upload
        method: HTTP method for upload (default "PUT")
        headers: Additional headers to include in upload request
    """

    uploader: str
    url: str
    method: str = "PUT"
    headers: dict[str, str] = field(default_factory=dict)

    def to_client_dict(self) -> dict[str, t.Any]:
        """Convert to dict for sending to client."""
        return {
            "uploader": self.uploader,
            "url": self.url,
            "method": self.method,
            "headers": self.headers,
        }


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
        external: Optional callback for external uploads (S3, GCS, etc.)
    """

    name: str
    accept: list[str] = field(default_factory=list)
    max_entries: int = 1
    max_file_size: int = 10 * 1024 * 1024  # 10MB default
    chunk_size: int = 64 * 1024  # 64KB chunks
    auto_upload: bool = True
    external: ExternalUploadCallback | None = None

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
            "external": self.external is not None,
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
        external: Whether the bytes go straight to external storage (S3, GCS),
            in which case no chunk file exists on any worker
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
    external: bool = False
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
        """Drop the bytes of an upload that ended on its own terms.

        For an upload nobody is still writing to -- consumed, or abandoned by a
        context manager. Use ``discard()`` when a writer in another process has
        to be told to stop.
        """
        if self.temp_path:
            upload_store.forget(self.temp_path)

    def discard(self) -> None:
        """Cancel the upload and leave the marker that stops a writer elsewhere.

        The chunk endpoint holds no state, so deleting the file cannot cancel
        anything: the next chunk would recreate it. The marker is what crosses
        the process boundary (#83).
        """
        if self.temp_path:
            upload_store.discard(self.temp_path)


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
        connection_id: ID of the connection that owns the component
        configs: Mapping of config names to UploadConfig objects
        entries: Mapping of config names to entry dicts (ref -> UploadEntry)
    """

    def __init__(self, component_id: str, connection_id: str = "") -> None:
        """Initialize the registry.

        Args:
            component_id: ID of the owning component
            connection_id: ID of the connection that owns the component. Component
                ids are only unique within a page, so the owner is what keeps two
                connections on the same page apart (#77).
        """
        self.component_id = component_id
        self.connection_id = connection_id
        self.configs: dict[str, UploadConfig] = {}
        self.entries: dict[str, dict[str, UploadEntry]] = {}

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
            ValueError: If config is unknown, the ref is malformed, or max entries reached
        """
        if config_name not in self.configs:
            raise ValueError(f"Unknown upload config: {config_name}")

        # ``ref`` comes from the client and reaches a temp filename, so it is
        # checked here: every registration path goes through add_entry.
        if not is_valid_ref(entry.ref):
            raise ValueError(f"Invalid upload ref: {entry.ref!r}")

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

        # Generate signed token. The owner is part of the signed data, so a token
        # issued for one connection cannot be replayed against another (#77), and
        # the size and file type ride along so the stateless chunk endpoint can
        # judge a chunk without reaching for this registry (#83).
        entry.upload_token = sign_upload_token(
            connection_id=self.connection_id,
            component_id=self.component_id,
            config_name=config_name,
            ref=entry.ref,
            max_bytes=min(entry.client_size, config.max_file_size),
            extension=Path(entry.client_name).suffix.lower(),
        )

        # The path is computed, not allocated: the worker that receives the chunks
        # derives the same one from the token without asking anyone (#83). An
        # external upload never passes through a worker at all, so it has none.
        entry.external = config.external is not None
        if not entry.external:
            entry.temp_path = self.chunk_path(config_name, entry.ref)

        self.entries[config_name][entry.ref] = entry
        return entry.upload_token

    def chunk_path(self, config_name: str, ref: str) -> Path:
        """Where one entry's bytes land.

        ``allow_upload`` hands the client an endpoint under ``connection_id or
        "-"``, so a component rendered outside a connection (``mount()``) uses
        the same stand-in here. Nothing can upload to it -- its token carries an
        empty owner, which no URL matches -- but the path stays well-formed.
        """
        return upload_store.chunk_path(self.connection_id or "-", self.component_id, config_name, ref)

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
            entry.discard()
        return entry

    @staticmethod
    def validate_token(token: str, max_age: int | None = None) -> tuple[str, str, str, str] | None:
        """Validate an upload token and return who it was issued to.

        Kept as a convenience for callers that only want the identity; the HTTP
        endpoint uses ``validate_upload_token`` directly, because it also needs
        the size limit and the file type the token carries. Static because
        verification never depended on this registry -- which is what makes the
        chunk endpoint work on any worker (#83).

        Args:
            token: Signed token to validate
            max_age: Maximum token age in seconds (default ``UPLOAD_TOKEN_MAX_AGE``)

        Returns:
            Tuple of (connection_id, component_id, config_name, ref) if valid, None otherwise
        """
        validated = validate_upload_token(token, max_age)
        if validated is None:
            return None
        return (validated.connection_id, validated.component_id, validated.config_name, validated.ref)

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
        """End every upload this component owns and drop its files.

        Called when the component leaves or its connection goes away. Entries the
        application already consumed have nothing left to write, so they are
        cleared rather than marked cancelled.
        """
        for entries in self.entries.values():
            for entry in entries.values():
                if entry.status is UploadStatus.CONSUMED:
                    entry.cleanup()
                else:
                    entry.discard()


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


#: Longest ``ref`` the server accepts. ``generate_ref()`` produces 23 characters.
REF_MAX_LENGTH = 64

#: A ``ref`` is a client-supplied string. It no longer reaches a filename -- the
#: chunk path hashes it (#83) -- but it is still an identity the server hands back
#: out, so it is held to characters that mean nothing to a filesystem or a URL.
REF_RE = re.compile(rf"[A-Za-z0-9_-]{{1,{REF_MAX_LENGTH}}}")


def is_valid_ref(ref: str) -> bool:
    """Whether ``ref`` is a reference the server is willing to register.

    Args:
        ref: Candidate reference, as received from the client

    Returns:
        True if the reference is safe to hand back out and to hash into a path
    """
    return isinstance(ref, str) and REF_RE.fullmatch(ref) is not None


def generate_ref() -> str:
    """Generate a unique upload reference.

    Returns:
        Unique reference string
    """
    return f"upload-{secrets.token_urlsafe(12)}"
