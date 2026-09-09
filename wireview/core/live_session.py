"""Page-level authentication boundaries (GAP-009, #58).

Phoenix names a group of LiveViews in the router and gives that group two
things: one place to declare ``on_mount`` hooks, and a boundary that forces a
full page load when navigation leaves it. wireview needs both, but its unit is
not the same one.

A Django view is the route here, and a page holds several components. So the
boundary is drawn around the **page**, not around a component, and one page
belongs to exactly one live_session::

    # myapp/live_sessions.py
    from wireview import live_session

    admin = live_session("admin", authorize=lambda ctx: ctx.user.is_staff)

    # myapp/views.py
    @admin.view
    def dashboard(request):
        return render(request, "admin/dashboard.html")

``authorize`` is one predicate with two enforcement points, which is the point
of it. The decorator runs it before the view produces a byte, because a join
that refuses later cannot recall HTML that already went out
(``docs/design/live-session.md`` §3-4); ``WireviewConsumer.command_join`` runs
the same predicate before it mounts anything. Two separate settings -- a view
decorator here and a hook there -- would let the two drift, which is the hole
AC2 names.

Around that predicate sit three further bindings:

- **The state envelope carries the session name and an authentication
  fingerprint** (``core/state.py`` v2). Without them a *validly* signed public
  page state and a protected component could be presented together, which
  needs no forgery at all.
- **A component may declare where it lives** with ``_live_sessions``. Declaring
  it means "only inside these sessions", and the check runs in
  ``Component._mount``, so it covers every path that produces a component: the
  join, a children restore, a LiveComponent a parent's render created, and a
  re-join.
- **Logging out kills the connections it authenticated.** ``user_logged_out``
  publishes to the fingerprint's topic and every socket on it closes
  (``wireview/apps.py``). A full page load only refreshes an honest client;
  it does not retire a socket somebody kept open.

Nothing here is on by default: a project that declares no live_session keeps
the behaviour it had, and ``wireview.W010`` reports components that look like
they meant to opt in.
"""

from __future__ import annotations

import functools
import logging
import typing as t
from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.utils.crypto import salted_hmac
from django.utils.decorators import method_decorator

from .. import settings as wireview_settings
from .session import SessionView
from .signing import signing_key

if t.TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

log = logging.getLogger("wireview")

__all__ = [
    "LiveSession",
    "declaration_allows",
    "LiveSessionContext",
    "all_live_sessions",
    "auth_fingerprint",
    "auth_topic",
    "get_live_session",
    "live_session",
]

#: Attribute :meth:`LiveSession.view` leaves on the request. The template tags read
#: it to stamp the page's session name into the header meta and into every
#: ``data-state`` the page issues.
REQUEST_ATTR = "wireview_live_session"

#: Salt for the authentication fingerprint. It never leaves a signed envelope, but
#: it is derived from the session key, so it is hashed rather than carried.
AUTH_SALT = "wireview.live_session.auth"

#: Topic prefix for "this authentication generation is over". Connections inside a
#: live_session subscribe to the topic for their own fingerprint;
#: :func:`invalidate_authentication` publishes to it.
AUTH_TOPIC_PREFIX = "wireview.auth."

AnyUser = t.Union[AbstractBaseUser, AnonymousUser]


def auth_fingerprint(user: AnyUser | None, session: t.Any) -> str:
    """Identify the authentication *generation* a state was issued under.

    A user pk is not enough, and neither is a session auth hash on its own:
    the pk is the same person before and after a logout, and an anonymous
    visitor has no pk at all. What changes at exactly the moments the boundary
    cares about is the session Django hands the browser -- ``login()`` cycles
    the key, ``logout()`` flushes it, a password change moves
    ``_auth_user_hash`` -- so all three go in.

    Projects on the signed-cookie backend have no session key (it is always
    ``None``); there the auth hash carries the whole weight, which still moves
    on login, logout and password change.

    Args:
        user: the request's user, or ``None`` for a call site without one.
        session: a :class:`~wireview.core.session.SessionView`, a Django session
            store, a plain mapping, or ``None``.

    Returns:
        A short hex digest. Equal digests mean "the same login, in the same
        browser session"; the value is opaque and reveals neither the session
        key nor the auth hash.
    """
    view = SessionView.wrap(session)
    pk = getattr(user, "pk", None)
    parts = [
        "" if pk is None else str(pk),
        view.session_key or "",
        str(view.get("_auth_user_hash", "")),
    ]
    return salted_hmac(AUTH_SALT, "\x1f".join(parts), secret=signing_key(), algorithm="sha256").hexdigest()[:32]


