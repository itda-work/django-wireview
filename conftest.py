"""Django's async-safety check stays on for the whole suite (#120).

``DJANGO_ALLOW_ASYNC_UNSAFE`` turns off the check that stops the ORM from
running on an event loop. Production has it off, so a suite that turns it on
passes code that fails on a real server: every join of the bookmarks page did,
while the tests were green. The suite refuses to start with it set.

One thread needs an exception. Playwright's sync API runs an event loop on the
test's own thread, so the test body and pytest-django's database fixtures look
like async code to Django while they are plain sync code driving a browser. For
E2E items only, that one thread is let through; the live server runs on a thread
of its own and keeps the check exactly as production has it, which is the point
of running it.
"""

from __future__ import annotations

import asyncio
import os
import threading
import typing as t

import pytest

# Fails the run on a sync iterator served under the ASGI handler (#129 follow-up).
pytest_plugins = ["testproj.warning_guard"]

FLAG = "DJANGO_ALLOW_ASYNC_UNSAFE"

_real_get_running_loop = asyncio.get_running_loop
_exempt_thread: threading.Thread | None = None


def _get_running_loop() -> asyncio.AbstractEventLoop:
    """``asyncio.get_running_loop`` as Django's check sees it: no loop on the exempt thread."""
    if threading.current_thread() is _exempt_thread:
        raise RuntimeError("no running event loop")
    return _real_get_running_loop()


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get(FLAG):
        raise pytest.UsageError(
            f"{FLAG} is set. It hides the ORM calls that fail on a real server, so the suite "
            f"does not run with it (#120). Unset it; E2E tests get the one exception they need "
            f"from conftest.py."
        )

    import django.utils.asyncio

    django.utils.asyncio.get_running_loop = _get_running_loop  # type: ignore[attr-defined]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> t.Generator[None, object, object]:
    global _exempt_thread
    if item.get_closest_marker("e2e") is None:
        return (yield)
    _exempt_thread = threading.current_thread()
    try:
        return (yield)
    finally:
        _exempt_thread = None
