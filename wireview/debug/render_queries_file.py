"""Render-part SQL for the editor: one JSON line per piece of work, in a file (#188).

The outermost scope of ``render_queries`` that ends in development is written as
one line to ``BASE_DIR/.wireview/render-queries/<start>-<pid>.<n>.jsonl``. An
editor reads the lines and puts a count at the end of each template line and
property that ran SQL. The format is versioned by its own ``version`` field, like
``wireview_lsp``'s metadata; docs/features/render-queries.md describes it and
docs/design/render-queries-editor.md gives the reasons.

What a line holds is **snapshots**: each render scope that ended inside the work,
rows or none, keyed by its component class. A reader keeps the latest snapshot
of each class, so a component fixed to run no SQL clears its old count, whether
it was drawn inside another one's render before or on its own now. Work with no
render in it (a handler, a task, a broadcast item) is not written.

A template line is told only with the digest of the source its running
``Template`` was compiled from (``source_of``): the dev server can run a cached
template that is no longer the file on disk. A property's ``def`` line comes
with the file's ``stat``, as strings, and is left out when the file changed
after the library was imported.

Each process writes its own files and never shortens one: past
``SEGMENT_LIMIT`` it opens the next number and removes the one before the last.
A line is written whole under a lock or taken back, and the first failure turns
writing off for the process. Nothing here ever raises into a render.

Written only while ``DEBUG_RENDER_QUERIES`` collects, ``DEBUG`` is on, and
nothing suppresses it: ``WIREVIEW_RENDER_QUERIES_DIR=off`` in the environment
(child processes inherit it) or ``wireview.testing`` imported in this process.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import threading
import time
import typing as t
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from functools import cached_property, lru_cache
from pathlib import Path

from django.conf import settings as django_settings

from .. import settings

if t.TYPE_CHECKING:
    from types import FrameType

    from .render_queries import Row, Scope

log = logging.getLogger("wireview.queries")

#: The shape of a line. A reader checks the major number and refuses a newer one;
#: a key added to what is already there raises the minor number.
VERSION = "1.0"
#: The environment variable that turns writing off, here and in every child process
ENVIRONMENT = "WIREVIEW_RENDER_QUERIES_DIR"
#: A segment past this many bytes gives way to the next
SEGMENT_LIMIT = 1 << 20
#: The most a line may take, in UTF-8 bytes
LINE_LIMIT = 64 * 1024
#: How much of a statement and of a tag's text a line keeps
SQL_WIDTH = 1000
TEXT_WIDTH = 200
#: How many render snapshots a line carries
RENDERS_LIMIT = 100
#: The same snapshot of a class is written again only after this many seconds
REPEAT_WINDOW = 60.0
#: How many classes' last snapshots a process remembers
KEYS_LIMIT = 4096
#: Another process's file is left alone until it has not changed for this long
IDLE = 10 * 60
#: and removed once it has not changed for this long, or when more than KEEP are idle
EXPIRE = 24 * 3600
KEEP = 32
#: The statements the log calls repeated: render_queries.REPEAT_THRESHOLD
REPEAT_THRESHOLD = 3

_DEF = "^\\s*(?:async\\s+)?def\\s+{}\\b"

#: Set by ``wireview.testing``: a test process writes nothing
_suppressed = False
#: Set by the first failure: writing is off until the process restarts
_failed = False
_said_no_base = False
_writer: _Writer | None = None
_writer_lock = threading.Lock()

# Seams for the tests
_clock = time.monotonic
_write = os.write


def suppress() -> None:
    """Write nothing in this process (``wireview.testing`` calls it on import)."""
    global _suppressed
    _suppressed = True


def wanted() -> bool:
    """Whether a piece of work is written, the setting being on. Never raises.

    Asked when the work starts (whether to gather what a line needs) and again when
    it ends (whether to write it): a condition that turned off in between holds.
    The caller has checked ``DEBUG_RENDER_QUERIES`` and that no ``queries()`` block
    collects for a test.
    """
    try:
        return _wanted()
    except Exception:  # a broken setting: the render goes on, nothing is written
        return False


def _wanted() -> bool:
    if _failed or _suppressed:
        return False
    if os.environ.get(ENVIRONMENT, "").strip().lower() == "off":
        return False
    if not django_settings.DEBUG:
        return False
    return directory() is not None


#: (the setting, BASE_DIR) -> the directory: asked at the start and end of every piece of work
_directories: dict[tuple[t.Any, t.Any], Path | None] = {}


def directory() -> Path | None:
    """Where the files go: ``DEBUG_RENDER_QUERIES_DIR``, or ``BASE_DIR/.wireview/render-queries``."""
    value = settings.DEBUG_RENDER_QUERIES_DIR
    if value is False:
        return None
    base = getattr(django_settings, "BASE_DIR", None) if value is None else None
    key = (value, base)
    try:
        return _directories[key]
    except (KeyError, TypeError):  # TypeError: an unhashable setting
        pass
    found = _directory(value, base)
    try:
        if len(_directories) > 16:
            _directories.clear()
        _directories[key] = found
    except TypeError:
        pass
    return found


def _directory(value: t.Any, base: t.Any) -> Path | None:
    global _said_no_base
    if value is None:
        if base is None:
            if not _said_no_base:
                _said_no_base = True
                log.info(
                    "Render queries are not written for the editor: settings has no BASE_DIR. "
                    "Set WIREVIEW['DEBUG_RENDER_QUERIES_DIR'] to a directory, or to False."
                )
            return None
        return Path(base) / ".wireview" / "render-queries"
    return Path(value)


def _base() -> str | None:
    base = getattr(django_settings, "BASE_DIR", None)
    return _real(str(base)) if base is not None else None


# The template's source


@lru_cache(maxsize=1)
def _allowed_loaders() -> tuple[type, ...]:
    from django.template.loaders import app_directories, filesystem

    return (filesystem.Loader, app_directories.Loader)


def source_of(node: t.Any, frame: FrameType | None) -> str | None:
    """The digest of the source the template running ``node`` was compiled from.

    It is read from the ``Template`` on the stack above ``frame`` whose ``origin``
    is the node's, and kept on that ``Template``. Only for the loaders that make a
    new ``Origin`` for every compile (Django's filesystem and app directories,
    cached or not): with another, two compiled templates could share an origin,
    and the one found need not be the node's. ``None`` when not certain, and when
    the digest cannot be made: this runs before the statement does, and must not
    keep it from running.
    """
    try:
        return _source_of(node, frame)
    except Exception:
        return None


def _source_of(node: t.Any, frame: FrameType | None) -> str | None:
    from django.template.base import Template

    origin = node.origin
    if type(getattr(origin, "loader", None)) not in _allowed_loaders():
        return None
    render = Template.render.__code__
    # The test environment swaps ``_render`` for its own function: the one installed now
    current = getattr(getattr(Template, "_render", None), "__code__", None)
    while frame is not None:
        code = frame.f_code
        if code is render or code is current:
            template = frame.f_locals.get("self")
            if isinstance(template, Template) and template.origin is origin:
                digest = template.__dict__.get("_wireview_source")
                if digest is None:
                    digest = template.__dict__["_wireview_source"] = digest_of(template.source)
                return digest
        frame = frame.f_back
    return None


def digest_of(text: str) -> str:
    """SHA-256 of ``text`` with its line ends made ``\\n``, as UTF-8: what an editor hashes the file to."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# A property's place


