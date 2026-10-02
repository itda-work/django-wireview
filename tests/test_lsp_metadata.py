"""Tests for the wireview_lsp management command."""

import functools
import json
from io import StringIO
from pathlib import Path

import pytest
from django import template
from django.core.management import call_command

from wireview import Component, LiveComponent, function_component
from wireview.management.commands.wireview_lsp import (
    METADATA_VERSION,
    extract_component_metadata,
    extract_function_components,
    extract_library,
    extract_metadata,
    find_template,
    template_roots,
)


@pytest.mark.unit
class TestWireviewLspCommand:
    """Test the wireview_lsp management command."""

    def test_outputs_valid_json(self):
        """Command should output valid JSON."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        output = out.getvalue()

        # Should be valid JSON
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_metadata_structure(self):
        """Output should have the expected structure."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Check top-level structure
        assert "version" in data
        assert "generated_at" in data
        assert "components" in data
        assert "modifiers" in data

        # The shape's own version: the extension under editors/vscode refuses another major
        assert data["version"] == METADATA_VERSION == "1.1"

    def test_component_metadata(self):
        """Component metadata should include all expected fields."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Should have at least one component (XTodoList from testproj)
        assert len(data["components"]) > 0

        # Check first component structure
        component = list(data["components"].values())[0]
        expected_fields = [
            "name",
            "fqn",
            "app_key",
            "module",
            "file_path",
            "line_number",
            "docstring",
            "template_name",
            "fields",
            "methods",
            "slots",
            "subscriptions",
            "subscriptions_is_dynamic",
            "temporary_assigns",
        ]
        for field in expected_fields:
            assert field in component, f"Missing field: {field}"

    def test_field_metadata(self):
        """Field metadata should include type and default information."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Find a component with fields
        for component in data["components"].values():
            if component["fields"]:
                field = list(component["fields"].values())[0]
                assert "type" in field
                assert "annotation" in field
                assert "default" in field
                assert "required" in field
                break

    def test_method_metadata(self):
        """Method metadata should include async flag and parameters."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # Find a component with methods
        for component in data["components"].values():
            if component["methods"]:
                method = list(component["methods"].values())[0]
                assert "is_async" in method
                assert "parameters" in method
                assert "docstring" in method
                assert "line_number" in method
                break

    def test_modifiers_included(self):
        """Event modifiers should be included in output."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        modifiers = data["modifiers"]
        assert len(modifiers) > 0

        # Check some expected modifiers
        expected_modifiers = ["prevent", "stop", "debounce", "throttle", "enter", "ctrl"]
        for mod in expected_modifiers:
            assert mod in modifiers, f"Missing modifier: {mod}"

        # Check modifier structure
        modifier = modifiers["debounce"]
        assert "description" in modifier
        assert "has_argument" in modifier
        assert modifier["has_argument"] is True
        assert "inlinejs" not in modifiers, "{% on %} refuses inlinejs; the editor must not offer it"

    def test_dynamic_subscriptions_flag(self):
        """Components with @property _subscriptions should be flagged."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        # XTodoItem has dynamic subscriptions
        if "XTodoItem" in data["components"]:
            component = data["components"]["XTodoItem"]
            assert component["subscriptions_is_dynamic"] is True

    def test_pretty_output(self):
        """--pretty flag should format JSON output."""
        out = StringIO()
        call_command("wireview_lsp", "--pretty", stdout=out)
        output = out.getvalue()

        # Pretty output should have newlines and indentation
        assert "\n" in output
        assert "  " in output  # Indentation

    def test_component_fqn_format(self):
        """FQN should be in module.ClassName format."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        for name, component in data["components"].items():
            # FQN should contain module and class name
            assert "." in component["fqn"]
            assert component["fqn"].endswith(component["name"])

    def test_app_key_format(self):
        """app_key should be in app:ClassName format."""
        out = StringIO()
        call_command("wireview_lsp", stdout=out)
        data = json.loads(out.getvalue())

        for name, component in data["components"].items():
            # app_key should have colon separator
            assert ":" in component["app_key"]
            parts = component["app_key"].split(":")
            assert len(parts) == 2
            assert parts[1] == component["name"]


