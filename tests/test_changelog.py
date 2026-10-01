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


REPO = "https://github.com/itda-work/django-wireview"


def _headings() -> list[str]:
    return [m.group(1) for line in CHANGELOG.read_text().splitlines() if (m := re.match(r"## \[([^\]]+)\]", line))]


def _links() -> dict[str, str]:
    return dict(re.findall(r"^\[([^\]]+)\]: (\S+)$", CHANGELOG.read_text(), re.MULTILINE))


def test_unreleased_compares_from_the_newest_release():
    """1.0.0rc4 added its section and its link but left ``[Unreleased]`` comparing from
    v1.0.0rc3, so "what changed since the release" showed rc4's changes too."""
    newest = _headings()[1]

    assert _headings()[0] == "Unreleased"
    assert _links()["Unreleased"] == f"{REPO}/compare/v{newest}...HEAD"


def test_each_release_compares_from_the_one_before():
    releases = _headings()[1:]
    links = _links()
    expected = {
        version: f"{REPO}/compare/v{previous}...v{version}" for version, previous in zip(releases, releases[1:])
    }
    expected[releases[-1]] = f"{REPO}/releases/tag/v{releases[-1]}"

    assert {v: links.get(v) for v in releases} == expected


def test_the_newest_release_is_the_package_version():
    """The release commit bumps ``pyproject.toml`` and adds the section together."""
    import tomllib

    project = tomllib.loads((CHANGELOG.parent / "pyproject.toml").read_text())["project"]

    assert _headings()[1] == project["version"]


ADVISORY = "GHSA-q2rr-5q2g-6xqp"


def _broadcast_flags() -> list[str]:
    from wireview.schemas import AutoBroadcast

    return [name for name, field in AutoBroadcast.model_fields.items() if field.annotation is bool]


def _advisory_entry() -> str:
    """The ``### Security`` entry of the release that fixed the advisory."""
    section = CHANGELOG.read_text().split("## [1.0.0rc4]", 1)[1].split("\n## [", 1)[0]
    security = section.split("### Security", 1)[1]
    return next(entry for entry in security.split("\n- ") if ADVISORY in entry)


def test_the_advisory_names_every_flag_that_broadcast():
    """An empty ``senders`` connected every flag's receiver to every model, ``m2m`` too: one
    ``user.groups.add(g)`` sent the user's password hash on two channels. The entry named three
    flags, so a project running ``m2m=True`` alone read itself out of the advisory."""
    entry = _advisory_entry()

    assert [flag for flag in _broadcast_flags() if f"`{flag}`" not in entry] == []
    assert "many-to-many change" in entry.split("Fixed here:", 1)[0]


def test_upgrading_names_every_flag_that_broadcast():
    upgrading = (CHANGELOG.parent / "docs" / "UPGRADING.md").read_text()
    box = next(p for p in upgrading.split("\n\n") if p.startswith("> **보안.**"))
    section = upgrading.split("### 9. `AUTO_BROADCAST`", 1)[1].split("\n### ", 1)[0]
    closing = section.rstrip().rsplit("\n\n", 1)[1]

    assert [flag for flag in _broadcast_flags() if f"`{flag}`" not in box] == []
    assert [flag for flag in _broadcast_flags() if f"`{flag}`" not in closing] == []
