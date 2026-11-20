"""Tests for the JS command builder."""

import json

import pytest

from wireview.js import JS


class TestJSBuilder:
    """Test JS command builder basic functionality."""

    @pytest.mark.unit
    def test_empty_js(self):
        """Empty JS object should have no commands."""
        js = JS()
        assert len(js) == 0
        assert not js
        assert js.to_json() == "[]"

    @pytest.mark.unit
    def test_single_command(self):
        """Single command should produce correct JSON."""
        js = JS().show("#modal")
        assert len(js) == 1
        assert js
        commands = json.loads(js.to_json())
        assert commands[0]["cmd"] == "show"
        assert commands[0]["to"] == "#modal"

    @pytest.mark.unit
    def test_command_chaining(self):
        """Commands should be chainable."""
        js = JS().show("#modal").add_class("#btn", "active").push("save")
        assert len(js) == 3
        commands = json.loads(js.to_json())
        assert commands[0]["cmd"] == "show"
        assert commands[1]["cmd"] == "add_class"
        assert commands[2]["cmd"] == "push"

    @pytest.mark.unit
    def test_str_returns_json(self):
        """str() should return JSON representation."""
        js = JS().hide("#modal")
        assert str(js) == js.to_json()

    @pytest.mark.unit
    def test_repr(self):
        """repr() should show internal structure."""
        js = JS().show("#modal")
        assert "JS(" in repr(js)
        assert "show" in repr(js)


class TestVisibilityCommands:
    """Test show, hide, toggle commands."""

    @pytest.mark.unit
    def test_show_basic(self):
        """show() should create show command."""
        js = JS().show("#el")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "show", "to": "#el"}

    @pytest.mark.unit
    def test_show_with_transition_string(self):
        """show() with string transition."""
        js = JS().show("#el", transition="fade-in")
        commands = json.loads(js.to_json())
        assert commands[0]["transition"] == {"transition": "fade-in"}

    @pytest.mark.unit
    def test_show_with_transition_tuple(self):
        """show() with tuple transition (class, duration)."""
        js = JS().show("#el", transition=("slide-in", 300))
        commands = json.loads(js.to_json())
        assert commands[0]["transition"] == {
            "transition": "slide-in",
            "time": 300,
        }

    @pytest.mark.unit
    def test_show_with_display(self):
        """show() with custom display value."""
        js = JS().show("#el", display="flex")
        commands = json.loads(js.to_json())
        assert commands[0]["display"] == "flex"

    @pytest.mark.unit
    def test_show_without_selector(self):
        """show() without selector targets current element."""
        js = JS().show()
        commands = json.loads(js.to_json())
        assert "to" not in commands[0]

    @pytest.mark.unit
    def test_hide_basic(self):
        """hide() should create hide command."""
        js = JS().hide("#modal")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "hide", "to": "#modal"}

    @pytest.mark.unit
    def test_hide_with_transition(self):
        """hide() with transition."""
        js = JS().hide("#el", transition="fade-out")
        commands = json.loads(js.to_json())
        assert commands[0]["transition"] == {"transition": "fade-out"}

    @pytest.mark.unit
    def test_toggle_basic(self):
        """toggle() should create toggle command."""
        js = JS().toggle("#dropdown")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "toggle", "to": "#dropdown"}

    @pytest.mark.unit
    def test_toggle_with_transitions(self):
        """toggle() with show and hide transitions."""
        js = JS().toggle(
            "#menu",
            show_transition="slide-down",
            hide_transition="slide-up",
        )
        commands = json.loads(js.to_json())
        assert commands[0]["show"] == {"transition": "slide-down"}
        assert commands[0]["hide"] == {"transition": "slide-up"}


class TestCSSClassCommands:
    """Test add_class, remove_class, toggle_class commands."""

    @pytest.mark.unit
    def test_add_class_string(self):
        """add_class() with string class."""
        js = JS().add_class("#btn", "active")
        commands = json.loads(js.to_json())
        assert commands[0] == {
            "cmd": "add_class",
            "to": "#btn",
            "classes": "active",
        }

    @pytest.mark.unit
    def test_add_class_list(self):
        """add_class() with list of classes."""
        js = JS().add_class("#card", ["highlighted", "shadow"])
        commands = json.loads(js.to_json())
        assert commands[0]["classes"] == "highlighted shadow"

    @pytest.mark.unit
    def test_add_class_with_transition(self):
        """add_class() with transition."""
        js = JS().add_class("#el", "active", transition="fade")
        commands = json.loads(js.to_json())
        assert commands[0]["transition"] == {"transition": "fade"}

    @pytest.mark.unit
    def test_remove_class(self):
        """remove_class() should create remove_class command."""
        js = JS().remove_class("#btn", "loading")
        commands = json.loads(js.to_json())
        assert commands[0]["cmd"] == "remove_class"
        assert commands[0]["classes"] == "loading"

    @pytest.mark.unit
    def test_toggle_class(self):
        """toggle_class() should create toggle_class command."""
        js = JS().toggle_class("#menu", "open")
        commands = json.loads(js.to_json())
        assert commands[0]["cmd"] == "toggle_class"
        assert commands[0]["classes"] == "open"


