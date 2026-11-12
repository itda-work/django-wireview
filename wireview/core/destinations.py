"""Which URLs a navigation the server asks for may go to (#262, GAP-066).

``redirect_to``, ``push_to``, ``replace_to`` and ``JS().navigate`` send a URL
the page goes to: by boost, which fetches it and draws it in this document, or
by the browser. The browser's client judges every destination again with its
own URL parser (navigation.mjs ``destination``): another origin is a browser
navigation and never drawn here, a scheme other than http or https is refused.
This is the server's half, at the call: a destination that is not a web page
-- ``javascript:``, ``data:`` -- or that a browser reads differently from how
it reads -- a control character it drops, a backslash it takes for a slash --
raises where the app named it, as Django's ``HttpResponseRedirect`` raises
``DisallowedRedirect`` for an unsafe scheme.

Which hosts a destination may name is the app's to decide: a URL taken from a
request (``?next=``) goes through ``url_has_allowed_host_and_scheme`` first.
An external http(s) URL is allowed here, for a payment page or a sign-in.

Imports nothing of wireview: ``js`` and ``core.meta`` both use it.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import DisallowedRedirect

#: The schemes a destination may name. A URL without one -- a path, ``?q``,
#: ``#top``, ``//host/path`` -- takes the page's
SCHEMES = frozenset({"http", "https"})


def check_destination(url: str) -> str:
    """``url`` if a navigation may go there, else ``DisallowedRedirect``.

    Refused: an ASCII control character anywhere (a tab, a newline: the
    browser's URL parser drops them, so ``java\\tscript:`` is ``javascript:``
    to it), a backslash (a slash to the parser in an http(s) URL:
    ``/\\\\host`` is another host), whitespace around it (stripped by the
    parser), and a scheme other than http or https, whatever its case.
    """
    if not isinstance(url, str):
        raise DisallowedRedirect(f"A navigation goes to a URL string, not {type(url).__name__}: {url!r}")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise DisallowedRedirect(
            f"Refused to navigate to {url!r}: it holds a control character, which a browser drops "
            "before it reads the URL"
        )
    if "\\" in url:
        raise DisallowedRedirect(
            f"Refused to navigate to {url!r}: a browser reads a backslash in an http(s) URL as a slash. "
            "Write the URL with slashes, or percent-encode it (%5C)"
        )
    if url != url.strip():
        raise DisallowedRedirect(f"Refused to navigate to {url!r}: it has whitespace around it")
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError as error:  # an unclosed IPv6 host: no URL at all
        raise DisallowedRedirect(f"Refused to navigate to {url!r}: {error}") from None
    if scheme and scheme not in SCHEMES:
        raise DisallowedRedirect(
            f"Refused to navigate to {url!r}: only http and https URLs, and URLs relative to the page, "
            f"are followed -- not {scheme}:"
        )
    return url