@lru_cache(maxsize=4096)
def _real(path: str) -> str:
    return os.path.realpath(path)


@lru_cache(maxsize=4096)
def _property_place(owner: type, name: str) -> tuple[str, int] | None:
    """The file and the ``def`` line of ``owner``'s property ``name``, past its decorators."""
    attribute = owner.__dict__.get(name)
    if isinstance(attribute, property):
        getter = attribute.fget
    elif isinstance(attribute, cached_property):
        getter = attribute.func
    else:
        getter = getattr(attribute, "fget", None) or getattr(attribute, "func", None)
    if getter is None:
        return None
    try:
        getter = inspect.unwrap(getter)
        file = inspect.getsourcefile(getter)
        lines, start = inspect.getsourcelines(getter)
    except (OSError, TypeError):
        return None
    if file is None:
        return None
    pattern = re.compile(_DEF.format(re.escape(name)))
    for offset, text in enumerate(lines):
        if pattern.match(text):
            return _real(file), start + offset
    return None


def _imported_at() -> int:
    import wireview

    return wireview._IMPORTED_AT_NS


# The record


def _component(scope: Scope) -> str | None:
    cls = scope.cls
    return f"{cls.__module__}.{cls.__qualname__}" if cls is not None else None


def _rel(path: str, base: str | None) -> str | None:
    if base is None:
        return None
    try:
        rel = os.path.relpath(path, base)
    except ValueError:  # another drive
        return None
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    return rel.replace(os.sep, "/")