class TestAttributeCommands:
    """Test set_attr, remove_attr commands."""

    @pytest.mark.unit
    def test_set_attr(self):
        """set_attr() should create set_attr command."""
        js = JS().set_attr("#input", "disabled", "true")
        commands = json.loads(js.to_json())
        assert commands[0] == {
            "cmd": "set_attr",
            "to": "#input",
            "attr": "disabled",
            "val": "true",
        }

    @pytest.mark.unit
    def test_remove_attr(self):
        """remove_attr() should create remove_attr command."""
        js = JS().remove_attr("#input", "disabled")
        commands = json.loads(js.to_json())
        assert commands[0] == {
            "cmd": "remove_attr",
            "to": "#input",
            "attr": "disabled",
        }


class TestSetValueCommand:
    """Test set_value command for input elements."""

    @pytest.mark.unit
    def test_set_value_empty(self):
        """set_value() should create set_value command with empty value."""
        js = JS().set_value("#input")
        commands = json.loads(js.to_json())
        assert commands[0] == {
            "cmd": "set_value",
            "to": "#input",
            "value": "",
        }

    @pytest.mark.unit
    def test_set_value_with_value(self):
        """set_value() should create set_value command with specified value."""
        js = JS().set_value("#name", "John")
        commands = json.loads(js.to_json())
        assert commands[0] == {
            "cmd": "set_value",
            "to": "#name",
            "value": "John",
        }

    @pytest.mark.unit
    def test_set_value_without_selector(self):
        """set_value() without selector targets current element."""
        js = JS().set_value()
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "set_value", "value": ""}


class TestFocusCommands:
    """Test focus commands."""

    @pytest.mark.unit
    def test_focus(self):
        """focus() should create focus command."""
        js = JS().focus("#search-input")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "focus", "to": "#search-input"}

    @pytest.mark.unit
    def test_focus_first(self):
        """focus_first() should create focus_first command."""
        js = JS().focus_first("#form")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "focus_first", "to": "#form"}

    @pytest.mark.unit
    def test_focus_first_input_only(self):
        """focus_first() with input_only flag."""
        js = JS().focus_first("#modal", input_only=True)
        commands = json.loads(js.to_json())
        assert commands[0]["input_only"] is True


class TestTransitionCommand:
    """Test transition command."""

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["fade-in 200ms", "fade-out 1.5s", "pulse 300"])
    def test_a_duration_inside_the_class_string_is_refused(self, value):
        # The string is class names only; the duration would stay on the element as a class.
        with pytest.raises(ValueError, match="duration separately"):
            JS().show("#x", transition=value)

    @pytest.mark.unit
    def test_transition_basic(self):
        """transition() should create transition command."""
        js = JS().transition("#card", "shake")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "transition", "to": "#card", "transition": "shake"}

    @pytest.mark.unit
    def test_transition_with_time(self):
        """transition() with explicit time."""
        js = JS().transition("#btn", "pulse", time=500)
        commands = json.loads(js.to_json())
        assert commands[0]["transition"] == "pulse"
        assert commands[0]["time"] == 500

    @pytest.mark.unit
    def test_transition_tuple(self):
        """transition() with tuple config."""
        js = JS().transition("#modal", ("fade-in", 300))
        commands = json.loads(js.to_json())
        assert commands[0]["transition"] == "fade-in"
        assert commands[0]["time"] == 300

    @pytest.mark.unit
    def test_transition_no_selector(self):
        """transition() without selector targets current element."""
        js = JS().transition(transition="bounce")
        commands = json.loads(js.to_json())
        assert "to" not in commands[0]
        assert commands[0]["transition"] == "bounce"


class TestPushCommand:
    """Test push command (server communication)."""

    @pytest.mark.unit
    def test_push_basic(self):
        """push() should create push command."""
        js = JS().push("save")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "push", "event": "save"}

    @pytest.mark.unit
    def test_push_with_value(self):
        """push() with value data."""
        js = JS().push("delete", value={"id": 123, "confirm": True})
        commands = json.loads(js.to_json())
        assert commands[0]["event"] == "delete"
        assert commands[0]["value"] == {"id": 123, "confirm": True}

    @pytest.mark.unit
    def test_push_with_target(self):
        """push() with target selector."""
        js = JS().push("submit", target="#other-component")
        commands = json.loads(js.to_json())
        assert commands[0]["target"] == "#other-component"


