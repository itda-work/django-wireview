"""Tests for event transpiler with JS command support."""

import pytest

from wireview.event_transpiler import transpile
from wireview.js import JS


class TestTranspileStringCommands:
    """Test transpile() with string commands (legacy)."""

    @pytest.mark.unit
    def test_basic_command(self):
        """Basic string command should produce wireview.send call."""
        event, code = transpile("click", "increment", {})
        assert event == "onclick"
        assert "wireview.send" in code
        assert "'increment'" in code
        # Event type passed for loading class support
        assert "'click'" in code

    @pytest.mark.unit
    def test_command_with_kwargs(self):
        """String command with kwargs should include them in JSON."""
        event, code = transpile("click", "save", {"id": 123})
        assert "wireview.send" in code
        assert "123" in code

    @pytest.mark.unit
    def test_debounce_modifier(self):
        """debounce modifier should wrap in debounce call."""
        event, code = transpile("input.debounce.300", "search", {})
        assert event == "oninput"
        assert "wireview.debounce(300)" in code

    @pytest.mark.unit
    def test_throttle_modifier(self):
        """throttle modifier should wrap in throttle call."""
        event, code = transpile("scroll.throttle.100", "update_position", {})
        assert event == "onscroll"
        assert "wireview.throttle(100)" in code

    @pytest.mark.unit
    def test_prevent_modifier(self):
        """prevent modifier should add preventDefault."""
        event, code = transpile("click.prevent", "submit", {})
        assert "event.preventDefault()" in code

    @pytest.mark.unit
    def test_key_modifier(self):
        """enter key modifier should check event.key."""
        event, code = transpile("keydown.enter", "submit", {})
        assert event == "onkeydown"
        assert "event.key" in code or "enter" in code.lower()


class TestTranspileJSCommands:
    """Test transpile() with JS command builder objects."""

    @pytest.mark.unit
    def test_basic_js_command(self):
        """JS command should produce wireview.exec call."""
        js = JS().show("#modal")
        event, code = transpile("click", js, {})
        assert event == "onclick"
        assert "wireview.exec" in code
        assert "event.target" in code

    @pytest.mark.unit
    def test_js_command_contains_json(self):
        """JS command code should contain serialized commands."""
        js = JS().toggle("#dropdown")
        event, code = transpile("click", js, {})
        assert '"cmd"' in code or "'cmd'" in code
        assert "toggle" in code

    @pytest.mark.unit
    def test_js_with_push(self):
        """JS with push should include event in JSON."""
        js = JS().push("save")
        event, code = transpile("click", js, {})
        assert "push" in code
        assert "save" in code

    @pytest.mark.unit
    def test_js_chained_commands(self):
        """Chained JS commands should all appear in output."""
        js = JS().show("#modal").add_class("#btn", "active").push("open")
        event, code = transpile("click", js, {})
        assert "show" in code
        assert "add_class" in code
        assert "push" in code

    @pytest.mark.unit
    def test_js_with_prevent_modifier(self):
        """JS command with prevent modifier."""
        js = JS().push("submit")
        event, code = transpile("click.prevent", js, {})
        assert "event.preventDefault()" in code
        assert "wireview.exec" in code

    @pytest.mark.unit
    def test_js_with_debounce_modifier(self):
        """JS command with debounce modifier."""
        js = JS().push("search")
        event, code = transpile("input.debounce.300", js, {})
        assert "wireview.debounce(300)" in code

    @pytest.mark.unit
    def test_js_with_throttle_modifier(self):
        """JS command with throttle modifier."""
        js = JS().push("update")
        event, code = transpile("scroll.throttle.100", js, {})
        assert "wireview.throttle(100)" in code

    @pytest.mark.unit
    def test_js_with_key_modifier(self):
        """JS command with key modifier."""
        js = JS().push("submit")
        event, code = transpile("keydown.enter", js, {})
        assert event == "onkeydown"
        assert "event.key" in code or "enter" in code.lower()

    @pytest.mark.unit
    def test_js_with_multiple_modifiers(self):
        """JS command with multiple modifiers."""
        js = JS().push("submit")
        event, code = transpile("keydown.enter.prevent", js, {})
        assert "event.preventDefault()" in code
        assert "enter" in code.lower()