def _group(row: Row, count: int, base: str | None) -> dict[str, t.Any]:
    sql = row.sql
    item: dict[str, t.Any] = {"count": count, "sql": sql[:SQL_WIDTH]}
    if len(sql) > SQL_WIDTH:
        item["truncated"] = True
    if row.template is not None:
        template: dict[str, t.Any] = {}
        if row.path is not None and os.path.isabs(row.path):
            file = _real(row.path)
            template["file"] = file
            rel = _rel(file, base)
            if rel is not None:
                template["rel"] = rel
        template["name"] = row.template
        if row.source is not None and "file" in template:
            template["source"] = row.source
        template["line"] = row.line
        template["node"] = row.node
        if row.text is not None:
            template["text"] = row.text[:TEXT_WIDTH]
        if row.approximate:
            template["approximate"] = True
        item["template"] = template
    elif row.prop is not None:
        prop: dict[str, t.Any] = {"name": row.prop, "owner": row.owner, "async": row.prop_async}
        place = _property_place(row.owner_cls, row.prop) if row.owner_cls is not None else None
        if place is not None:
            file, line = place
            try:
                stat = os.stat(file)
            except OSError:
                stat = None
            # Edited after the library was imported: what runs may not be what is on disk
            if stat is not None and stat.st_mtime_ns <= _imported_at():
                prop["file"] = file
                rel = _rel(file, base)
                if rel is not None:
                    prop["rel"] = rel
                prop["line"] = line
                prop["stat"] = [str(stat.st_mtime_ns), str(stat.st_size)]
        item["property"] = prop
    inner = row.scope
    if inner is not None and inner is not row.render and inner.kind != "render":
        item["in"] = {"kind": inner.kind, "detail": inner.detail}
    return item


