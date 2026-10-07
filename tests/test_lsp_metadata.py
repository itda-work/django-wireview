"""Tests for the wireview_lsp management command."""

import functools
import json
import typing as t
import weakref
from io import StringIO
from pathlib import Path

import pydantic
import pytest
from django import template
from django.core.management import call_command
from pydantic import AfterValidator, PlainSerializer, PrivateAttr

import wireview as wireview_package
from wireview import Component, LiveComponent, function_component
from wireview.core.handlers import is_client_callable
from wireview.management.commands.wireview_lsp import (
    METADATA_VERSION,
    extract_component_metadata,
    extract_fields,
    extract_function_components,
    extract_library,
    extract_metadata,
    find_template,
    method_metadata,
    original_function,
    serialize_default,
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
        assert data["version"] == METADATA_VERSION == "2.0"

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
    # A mixin's and pydantic's methods are the framework's: never handlers, so 2.0 only names them (#162)
    assert "presence_join" in chat["inherited_methods"]["wireview.features.presence.PresenceMixin"]
    assert "model_dump" in chat["inherited_methods"]["pydantic.main.BaseModel"]
    assert "presence_join" not in chat["methods"] and "model_dump" not in chat["methods"]


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
        mixed_in = metadata["framework_methods"]["wireview.features.presence.PresenceMixin"]["presence_join"]
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


@pytest.mark.integration
def test_the_metadata_is_the_same_on_every_run(tmp_path):
    """Two runs differ only in when they ran: a repr that holds a memory address made every run differ."""
    import subprocess
    import sys

    manage = Path(__file__).parent / "manage.py"
    outputs = []
    for run in range(2):
        output = tmp_path / f"metadata-{run}.json"
        subprocess.run(
            [sys.executable, str(manage), "wireview_lsp", "--output", str(output)],
            cwd=manage.parent,
            check=True,
            capture_output=True,
        )
        data = json.loads(output.read_text())
        del data["generated_at"]
        outputs.append(data)
    assert outputs[0] == outputs[1]
    assert " at 0x" not in json.dumps(outputs[0])


# A component whose reprs hold names with <...> in them: a lambda, a nested function, a weakref
_ADDRESS_PROBE = """
import json, os, sys, typing as t, weakref
os.environ["DJANGO_SETTINGS_MODULE"] = "testproj.settings"
import django
django.setup()
from pydantic import AfterValidator, BeforeValidator, PlainSerializer
from wireview import Component
from wireview.management.commands.wireview_lsp import extract_fields, serialize_default

def outer():
    def inner(v):
        return v
    return inner

class Item:
    pass

item = Item()

class AddressProbe(Component, public=False):
    lam: t.Annotated[int, AfterValidator(lambda v: v)] | None = None
    loc: t.Annotated[int, BeforeValidator(outer())] | None = None
    ser: t.Annotated[int, PlainSerializer(lambda v: v)] | None = None
    lambdas: list = [lambda: 1]
    nested: list = [outer()]

out = {name: [field["annotation"], field["default"]] for name, field in extract_fields(AddressProbe).items()}
out["weakref"] = serialize_default([weakref.ref(item)])
print(json.dumps(out, sort_keys=True))
"""


@pytest.mark.integration
def test_a_lambda_a_nested_function_and_a_weakref_are_the_same_on_every_run():
    """Their names hold <lambda> and <locals>: the address after such a name stayed."""
    import subprocess
    import sys

    tests = Path(__file__).parent
    outputs = [
        subprocess.run(
            [sys.executable, "-c", _ADDRESS_PROBE], cwd=tests, check=True, capture_output=True, text=True
        ).stdout
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]
    assert " at 0x" not in outputs[0]
    assert "<lambda>" in outputs[0] and "<locals>" in outputs[0]


class _Marker:
    """An object whose repr is the default one: with a memory address."""


@pytest.mark.unit
class TestNoMemoryAddresses:
    """Only the addresses of objects come out: a string that reads like one is data."""

    def test_a_string_in_a_default_stays_as_it_is(self):
        assert serialize_default("meet at 0xCAFE") == "meet at 0xCAFE"
        assert serialize_default(["meet at 0xCAFE"]) == "['meet at 0xCAFE']"
        assert serialize_default({"where": "meet at 0xCAFE"}) == "{'where': 'meet at 0xCAFE'}"
        assert serialize_default(("<a at 0x1>",)) == "('<a at 0x1>',)"

    def test_an_object_in_a_default_loses_its_address(self):
        assert serialize_default([_Marker(), "meet at 0xCAFE"]) == (f"[<{__name__}._Marker object>, 'meet at 0xCAFE']")
        assert serialize_default(_Marker()) == f"<{__name__}._Marker object>"

    def test_a_lambda_a_nested_function_and_a_weakref_lose_their_addresses(self):
        def outer():
            def inner(v):
                return v

            return inner

        lambdas = serialize_default([lambda: 1])
        assert " at 0x" not in lambdas and "<lambda>" in lambdas
        nested = serialize_default([outer()])
        assert " at 0x" not in nested and "<locals>.outer.<locals>.inner>" in nested
        target = _Marker()
        reference = serialize_default([weakref.ref(target)])
        assert " at 0x" not in reference and reference.startswith("[<weakref; to ")

    def test_a_validator_in_a_union_loses_its_address(self):
        class Validated(Component, public=False):
            lam: t.Annotated[int, AfterValidator(lambda v: v)] | None = None
            ser: t.Annotated[int, PlainSerializer(lambda v: v)] | None = None

        for name, field in extract_fields(Validated).items():
            assert " at 0x" not in field["annotation"], name
            assert "<lambda>" in field["annotation"], name

    def test_a_literal_keeps_its_strings_and_annotated_loses_its_addresses(self):
        class Addresses(Component, public=False):
            choice: t.Literal["meet at 0xCAFE"] = "meet at 0xCAFE"
            # Inside a union: pydantic keeps the Annotated (at the top it moves the metadata out)
            marked: t.Annotated[int, _Marker()] | None = None

        fields = extract_fields(Addresses)
        assert fields["choice"]["annotation"] == "typing.Literal['meet at 0xCAFE']"
        assert fields["choice"]["default"] == "meet at 0xCAFE"
        assert " at 0x" not in fields["marked"]["annotation"]
        assert f"<{__name__}._Marker object>" in fields["marked"]["annotation"]


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

    def test_a_tag_named_like_a_django_block_is_read_for_itself(self):
        """Only Django's own blocktranslate runs to an end tag: another library's tag of that name may not."""
        from django.templatetags.i18n import do_block_translate

        register = template.Library()
        register.simple_tag(lambda: "OK", name="blocktranslate")
        register.tag("translateblock", do_block_translate)

        tags = extract_library(register)["tags"]
        assert tags["blocktranslate"]["end"] is None
        # Django's function under another name ends where it computes from its name
        assert (tags["translateblock"]["end"], tags["translateblock"]["intermediate"]) == (
            "endtranslateblock",
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

    def test_a_filter_says_whether_the_filter_tag_refuses_it(self, metadata):
        filters = metadata["template_builtins"]["filters"]
        assert filters["safe"]["forbidden_in_filter_tag"] is True
        assert filters["escape"]["forbidden_in_filter_tag"] is True
        assert filters["upper"]["forbidden_in_filter_tag"] is False
        assert filters["force_escape"]["forbidden_in_filter_tag"] is False

    def test_the_filter_tag_refuses_a_function_by_the_name_it_was_last_registered_under(self):
        """do_filter reads the function's _filter_name, which the last registration of it set."""
        from django.template.defaultfilters import safe

        register = template.Library()

        @register.filter(name="safe")
        def harmless(value):
            return value

        try:
            register.filter("okay", safe)
            filters = extract_library(register)["filters"]
            assert filters["safe"]["forbidden_in_filter_tag"] is True, "another function, by the name safe"
            assert filters["okay"]["forbidden_in_filter_tag"] is False, "Django's safe, now named okay"
            builtins = extract_metadata()["template_builtins"]["filters"]
            assert builtins["safe"]["forbidden_in_filter_tag"] is False, "the same function: the builtin too"
        finally:
            safe._filter_name = "safe"

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


# Format 2.0 (#162): what the framework defines is described once, at the top.
# The same entries on every component were 97% of the output.


@pytest.mark.unit
class TestFrameworkMethodsDescribedOnce:
    def test_a_component_lists_only_what_its_own_code_defines(self, metadata):
        framework_code = (Path(wireview_package.__file__).parent, Path(pydantic.__file__).parent)
        for component in metadata["components"].values():
            for name, method in component["methods"].items():
                assert not Path(method["file_path"]).is_relative_to(framework_code[0]), (component["name"], name)
                assert not Path(method["file_path"]).is_relative_to(framework_code[1]), (component["name"], name)

    def test_every_inherited_name_is_described_as_the_component_has_it(self, metadata):
        """Read back onto a component, the description is what 1.1 listed there: nothing is lost."""
        described = metadata["framework_methods"]
        for key, cls in Component._all.items():
            for owner, names in metadata["components"][key]["inherited_methods"].items():
                for name in names:
                    attr = getattr(cls, name)
                    assert described[owner][name] == method_metadata(original_function(attr)), (key, name)
                    assert not is_client_callable(cls, name), (key, name)
        assert all("is_handler" not in method for owner in described.values() for method in owner.values())

    def test_an_override_of_a_framework_name_is_the_components_own(self):
        class Overrides(Component, public=False):
            _scratch: int = PrivateAttr(default=0)

            async def joined(self) -> None:
                """Mine."""

            def model_post_init(self, context: t.Any) -> None:
                """Mine too: pydantic wraps it to set the private attributes first."""

        extracted = extract_component_metadata(Overrides)
        joined = extracted["methods"]["joined"]
        assert (joined["is_handler"], joined["docstring"], joined["file_path"]) == (False, "Mine.", __file__)
        assert extracted["methods"]["model_post_init"]["file_path"] == __file__
        assert not any("joined" in names for names in extracted["inherited_methods"].values())

    def test_a_method_pydantic_puts_on_the_class_is_the_frameworks(self):
        class Private(Component, public=False):
            _scratch: int = PrivateAttr(default=0)

        assert "model_post_init" in vars(Private), "pydantic sets it on the class itself"
        framework: dict = {}
        extracted = extract_component_metadata(Private, framework_methods=framework)
        assert "model_post_init" not in extracted["methods"]
        (owner,) = [owner for owner, names in extracted["inherited_methods"].items() if "model_post_init" in names]
        # The path of the function pydantic runs, whichever pydantic this is: its module alone
        # would stand for every function that module puts on a class under that name
        injected = original_function(vars(Private)["model_post_init"])
        assert owner == f"{injected.__module__}.{injected.__qualname__}"
        assert framework[owner]["model_post_init"] == method_metadata(injected)

    def test_two_injected_functions_under_one_name_are_two_entries(self):
        def init_one(self, context: t.Any, /) -> None:
            """One."""

        def init_two(self, context: t.Any, /) -> None:
            """Two."""

        for func in (init_one, init_two):
            func.__module__ = "pydantic._internal._fake"
            func.__qualname__ = func.__name__

        class One(Component, public=False):
            pass

        class Two(Component, public=False):
            pass

        One.model_post_init = init_one  # type: ignore[method-assign]
        Two.model_post_init = init_two  # type: ignore[method-assign]
        shared: dict = {}
        one = extract_component_metadata(One, framework_methods=shared)["inherited_methods"]
        two = extract_component_metadata(Two, framework_methods=shared)["inherited_methods"]
        assert "model_post_init" in one["pydantic._internal._fake.init_one"]
        assert "model_post_init" in two["pydantic._internal._fake.init_two"]
        assert shared["pydantic._internal._fake.init_one"]["model_post_init"]["docstring"] == "One."
        assert shared["pydantic._internal._fake.init_two"]["model_post_init"]["docstring"] == "Two."

    def test_a_handler_is_never_the_frameworks(self):
        # A framework function a user class names as its own handler is the component's
        def shout(self) -> None:
            """Shout."""

        shout.__module__ = "wireview.somewhere"

        class Borrowed(Component, public=False):
            pass

        Borrowed.shout = shout  # type: ignore[attr-defined]
        extracted = extract_component_metadata(Borrowed)
        assert extracted["methods"]["shout"]["is_handler"] is True

    @pytest.mark.parametrize("reversed_order", [False, True], ids=["ordinary-first", "reused-first"])
    @pytest.mark.parametrize("reuse", ["alias", "rebound"])
    def test_a_framework_function_a_user_class_sets_is_not_shared(self, reuse, reversed_order):
        """A framework function a user class sets is the component's own entry.

        ``leaving = Component.joined`` is joined's function under leaving's name, and
        ``new = staticmethod(Component.new.__func__)`` is new's function bound otherwise: its
        signature takes ``cls``. Shared, either was whichever component came first's, and the
        other's hover, arguments and definition were the wrong ones.
        """

        class Ordinary(Component, public=False):
            pass

        if reuse == "alias":

            class Reused(Component, public=False):
                leaving = Component.joined

            name = "leaving"
        else:

            class Reused(Component, public=False):
                new = staticmethod(Component.new.__func__)

            name = "new"

        classes = (Reused, Ordinary) if reversed_order else (Ordinary, Reused)
        shared: dict = {}
        extracted = [(cls, extract_component_metadata(cls, framework_methods=shared)) for cls in classes]
        for cls, component in extracted:
            expanded = {
                each: shared[owner][each] for owner, names in component["inherited_methods"].items() for each in names
            }
            expanded.update({each: _without_handler(method) for each, method in component["methods"].items()})
            # As 1.1 described each method on each component
            assert expanded == {each: method_metadata(original_function(getattr(cls, each))) for each in expanded}, (
                cls.__name__
            )
        reused = dict(extracted)[Reused]
        assert reused["methods"][name]["is_handler"] is False
        if reuse == "rebound":
            assert list(reused["methods"]["new"]["parameters"]) == ["cls", "kwargs"]
            assert (
                list(dict(extracted)[Ordinary]["inherited_methods"])
                and "cls" not in (shared["wireview.core.component.Component"]["new"]["parameters"])
            )


def _without_handler(method: dict) -> dict:
    return {key: value for key, value in method.items() if key != "is_handler"}
