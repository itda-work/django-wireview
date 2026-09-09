"""HTTP upload endpoint for wireview file uploads.

Chunks arrive over HTTP, not over the WebSocket, so nothing routes them to the
worker that holds the connection. Since #83 nothing has to: this endpoint keeps
no per-upload state. The signed token is the whole authority -- it says which
connection, component, upload and entry the chunk belongs to, how many bytes may
arrive and what the finished file has to look like -- and the bytes go to a path
computed from that identity (``features/upload_store.py``). Any worker reaches
the same verdict and the same file.

What the endpoint cannot do from here is keep the *component's* view of the
upload true, because that object lives on the owning worker. So it publishes what
it did to the connection's progress group, and the consumer applies it to the
entry (see ``WireviewConsumer.upload_progress`` and friends). Before #83 the two
were the same object in the same process and this happened for free.

The one thing that does not follow from the token is a shared chunk directory;
``docs/DEPLOYMENT.md`` says what that means for one host and for several.
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
from .features import upload_store
from .features.uploads import (
    UploadToken,
    upload_group_name,
    validate_magic_bytes,
    validate_upload_token,
)

log = logging.getLogger("wireview.uploads")


@method_decorator(csrf_exempt, name="dispatch")
class UploadView(View):
    """HTTP endpoint for chunked file uploads.

    Handles binary chunk uploads and publishes progress to the connection's
    group, where the worker that owns the WebSocket picks it up.

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

        # The token, and only the token. Consulting an index here is what made a
        # chunk that reached the wrong worker a 404 before #83.
        validated = validate_upload_token(token)
        if validated is None:
            log.warning(f"Invalid upload token for {component_id}/{upload_name}")
            return JsonResponse({"error": "Invalid token"}, status=403)

        if (
            validated.connection_id != connection_id
            or validated.component_id != component_id
            or validated.config_name != upload_name
            or validated.ref != entry_ref
        ):
            log.warning(f"Token mismatch for {component_id}/{upload_name}/{entry_ref}")
            return JsonResponse({"error": "Token mismatch"}, status=403)

        try:
            path = upload_store.chunk_path(connection_id, component_id, upload_name, entry_ref)
        except upload_store.InvalidConnectionId:
            # A signed token names it, so this is not reachable from a browser;
            # the check stays because the value becomes a directory name.
            log.warning(f"Invalid connection id on an upload URL: {connection_id!r}")
            return JsonResponse({"error": "Invalid connection"}, status=400)

        # A cancel, a leave or a disconnect on the owning worker leaves a marker
        # here, since it cannot reach into this process to stop the write.
        if upload_store.is_discarded(path):
            log.info(f"Upload cancelled: {upload_name}/{entry_ref}")
            return JsonResponse({"error": "Upload cancelled"}, status=410)

        chunk_data = request.body
        written = upload_store.size_of(path)

        if written == 0:
            # First chunk of this entry: a cheap moment to collect what a worker
            # that died mid-upload left behind, throttled per process.
            await sync_to_async(upload_store.sweep_if_due)()

        if written + len(chunk_data) > validated.max_bytes:
            # The size the client announced is what the entry was validated
            # against, so exceeding it means the announcement was a lie. Before
            # #83 nothing checked the size here at all.
            log.warning(f"Upload exceeds its announced size: {upload_name}/{entry_ref}")
            upload_store.discard(path)
            await self._publish(validated, "upload.error", errors=["Upload exceeds the announced file size"])
            return JsonResponse({"error": "Upload too large"}, status=413)

        written = await self._write_chunk(path, chunk_data)

        # The write is the only await between the check above and here, so a
        # cancel can land in the middle of it. Checking again keeps a chunk that
        # raced a cancel from leaving the file behind (#77, now across processes).
        if upload_store.is_discarded(path):
            log.info(f"Upload cancelled mid-chunk: {upload_name}/{entry_ref}")
            upload_store.remove(path)
            return JsonResponse({"error": "Upload cancelled"}, status=410)

        progress = min(99, int((written / max(1, validated.max_bytes)) * 100))
        await self._publish(validated, "upload.progress", progress=progress, bytes_received=written)

        is_last_chunk = chunk_index >= total_chunks - 1
        complete = is_last_chunk or written >= validated.max_bytes

        if complete:
            if not await sync_to_async(validate_magic_bytes)(path, validated.extension):
                upload_store.discard(path)
                await self._publish(validated, "upload.error", errors=["File content doesn't match file type"])
                return JsonResponse({"error": "Invalid file content", "progress": progress}, status=400)

            progress = 100
            await self._publish(validated, "upload.completed", bytes_received=written, path=str(path))
            log.info(f"Upload complete: {upload_name}/{entry_ref} ({written} bytes)")

        return JsonResponse(
            {
                "status": "ok",
                "progress": progress,
                "bytes_received": written,
                "complete": complete,
            }
        )

    @staticmethod
    @sync_to_async
    def _write_chunk(path: Path, data: bytes) -> int:
        """Append chunk data to the entry's file, returning its new size."""
        return upload_store.append_chunk(path, data)

    @staticmethod
    async def _publish(token: UploadToken, message_type: str, **payload: t.Any) -> None:
        """Tell the owning worker what happened to one of its uploads.

        One group per connection, subscribed once in the consumer's ``connect()``.
        The component id travels in the payload because the owner has to find the
        entry this concerns, not just forward a number to the browser.
        """
        try:
            await get_broker().publish(
                upload_group_name(token.connection_id),
                {
                    "type": message_type,
                    "component": token.component_id,
                    "upload": token.config_name,
                    "ref": token.ref,
                    **payload,
                },
            )
        except Exception as e:
            log.warning(f"Failed to publish {message_type}: {e}")
