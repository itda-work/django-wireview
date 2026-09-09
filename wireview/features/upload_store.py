"""Where a chunked upload's bytes live, and who may still write to them.

Every path here is *computed* from the signed identity of an upload
``(connection_id, component_id, config_name, ref)``, never handed out and
remembered. That is what lets any worker serve a chunk (#83): the worker that
holds the WebSocket and the worker that receives the HTTP POST derive the same
file name without talking to each other.

::

    <UPLOAD_TEMP_DIR or system temp>/wireview-uploads/<connection>/<digest>.part
                                                                  <digest>.cancelled
                                                     <connection>/.gone

``digest`` hashes the component id, the upload name and the ref instead of
putting them in the path: all three come from templates and from the client, and
a hash cannot climb out of the directory. The connection id is minted by the
server (``secrets.token_urlsafe``) and is checked against that alphabet anyway,
since it *is* a directory name.

The two markers are how a cancel reaches a write happening in another process.
Deleting a ``.part`` cannot do it -- the writer would simply recreate it -- so a
cancel leaves ``<digest>.cancelled`` behind and a disconnect leaves ``.gone`` in
the connection's directory. Writers check both before and after each chunk. The
markers are tiny and ``sweep()`` collects them along with everything else that
outlived its token.

Sharing this directory between workers is the one deployment requirement the
design has; ``docs/DEPLOYMENT.md`` says what that means for one host and for
several.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import time
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

log = logging.getLogger("wireview.uploads")

#: Directory created under the temp dir, so a shared volume can hold other things.
STORE_DIR_NAME = "wireview-uploads"

#: Chunks accumulate here; the suffix keeps a partial file distinguishable.
CHUNK_SUFFIX = ".part"

#: Written next to a chunk file when its upload is cancelled or its component leaves.
CANCELLED_SUFFIX = ".cancelled"

#: Written into a connection's directory when the connection goes away.
GONE_MARKER = ".gone"

#: A connection id becomes a directory name, so it is held to the alphabet
#: ``secrets.token_urlsafe`` produces.
CONNECTION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class InvalidConnectionId(ValueError):
    """A connection id that is not safe to use as a directory name."""


def store_root() -> Path:
    """The directory every upload of every connection lives under.

    ``WIREVIEW["UPLOAD_TEMP_DIR"]`` decides the parent, read on every call so a
    test or a runtime change takes effect. An empty value is the unset value,
    not a path: ``Path("")`` is the working directory, so an environment
    variable nobody set would otherwise drop chunks into the deployed source
    tree without a word.

    Returns:
        The store directory, created if it does not exist yet

    Raises:
        ImproperlyConfigured: If ``UPLOAD_TEMP_DIR`` is set but unusable. An
            operator who pointed uploads at a shared volume must not silently
            get local disk instead.
    """
    from .. import settings as wireview_settings

    configured = wireview_settings.UPLOAD_TEMP_DIR
    parent = Path(configured) if configured else Path(tempfile.gettempdir())
    root = parent / STORE_DIR_NAME
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if configured:
            raise ImproperlyConfigured(
                f"WIREVIEW['UPLOAD_TEMP_DIR'] = {configured!r} cannot be used for upload temp files: {e}"
            ) from e
        raise
    return root


def connection_dir(connection_id: str) -> Path:
    """The directory holding one connection's chunk files.

    Args:
        connection_id: Owner of the uploads, as minted in ``connect()``

    Returns:
        Path to the connection's directory (not created)

    Raises:
        InvalidConnectionId: If the id could not be a directory name
    """
    if not isinstance(connection_id, str) or CONNECTION_ID_RE.fullmatch(connection_id) is None:
        raise InvalidConnectionId(f"Invalid connection id: {connection_id!r}")
    return store_root() / connection_id


def chunk_digest(component_id: str, config_name: str, ref: str) -> str:
    """Name one upload's file, without letting client input into the path.

    Args:
        component_id: Id of the component that owns the upload
        config_name: Name of the upload config (``allow_upload("images")``)
        ref: Entry reference

    Returns:
        A 32-character hex digest, stable across processes and runs
    """
    material = f"{component_id}\0{config_name}\0{ref}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


def chunk_path(connection_id: str, component_id: str, config_name: str, ref: str) -> Path:
    """Where this upload's bytes go. The same answer on every worker.

    Args:
        connection_id: Owner of the upload
        component_id: Id of the component that owns the upload
        config_name: Name of the upload config
        ref: Entry reference

    Returns:
        Path to the ``.part`` file (which need not exist yet)
    """
    return connection_dir(connection_id) / f"{chunk_digest(component_id, config_name, ref)}{CHUNK_SUFFIX}"


def cancelled_path(path: Path) -> Path:
    """The cancellation marker that belongs to a chunk file."""
    return path.with_suffix(CANCELLED_SUFFIX)


def gone_path(connection_id: str) -> Path:
    """The marker saying this connection is over."""
    return connection_dir(connection_id) / GONE_MARKER


def size_of(path: Path) -> int:
    """Bytes written so far, or 0 if nothing has been."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def append_chunk(path: Path, data: bytes) -> int:
    """Append one chunk, creating the file and its directory on first use.

    Args:
        path: Chunk file, from ``chunk_path()``
        data: Raw chunk bytes

    Returns:
        Size of the file after the write
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write(data)
        return f.tell()


def is_discarded(path: Path) -> bool:
    """Whether this upload has been cancelled, or its connection has gone away.

    Checked before and after every chunk write: the write is the only await in
    the request, so a cancel on the owning worker can land in the middle of it.
    """
    return cancelled_path(path).exists() or (path.parent / GONE_MARKER).exists()


def discard(path: Path) -> None:
    """Cancel one upload: leave the marker, drop the bytes.

    The marker is what makes this cross a process boundary. Removing the file
    alone would only make the next chunk recreate it.
    """
    marker = cancelled_path(path)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as e:  # pragma: no cover - a store we cannot write to
        log.warning(f"Could not mark upload {path.name} cancelled: {e}")
    remove(path)


def remove(path: Path) -> None:
    """Delete one upload's bytes, leaving any marker in place."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:  # pragma: no cover - a store we cannot write to
        log.warning(f"Could not remove upload file {path}: {e}")


