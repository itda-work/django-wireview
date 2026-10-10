"""The pytest plugin ``wireview`` (the ``pytest11`` entry point), loaded wherever wireview is installed.

A component ``mount()`` made hears what is published in its process (#178, #201)
until whatever made it is over. Without this it heard on until the collector
found it: a test that shares an event loop with the one before it -- a module- or
session-scoped loop -- heard what that one left, and a receiver of the earlier
test that raised failed the later one, depending on when the collector ran.

What "over" means follows the fixture scopes. A mount made while a fixture wider
than a test is set up -- module, class, session, and unittest's ``setUpClass``,
which pytest runs as a class-scoped fixture -- belongs to that fixture and stops
hearing when it is torn down, so the tests sharing it hear alike. Any other mount,
in a test or a function-scoped fixture, stops when the test ends.

Importing it imports the package ``wireview`` (its ``__init__`` loads nothing
else, Django included), and pytest rewrites the asserts of the package of every
plugin it loads from an entry point. It never imports ``wireview.testing``: a run
that does not use it pays a dictionary lookup per test. ``-p no:wireview`` turns
it off, and then only the event loop keeps tests apart; the environment variable
``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` keeps pytest from loading it at all
(``docs/features/testing.md``).
"""

from __future__ import annotations

import sys
import typing as t

import pytest

#: The fixtures wider than a test being set up now, innermost last: the mounts
#: made during the setup of each
_setting_up: list[list[t.Any]] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(
    fixturedef: pytest.FixtureDef[t.Any], request: pytest.FixtureRequest
) -> t.Generator[None, None, None]:
    if fixturedef.scope == "function":
        yield
        return
    made: list[t.Any] = []
    _setting_up.append(made)
    # An async fixture runs to its end inside this call too (pytest-asyncio)
    yield
    _setting_up.pop()
    if made:
        fixturedef.addfinalizer(lambda: _stop_listening(made))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> t.Generator[None, None, None]:
    yield
    # Setup, call and teardown are over, the fixtures of this test torn down
    testing = sys.modules.get("wireview.testing")
    if testing is not None:
        testing._end_of_test()


def kept(mounted: t.Any) -> bool:
    """Whether a fixture wider than a test is making ``mounted``; it is then the fixture's to end.

    ``wireview.testing`` asks, for each mount.
    """
    if not _setting_up:
        return False
    _setting_up[-1].append(mounted)
    return True


def _stop_listening(made: list[t.Any]) -> None:
    for mounted in made:
        mounted._stop_listening()
    made.clear()
