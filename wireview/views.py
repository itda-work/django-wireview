"""HTTP upload endpoint for wireview file uploads.

This module provides the HTTP endpoint for chunked file uploads.
Binary data is sent here (not through WebSocket JSON).
Progress updates are sent back to the client via the channel layer.
"""

from __future__ import annotations

import logging
import typing as t
from pathlib import Path

from asgiref.sync import sync_to_async
from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .core.transport import get_broker
from .features.uploads import (
    UploadRegistry,
    UploadStatus,
    create_temp_file,
    upload_group_name,
    validate_magic_bytes,
)

log = logging.getLogger("wireview.uploads")

# Process-local index of upload registries, keyed by (connection_id, component_id).
# The consumer populates it when a component with uploads joins.
#
# Component ids are only unique within a page and templates commonly fix them
# (``{% component 'X' id="bookmarks" %}``), so the owning connection is part of the
# key: two connections on the same page get their own entry, and neither can drop
# or clean up the other's (#77). The index is still per process; a chunk has to
# reach the process that holds the WebSocket (see docs/DEPLOYMENT.md).
_upload_registries: dict[tuple[str, str], UploadRegistry] = {}


def register_upload_registry(connection_id: str, component_id: str, registry: UploadRegistry) -> None:
    """Register a component's upload registry for HTTP access.

    Called by the consumer when a component with uploads joins.

    Args:
        connection_id: ID of the connection that owns the component
        component_id: ID of the component
        registry: The component's upload registry
    """
    _upload_registries[(connection_id, component_id)] = registry
    log.debug(f"Registered upload registry for component {component_id} of connection {connection_id}")


def unregister_upload_registry(connection_id: str, component_id: str) -> None:
    """Unregister one component's upload registry and clean up its temp files.

    Called when a component leaves, is retired, or is replaced by a re-join. Only
    the owning connection's entry is touched.

    Args:
        connection_id: ID of the connection that owns the component
        component_id: ID of the component
    """
    registry = _upload_registries.pop((connection_id, component_id), None)
    if registry is not None:
        registry.cleanup_all()
        log.debug(f"Unregistered upload registry for component {component_id} of connection {connection_id}")


def unregister_connection_uploads(connection_id: str) -> None:
    """Release every upload registry a connection owns and clean up its temp files.

    The single cleanup entry point for a connection going away: the consumer calls
    it on disconnect, and discarding a connection on logout (#58) will reuse it.

    Args:
        connection_id: ID of the connection whose uploads should be released
    """
    keys = [key for key in _upload_registries if key[0] == connection_id]
    for key in keys:
        registry = _upload_registries.pop(key)
        registry.cleanup_all()
    if keys:
        log.debug(f"Released {len(keys)} upload registries of connection {connection_id}")


def get_upload_registry(connection_id: str, component_id: str) -> UploadRegistry | None:
    """Get a component's upload registry.

    Args:
        connection_id: ID of the connection that owns the component
        component_id: ID of the component

    Returns:
        The registry if found, None otherwise
    """
    return _upload_registries.get((connection_id, component_id))


