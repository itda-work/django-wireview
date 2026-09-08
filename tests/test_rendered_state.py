"""Rendered snapshots must round-trip so render state can live outside the process."""

import pytest

from wireview.core.rendered import Rendered


@pytest.mark.unit
def test_rendered_round_trips_through_dict():
    rendered = Rendered.from_marked_html("<p><!--$0-->a<!--/$0--> and <!--$1-->b<!--/$1--></p>")

    restored = Rendered.from_dict(rendered.to_dict())

    assert restored.static == rendered.static
    assert restored.dynamic == rendered.dynamic
    assert restored.fingerprint == rendered.fingerprint


@pytest.mark.unit
def test_restored_snapshot_diffs_like_the_original():
    before = Rendered.from_marked_html("<p><!--$0-->1<!--/$0--></p>")
    after = Rendered.from_marked_html("<p><!--$0-->2<!--/$0--></p>")

    diff = after.get_diff(Rendered.from_dict(before.to_dict()))

    assert diff is not None and not diff.is_full
    assert diff.to_payload() == {"0": "2"}


@pytest.mark.unit
def test_from_dict_recomputes_fingerprint():
    data = Rendered.from_marked_html("<p><!--$0-->x<!--/$0--></p>").to_dict()
    data["f"] = "bogus"

    assert Rendered.from_dict(data).fingerprint != "bogus"