class TestBrowserCommands:
    """Test navigate, dispatch commands."""

    @pytest.mark.unit
    def test_navigate_basic(self):
        """navigate() should create navigate command."""
        js = JS().navigate("/dashboard")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "navigate", "url": "/dashboard"}

    @pytest.mark.unit
    def test_navigate_replace(self):
        """navigate() with replace flag."""
        js = JS().navigate("/login", replace=True)
        commands = json.loads(js.to_json())
        assert commands[0]["replace"] is True

    @pytest.mark.unit
    def test_dispatch_basic(self):
        """dispatch() should create dispatch command."""
        js = JS().dispatch("my-event")
        commands = json.loads(js.to_json())
        assert commands[0] == {"cmd": "dispatch", "event": "my-event"}

    @pytest.mark.unit
    def test_dispatch_with_detail(self):
        """dispatch() with custom detail data."""
        js = JS().dispatch("notify", detail={"message": "Done!"})
        commands = json.loads(js.to_json())
        assert commands[0]["detail"] == {"message": "Done!"}

    @pytest.mark.unit
    def test_dispatch_with_target(self):
        """dispatch() with target selector."""
        js = JS().dispatch("custom-event", to="#target")
        commands = json.loads(js.to_json())
        assert commands[0]["to"] == "#target"

    @pytest.mark.unit
    def test_dispatch_no_bubbles(self):
        """dispatch() with bubbles=False."""
        js = JS().dispatch("local-event", bubbles=False)
        commands = json.loads(js.to_json())
        assert commands[0]["bubbles"] is False


class TestComplexChaining:
    """Test complex command chains."""

    @pytest.mark.unit
    def test_modal_workflow(self):
        """Test typical modal open/close workflow."""
        # Open modal with animation
        open_js = (
            JS()
            .show("#modal", transition="fade-in")
            .add_class("#overlay", "active")
            .focus_first("#modal", input_only=True)
        )
        commands = json.loads(open_js.to_json())
        assert len(commands) == 3
        assert commands[0]["cmd"] == "show"
        assert commands[1]["cmd"] == "add_class"
        assert commands[2]["cmd"] == "focus_first"

    @pytest.mark.unit
    def test_form_submit_workflow(self):
        """Test form submission with UI feedback."""
        js = JS().add_class("#submit-btn", "loading").set_attr("#submit-btn", "disabled", "true").push("submit")
        commands = json.loads(js.to_json())
        assert len(commands) == 3
        assert commands[2]["cmd"] == "push"
        assert commands[2]["event"] == "submit"

    @pytest.mark.unit
    def test_notification_workflow(self):
        """Test notification show and auto-hide."""
        js = JS().show("#toast", transition=("slide-in", 200)).dispatch("toast:shown", detail={"type": "success"})
        commands = json.loads(js.to_json())
        assert len(commands) == 2


@pytest.mark.unit
def test_a_transition_dict_declares_only_what_the_client_reads():
    """TransitionConfig declared ``to``, and the client's applyTransition never read it: a key that type-checks
    and does nothing."""
    import re
    from pathlib import Path

    from wireview.js import TransitionConfig

    source = (Path(__file__).resolve().parent.parent / "wireview/static/wireview/wireview.js").read_text(
        encoding="utf-8"
    )
    body = re.search(r"async function runTransition\(handle, config\) \{\n(.*?)\n\}", source, re.S)
    assert body, "runTransition moved: point this test at what reads a transition"
    assert set(TransitionConfig.__annotations__) == set(re.findall(r"config\.(\w+)", body.group(1)))


# toggle_attr, exec, relative targets, combining, JS() in arguments (#237, GAP-041)


class TestToggleAttr:
    def test_present_or_absent(self):
        assert JS().toggle_attr("#menu", "open")._commands == [
            {"cmd": "toggle_attr", "to": "#menu", "attr": "open", "val": ""}
        ]

    def test_between_two_values(self):
        assert JS().toggle_attr(None, "aria-expanded", ("true", "false"))._commands == [
            {"cmd": "toggle_attr", "attr": "aria-expanded", "vals": ["true", "false"]}
        ]

    @pytest.mark.parametrize("value", [("a",), ("a", "b", "c"), ("a", 1)])
    def test_a_pair_is_two_strings(self, value):
        with pytest.raises(ValueError, match="pair"):
            JS().toggle_attr("#m", "x", value)

    def test_a_list_pair_is_a_pair(self):
        assert JS().toggle_attr("#m", "x", ["a", "b"])._commands[0]["vals"] == ["a", "b"]

    @pytest.mark.parametrize("value", [True, 1, None])
    def test_a_value_is_a_string(self, value):
        with pytest.raises(ValueError, match="str"):
            JS().toggle_attr("#m", "x", value)

    def test_it_needs_the_attribute(self):
        with pytest.raises(ValueError, match="name"):
            JS().toggle_attr("#m")


