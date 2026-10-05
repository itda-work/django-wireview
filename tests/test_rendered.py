"""Tests for the Phoenix LiveView-style Rendered structure."""

import pytest

from wireview.core.rendered import (
    MARKER_PATTERN,
    ComponentRef,
    Comprehension,
    Rendered,
    RenderedDiff,
    Stale,
    _marked_content,
    component_refs,
    has_markers,
    inject_marker,
    keep_stale,
    may_name_components,
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


class TestKeepStale:
    """Parts a nested component drew in another component's pass, put back as its last render drew them (#111)."""

    previous = "<!--$0-->A<!--/$0--><!--$1-->B<!--/$1-->"

    @pytest.mark.unit
    def test_it_puts_back_the_parts_it_pairs(self):
        html = "<!--$4-->a<!--/$4--><!--$6-->b<!--/$6-->"

        kept = keep_stale(html, {4, 6}, Rendered.from_marked_html(self.previous))

        assert kept == "<!--$4-->A<!--/$4--><!--$6-->B<!--/$6-->"

    @pytest.mark.unit
    def test_markers_it_cannot_pair_are_drawn_as_they_are(self):
        """A stale part inside a variable's output is text to the parser but a part to the scan: the counts differ."""
        html = "<!--$4-->x<!--$5-->a<!--/$5-->y<!--/$4--><!--$6-->b<!--/$6-->"

        assert keep_stale(html, {5, 6}, Rendered.from_marked_html(self.previous)) == html

    @pytest.mark.unit
    def test_markers_numbered_before_the_component_are_not_its_own(self):
        """A fill the host drew before the component holds the host's markers; its own render has the text."""
        previous = Rendered.from_marked_html("<div><em>T</em><!--$0-->A<!--/$0--></div>")
        html = "<div><em><!--$2-->T<!--/$2--></em><!--$5-->a<!--/$5--></div>"

        assert keep_stale(html, {5}, previous) == html
        kept = keep_stale(html, {5}, previous, first=3)
        assert kept == "<div><em><!--$2-->T<!--/$2--></em><!--$5-->A<!--/$5--></div>"

    @pytest.mark.unit
    def test_an_index_with_a_leading_zero_is_text_to_the_scan_as_to_the_parser(self):
        """The library never writes "05"; a user's string that does is no marker to either (#176)."""
        previous = Rendered.from_marked_html("<!--$05-->x<!--$0-->A<!--/$0-->")
        html = "<!--$05-->x<!--$5-->a<!--/$5-->"

        assert previous.static == ["<!--$05-->x", ""]
        assert keep_stale(html, {5}, previous) == "<!--$05-->x<!--$5-->A<!--/$5-->"


class TestParseRoundTrip:
    """Any render the template engine can mark parses back to exactly itself (#176).

    The parser takes a value or a block with nothing marked inside it without
    a frame of its own; everything else folds through the stack. Random renders
    of every shape -- values, references, blocks, loops in blocks in loops --
    are marked the way ``keep_stale`` marks a kept part and parsed again.
    """

    TEXTS = ["", "a", " ", "<li class='x'>", "x\ny", "&amp;"]

    def _value(self, rng, depth):
        r = rng.random()
        if depth > 3 or r < 0.45:
            return rng.choice(self.TEXTS)
        if r < 0.55:
            return ComponentRef(f"kid-{rng.randint(0, 9)}")
        if r < 0.8:
            return self._rendered(rng, depth + 1, at_least=1)  # a block with nothing dynamic is text
        static_count = rng.randint(0, 3)
        static = [rng.choice(self.TEXTS) for _ in range(static_count + 1)]
        items = [[self._value(rng, depth + 1) for _ in range(static_count)] for _ in range(rng.randint(0, 4))]
        return Comprehension(static=static, dynamics=items) if items else Comprehension()

    def _rendered(self, rng, depth, at_least=0):
        count = rng.randint(at_least, 4)
        return Rendered(
            static=[rng.choice(self.TEXTS) for _ in range(count + 1)],
            dynamic=[self._value(rng, depth) for _ in range(count)],
        )

    @pytest.mark.unit
    def test_a_marked_render_parses_back_to_itself(self):
        import itertools
        import random

        rng = random.Random(176)
        for _ in range(3000):
            render = self._rendered(rng, 0)
            counter = itertools.count()
            html = _marked_content(render, 0, lambda: next(counter))
            assert Rendered.from_marked_html(html) == render, html

    @pytest.mark.unit
    def test_a_stale_part_is_found_without_a_frame_of_its_own(self):
        html = "<p><!--$0-->a<!--/$0--><!--$B1-->b<!--/$B1--><!--$B2-->c<!--$3-->d<!--/$3--><!--/$B2--></p>"
        parsed = Rendered.from_marked_html(html, {0, 1, 3})

        assert parsed.dynamic[0] == Stale("a")
        assert parsed.dynamic[1] == Stale("b")
        assert parsed.dynamic[2] == Rendered(static=["c", ""], dynamic=[Stale("d")])


class TestMayNameComponents:
    """A render known to hold no LiveComponent's reference is not walked for one (#176)."""

    @pytest.mark.unit
    def test_a_render_without_a_reference_comment_names_none(self):
        rendered = Rendered.from_marked_html("<p><!--$0-->a<!--/$0--><!--$C1--><!--$I1-->x<!--/$I1--><!--/$C1--></p>")

        assert not may_name_components(rendered)
        assert component_refs(rendered) == []

    @pytest.mark.unit
    def test_a_reference_anywhere_is_found(self):
        html = "<p><!--$C0--><!--$I0--><!--$B1--><!--$2--><!--@wv:kid--><!--/$2--><!--/$B1--><!--/$I0--><!--/$C0--></p>"
        rendered = Rendered.from_marked_html(html)

        assert may_name_components(rendered)
        assert component_refs(rendered) == ["kid"]

    @pytest.mark.unit
    def test_what_a_kept_part_holds_is_not_known_from_the_html(self):
        """settle() puts back an earlier render's value, which may be a reference."""
        previous = Rendered.from_marked_html("<p><!--$0--><!--@wv:kid--><!--/$0--></p>")
        rendered = Rendered.from_marked_html("<p><!--$0--><!--/$0--></p>", {0})
        rendered.settle(previous)

        assert may_name_components(rendered)
        assert component_refs(rendered) == ["kid"]

    @pytest.mark.unit
    def test_a_reference_the_parse_joins_back_is_found(self):
        """A stray marker inside a reference comment is dropped and the comment joined: the HTML holds no "<!--@wv:"."""
        rendered = Rendered.from_marked_html("<!--$0--><!--@<!--/$B2-->wv:c9--><!--/$0-->")

        assert rendered.dynamic == [ComponentRef("c9")]
        assert may_name_components(rendered)
        assert component_refs(rendered) == ["c9"]

    @pytest.mark.unit
    def test_a_reference_comment_outside_a_part_names_none(self):
        rendered = Rendered.from_marked_html("<p><!--@wv:kid--><!--$0-->a<!--/$0--></p>")

        assert not may_name_components(rendered)
        assert component_refs(rendered) == []

    @pytest.mark.unit
    def test_a_render_built_any_other_way_is_walked(self):
        assert may_name_components(None)
        assert may_name_components(Rendered(["", ""], [ComponentRef("kid")]))
        assert component_refs(Rendered(["", ""], [ComponentRef("kid")])) == ["kid"]
