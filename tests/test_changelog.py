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


SECURITY_MD = CHANGELOG.parent / "SECURITY.md"
ADVISORY_ID = re.compile(r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}")


def _security_entries(release: str) -> list[str]:
    """The entries of a release's ``### Security`` section, whitespace folded."""
    section = CHANGELOG.read_text().split(f"## [{release}]", 1)[1].split("\n## [", 1)[0]
    if "\n### Security\n" not in section:
        return []
    security = section.split("\n### Security\n", 1)[1].split("\n### ", 1)[0]
    return [" ".join(entry.split()) for entry in re.split(r"^- ", security, flags=re.MULTILINE)[1:]]


def _published_advisories() -> dict[str, str]:
    """``SECURITY.md``'s advisory table: id to the version that fixed it."""
    table = SECURITY_MD.read_text().split("## 공개된 보안 권고", 1)[1].split("\n## ", 1)[0]
    return {
        ids[0]: cells[-1]
        for line in table.splitlines()
        if line.startswith("| ")
        for cells in [[c.strip() for c in line.strip("|").split("|")]]
        if (ids := ADVISORY_ID.findall(cells[0]))
    }


# The first release whose ``### Security`` entries are all advisories. 0.3.0's are not, and
# the rule holds from here on.
FIRST_ADVISORY_RELEASE = "1.0.0rc4"


def test_every_security_entry_since_the_advisories_began_is_one():
    """1.0.0rc3 and rc4 sent every visitor's session key to the broker as a group name; the
    fix was filed under ``### Fixed`` as a ``signed_cookies`` join failure, and the release's
    security section did not mention it. Each entry here opens with its advisory's link.
    Every release from the first advisory up is read, so cutting ``[Unreleased]`` into a
    numbered release keeps the check."""
    releases = _headings()
    entries = [
        entry
        for release in releases[: releases.index(FIRST_ADVISORY_RELEASE) + 1]
        for entry in _security_entries(release)
    ]

    assert [
        entry[:80]
        for entry in entries
        if not re.match(rf"\[({ADVISORY_ID.pattern})\]\({REPO}/security/advisories/\1\) \(", entry)
    ] == []
    assert [ADVISORY_ID.findall(entry)[0] for entry in entries if "session key" in entry and "digest" in entry] == [
        "GHSA-4v8p-p6p8-78pj"
    ]


def test_security_md_lists_every_advisory_the_changelog_names():
    releases = _headings()
    fixed_in = {
        advisory: release
        for release in releases
        for entry in _security_entries(release)
        for advisory in ADVISORY_ID.findall(entry.split(" ", 1)[0])
    }
    published = _published_advisories()

    assert set(published) == set(fixed_in)
    assert {a: v for a, v in published.items() if fixed_in[a] != "Unreleased"} == {
        a: r for a, r in fixed_in.items() if r != "Unreleased"
    }
    # Not yet released: the version the table names is one no release heading has yet.
    assert [a for a, r in fixed_in.items() if r == "Unreleased" and published[a] in releases] == []