@pytest.mark.unit
def test_is_handler_says_what_a_client_can_call():
    # #110: a mixin's framework methods were listed like handlers
    out = StringIO()
    call_command("wireview_lsp", stdout=out)
    (chat,) = [c for c in json.loads(out.getvalue())["components"].values() if c["name"] == "XChatRoom"]

    assert chat["methods"]["send_message"]["is_handler"] is True
    assert chat["methods"]["presence_join"]["is_handler"] is False
    assert chat["methods"]["model_dump"]["is_handler"] is False


# What the editor extension reads (#156). Each of these was missing from 1.0, and
# without it the extension either could not find something or reported a
# template that renders.


@pytest.fixture(scope="module")
def metadata():
    return extract_metadata()


@pytest.mark.unit
class TestComponentsForAnEditor:
    def test_a_component_says_whether_it_is_live(self, metadata):
        assert metadata["components"]["XTodoList"]["kind"] == "component"
        assert metadata["components"]["Counter"]["kind"] == "live_component"

    def test_the_template_is_a_file_on_disk(self, metadata):
        # template_name alone is relative to a directory the editor does not know
        todo = metadata["components"]["XTodoList"]
        assert Path(todo["template_path"]).is_file()
        assert todo["template_path"].endswith(todo["template_name"])

    def test_every_listed_component_template_is_found(self, metadata):
        # The project's own components: a test module may register one whose template is a made-up name
        project = (str(Path(__file__).parent / "testproj"), str(Path(__file__).parent.parent / "examples"))
        components = [c for c in metadata["components"].values() if c["file_path"].startswith(project)]
        assert len(components) > 20
        missing = [c["name"] for c in components if c["template_name"] and not c["template_path"]]
        assert missing == []

    def test_a_linked_template_has_its_real_path(self, tmp_path):
        """The editor names a document by its real path: a link's own path never matched an owner."""
        root = tmp_path / "templates"
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "shared").mkdir(parents=True)
        (elsewhere / "real.html").write_text("x")
        (elsewhere / "shared" / "card.html").write_text("x")
        root.mkdir()
        (root / "linked.html").symlink_to(elsewhere / "real.html")
        (root / "shared").symlink_to(elsewhere / "shared", target_is_directory=True)

        assert find_template("linked.html", [root]) == str((elsewhere / "real.html").resolve())
        assert find_template("shared/card.html", [root]) == str((elsewhere / "shared" / "card.html").resolve())

    def test_a_template_no_directory_holds_has_no_path(self):
        assert find_template("no/such/template.html", template_roots()) is None
        assert find_template(None, template_roots()) is None

    def test_a_method_names_the_file_that_defines_it(self, metadata):
        # A mixin's handler is not in the component's file: line_number alone pointed into the wrong one
        chat = metadata["components"]["XChatRoom"]
        own = chat["methods"]["send_message"]
        assert own["file_path"] == chat["file_path"]
        mixed_in = chat["methods"]["presence_join"]
        assert mixed_in["file_path"].endswith("presence.py")
        line = Path(own["file_path"]).read_text().splitlines()[own["line_number"] - 1]
        assert "def send_message" in line

    def test_a_handler_is_listed_whatever_its_case(self):
        # Names were skipped unless all lowercase: the editor called a working binding unknown
        class CamelCased(Component, public=False):
            async def toggleAll(self) -> None: ...

        methods = extract_component_metadata(CamelCased)["methods"]
        assert methods["toggleAll"]["is_handler"] is True
        assert "Meta" not in methods, "a nested class is callable and is not a method"

    def test_a_field_kept_out_of_the_state_is_still_a_field(self):
        # A template passes it like any other; 1.0 left it out and the editor called it unknown
        class Kept(Component, public=False):
            class Meta:
                exclude_fields = {"scratch"}

            title: str
            scratch: str = ""

        fields = extract_component_metadata(Kept)["fields"]
        assert fields["scratch"]["in_state"] is False
        assert fields["title"]["in_state"] is True
        assert fields["title"]["required"] is True
        assert not {"id", "user", "wire", "session"} & set(fields)

    def test_a_component_that_reads_its_own_arguments_says_so(self):
        class Plain(Component, public=False):
            n: int = 0

        class Builds(Component, public=False):
            @classmethod
            def new(cls, **kwargs):
                return cls(n=kwargs.get("start", 0))

            n: int = 0

        class Child(LiveComponent, public=False):
            async def update(self, **assigns) -> None:
                await super().update(**assigns)

        class PlainChild(LiveComponent, public=False):
            n: int = 0

        assert extract_component_metadata(Plain)["accepts_extra_kwargs"] is False
        assert extract_component_metadata(Builds)["accepts_extra_kwargs"] is True
        assert extract_component_metadata(Child)["accepts_extra_kwargs"] is True
        assert extract_component_metadata(PlainChild)["accepts_extra_kwargs"] is False

    def test_properties_are_template_variables(self):
        class Shelf(Component, public=False):
            books: list[str] = []

            @property
            def count(self) -> int:
                """How many books."""
                return len(self.books)

            @functools.cached_property
            def first(self):
                return self.books[0]

            @property
            def _hidden(self):
                return 1

        properties = extract_component_metadata(Shelf)["properties"]
        assert set(properties) == {"count", "first"}, "framework and private properties are not the user's variables"
        assert properties["count"]["type"] == "int"
        assert properties["count"]["docstring"] == "How many books."
        assert properties["count"]["file_path"] == __file__
        assert properties["first"]["type"] is None

    def test_a_property_the_subclass_redefines_is_the_subclass_one(self):
        """The nearest definition in the MRO is the one a template reads, property or not."""

        class Base(Component, public=False):
            @property
            def value(self) -> str:
                return "base"

            @property
            def label(self) -> str:
                return "base"

            @functools.cached_property
            def cached(self) -> str:
                return "base"

        class Shadowed(Base, public=False):
            value: t.ClassVar[int] = 42

            @property
            def label(self) -> int:
                return 1

            def cached(self) -> int:  # a plain method now
                return 1

        properties = extract_component_metadata(Shadowed)["properties"]
        assert Shadowed.value == 42
        assert "value" not in properties, "a class attribute hides the parent's property"
        assert "cached" not in properties, "a method hides the parent's cached_property"
        assert properties["label"]["type"] == "int"
        assert extract_component_metadata(Base)["properties"]["value"]["type"] == "str"


