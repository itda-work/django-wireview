"""A page on another site cannot open this site's socket (#96).

The handshake carries the browser's cookies, so a socket opened from any page
the user visits would act as them. The consumer compares the Origin with
ALLOWED_HOSTS before it accepts, whatever asgi.py wraps around it.
"""

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from wireview.consumer import WireviewConsumer
from wireview.core.origin import origin_refusal

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _connect(*headers: tuple[bytes, bytes]) -> bool:
    communicator = WebsocketCommunicator(WireviewConsumer.as_asgi(), "/__wireview__", headers=list(headers))
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    await communicator.disconnect()
    return connected


@override_settings(ALLOWED_HOSTS=["example.com", ".example.org"])
@pytest.mark.parametrize(
    "origin, allowed",
    [
        (b"https://example.com", True),
        (b"https://example.com:8443", True),
        (b"https://app.example.org", True),
        (b"https://evil.example", False),
        (b"https://example.com.evil.example", False),
        (b"null", False),
        (b"file://", False),
    ],
)
async def test_the_origin_has_to_be_an_allowed_host(origin, allowed):
    assert await _connect((b"origin", origin)) is allowed


@override_settings(ALLOWED_HOSTS=["example.com"])
async def test_a_client_without_an_origin_is_not_a_browser_and_connects():
    assert await _connect() is True


@override_settings(ALLOWED_HOSTS=["example.com"])
async def test_two_origin_headers_are_refused():
    assert await _connect((b"origin", b"https://example.com"), (b"origin", b"https://evil.example")) is False


@override_settings(ALLOWED_HOSTS=[], DEBUG=True)
async def test_debug_with_no_allowed_hosts_accepts_localhost_as_django_does():
    assert await _connect((b"origin", b"http://localhost:8000")) is True
    assert await _connect((b"origin", b"http://127.0.0.1:8000")) is True
    assert await _connect((b"origin", b"https://evil.example")) is False


@override_settings(ALLOWED_HOSTS=["example.com"], WIREVIEW={"CHECK_ORIGIN": False})
async def test_the_check_can_be_turned_off():
    assert await _connect((b"origin", b"https://evil.example")) is True


@override_settings(ALLOWED_HOSTS=["example.com"])
async def test_the_refusal_says_why():
    reason = origin_refusal({"headers": [(b"origin", b"https://evil.example")]})
    assert "evil.example" in reason and "ALLOWED_HOSTS" in reason