class _Record:
    """One line: what it says, and the snapshot of each class in it."""

    def __init__(self, scope: Scope) -> None:
        renders = ([scope] if scope.kind == "render" else []) + scope.renders
        kept = renders[:RENDERS_LIMIT]
        dropped = renders[RENDERS_LIMIT:]
        index = {id(render): position for position, render in enumerate(kept)}
        base = _base()
        rows = scope.rows
        repeated = {key for key, n in Counter((row.place, row.sql) for row in rows).items() if n >= REPEAT_THRESHOLD}

        groups: dict[tuple[t.Any, ...], list[Row]] = {}
        for row in rows:
            render = row.render
            by = index.get(id(render)) if render is not None else None
            if render is not None and by is None:
                by = -1  # its render was left out
            inner = row.scope if row.scope is not None and row.scope is not render else None
            groups.setdefault((by, row.place, row.sql, id(inner) if inner is not None else None), []).append(row)

        partial = {_component(render) for render in dropped}
        items: list[tuple[int | None, bool, dict[str, t.Any]]] = []
        more_groups = more_statements = 0
        for (by, place, sql, _inner), same in groups.items():
            if by == -1:
                more_groups += 1
                more_statements += len(same)
                continue
            item = _group(same[0], len(same), base)
            if by is not None:
                item = {"by": by, **item}
            is_repeated = (place, sql) in repeated
            if is_repeated:
                item["repeated"] = True
            items.append((by, is_repeated, item))
        # Repeats first, then the most statements: what a byte limit cuts is the least telling
        items.sort(key=lambda entry: (not entry[1], -entry[2]["count"]))

        self.renders = kept
        self.head: dict[str, t.Any] = {
            "version": VERSION,
            "at": None,
            "process": None,
            "segment": None,
            "base": base,
            "kind": scope.kind,
            "detail": scope.detail,
            "count": len(rows),
            "renders": [
                {
                    "kind": render.kind,
                    "component": _component(render),
                    "name": render.name,
                    "id": render.id,
                    "why": render.detail,
                }
                for render in kept
            ],
        }
        if dropped:
            self.head["renders_more"] = len(dropped)
        self.items = items
        self.more = (more_groups, more_statements)
        self.partial = partial

    def line(self, process: str, segment: int) -> bytes | None:
        """The line, cut to ``LINE_LIMIT``; ``None`` when even no rows do not fit."""
        self.head["at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.head["process"] = process
        self.head["segment"] = segment
        whole = len(self.items)
        encoded = self._encode(whole)
        if len(encoded) <= LINE_LIMIT:
            return encoded
        low, high = 0, whole - 1  # the most rows that fit lies in [low, high]
        if len(self._encode(0)) > LINE_LIMIT:
            return None
        while low < high:
            middle = (low + high + 1) // 2
            if len(self._encode(middle)) <= LINE_LIMIT:
                low = middle
            else:
                high = middle - 1
        return self._encode(low)

    def _encode(self, keep: int) -> bytes:
        record = dict(self.head)
        rows = [item for _, _, item in self.items[:keep]]
        record["rows"] = rows
        groups, statements = self.more
        partial = set(self.partial)
        for by, _, item in self.items[keep:]:
            groups += 1
            statements += item["count"]
            if by is not None:
                partial.add(self.head["renders"][by]["component"])
        if groups:
            record["more"] = {"groups": groups, "statements": statements}
        named = sorted(component for component in partial if component is not None)
        if named:
            record["partial"] = named
        #: What the last encoding kept: the line ``line()`` returns is its last encoding
        self.kept = keep
        self.kept_partial = set(named)
        return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    def snapshots(self) -> dict[str, str]:
        """Each class -> what this work observed of it, every row, before any cut.

        Compared with what was last *published* for the class: what a reader holds.
        Nothing is encoded to find a repeat.
        """
        return self._snapshots(self.items, self.partial)

    def published(self) -> dict[str, str]:
        """Each class -> what the line ``line()`` returned says of it: the rows the byte limit kept.

        A class cut short is published partial, so the same rows seen whole later
        are not taken for a repeat of it.
        """
        return self._snapshots(self.items[: self.kept], self.kept_partial)

    def _snapshots(self, items: list[tuple[int | None, bool, dict[str, t.Any]]], partial: set[t.Any]) -> dict[str, str]:
        renders = self.head["renders"]
        by_class: dict[str, list[str]] = {}
        for render in renders:
            if render["component"] is not None:
                by_class.setdefault(render["component"], [])
        for by, _, item in items:
            if by is None:
                continue
            component = renders[by]["component"]
            if component is not None:
                by_class[component].append(repr([(key, value) for key, value in item.items() if key != "by"]))
        return {component: repr((component in partial, sorted(rows))) for component, rows in by_class.items()}


# Writing


class _Writer:
    """The files of this process in one directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        started = datetime.fromtimestamp(_imported_at() / 1e9, timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.process = f"{started}-{os.getpid()}"
        self.segment = 0
        self.fd: int | None = None
        self.size = 0
        self.lock = threading.Lock()
        #: class -> (what the last line written said of it, whether that was cut short, when)
        self.published: OrderedDict[str, tuple[str, bool, float]] = OrderedDict()
        #: Turned off by a failure, under ``lock``: a thread that was waiting for it writes nothing after
        self.off = False

    def path(self, segment: int) -> Path:
        return self.directory / f"{self.process}.{segment}.jsonl"

    def emit(self, scope: Scope) -> None:
        """Write the line of ``scope`` if it tells a reader something new. Raises nothing.

        Everything, from reading the scope to the write, runs under the lock and one
        failure boundary: a failure turns the writer and the process off before the
        lock is let go, so a thread that was waiting for it writes nothing after.
        """
        if not scope.renders and scope.kind != "render":
            return  # no render in it: not a snapshot of anything (1.0)
        failure: BaseException | None = None
        with self.lock:
            if self.off or _failed:
                return
            try:
                self._emit(scope)
            except Exception as error:
                self.off = True
                self._close()
                failure = error if _mark_failed() else None
        if failure is not None:
            _say_failed(self.directory, failure)

    def _emit(self, scope: Scope) -> None:
        record = _Record(scope)
        observed = record.snapshots()
        now = _clock()
        segment = self.segment + 1 if self._next_segment(0) else self.segment
        line: bytes | None = None
        cut: list[str] = []
        for component, said in observed.items():
            last = self.published.get(component)
            if last is None or now - last[2] >= REPEAT_WINDOW:
                break
            if last[1]:
                cut.append(component)  # published cut short: what a line says now is told by encoding it
            elif last[0] != said:
                break
        else:
            if not cut:
                return
            line = record.line(self.process, segment)
            if line is None:
                return
            published = record.published()
            if all(published[component] == self.published[component][0] for component in cut):
                return
        if line is None:
            line = record.line(self.process, segment)
            if line is None:
                return
        if self._next_segment(len(line)):
            self._open()
            if self.segment != segment:
                line = record.line(self.process, self.segment)
                if line is None:
                    return
        self._append(line)
        partial = record.kept_partial
        for component, said in record.published().items():
            self.published[component] = (said, component in partial, now)
            self.published.move_to_end(component)
        while len(self.published) > KEYS_LIMIT:
            self.published.popitem(last=False)

    def _next_segment(self, length: int) -> bool:
        """Whether a line of ``length`` bytes goes to a new segment."""
        if self.fd is None:
            return True
        try:
            if os.fstat(self.fd).st_nlink == 0:
                return True  # removed under it
        except OSError:
            return True
        return self.size > 0 and self.size + length > SEGMENT_LIMIT

    def _open(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None
        self._prepare()
        self.segment += 1
        self.fd = os.open(self.path(self.segment), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self.size = os.fstat(self.fd).st_size
        if self.segment > 2:
            try:
                self.path(self.segment - 2).unlink()
            except OSError:
                pass
        self._sweep()

    def _prepare(self) -> None:
        directory = self.directory
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        ignore = directory / ".gitignore"
        if not ignore.exists():
            # A project that does not ignore .wireview/ still does not commit these
            ignore.write_text("*\n", encoding="utf-8")

    def _sweep(self) -> None:
        """Remove other processes' files that nobody writes any more."""
        now = time.time()
        idle: list[tuple[float, Path]] = []
        try:
            entries = list(self.directory.glob("*.jsonl"))
        except OSError:
            return
        for path in entries:
            if path.name.startswith(self.process + "."):
                continue
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if now - modified < IDLE:
                continue
            if now - modified > EXPIRE:
                _remove(path)
            else:
                idle.append((modified, path))
        idle.sort()
        for _, path in idle[: max(0, len(idle) - KEEP)]:
            _remove(path)

    def _append(self, line: bytes) -> None:
        """All of ``line``, or none of it: a short write is taken back and fails."""
        assert self.fd is not None
        before = self.size
        view = memoryview(line)
        try:
            while view:
                written = _write(self.fd, view)
                if written <= 0:
                    raise OSError(f"wrote {written} bytes of {len(view)}")
                view = view[written:]
        except BaseException:
            try:
                os.ftruncate(self.fd, before)
            except OSError:
                pass
            raise
        self.size = before + len(line)

    def close(self) -> None:
        """Close the segment, once a write that is going on has finished."""
        with self.lock:
            self.off = True
            self._close()

    def _close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass  # gone already (another process swept it), or held open (Windows)


def emit(scope: Scope) -> None:
    """Write the line of ``scope``, the outermost scope of a piece of work. Never raises."""
    global _writer
    if _failed:
        return
    where: Path | None = None
    try:
        where = directory()
        if where is None:
            return
        with _writer_lock:
            if _writer is None or (_writer.directory is not where and _writer.directory != where):
                if _writer is not None:
                    _writer.close()
                _writer = _Writer(where)
            writer = _writer
        writer.emit(scope)
    except Exception as error:
        # Off everywhere first, then said: a writer waiting for its lock sees the process off
        first = _mark_failed()
        with _writer_lock:
            if _writer is not None:
                _writer.close()
        if first:
            _say_failed(where, error)


_fail_lock = threading.Lock()


def _mark_failed() -> bool:
    """Turn writing off for the process; whether this was the first failure."""
    global _failed
    with _fail_lock:
        if _failed:
            return False
        _failed = True
        return True


def _say_failed(where: Path | None, error: BaseException) -> None:
    log.warning("Render queries are no longer written for the editor in this process (%s): %s", where, error)


def _reset() -> None:
    """Forget the writer and a failure (tests)."""
    global _writer, _failed, _said_no_base
    with _writer_lock:
        if _writer is not None:
            _writer.close()
        _writer = None
    _failed = False
    _said_no_base = False
    _directories.clear()


__all__ = ["ENVIRONMENT", "VERSION", "digest_of", "directory", "emit", "source_of", "suppress", "wanted"]