class TestRelativeTargets:
    def test_inner_and_closest_render_as_objects(self):
        commands = JS().hide(JS.closest(".card")).add_class(JS.inner(".chevron"), "open")._commands
        assert commands[0]["to"] == {"closest": ".card"}
        assert commands[1]["to"] == {"inner": ".chevron"}

    def test_every_command_with_a_target_takes_one(self):
        to = JS.inner("li")
        chain = (
            JS()
            .show(to)
            .toggle(to)
            .remove_class(to, "a")
            .toggle_class(to, "a")
            .transition(to, "pulse")
            .set_attr(to, "a", "1")
            .remove_attr(to, "a")
            .set_value(to, "")
            .focus(to)
            .focus_first(to)
            .dispatch("x", to=to)
            .exec("data-x", to=to)
        )
        assert all(command["to"] == {"inner": "li"} for command in chain._commands)

    @pytest.mark.parametrize("selector", ["", "  ", None, 3])
    def test_a_relative_target_takes_a_selector(self, selector):
        with pytest.raises(ValueError, match="CSS selector"):
            JS.inner(selector)

    def test_push_names_its_component_over_the_document(self):
        with pytest.raises(TypeError, match="push"):
            JS().push("save", target=JS.closest("[wireview-component]"))  # type: ignore[arg-type]


class TestExec:
    def test_names_an_attribute(self):
        assert JS().exec("data-cancel", to="#modal")._commands == [
            {"cmd": "exec", "attr": "data-cancel", "to": "#modal"}
        ]
        assert JS().exec("wire-on-click")._commands == [{"cmd": "exec", "attr": "wire-on-click"}]

    @pytest.mark.parametrize("attr", ["", "data x", 'a"b', "a=b", None])
    def test_not_an_attribute_name_is_refused(self, attr):
        with pytest.raises(ValueError, match="attribute"):
            JS().exec(attr)


class TestCombining:
    def test_concat_leaves_both_chains_as_they_were(self):
        close = JS().hide("#modal")
        save = JS().push("save")
        both = close.concat(save)
        assert [c["cmd"] for c in both._commands] == ["hide", "push"]
        assert close._commands == [{"cmd": "hide", "to": "#modal"}]
        assert save._commands == [{"cmd": "push", "event": "save"}]

    def test_a_later_command_on_the_result_does_not_reach_the_originals(self):
        close = JS().hide("#modal")
        both = close + JS().push("save", value={"n": 1})
        both.add_class("#x", "y")
        both._commands[1]["value"]["n"] = 2
        assert len(close) == 1
        assert both is not close

    def test_the_copy_is_deep(self):
        save = JS().push("save", value={"n": 1})
        joined = JS().concat(save)
        joined._commands[0]["value"]["n"] = 2
        assert save._commands[0]["value"] == {"n": 1}

    def test_only_chains_join(self):
        with pytest.raises(TypeError):
            JS().concat("hide")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            JS() + [{"cmd": "hide"}]  # type: ignore[operator]

    def test_many_at_once(self):
        a, b, c = JS().show("#a"), JS().show("#b"), JS().show("#c")
        assert [cmd["to"] for cmd in a.concat(b, c)._commands] == ["#a", "#b", "#c"]


class TestJsInArguments:
    def test_a_chain_in_a_pushed_value_goes_as_its_commands(self):
        chain = JS().push("save", value={"undo": JS().show("#m")})
        assert json.loads(chain.to_json())[0]["value"] == {"undo": [{"cmd": "show", "to": "#m"}]}

    def test_a_chain_in_a_bindings_arguments_goes_as_its_commands(self):
        from wireview.event_transpiler import binding

        _, value = binding("click", "remove", {"undo": JS().show("#m")})
        assert json.loads(value)["a"] == {"undo": [{"cmd": "show", "to": "#m"}]}


class TestProtocolNeeded:
    def test_older_commands_need_nothing(self):
        from wireview.js import protocol_needed

        assert protocol_needed(JS().show("#a").set_attr("#a", "x", "1").push("save")._commands) == 0

    @pytest.mark.parametrize(
        "chain",
        [
            JS().toggle_attr("#a", "x"),
            JS().exec("data-x"),
            JS().show(JS.inner("li")),
            JS().dispatch("x", to=JS.closest("div")),
        ],
    )
    def test_new_ones_need_the_version_that_runs_them(self, chain):
        from wireview.core.rendered import JS_CHAINS_SINCE
        from wireview.js import protocol_needed

        assert protocol_needed(chain._commands) == JS_CHAINS_SINCE