@pytest.mark.unit
class TestTheRestOfTheProject:
    def test_a_modifier_says_what_its_argument_is(self, metadata):
        modifiers = metadata["modifiers"]
        assert modifiers["debounce"]["argument"] == "number"
        assert modifiers["key"]["argument"] == "text"
        assert modifiers["prevent"]["argument"] is None
        # has_argument is what 1.0 readers have
        assert all(m["has_argument"] is (m["argument"] is not None) for m in modifiers.values())

    def test_hooks_point_at_the_line_that_registers_them(self, metadata):
        timeago = metadata["hooks"]["Timeago"]
        assert timeago["static_path"] == "hooks/hooks/lifecycle.js"
        line = Path(timeago["file_path"]).read_text().splitlines()[timeago["line_number"] - 1]
        assert "Timeago" in line and "hooks" in line

    def test_function_components_are_listed_once(self):
        from wireview.function_components import _registry

        @function_component(name="lsp_probe_badge", slots={"icon": {"required": True}})
        def badge(text: str, tone: str = "info"):
            """A badge."""
            return text

        try:
            found = extract_function_components(template_roots())
        finally:
            for key in [key for key, fc in _registry.items() if fc is badge]:
                del _registry[key]

        probe = found["lsp_probe_badge"]
        assert [name for name in found if "lsp_probe_badge" in name] == ["lsp_probe_badge"], "not under its FQN too"
        assert probe["file_path"] == __file__
        assert probe["docstring"] == "A badge."
        assert probe["parameters"]["text"]["has_default"] is False
        assert probe["parameters"]["tone"]["default"] == "info"
        assert probe["slots"] == {"icon": {"required": True}}

    def test_template_dirs_are_where_the_loaders_look(self, metadata):
        dirs = metadata["template_dirs"]
        assert dirs and all(Path(d).is_dir() for d in dirs)
        assert any((Path(d) / "todo" / "list.html").is_file() for d in dirs)

    def test_template_dirs_come_from_the_loaders_not_app_dirs(self, settings, tmp_path):
        """A project that lists its loaders has APP_DIRS off: the apps' templates were missed."""
        templates = [dict(settings.TEMPLATES[0])]
        templates[0]["APP_DIRS"] = False
        templates[0]["DIRS"] = [str(tmp_path)]
        templates[0]["OPTIONS"] = {
            **templates[0]["OPTIONS"],
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    ["django.template.loaders.filesystem.Loader", "django.template.loaders.app_directories.Loader"],
                )
            ],
        }
        settings.TEMPLATES = templates
        dirs = template_roots()
        assert dirs[0] == tmp_path.resolve(), "DIRS first, as the filesystem loader comes first"
        assert any((d / "todo" / "list.html").is_file() for d in dirs)
        todo = extract_metadata()["components"]["XTodoList"]
        assert todo["template_path"] and Path(todo["template_path"]).is_file()


