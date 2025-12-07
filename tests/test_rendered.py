"""Tests for the Phoenix LiveView-style Rendered structure."""

import pytest

from wireview.core.rendered import (
    MARKER_PATTERN,
    Rendered,
    RenderedDiff,
    has_markers,
    inject_marker,
)


class TestMarkerFunctions:
    """Test marker utility functions."""

    @pytest.mark.unit
    def test_has_markers_true(self):
        html = "<div><!--$0-->content<!--/$0--></div>"
        assert has_markers(html) is True

    @pytest.mark.unit
    def test_has_markers_false(self):
        html = "<div>No markers here</div>"
        assert has_markers(html) is False

    @pytest.mark.unit
    def test_inject_marker(self):
        result = inject_marker("hello", 0)
        assert result == "<!--$0-->hello<!--/$0-->"

    @pytest.mark.unit
    def test_inject_marker_with_index(self):
        result = inject_marker("world", 5)
        assert result == "<!--$5-->world<!--/$5-->"

    @pytest.mark.unit
    def test_marker_pattern_simple(self):
        html = "<!--$0-->content<!--/$0-->"
        match = MARKER_PATTERN.search(html)
        assert match is not None
        assert match.group(1) == "0"
        assert match.group(2) == "content"

    @pytest.mark.unit
    def test_marker_pattern_multiple(self):
        html = "<!--$0-->first<!--/$0--><!--$1-->second<!--/$1-->"
        matches = list(MARKER_PATTERN.finditer(html))
        assert len(matches) == 2
        assert matches[0].group(2) == "first"
        assert matches[1].group(2) == "second"


class TestRendered:
    """Test Rendered dataclass."""

    @pytest.mark.unit
    def test_from_marked_html_simple(self):
        html = "<div><!--$0-->5<!--/$0--></div>"
        rendered = Rendered.from_marked_html(html)
        assert rendered.static == ["<div>", "</div>"]
        assert rendered.dynamic == ["5"]
        assert rendered.fingerprint != ""

    @pytest.mark.unit
    def test_from_marked_html_multiple(self):
        html = "<span><!--$0-->Hello<!--/$0-->, <!--$1-->World<!--/$1-->!</span>"
        rendered = Rendered.from_marked_html(html)
        assert rendered.static == ["<span>", ", ", "!</span>"]
        assert rendered.dynamic == ["Hello", "World"]

    @pytest.mark.unit
    def test_from_marked_html_no_markers(self):
        html = "<div>Static content</div>"
        rendered = Rendered.from_marked_html(html)
        assert rendered.static == ["<div>Static content</div>"]
        assert rendered.dynamic == []

    @pytest.mark.unit
    def test_from_html_without_markers(self):
        html = "<div>Plain HTML</div>"
        rendered = Rendered.from_html_without_markers(html)
        assert rendered.static == [html]
        assert rendered.dynamic == []
        assert not rendered.has_markers()

    @pytest.mark.unit
    def test_to_html(self):
        rendered = Rendered(
            static=["<div>", "</div>"],
            dynamic=["content"],
            fingerprint="abc123",
        )
        assert rendered.to_html() == "<div>content</div>"

    @pytest.mark.unit
    def test_to_html_multiple_dynamic(self):
        rendered = Rendered(
            static=["<span>", " + ", "</span>"],
            dynamic=["1", "2"],
            fingerprint="abc",
        )
        assert rendered.to_html() == "<span>1 + 2</span>"

    @pytest.mark.unit
    def test_fingerprint_computed(self):
        rendered = Rendered(
            static=["<div>", "</div>"],
            dynamic=["x"],
        )
        assert rendered.fingerprint != ""
        assert len(rendered.fingerprint) == 8

    @pytest.mark.unit
    def test_fingerprint_changes_with_structure(self):
        r1 = Rendered(static=["<div>", "</div>"], dynamic=["x"])
        r2 = Rendered(static=["<span>", "</span>"], dynamic=["x"])
        assert r1.fingerprint != r2.fingerprint

    @pytest.mark.unit
    def test_fingerprint_same_for_same_structure(self):
        r1 = Rendered(static=["<div>", "</div>"], dynamic=["x"])
        r2 = Rendered(static=["<div>", "</div>"], dynamic=["y"])
        assert r1.fingerprint == r2.fingerprint

    @pytest.mark.unit
    def test_has_markers(self):
        r1 = Rendered(static=["<div>", "</div>"], dynamic=["x"])
        r2 = Rendered(static=["<div>content</div>"], dynamic=[])
        assert r1.has_markers() is True
        assert r2.has_markers() is False


