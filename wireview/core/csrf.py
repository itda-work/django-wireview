"""The CSRF token a component's template gets for ``{% csrf_token %}`` (#206).

A component's template renders with its own context -- fields, properties,
``this`` -- not the page's ``RequestContext``, so Django's ``csrf`` context
processor never reached it: the token was empty on the first HTTP render and on
every live render after, and a plain POST form in a component got a 403.

- **The HTTP render** takes the page's own ``csrf_token``, the lazy value of the
  context processor: ``get_token(request)`` runs only when a template reads it,
  so only a page that draws a form gets the cookie, as on any Django page.
- **A live render** has no request. The socket's handshake carried the
  browser's cookies (and the session), so the connection reads the CSRF secret
  there once and masks it once. One token per connection, as Phoenix masks once
  per LiveView process: a ``{% csrf_token %}`` is a static part, sent with a
  join's full render and not again, and a mask that changed every render would
  change the statics' fingerprint every render.

The secret is read with Django's own middleware, so the cookie name,
``CSRF_USE_SESSIONS``, the format check and the masked cookies of Django before
4.0 go exactly as they do for a request. A secret Django would replace -- none,
or one of the wrong format -- gives no token: a new one could not reach the
browser over a socket.

A render shared between connections (``Meta.shared_render``) and a
``Broadcast`` item get none: each is drawn once for many viewers
(``core/shared_render.py``, ``core/watched.py``).
"""

from __future__ import annotations

import logging
import typing as t
from collections.abc import Mapping

from django.http import HttpRequest
from django.http.cookie import parse_cookie
from django.middleware.csrf import CsrfViewMiddleware, get_token

__all__ = ("NO_TOKEN", "cookies_of", "mask", "secret")

log = logging.getLogger("wireview")


def _never_called(request: HttpRequest) -> t.NoReturn:
    raise AssertionError("only process_request is used")


def secret(cookies: Mapping[str, str], session: Mapping[str, t.Any]) -> str | None:
    """The CSRF secret a request with these cookies and this session would carry, or ``None``."""
    request = HttpRequest()
    request.COOKIES = dict(cookies)
    request.session = session  # type: ignore[assignment]
    try:
        CsrfViewMiddleware(_never_called).process_request(request)
    except Exception:  # a session Django could not read is no secret either
        log.exception("Could not read the CSRF secret of a connection")
        return None
    if request.META.get("CSRF_COOKIE_NEEDS_UPDATE"):
        # None there, or one of the wrong format: Django would make a new one
        # and send it with the response. A socket has no response.
        return None
    return request.META.get("CSRF_COOKIE")


def mask(secret: str) -> str | None:
    """A token for ``secret``, masked as Django masks one for a response; ``None`` if it cannot be."""
    request = HttpRequest()
    request.META["CSRF_COOKIE"] = secret
    try:
        return get_token(request)
    except ValueError:  # a character Django's alphabet does not have (a session value written by hand)
        return None


def cookies_of(scope: Mapping[str, t.Any]) -> dict[str, str]:
    """An ASGI connection's cookies: in the scope from Channels' ``CookieMiddleware``, else from the header."""
    cookies = scope.get("cookies")
    if cookies is not None:
        return dict(cookies)
    for name, value in scope.get("headers") or ():
        if name == b"cookie":
            return parse_cookie(value.decode("latin1"))
    return {}


class _NoToken:
    """What ``csrf_token`` is on a connection that carries no CSRF secret: empty, and said once."""

    __slots__ = ()

    def __bool__(self) -> bool:
        _tell()
        return False

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return "<no CSRF token>"


#: ``csrf_token`` in a live render's context when the connection has no secret
NO_TOKEN = _NoToken()

_told = False


def _tell() -> None:
    global _told
    if not _told:
        _told = True
        log.warning(
            "A component's template rendered {%% csrf_token %%} on a connection that carries no CSRF cookie, "
            "so the token is empty and the form will be refused (403). The page's first render set no cookie: "
            "it drew no {%% csrf_token %%}. Decorate the view with django.views.decorators.csrf.ensure_csrf_cookie."
        )