@method_decorator(csrf_exempt, name="dispatch")
class UploadView(View):
    """HTTP endpoint for chunked file uploads.

    Handles binary chunk uploads and sends progress via channel layer.

    URL: /__wireview_upload__/<connection_id>/<component_id>/<upload_name>/

    Headers:
        X-Upload-Token: Signed upload token
        X-Chunk-Index: 0-based chunk index
        X-Total-Chunks: Total number of chunks
        X-Entry-Ref: Entry reference

    Body:
        Raw binary chunk data
    """

    async def post(
        self,
        request: HttpRequest,
        connection_id: str,
        component_id: str,
        upload_name: str,
    ) -> JsonResponse:
        """Handle chunk upload."""
        # Get headers with validation
        token = request.headers.get("X-Upload-Token", "")
        entry_ref = request.headers.get("X-Entry-Ref", "")

        # Parse integer headers with error handling
        try:
            chunk_index = int(request.headers.get("X-Chunk-Index", "0"))
            total_chunks = int(request.headers.get("X-Total-Chunks", "1"))
        except ValueError:
            log.warning(f"Invalid chunk headers for {component_id}/{upload_name}")
            return JsonResponse({"error": "Invalid chunk headers"}, status=400)

        # Validate header values
        if chunk_index < 0 or total_chunks < 1 or chunk_index >= total_chunks:
            log.warning(f"Invalid chunk range for {component_id}/{upload_name}")
            return JsonResponse({"error": "Invalid chunk range"}, status=400)

        log.debug(f"Upload chunk {chunk_index + 1}/{total_chunks} for {component_id}/{upload_name}/{entry_ref}")

        # Get registry
        registry = get_upload_registry(connection_id, component_id)
        if not registry:
            log.warning(f"Upload registry not found for component {component_id}")
            return JsonResponse({"error": "Component not found"}, status=404)

        # Validate token
        validation = registry.validate_token(token)
        if not validation:
            log.warning(f"Invalid upload token for {component_id}/{upload_name}")
            return JsonResponse({"error": "Invalid token"}, status=403)

        conn_id, comp_id, config_name, ref = validation
        if conn_id != connection_id or comp_id != component_id or config_name != upload_name or ref != entry_ref:
            log.warning(f"Token mismatch for {component_id}/{upload_name}/{entry_ref}")
            return JsonResponse({"error": "Token mismatch"}, status=403)

        # Get entry
        entry = registry.get_entry(upload_name, ref)
        if not entry:
            log.warning(f"Upload entry not found: {upload_name}/{ref}")
            return JsonResponse({"error": "Entry not found"}, status=404)

        if entry.status == UploadStatus.CANCELLED:
            log.info(f"Upload cancelled: {upload_name}/{ref}")
            return JsonResponse({"error": "Upload cancelled"}, status=410)

        # Get chunk data
        chunk_data = request.body

        # Initialize temp file on first chunk
        if entry.temp_path is None:
            entry.temp_path = create_temp_file(ref)
            entry.status = UploadStatus.UPLOADING
            log.debug(f"Created temp file: {entry.temp_path}")

        # Write chunk to temp file
        await self._write_chunk(entry.temp_path, chunk_data)

        # The write is the only await between the check above and here, so a cancel
        # or a leave can land in the middle of it. Checking again keeps a chunk that
        # raced a cancel from leaving the temp file behind (#77).
        if entry.status == UploadStatus.CANCELLED:
            log.info(f"Upload cancelled mid-chunk: {upload_name}/{ref}")
            entry.cleanup()
            return JsonResponse({"error": "Upload cancelled"}, status=410)

        entry.bytes_received += len(chunk_data)
        entry.chunk_count += 1
        entry.progress = min(99, int((entry.bytes_received / entry.client_size) * 100))

        # Send progress via channel layer
        await self._send_progress(connection_id, upload_name, entry)

        # Check if this is the last chunk
        is_last_chunk = chunk_index >= total_chunks - 1

        if is_last_chunk or entry.bytes_received >= entry.client_size:
            # Validate magic bytes
            ext = Path(entry.client_name).suffix.lower()
            if not validate_magic_bytes(entry.temp_path, ext):
                entry.status = UploadStatus.ERROR
                entry.errors.append("File content doesn't match file type")
                await self._send_error(connection_id, upload_name, entry)
                return JsonResponse(
                    {"error": "Invalid file content", "progress": entry.progress},
                    status=400,
                )

            entry.status = UploadStatus.COMPLETED
            entry.progress = 100
            log.info(f"Upload complete: {upload_name}/{ref} ({entry.bytes_received} bytes, {entry.chunk_count} chunks)")

        return JsonResponse(
            {
                "status": "ok",
                "progress": entry.progress,
                "bytes_received": entry.bytes_received,
                "complete": entry.status == UploadStatus.COMPLETED,
            }
        )

    @staticmethod
    @sync_to_async
    def _write_chunk(path: Path, data: bytes) -> None:
        """Write chunk data to temp file."""
        with open(path, "ab") as f:
            f.write(data)

    @staticmethod
    async def _send_progress(connection_id: str, upload_name: str, entry: "UploadEntry") -> None:
        """Send progress update via channel layer."""

        # One group per connection, subscribed once in the consumer's connect().
        # The client routes the update by upload name and ref, both in the payload.
        group_name = upload_group_name(connection_id)

        try:
            await get_broker().publish(
                group_name,
                {
                    "type": "upload.progress",
                    "upload": upload_name,
                    "ref": entry.ref,
                    "progress": entry.progress,
                    "bytes_received": entry.bytes_received,
                },
            )
        except Exception as e:
            log.warning(f"Failed to send upload progress: {e}")

    @staticmethod
    async def _send_error(connection_id: str, upload_name: str, entry: "UploadEntry") -> None:
        """Send error via channel layer."""

        group_name = upload_group_name(connection_id)

        try:
            await get_broker().publish(
                group_name,
                {
                    "type": "upload.error",
                    "upload": upload_name,
                    "ref": entry.ref,
                    "errors": entry.errors,
                },
            )
        except Exception as e:
            log.warning(f"Failed to send upload error: {e}")


# Type hint for UploadEntry
if t.TYPE_CHECKING:
    from .features.uploads import UploadEntry
