"""Loading an app's JavaScript hooks without the page naming them (GAP-032, #71).

The control for all of this is ``examples/hooks``: it behaved the same before
and after the page stopped naming its hook file, and its E2E is what says so.
What is here is the part a browser cannot show -- where files are looked for,
what the header emits, and what ``manage.py check`` can see.
"""

import pytest
from django.template import Context, Template

from wireview import settings as wireview_settings
from wireview.checks import check_hook_files
from wireview.features.hooks import hook_files, hook_names, required_hook_names, reset_caches

pytestmark = pytest.mark.unit

#: What ``examples.hooks`` ships. The first segment is the app label (Django's
#: static namespacing), the second is wireview's convention -- they coincide
#: here only because the example app is itself called ``hooks``.
EXAMPLE_FILE = "hooks/hooks/lifecycle.js"


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Discovery is cached for the process; every test here starts from cold.

    Only before the test: a monkeypatched ``hook_names`` is still in place while
    finalizers run, and clearing a cache that is no longer a cache is an error
    with nothing to say.
    """
    reset_caches()


class TestWhatIsFound:
    def test_an_apps_hook_directory_is_found(self):
        assert EXAMPLE_FILE in hook_files()

    def test_the_paths_are_logical_not_filesystem_ones(self):
        """``{% static %}`` has to be able to resolve them, or hashed storage breaks."""
        for path in hook_files():
            assert not path.startswith("/")
            assert "static" not in path.split("/")

    def test_the_order_does_not_move(self):
        reset_caches()
        first = hook_files()
        reset_caches()
        assert hook_files() == first

    def test_the_names_are_read_out_of_the_file(self):
        """The file is the only place the hook's name is written."""
        assert hook_names().get("Timeago") == EXAMPLE_FILE
        assert hook_names().get("Noter") == EXAMPLE_FILE

    def test_the_templates_that_ask_for_a_hook_are_found(self):
        wanted = required_hook_names()

        assert "Timeago" in wanted
        assert any("x-lifecycle.html" in where for where in wanted["Timeago"])


class TestWhatTheHeaderEmits:
    def render(self) -> str:
        return Template("{% load wireview %}{% wireview_header %}").render(Context({}))

    def test_every_hook_file_gets_a_deferred_script(self):
        html = self.render()

        assert f'<script defer src="/static/{EXAMPLE_FILE}"></script>' in html

    def test_the_bundle_comes_first(self):
        """Deferred scripts run in order, and a hook file needs window.wireview."""
        html = self.render()

        assert html.index("wireview.min.js") < html.index(EXAMPLE_FILE)

    def test_the_setting_turns_collection_off(self, monkeypatch):
        """For a project that puts the same files through its own bundler."""
        monkeypatch.setattr(wireview_settings, "COLLECT_HOOKS", False)
        html = self.render()

        assert EXAMPLE_FILE not in html
        assert "wireview.min.js" in html, "turning off collection must not unload wireview"


class TestTheCheck:
    def test_a_template_naming_an_unknown_hook_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            "wireview.features.hooks.required_hook_names",
            lambda: {"NoSuchHook": ["myapp/templates/thing.html"]},
        )

        messages = check_hook_files(None)

        assert [m.id for m in messages] == ["wireview.W011"]
        assert "NoSuchHook" in messages[0].msg
        assert "thing.html" in messages[0].msg

    def test_a_template_naming_a_collected_hook_is_not(self, monkeypatch):
        """The control. "Always warns" and "warns rightly" look the same from one test."""
        monkeypatch.setattr(
            "wireview.features.hooks.required_hook_names",
            lambda: {"Timeago": ["examples/hooks/templates/hooks/x-lifecycle.html"]},
        )

        assert check_hook_files(None) == []

    def test_the_project_as_it_stands_is_clean(self):
        """Every wire-hook in this repository is registered by a collected file."""
        assert check_hook_files(None) == []

    def test_nothing_is_said_when_collection_is_off(self, monkeypatch):
        monkeypatch.setattr(wireview_settings, "COLLECT_HOOKS", False)
        monkeypatch.setattr(
            "wireview.features.hooks.required_hook_names",
            lambda: {"NoSuchHook": ["myapp/templates/thing.html"]},
        )

        assert check_hook_files(None) == []

    def test_nothing_is_said_when_no_app_uses_the_convention(self, monkeypatch):
        """A project registering its hooks its own way has nothing to compare against."""
        monkeypatch.setattr("wireview.features.hooks.hook_names", lambda: {})
        monkeypatch.setattr(
            "wireview.features.hooks.required_hook_names",
            lambda: {"NoSuchHook": ["myapp/templates/thing.html"]},
        )

        assert check_hook_files(None) == []
