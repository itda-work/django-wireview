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
