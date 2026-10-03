"""Guard: docs/UPGRADING.md sends every reader through every section they need, and its newest
section tells them every change they have to act on.

1.0.0rc4-to-1.0 was added above the table's sections, and every row still ended at the
section before it -- the rc4 row said there was nothing to change -- so a reader who
trusted the table missed two silent changes. Then that section collected the notes of the
tracks that wrote to it and missed those that only wrote the changelog.
"""

import re
from pathlib import Path

import pytest

from scripts.docs_site.nav import slug

pytestmark = pytest.mark.unit

UPGRADING = Path(__file__).resolve().parent.parent / "docs" / "UPGRADING.md"
CHANGELOG = UPGRADING.parent.parent / "CHANGELOG.md"


def _migration_sections() -> list[str]:
    """The ``## <from>에서 <to>`` sections, newest first as the document orders them."""
    return [slug(h) for h in re.findall(r"^## (.+에서 .+)$", UPGRADING.read_text(), re.MULTILINE)]


def _rows() -> dict[str, str]:
    table = UPGRADING.read_text().split("## 어디서 오나", 1)[1].split("\n## ", 1)[0]
    return {
        cells[0]: cells[1]
        for line in table.splitlines()
        if line.startswith("| ") and not line.startswith("| 쓰던") and not set(line) <= set("|- ")
        for cells in [[c.strip() for c in line.strip("|").split("|")]]
    }


def test_every_row_ends_with_the_newest_section():
    newest = _migration_sections()[0]

    assert {version: links.rsplit("(#", 1)[-1].split(")", 1)[0] for version, links in _rows().items()} == {
        version: newest for version in _rows()
    }


def test_every_row_reads_its_sections_oldest_first():
    """Sections are newest first in the document; a row lists them in the order to apply them."""
    order = _migration_sections()
    for version, links in _rows().items():
        sections = [a for a in re.findall(r"\(#([^)]+)\)", links) if a in order]
        assert sections == sorted(sections, key=order.index, reverse=True), version


# The section a reader of each row starts from. Every section above it is theirs to read too,
# except one that says there is nothing to change.
FIRST_SECTION = {
    "0.4.x": "04에서-10으로",
    "0.5.x": "05에서-06으로",
    "0.6.x, 0.7.x, 1.0.0rc1": "100rc1에서-10으로",
    "1.0.0rc2, 1.0.0rc3": "100rc1에서-10으로",
    "1.0.0rc4": "100rc4에서-10으로",
    "1.0.x": "10에서-11로",
    "1.1.x": "11에서-12로",
}
# A row that reads only some subsections of a section names them: rc2 and rc3 already had the
# rest of rc1-to-1.0.
SUBSECTIONS = {
    "1.0.0rc2, 1.0.0rc3": ["9-auto_broadcast는-senders에-적은-모델만-알린다-보안", "10-의존성-하한"],
}


def _sections_with_nothing_to_change() -> set[str]:
    text = UPGRADING.read_text()
    return {
        slug(heading)
        for heading, body in re.findall(r"^## (.+에서 .+)\n\n(.*)$", text, re.MULTILINE)
        if body.startswith("고칠 것이 없다")
    }


def test_every_row_reads_every_section_from_its_own_up():
    """Checking only a row's last section let one drop a section in between: without
    1.0.0rc1-to-1.0 the 0.5 row skipped rc2's breaking changes, and without §9 and §10 the
    rc2/rc3 row skipped the security fix and the raised dependency floors."""
    order = _migration_sections()
    skip = _sections_with_nothing_to_change()
    rows = _rows()

    assert set(rows) == set(FIRST_SECTION)
    for version, links in rows.items():
        anchors = re.findall(r"\(#([^)]+)\)", links)
        newer = order[: order.index(FIRST_SECTION[version]) + 1]
        expected = [section for section in reversed(newer) if section not in skip]

        assert [a for a in anchors if a in order] == expected, version
        assert [a for a in anchors if a not in order] == SUBSECTIONS.get(version, []), version


