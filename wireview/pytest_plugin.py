"""The pytest plugin ``wireview`` (the ``pytest11`` entry point), loaded wherever wireview is installed.

A component ``mount()`` made hears what is published in its process (#178, #201)
until the test that made it ends. Without this it heard on until the collector
found it: a test that shares an event loop with the one before it -- a module- or
session-scoped loop -- heard what that one left, and a receiver of the earlier
test that raised failed the later one, depending on when the collector ran.

It imports nothing of wireview: a run that never imports ``wireview.testing``
pays a dictionary lookup per test. ``-p no:wireview`` turns it off, and then only
the event loop keeps tests apart (``docs/features/testing.md``).
"""

from __future__ import annotations

import sys
import typing as t

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> t.Generator[None, None, None]:
    yield
    # Setup, call and teardown are over, the fixtures of this test torn down
    testing = sys.modules.get("wireview.testing")
    if testing is not None:
        testing._end_of_test()