def declaration_allows(component_class: type, policy: "LiveSession | None") -> bool:
    """Whether ``_live_sessions`` lets this class mount inside ``policy``.

    Declaring nothing means "anywhere", which is where every component stood
    before boundaries existed. Declaring one or more names means *only* there --
    a page with no boundary included, because that is what a public page is.

    Separate from the mount hooks because it needs no ``await``: the live render
    of a nested ``{% component %}`` has no async seam to run hooks in, and this
    much can still be enforced there.
    """
    allowed = getattr(component_class, "_live_sessions", None)
    if not allowed:
        return True
    return (policy.name if policy is not None else "") in allowed


@dataclass(frozen=True)
class LiveSessionContext:
    """What an ``authorize`` predicate gets to decide on.

    A single object rather than positional arguments so that a later addition
    does not break every predicate a project has written.

    Attributes:
        user: the request's (or connection's) user. Never ``None`` -- an
            unauthenticated caller is an ``AnonymousUser``.
        session: read-only view of the Django session.
    """

    user: AnyUser
    session: SessionView


#: Every declared session, by name. Module import order decides when entries land here,
#: which is why :func:`get_live_session` is resolved at request time rather than at import.
_REGISTRY: dict[str, "LiveSession"] = {}


class LiveSession:
    """A named page boundary. Build one with :func:`live_session`."""

    def __init__(
        self,
        name: str,
        *,
        authorize: t.Callable[[LiveSessionContext], bool] | None = None,
        on_mount: t.Sequence[t.Any] = (),
        login_url: str | None = None,
    ) -> None:
        self.name = name
        self.authorize = authorize
        self.on_mount = list(on_mount)
        self.login_url = login_url

    def __repr__(self) -> str:
        return f"<LiveSession {self.name!r}>"

    # Policy

    def allows(self, user: AnyUser | None, session: t.Any) -> bool:
        """Whether this user may be inside the boundary.

        Synchronous on purpose: the same call has to work from a view and from
        a WebSocket join, and a predicate that touches the ORM is the normal
        case. The join wraps it in ``database_sync_to_async`` rather than asking
        projects to write two versions.
        """
        if self.authorize is None:
            return True
        context = LiveSessionContext(user=user or AnonymousUser(), session=SessionView.wrap(session))
        return bool(self.authorize(context))

    # HTTP

    def view(self, target: t.Any) -> t.Any:
        """Put a view inside this boundary. Works on a function or a class-based view.

        The wrapper does two things before the view runs: it refuses the request
        when :meth:`allows` says no, and it records the session name on the
        request so the page's header meta and every ``data-state`` it issues
        carry it.
        """
        if isinstance(target, type):
            target.dispatch = method_decorator(self.view)(target.dispatch)  # type: ignore[attr-defined]
            setattr(target, REQUEST_ATTR, self.name)
            return target

        @functools.wraps(target)
        def wrapper(request: "HttpRequest", *args: t.Any, **kwargs: t.Any) -> t.Any:
            user = getattr(request, "user", None)
            if not self.allows(user, getattr(request, "session", None)):
                return self.deny(request)
            setattr(request, REQUEST_ATTR, self.name)
            return target(request, *args, **kwargs)

        return wrapper

    def deny(self, request: "HttpRequest") -> "HttpResponse":
        """What an unauthorized request gets.

        The split follows ``django.contrib.auth``: somebody who is not logged in
        is sent to the login page, because logging in may be all that is
        missing; somebody who *is* logged in and still fails the predicate gets
        a 403, because it will not be.
        """
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            raise PermissionDenied(f"live_session {self.name!r} refused this user")
        return redirect_to_login(request.get_full_path(), self.login_url or wireview_settings.LOGIN_URL)

    # Mount hooks

    async def run_on_mount(self, component: t.Any, params: dict[str, t.Any], session: t.Any) -> dict[str, t.Any]:
        """Run this session's hooks against one component, before the component's own.

        Same protocol as ``_on_mount``: ``{"cont": True}`` or ``{"halt": True}``.
        """
        for hook_class in self.on_mount:
            hook = getattr(hook_class, "on_mount", None)
            if hook is None:
                continue
            if isinstance(hook, staticmethod):
                hook = hook.__func__
            result = await hook(component, params or {}, session or {})
            if result and result.get("halt"):
                return {"halt": True, "hook": getattr(hook_class, "__name__", repr(hook_class))}
        return {"cont": True}