# What changed since 1.1.0, entry by entry. A key is a phrase of one CHANGELOG entry of the
# release after 1.1.0 (the [Unreleased] section until it is released); its value names the
# bullet of "1.1에서 1.2로" that tells an upgrading project what to do -- a phrase of its bold
# lead -- and a phrase its text, sub-bullets included, says about this entry. Under
# NO_UPGRADE_NOTE the value says why nothing needs telling. The rc4-to-1.0 section was written
# from the entries one track at a time and missed the StrEnum and stream container changes --
# both silent. An entry with neither fails here, so each new one is a decision. The next
# release moves this table to its own entries and section.
UPGRADE_NOTE = {
    "`push_to` to the page's own path": ("같은 경로로 가는 `push_to`", "`redirect_to`로 바꾼다"),
    "`replace_to` to another path fetches": ("다른 경로로 가는 `replace_to`", "같은 경로의 쿼리로 바꾼다"),
    "`mount(params=...)` runs `params_changed()`": ("`mount(params=...)`와 `follow_push()`", 'path="/items/"'),
}

DOCS_ONLY = "문서·저장소만 바뀌었고 라이브러리 동작은 같다"
NO_UPGRADE_NOTE = {
    "The package's Documentation URL": "패키지 메타데이터의 링크만 바뀌었다",
    "The documentation site serves the images": DOCS_ONLY,
    "The README opens with": DOCS_ONLY,
    "no longer says the layers' logs count": DOCS_ONLY,
    "Browser tests for what the documentation claims": "테스트만 늘었다",
    "`mount()` takes `path=`": "더한 옵션이다. 쓰는 법은 `mount(params=...)`와 `follow_push()` 항목에 있다",
    "IME is composing in": "고친 결함이다. 조합이 끊기던 화면이 끊기지 않을 뿐 할 일은 없다",
}

PREVIOUS = "1.1.0"
SECTION = "1.1에서 1.2로"


def _entries_since_previous() -> list[str]:
    """Every entry of the release after PREVIOUS, whitespace folded."""
    text = CHANGELOG.read_text()
    release = re.split(r"^## \[", text.split(f"\n## [{PREVIOUS}]", 1)[0], flags=re.MULTILINE)[-1]
    return [" ".join(entry.split()) for entry in re.split(r"^- ", release, flags=re.MULTILINE)[1:]]


def _section_bullets() -> list[str]:
    section = UPGRADING.read_text().split(f"\n## {SECTION}\n", 1)[1].split("\n## ", 1)[0]
    return [" ".join(bullet.split()) for bullet in re.split(r"^- ", section, flags=re.MULTILINE)[1:]]


def test_the_newest_section_is_the_one_the_tables_read():
    assert _migration_sections()[0] == slug(SECTION)


def test_every_change_since_the_previous_release_is_accounted_for():
    entries = _entries_since_previous()
    keys = [*UPGRADE_NOTE, *NO_UPGRADE_NOTE]

    unaccounted = [entry[:100] for entry in entries if not any(key in entry for key in keys)]
    ambiguous = {key: n for key in keys if (n := sum(key in entry for entry in entries)) != 1}
    claimed_twice = [entry[:100] for entry in entries if sum(key in entry for key in keys) > 1]

    assert (unaccounted, ambiguous, claimed_twice) == ([], {}, [])


def test_every_note_names_a_change_and_every_named_note_exists():
    bullets = _section_bullets()
    leads = [bullet.split("**")[1] if bullet.startswith("**") else bullet for bullet in bullets]
    named = {lead for lead, _ in UPGRADE_NOTE.values()}

    missing = {lead: n for lead in named if (n := sum(lead in each for each in leads)) != 1}
    orphans = [each for each in leads if not any(lead in each for lead in named)]
    assert (missing, orphans) == ({}, [])

    unsaid = {
        entry: phrase
        for entry, (lead, phrase) in UPGRADE_NOTE.items()
        for bullet, each in zip(bullets, leads)
        if lead in each and phrase not in bullet
    }
    assert unsaid == {}
