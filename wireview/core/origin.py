"""Which browser pages may open a wireview socket (#96).

A WebSocket handshake carries the browser's cookies, so without a check any
site a logged-in user visits can open a socket to this one and act as them
(cross-site WebSocket hijacking). SameSite=Lax session cookies narrow that,
and do nothing for a sibling subdomain or a project that set SameSite=None.

The consumer checks before it accepts, the way Phoenix's ``check_origin`` does
by default, so the protection does not depend on how ``asgi.py`` wraps the
route. The rule is Django's own for the Host header: the Origin's host has to
match ``ALLOWED_HOSTS`` (localhost and friends when DEBUG leaves it empty).

A connection without an Origin header is let through. Browsers always send
one on a WebSocket handshake, so its absence means a client that is not a
browser -- and CSWSH needs a browser to carry someone else's cookies.
"""

from __future__ import annotations

import typing as t
from urllib.parse import urlparse

from django.conf import settings as django_settings
from django.http.request import split_domain_port, validate_host

from .. import settings

_DEBUG_HOSTS = [".localhost", "127.0.0.1", "[::1]"]


def origin_refusal(scope: t.Mapping[str, t.Any]) -> str:
    """Why this handshake's Origin may not connect, or ``""`` if it may."""
    if not settings.CHECK_ORIGIN:
        return ""
    origins = [value for name, value in scope.get("headers", []) if name == b"origin"]
    if not origins:
        return ""
    if len(origins) > 1:
        return "more than one Origin header"
    try:
        origin = origins[0].decode("latin-1")
    except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes every byte
        return "an Origin header that is not text"
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # "null" (a sandboxed frame, a file:// page) names no site to trust
        return f"Origin {origin!r} names no host"
    domain, _port = split_domain_port(parsed.netloc)
    allowed = list(django_settings.ALLOWED_HOSTS)
    if django_settings.DEBUG and not allowed:
        allowed = _DEBUG_HOSTS
    if domain and validate_host(domain, allowed):
        return ""
    return f"Origin {origin!r} is not in ALLOWED_HOSTS"