@pytest.mark.unit
class TestTemplateTagsAndFilters:
    """The engine's own tags and filters, so the editor needs no list of its own."""

    def test_builtins_come_from_the_engine(self, metadata):
        builtins = metadata["template_builtins"]
        assert {"if", "for", "block", "extends", "include", "load", "url", "with"} <= set(builtins["tags"])
        assert {"default", "date", "length", "safe", "upper"} <= set(builtins["filters"])

    def test_a_block_tag_names_its_end_and_what_stands_between(self, metadata):
        tags = metadata["template_builtins"]["tags"]
        assert (tags["if"]["end"], tags["if"]["intermediate"]) == ("endif", ["elif", "else"])
        assert (tags["for"]["end"], tags["for"]["intermediate"]) == ("endfor", ["empty"])
        assert tags["comment"]["end"] == "endcomment"  # skip_past, not parse
        assert tags["load"]["end"] is None
        i18n = metadata["template_libraries"]["i18n"]["tags"]
        assert (i18n["blocktranslate"]["end"], i18n["blocktranslate"]["intermediate"]) == (
            "endblocktranslate",
            ["plural"],
        )

    def test_every_builtin_block_tag_is_recognised(self, metadata):
        # The names come out of each compile function's source. A Django that writes one
        # differently would leave its end tag unknown, and the editor would flag it.
        blocks = {name for name, tag in metadata["template_builtins"]["tags"].items() if tag["end"]}
        expected = {
            "autoescape",
            "block",
            "comment",
            "filter",
            "for",
            "if",
            "ifchanged",
            "spaceless",
            "verbatim",
            "with",
        }
        assert expected <= blocks
        for name in blocks:
            assert metadata["template_builtins"]["tags"][name]["end"] == f"end{name}"

    def test_the_wireview_library_is_loadable_and_its_blocks_end(self, metadata):
        library = metadata["template_libraries"]["wireview"]
        assert library["module"] == "wireview.templatetags.wireview"
        # Where {% load wireview %} goes to
        assert Path(library["file_path"]) == Path(__file__).parent.parent / "wireview" / "templatetags" / "wireview.py"
        ends = {name: tag["end"] for name, tag in library["tags"].items() if tag["end"]}
        assert ends == {
            "component_block": "endcomponent",
            "fill": "endfill",
            "func_block": "endfunc",
            "live_component_block": "endlive_component",
        }
        assert set(library["filters"]) == {"str", "concat"}

    def test_a_tag_points_at_the_function_the_user_wrote(self, metadata):
        # simple_tag registers a closure from django/template/library.py; the docstring and the
        # line an editor wants are the wrapped function's
        on = metadata["template_libraries"]["wireview"]["tags"]["on"]
        assert on["file_path"].endswith("templatetags/wireview.py")
        assert "Bind an event handler" in on["docstring"]
        line = Path(on["file_path"]).read_text().splitlines()[on["line_number"] - 1]
        assert line.startswith("@register.simple_tag") or line.startswith("def on(")

    def test_a_filter_says_whether_it_takes_an_argument(self, metadata):
        filters = metadata["template_builtins"]["filters"]
        assert filters["upper"]["argument"] == "none"
        assert filters["default"]["argument"] == "required"
        assert filters["date"]["argument"] == "optional"
        # needs_autoescape: Django passes autoescape itself, it is not the filter's argument
        assert filters["linebreaks"]["argument"] == "none"
        assert filters["urlizetrunc"]["argument"] == "required"

    def test_a_simple_block_tag_ends_where_it_was_told(self):
        register = template.Library()

        @register.simple_block_tag
        def panel(content):
            return content

        @register.simple_block_tag(end_name="closecard")
        def card(content):
            return content

        tags = extract_library(register)["tags"]
        assert tags["panel"]["end"] == "endpanel"
        assert tags["card"]["end"] == "closecard"