def live_session(
    name: str,
    *,
    authorize: t.Callable[[LiveSessionContext], bool] | None = None,
    on_mount: t.Sequence[t.Any] = (),
    login_url: str | None = None,
) -> LiveSession:
    """Declare a page boundary and register it under ``name``.

    Args:
        name: identifier for the boundary. It travels to the browser in the
            header meta and inside every signed state, so it must be stable
            across deploys and unique in the project.
        authorize: predicate over a :class:`LiveSessionContext`. Runs before the
            view renders and again before a join mounts anything. ``None``
            authorizes everyone, which is what a boundary that only groups hooks
            or only forces full loads wants.
        on_mount: hooks applied to every component on the page, before that
            component's own ``_on_mount``. Same protocol.
        login_url: where :meth:`deny` sends an anonymous visitor. Defaults to
            Django's ``LOGIN_URL``.

    Raises:
        ImproperlyConfigured: the name is empty, or already taken by another
            declaration. Two boundaries under one name would make the name in a
            signed state ambiguous, which is the one thing it cannot be.
    """
    if not name:
        raise ImproperlyConfigured("A live_session needs a name: it is what a signed state carries.")
    session = LiveSession(name, authorize=authorize, on_mount=on_mount, login_url=login_url)
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not session:
        raise ImproperlyConfigured(
            f"live_session({name!r}) is already declared. A name identifies one policy; "
            "reuse the object you declared instead of building a second one."
        )
    _REGISTRY[name] = session
    return session


def get_live_session(name: str) -> LiveSession | None:
    """The session declared under ``name``, or ``None`` if there is none."""
    return _REGISTRY.get(name) if name else None


def all_live_sessions() -> dict[str, LiveSession]:
    """Every declared session, by name. A copy: the registry is not editable from outside."""
    return dict(_REGISTRY)


def auth_topic(fingerprint: str) -> str:
    """The topic every connection authenticated under ``fingerprint`` listens on."""
    return f"{AUTH_TOPIC_PREFIX}{fingerprint}"


def invalidate_authentication(user: AnyUser | None, session: t.Any, *, reason: str = "logged out") -> None:
    """Close every socket standing on this authentication generation.

    A full page load is how an honest client picks up a new auth context, and it
    is not enough on its own: a connection opened while the user was logged in
    outlives the logout, and nothing in the request/response cycle touches it.
    So the logout publishes, and the sockets close (``WireviewConsumer.
    session_invalidated``).

    Call it with the *pre-logout* user and session: it is the generation being
    retired that names the topic. Django's ``user_logged_out`` fires before
    ``session.flush()``, which is what makes that possible.

    Failures are logged, never raised. Losing a socket teardown must not turn a
    logout into a 500 -- the honest client still lands on a fresh page, and the
    states the old generation issued are refused on their next join either way.
    """
    from asgiref.sync import async_to_sync

    from .transport import get_broker

    topic = auth_topic(auth_fingerprint(user, session))
    try:
        async_to_sync(get_broker().publish)(topic, {"type": "session_invalidated", "reason": reason})
    except Exception:  # pragma: no cover - transport failures must not break a logout
        log.exception("Could not publish the session invalidation for %s", topic)


def _on_user_logged_out(sender: t.Any, request: t.Any = None, user: t.Any = None, **kwargs: t.Any) -> None:
    """``user_logged_out`` receiver. Registered in ``WireviewConfig.ready()``."""
    if request is None:
        return
    invalidate_authentication(user or getattr(request, "user", None), getattr(request, "session", None))