def forget(path: Path) -> None:
    """Delete one upload's bytes and its marker.

    For an upload that ended on its own terms -- consumed by the application --
    where nothing is left to warn a writer about.
    """
    remove(path)
    remove(cancelled_path(path))


def discard_connection(connection_id: str) -> None:
    """End every upload of one connection: mark it gone, drop the bytes.

    Called on disconnect. The directory survives, holding only ``.gone``, so a
    chunk still in flight on another worker learns that it has nowhere to go;
    ``sweep()`` removes it once no token could still be valid.
    """
    try:
        directory = connection_dir(connection_id)
    except InvalidConnectionId:
        return
    if not directory.exists():
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / GONE_MARKER).touch()
        for entry in directory.iterdir():
            if entry.name != GONE_MARKER and entry.is_file():
                entry.unlink(missing_ok=True)
    except OSError as e:  # pragma: no cover - a store we cannot write to
        log.warning(f"Could not release uploads of connection {connection_id}: {e}")


def sweep(max_age: int | None = None, *, root: Path | None = None) -> int:
    """Remove everything older than a token could be.

    A worker that dies takes its cancel path with it, and a stateless endpoint
    accepts chunks for a component that is already gone until the token expires.
    What is left behind is bounded by ``UPLOAD_TOKEN_MAX_AGE``, so age is the
    whole rule: nothing older can belong to an upload that could still finish.

    Args:
        max_age: Seconds a file may go untouched. Defaults to
            ``UPLOAD_TOKEN_MAX_AGE``.
        root: Store directory to sweep. Defaults to ``store_root()``.

    Returns:
        Number of files removed
    """
    from .. import settings as wireview_settings

    if max_age is None:
        max_age = wireview_settings.UPLOAD_TOKEN_MAX_AGE
    directory = root if root is not None else store_root()
    if not directory.exists():
        return 0

    cutoff = time.time() - max_age
    removed = 0
    for connection in sorted(directory.iterdir()):
        if not connection.is_dir():
            continue
        for entry in sorted(connection.iterdir()):
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:  # pragma: no cover - raced with another sweep
                continue
        try:
            if not any(connection.iterdir()):
                connection.rmdir()
        except OSError:  # pragma: no cover - raced with a new upload
            pass
    return removed


#: Seconds between opportunistic sweeps in one process. Long enough that a busy
#: worker does not walk the store on every upload, short enough that a deployment
#: without the management command still collects what a crash left behind.
SWEEP_INTERVAL = 600

_last_sweep = 0.0


def sweep_if_due(interval: int = SWEEP_INTERVAL) -> int:
    """Sweep at most once per ``interval`` in this process.

    Called from the write path, so a deployment that never runs
    ``manage.py wireview_upload_gc`` still bounds what it leaves behind. The
    throttle is process-local on purpose: several workers sweeping the same
    store is harmless, and coordinating them would need the shared state this
    design just removed.
    """
    global _last_sweep

    now = time.monotonic()
    if now - _last_sweep < interval:
        return 0
    _last_sweep = now
    try:
        return sweep()
    except (OSError, ImproperlyConfigured) as e:  # pragma: no cover - a store we cannot read
        log.warning(f"Upload sweep failed: {e}")
        return 0


def reset_sweep_clock() -> None:
    """Forget when the last sweep ran. For tests."""
    global _last_sweep

    _last_sweep = 0.0


__all__ = [
    "CANCELLED_SUFFIX",
    "CHUNK_SUFFIX",
    "GONE_MARKER",
    "STORE_DIR_NAME",
    "InvalidConnectionId",
    "append_chunk",
    "cancelled_path",
    "chunk_digest",
    "chunk_path",
    "connection_dir",
    "discard",
    "discard_connection",
    "forget",
    "gone_path",
    "is_discarded",
    "remove",
    "reset_sweep_clock",
    "size_of",
    "store_root",
    "sweep",
    "sweep_if_due",
]
