"""Open pages join their components again when a template changes under the dev server (#180).

Django's autoreloader does not restart for a template: ``django.template.autoreload``
answers ``file_changed`` by emptying the template loaders, so the next render
uses the new file. A page that is open learns nothing of it. Its components show
the old markup until their next event, and one whose join failed on a broken
template stays refused on its connection (``repo.join_failed``) after the file is
fixed -- only a page reload brings it back.

So, in development, the same signal tells every open connection in this process
to send ``rejoin``: the page joins each of its components again with the state
its element carries, which is the latest one. Both ends wait their turn. The
session mails the change to itself, so ``rejoin`` goes out behind the answers to
the message it is handling; the page asks ``sync`` and joins once that is
answered, holding what it would send meanwhile, so every render it was owed is
in its states (``static/wireview/rejoins.mjs``). A join under an id tries a failed
one again (``repo.retry_join``), and a component that was fine is replaced by an
instance rendered from the new template, its state kept.

The signal comes on the reloader's thread; a session lives on its server's event
loop. A session registers the loop it started on, and the receiver hands each
one over with ``call_soon_threadsafe``. Only the connections of this process
hear it: each process runs its own reloader (``runserver``), and a server that
restarts the process on a change (``uvicorn --reload``) is a reconnect, which
joins everything on a new connection with no failure remembered.

Off unless ``REJOIN_ON_TEMPLATE_CHANGE`` (``None`` follows ``DEBUG``): a session
does not register, and the receiver returns at once -- in production the signal
is never sent at all, as no autoreloader runs.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import typing as t
import weakref
from pathlib import Path

from django.utils.autoreload import file_changed

from .. import settings

if t.TYPE_CHECKING:
    from ..session import WireviewSession

log = logging.getLogger("wireview")

#: The open sessions of this process, with the loop each one runs on. Weak, so a
#: session nobody stopped (a bare one in a test) does not outlive its owner.
_sessions: weakref.WeakKeyDictionary[WireviewSession, asyncio.AbstractEventLoop] = weakref.WeakKeyDictionary()
_lock = threading.Lock()
#: The tasks sending ``rejoin``, kept until they end: a loop holds only a weak reference
_tasks: set[asyncio.Task[None]] = set()


def enabled() -> bool:
    """Whether a template change makes the open pages join again: ``REJOIN_ON_TEMPLATE_CHANGE``."""
    setting = settings.REJOIN_ON_TEMPLATE_CHANGE
    return bool(settings.DEBUG if setting is None else setting)


def register(session: WireviewSession) -> None:
    """Hear template changes for ``session``, on the loop it is starting on. Nothing when off."""
    if not enabled():
        return
    loop = asyncio.get_running_loop()
    with _lock:
        _sessions[session] = loop


def unregister(session: WireviewSession) -> None:
    with _lock:
        _sessions.pop(session, None)


def registered(session: WireviewSession) -> bool:
    """Whether ``session`` still hears template changes: started, on, and not stopped since."""
    with _lock:
        return session in _sessions


def is_template(file_path: Path) -> bool:
    """Whether Django empties its template loaders for a change to ``file_path``, instead of restarting.

    The same test as ``django.template.autoreload.template_changed``: a file
    under a directory the Django template engines load from, not a ``.py``.
    """
    if file_path.suffix == ".py":
        return False
    from django.template.autoreload import get_template_directories

    return any(directory in file_path.parents for directory in get_template_directories())


def templates_changed(sender: t.Any, file_path: Path, **kwargs: t.Any) -> None:
    """``file_changed`` receiver: every open session sends ``rejoin`` if a template changed.

    Returns ``None`` whatever it does. A true result tells the autoreloader the
    change is handled and not to restart; that is Django's own receiver's to
    say for a template, and for anything else a restart is still due.
    """
    if not enabled():
        return
    with _lock:
        targets = list(_sessions.items())
    if not targets or not is_template(Path(file_path)):
        return
    log.debug("%s changed: the open pages join their components again", file_path)
    for session, loop in targets:
        try:
            loop.call_soon_threadsafe(_start, session, loop)
        except RuntimeError:
            # Its loop closed: the server is going away, and the session with it
            unregister(session)


def _start(session: WireviewSession, loop: asyncio.AbstractEventLoop) -> None:
    """On the session's loop: tell it. A session stopped since the snapshot does nothing (``registered``)."""
    task = loop.create_task(session.templates_changed())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def connect() -> None:
    """Connect the receiver. ``WireviewConfig.ready()`` calls it once.

    After Django's own receiver, which ``django.template`` connected on import:
    receivers run in order, so the loaders are empty before any session is told.
    """
    import django.template.autoreload  # noqa: F401 -- connects Django's receiver first

    file_changed.connect(templates_changed, dispatch_uid="wireview.template_reload")
