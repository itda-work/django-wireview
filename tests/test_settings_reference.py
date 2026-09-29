"""``docs/features/settings.md`` lists every setting, and only those (#119).

The README's block had seven of eighteen keys missing; the rest were scattered
over feature pages. A key that is in ``DEFAULT`` but documented nowhere is still
frozen by 1.0, just without anyone having decided to.
"""

import re
from pathlib import Path

import pytest

from wireview.settings import DEFAULT, REMOVED

pytestmark = pytest.mark.unit

REFERENCE = Path(__file__).resolve().parent.parent / "docs" / "features" / "settings.md"


def _documented() -> set[str]:
    return set(re.findall(r"^\| `([A-Z_]+)` \|", REFERENCE.read_text(), re.M))


def test_every_setting_is_documented():
    assert set(DEFAULT) - _documented() == set()


def test_only_real_settings_are_documented():
    assert _documented() - set(DEFAULT) == set()


def test_no_removed_setting_is_documented_as_live():
    assert _documented() & set(REMOVED) == set()