class TestRenderedDiff:
    """Test Rendered.get_diff() method."""

    @pytest.mark.unit
    def test_first_render_returns_full(self):
        rendered = Rendered(
            static=["<div>", "</div>"],
            dynamic=["5"],
            fingerprint="abc123",
        )
        diff = rendered.get_diff(None)
        assert diff is not None
        assert diff.is_full is True
        assert diff.static == ["<div>", "</div>"]
        assert diff.dynamic == ["5"]
        assert diff.fingerprint == "abc123"

    @pytest.mark.unit
    def test_unchanged_returns_none(self):
        r1 = Rendered(static=["<div>", "</div>"], dynamic=["5"])
        r2 = Rendered(static=["<div>", "</div>"], dynamic=["5"])
        diff = r2.get_diff(r1)
        assert diff is None

    @pytest.mark.unit
    def test_changed_dynamic_returns_partial(self):
        r1 = Rendered(static=["<div>", "</div>"], dynamic=["5"])
        r2 = Rendered(static=["<div>", "</div>"], dynamic=["6"])
        diff = r2.get_diff(r1)
        assert diff is not None
        assert diff.is_full is False
        assert diff.changes == {"0": "6"}

    @pytest.mark.unit
    def test_multiple_changed_dynamic(self):
        r1 = Rendered(static=["", " + ", ""], dynamic=["1", "2"])
        r2 = Rendered(static=["", " + ", ""], dynamic=["10", "20"])
        diff = r2.get_diff(r1)
        assert diff is not None
        assert diff.is_full is False
        assert diff.changes == {"0": "10", "1": "20"}

    @pytest.mark.unit
    def test_partial_change(self):
        r1 = Rendered(static=["", " + ", ""], dynamic=["1", "2"])
        r2 = Rendered(static=["", " + ", ""], dynamic=["1", "20"])
        diff = r2.get_diff(r1)
        assert diff is not None
        assert diff.is_full is False
        assert diff.changes == {"1": "20"}  # Only second value changed

    @pytest.mark.unit
    def test_structure_change_returns_full(self):
        r1 = Rendered(static=["<div>", "</div>"], dynamic=["5"])
        r2 = Rendered(static=["<span>", "</span>"], dynamic=["5"])
        diff = r2.get_diff(r1)
        assert diff is not None
        assert diff.is_full is True


class TestRenderedDiffPayload:
    """Test RenderedDiff.to_payload() method."""

    @pytest.mark.unit
    def test_full_payload(self):
        diff = RenderedDiff(
            is_full=True,
            static=["<div>", "</div>"],
            dynamic=["5"],
            fingerprint="abc123",
        )
        payload = diff.to_payload()
        assert payload == {
            "s": ["<div>", "</div>"],
            "d": ["5"],
            "f": "abc123",
        }

    @pytest.mark.unit
    def test_partial_payload(self):
        diff = RenderedDiff(
            is_full=False,
            changes={"0": "6", "2": "new"},
        )
        payload = diff.to_payload()
        assert payload == {"0": "6", "2": "new"}

    @pytest.mark.unit
    def test_is_empty(self):
        diff1 = RenderedDiff(is_full=False, changes={})
        diff2 = RenderedDiff(is_full=False, changes={"0": "x"})
        diff3 = RenderedDiff(is_full=True, static=["a"], dynamic=["b"])

        assert diff1.is_empty() is True
        assert diff2.is_empty() is False
        assert diff3.is_empty() is False


class TestRenderedRoundTrip:
    """Test parsing and reconstruction."""

    @pytest.mark.unit
    def test_roundtrip_simple(self):
        original = '<div class="count"><!--$0-->42<!--/$0--></div>'
        rendered = Rendered.from_marked_html(original)

        # Reconstruct HTML (without markers)
        html = rendered.to_html()
        assert html == '<div class="count">42</div>'

    @pytest.mark.unit
    def test_roundtrip_complex(self):
        original = """<ul><!--$0--><li>Item 1</li><!--/$0--><!--$1--><li>Item 2</li><!--/$1--></ul>"""
        rendered = Rendered.from_marked_html(original)

        assert len(rendered.static) == 3
        assert len(rendered.dynamic) == 2
        assert rendered.dynamic[0] == "<li>Item 1</li>"
        assert rendered.dynamic[1] == "<li>Item 2</li>"


class TestBandwidthSavings:
    """Test realistic bandwidth savings scenarios."""

    @pytest.mark.unit
    def test_counter_increment(self):
        """Simulate counter increment: 5 -> 6"""
        html1 = '<div class="counter">Count: <!--$0-->5<!--/$0--></div>'
        html2 = '<div class="counter">Count: <!--$0-->6<!--/$0--></div>'

        r1 = Rendered.from_marked_html(html1)
        r2 = Rendered.from_marked_html(html2)

        # First render
        diff1 = r1.get_diff(None)
        payload1 = diff1.to_payload()

        # Update
        diff2 = r2.get_diff(r1)
        payload2 = diff2.to_payload()

        # Verify partial update is much smaller
        import json

        full_size = len(json.dumps(payload1))
        partial_size = len(json.dumps(payload2))

        assert partial_size < full_size
        assert payload2 == {"0": "6"}

    @pytest.mark.unit
    def test_status_toggle(self):
        """Simulate status toggle: active -> inactive"""
        html1 = '<span class="status"><!--$0-->Active<!--/$0--></span>'
        html2 = '<span class="status"><!--$0-->Inactive<!--/$0--></span>'

        r1 = Rendered.from_marked_html(html1)
        r2 = Rendered.from_marked_html(html2)

        r1.get_diff(None)  # First render
        diff = r2.get_diff(r1)

        assert diff is not None
        assert diff.is_full is False
        assert diff.changes == {"0": "Inactive"}
