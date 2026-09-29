"""Guard: each release in CHANGELOG.md lists each kind of change once.

The release notes are cut from these sections. A second ``### Added`` under one
release -- left when an entry was inserted above an existing section -- put the
fixes that followed it under "Added", and nothing failed.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def _releases() -> dict[str, list[str]]:
    releases: dict[str, list[str]] = {}
    current = None
    for line in CHANGELOG.read_text().splitlines():
        if match := re.match(r"## \[([^\]]+)\]", line):
            current = match.group(1)
            releases[current] = []
        elif current and (match := re.match(r"### (.+)", line)):
            releases[current].append(match.group(1).strip())
    return releases


def test_each_release_has_each_section_once():
    repeated = {
        release: sorted({s for s in sections if sections.count(s) > 1})
        for release, sections in _releases().items()
        if len(sections) != len(set(sections))
    }
    assert repeated == {}
