"""The pytest plugin ``wireview`` ends what a mount hears with whatever made it (#201).

A mount made in a test, or in a function-scoped fixture, stops hearing when the
test ends; one a fixture wider than a test made -- module, class, unittest's
``setUpClass`` -- when that fixture is torn down, so every test sharing it hears.

Each case runs in a pytest of its own: the order of its tests is what is under
test, so it must not depend on what else this run selected (``-k``, a random
order plugin), and turning the plugin off must be possible.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent

COMPONENTS = """
import pytest
import pytest_asyncio
from asgiref.sync import async_to_sync
from django.template import Template
from django.test import SimpleTestCase

from wireview import Broadcast, Component, ComponentTestCase, abroadcast, mount


class PlugFeed(Component):
    heard: list[str] = []

    class Meta:
        subscriptions = {"plug-feed"}

    async def notification(self, channel, **kwargs):
        self.heard = [*self.heard, kwargs["text"]]

    @classmethod
    def _get_template(cls, template_name=None):
        return Template("{% load wireview %}<p {% tag_header %}>{{ heard|join:',' }}</p>")


async def patched(view, event):
    \"\"\"What a Broadcast to the feed's topic wrote on ``view``.\"\"\"
    view.clear_messages()
    await Broadcast(PlugFeed, "plug-feed").push_event(event).asend()
    return [m["event"] for m in view.sent_messages if m["type"] == "push_event"]
"""

# A test leaves a mount behind on a loop the next test shares, held here so the
# collector cannot be what stops it hearing
SHARED_LOOP = """
LEFT_BEHIND = []


@pytest.mark.asyncio(loop_scope="module")
async def test_a_test_leaves_a_mount_behind():
    LEFT_BEHIND.append(await mount(PlugFeed, id="left"))


@pytest.mark.asyncio(loop_scope="module")
async def test_a_later_test_on_the_same_loop_is_not_heard_by_it():
    left, = LEFT_BEHIND
    await abroadcast("plug-feed", text="later")
    assert await patched(left, "later") == []
    assert left.component.heard == []
"""

MODULE_FIXTURE = """
@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def feed():
    return await mount(PlugFeed, id="module-feed")


@pytest.mark.asyncio(loop_scope="module")
async def test_the_first_test_hears(feed):
    await abroadcast("plug-feed", text="one")
    assert await patched(feed, "one") == ["one"]
    assert feed.component.heard == ["one"]


@pytest.mark.asyncio(loop_scope="module")
async def test_the_second_test_hears_too(feed):
    await abroadcast("plug-feed", text="two")
    assert await patched(feed, "two") == ["two"]
    assert feed.component.heard == ["one", "two"]
"""

# The class's fixture is torn down before the test after the class: what it
# mounted hears nothing more
CLASS_FIXTURE = """
TORN_DOWN = []


class TestAClass:
    @pytest_asyncio.fixture(scope="class", loop_scope="module")
    async def feed(self):
        view = await mount(PlugFeed, id="class-feed")
        TORN_DOWN.append(view)
        return view

    @pytest.mark.asyncio(loop_scope="module")
    async def test_the_first_test_hears(self, feed):
        assert await patched(feed, "one") == ["one"]

    @pytest.mark.asyncio(loop_scope="module")
    async def test_the_second_test_hears_too(self, feed):
        assert await patched(feed, "two") == ["two"]


@pytest.mark.asyncio(loop_scope="module")
async def test_after_the_class_its_fixtures_mount_hears_nothing():
    view, = TORN_DOWN
    assert await patched(view, "after") == []
"""

# unittest's setUpClass is a class-scoped fixture under pytest. It mounts on a
# loop of its own (async_to_sync), so only what is published to every loop --
# a Broadcast -- reaches it from the tests' loops.
SET_UP_CLASS = """
class TestSetUpClass(ComponentTestCase, SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.feed = async_to_sync(mount)(PlugFeed, id="set-up-class-feed")

    async def test_the_first_test_hears(self):
        assert await patched(self.feed, "one") == ["one"]

    async def test_the_second_test_hears_too(self):
        assert await patched(self.feed, "two") == ["two"]
"""


def _pytest(directory: Path, source: str, *options: str) -> str:
    (directory / "test_probe.py").write_text(COMPONENTS + source)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--ds", "testproj.settings", "--nomigrations"]
        + ["-o", "asyncio_default_fixture_loop_scope=function", "-rA", *options, "test_probe.py"],
        cwd=directory,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")])},
        capture_output=True,
        text=True,
        timeout=50,
    )
    return result.stdout + result.stderr


def _passed(output: str, *names: str) -> None:
    for name in names:
        assert f"PASSED test_probe.py::{name}" in output, output
    assert " failed" not in output and " error" not in output, output


def test_a_later_test_on_a_shared_loop_is_not_heard_by_an_earlier_tests_mount(tmp_path):
    """Whatever the loop: a test's mount stops hearing when the test ends."""
    output = _pytest(tmp_path, SHARED_LOOP)

    _passed(output, "test_a_test_leaves_a_mount_behind", "test_a_later_test_on_the_same_loop_is_not_heard_by_it")


def test_without_the_plugin_the_earlier_tests_mount_hears_on(tmp_path):
    """``-p no:wireview``: only the event loop keeps tests apart, and a shared one does not."""
    output = _pytest(tmp_path, SHARED_LOOP, "-p", "no:wireview")

    assert "FAILED test_probe.py::test_a_later_test_on_the_same_loop_is_not_heard_by_it" in output, output


def test_every_test_sharing_a_module_fixtures_mount_hears(tmp_path):
    output = _pytest(tmp_path, MODULE_FIXTURE)

    _passed(output, "test_the_first_test_hears", "test_the_second_test_hears_too")


def test_a_class_fixtures_mount_hears_until_the_fixture_is_torn_down(tmp_path):
    output = _pytest(tmp_path, CLASS_FIXTURE)

    _passed(
        output,
        "TestAClass::test_the_first_test_hears",
        "TestAClass::test_the_second_test_hears_too",
        "test_after_the_class_its_fixtures_mount_hears_nothing",
    )


def test_every_test_of_a_class_hears_what_set_up_class_mounted(tmp_path):
    output = _pytest(tmp_path, SET_UP_CLASS)

    _passed(output, "TestSetUpClass::test_the_first_test_hears", "TestSetUpClass::test_the_second_test_hears_too")


def test_the_pytest_plugin_is_registered(request: pytest.FixtureRequest):
    """Installed with the package (the pytest11 entry point), so an app's suite runs it too."""
    assert request.config.pluginmanager.has_plugin("wireview")
