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
    return [slug(h) for h in re.findall(r"^## (.+에서 .+)$", UPGRADING.read_text(encoding="utf-8"), re.MULTILINE)]


def _rows() -> dict[str, str]:
    table = UPGRADING.read_text(encoding="utf-8").split("## 어디서 오나", 1)[1].split("\n## ", 1)[0]
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
    "1.2.x": "12에서-13으로",
    "1.3.x": "13에서-14로",
    "1.4.x": "14에서-15로",
    "1.5.x": "15에서-16으로",
}
# A row that reads only some subsections of a section names them: rc2 and rc3 already had the
# rest of rc1-to-1.0.
SUBSECTIONS = {
    "1.0.0rc2, 1.0.0rc3": ["9-auto_broadcast는-senders에-적은-모델만-알린다-보안", "10-의존성-하한"],
}


def _sections_with_nothing_to_change() -> set[str]:
    text = UPGRADING.read_text(encoding="utf-8")
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


# What changed since 1.5.1, entry by entry. A key is a phrase of one CHANGELOG entry of the
# release after 1.5.1 (the [Unreleased] section until it is released); its value names the
# bullet of "1.5에서 1.6으로" that tells an upgrading project what to do -- a phrase of its bold
# lead -- and a phrase its text, sub-bullets included, says about this entry. Under
# NO_UPGRADE_NOTE the value says why nothing needs telling. The rc4-to-1.0 section was written
# from the entries one track at a time and missed the StrEnum and stream container changes --
# both silent. An entry with neither fails here, so each new one is a decision. The next
# release moves this table to its own entries and section.
UPGRADE_NOTE = {
    "A connection whose app ended without a disconnect is ended all the same": (
        "연결이 끝날 때의 `leaving()`은 모두 합쳐 5초 안에 끝나야 한다",
        "프레임워크가 대신 떠난다",
    ),
    "Presence keeps a meta per producer under each user": (
        "Presence는 탭마다 기록한다",
        "프로듀서가 둘인 페이지가",
    ),
    "A presence producer answers the sync requests": (
        "다른 사람의 목록에 올리는 것은 프로듀서다",
        "프로듀서의 `notification()`은 전처럼 그 메시지를 받지 않고",
    ),
    "`presence_users_in(topic)` lists the users of one topic": (
        "토픽 여럿을 듣는 트래커는 토픽마다 기록한다",
        "새 메서드 `presence_users_in(topic)`",
    ),
    "A presence tracker that subscribes to several topics keeps each topic's metas apart": (
        "토픽 여럿을 듣는 트래커는 토픽마다 기록한다",
        "`presence_users_in(topic)`",
    ),
    "A component `mount()`ed in a test hears the notifications published in its process": (
        "테스트의 `mount()`가 같은 프로세스의 알림을 받는다",
        "직접 부르기를 지운다",
    ),
    "`mount()` recorded a handler's mail": (
        "핸들러의 메일이 핸들러가 끝난 뒤 `sent_messages`에 들어간다",
        "브라우저가 받는 순서로 고친다",
    ),
    "A pytest plugin, `wireview`": (
        "새로: pytest 플러그인 `wireview`",
        "`-p no:wireview`",
    ),
    "`follow_push(Destination)` to another path left the instance it came from listening": (
        "다른 경로로 `follow_push(Destination)`한 뒤 원래 인스턴스는 언다",
        "목적지 컴포넌트로 옮긴다",
    ),
    "A nested `{% component %}` its drawer's render no longer draws retires with that render": (
        "그린 쪽이 더는 그리지 않는 중첩 컴포넌트는 그 렌더와 함께 은퇴한다",
        "id가 있어도 언제나 새로 시작한다",
    ),
    "its old state gone, whatever order the page's leave and the next render come in": (
        "그린 쪽이 더는 그리지 않는 중첩 컴포넌트는 그 렌더와 함께 은퇴한다",
        "숨겨도 상태를 지켜야 하면",
    ),
    "An event for a component id the connection holds no instance under is answered": (
        "인스턴스 없는 id로 온 이벤트에 답하고",
        "`gone`(프로토콜 `vsn` 9)으로 답하고",
    ),
    "`stream_insert(..., update_only=True)`, on a component and on `Broadcast`": (
        "프로토콜 10과 롤링 배포",
        "`update_only` 삽입은 보내지 않는다",
    ),
    "`stream(..., at=-1, reset=True)`": ("프로토콜 10과 롤링 배포", "`at`이 길이를 넘으면"),
    "`wire-viewport-top` and `wire-viewport-bottom` inside an element with its own scrollbar": (
        "`wire-viewport-*`가 더 자주 불린다",
        "우회하는 JS 훅",
    ),
    "A page read and appended with `stream(queryset, reset=False)`": (
        "읽어 붙이는 목록은 QuerySet을 `stream(..., reset=False)`에 넘긴다",
        "`start_async`로 넘기거나",
    ),
    "`{% csrf_token %}` in a component's template renders a token": (
        "컴포넌트 템플릿의 `{% csrf_token %}`이 토큰을 그린다",
        "`csrf_exempt`를 붙여 두었다면",
    ),
    "A nested `{% component %}` its drawer's render drew anew hears its channels from its `joined()` on": (
        "부모의 렌더가 새로 그린 중첩 컴포넌트는 `joined()` 뒤에야 알림을 받는다",
        "`joined()`에서 초기화하는 상태를 `notification()`에서 읽어도 된다",
    ),
    "A boosted navigation after another tab logged in, logged out": (
        "다른 탭이 신분을 바꾼 뒤의 boost 이동이 새 소켓을 연다",
        "쿠키가 소켓에 닿게 고친다",
    ),
    "A component's template renders with Django's template engine only": (
        "컴포넌트 템플릿은 Django 템플릿 엔진만 그린다",
        "`DjangoTemplates` 엔진의 `DIRS`나 앱 디렉터리로 옮긴다",
    ),
    "Django's `{% querystring %}` works in a component's template": (
        "컴포넌트 템플릿의 `{% querystring %}`이 동작한다",
        "이유를 적은 `AttributeError`로 실패한다",
    ),
    "A presence producer (`PresenceMixin`) is drawn with an id": (
        "Presence 프로듀서는 id를 주어 그린다",
        "`manage.py check`의 W023이 가리키는",
    ),
    "A presence producer whose last `leaving()` did not leave": (
        "`leaving()`이 퇴장하지 못한 프로듀서는 프레임워크가 퇴장시킨다",
        "`mount()`는 `leaving()`을 부르지 않으므로",
    ),
    "What a `JS()` command changes on an element": (
        "`JS()` 명령이 바꾼 것이 렌더 뒤에도 남는다",
        "로딩 클래스",
    ),
    "A hook's `pushEvent` callback runs after the render its event caused": (
        "훅 `pushEvent`의 콜백은 그 이벤트의 렌더가 DOM에 반영된 뒤에 돈다",
        "beforeUpdate()",
    ),
    "A hook's `pushEvent` without a callback returns a Promise": (
        "콜백 없는 훅 `pushEvent`가 응답의 Promise를 돌려준다",
        "return this.pushEvent",
    ),
    "A `JS()` command's selector acts on every element it matches": (
        "선택자가 맞는 요소 전부에 적용된다",
        "선택자를 좁힌다",
    ),
    "`Meta.shared_render`'s scope (and `wireview.W019`) read the component's own template file only": (
        "끼워 넣은 템플릿에서 보는 사람을 읽는 클래스를 공유하지 않는다",
        "`manage.py check`를 돌린다",
    ),
}

EXAMPLE = "저장소의 예제만 바뀌었다"
NO_UPGRADE_NOTE = {
    "A hook a morph handed to another component": "마운트되지 않던 훅이 마운트될 뿐이다",
    "`manage.py wireview_lsp --skip-checks`": "편집기 메타데이터가 빠뜨리던 컴포넌트를 실을 뿐이다",
    '`{% island "Map" props=... %}` puts an element': "새 태그라 할 일이 없다",
    "A message naming a component by something that is not a string": "no client of ours sends one",
    "`examples/chat_lv2`, the second step": EXAMPLE,
    "`examples/chat_lv3`, the third step": EXAMPLE,
    "`examples/chat_lv4`, the last step": EXAMPLE,
    'The chat examples (`chat`, `chat_lv2`, `chat_lv3`) drew "No messages yet"': EXAMPLE,
    "Every example's README opens with a clip of it working": EXAMPLE,
    "The todo example left what was typed in its input": EXAMPLE,
    "The rating example's distribution bars never showed": EXAMPLE,
    "The example READMEs start from uv alone": "저장소의 개발 환경과 예제 문서만 바뀌었다",
    "`manage.py wireview_check_templates` read the report of the template diagnostics": (
        "읽히지 않던 보고가 읽힐 뿐 할 일이 없다"
    ),
    "Documented when `broadcast()` (after the commit) and `abroadcast()` (at once) publish": "문서와 테스트만 바뀌었다",
    "A `stream()` in a `start_async` task replaced or cancelled": "닫히던 연결이 닫히지 않을 뿐 할 일이 없다",
    "A page that left while the server answered it": "정상 이탈이 ERROR 로그와 오류 지표에서 빠질 뿐 할 일이 없다",
    "The README opens with who wireview is for": "문서만 바뀌었다",
    "A nested `{% component %}` a render drew that the page has yet to join goes": (
        "화면에 없는 인스턴스가 사라질 뿐 할 일이 없다"
    ),
    "A session's mail to itself is no longer lost to a full channel": (
        "잃던 메일을 잃지 않을 뿐 할 일이 없다. 상한을 넘는 메일은 전에도 잃던 것이고 이제는 페이지가 다시 join한다"
    ),
    "A logout closes a busy connection on the login that ended": "닫혀야 할 연결이 닫힐 뿐 할 일이 없다",
    "A visitor who is not logged in no longer closes every anonymous connection": (
        "닫히지 말아야 할 연결이 남을 뿐 할 일이 없다"
    ),
}

PREVIOUS = "1.5.1"
SECTION = "1.5에서 1.6으로"


def _entries_since_previous() -> list[str]:
    """Every entry of the release after PREVIOUS, whitespace folded."""
    text = CHANGELOG.read_text(encoding="utf-8")
    release = re.split(r"^## \[", text.split(f"\n## [{PREVIOUS}]", 1)[0], flags=re.MULTILINE)[-1]
    return [" ".join(entry.split()) for entry in re.split(r"^- ", release, flags=re.MULTILINE)[1:]]


def _section_bullets() -> list[str]:
    section = UPGRADING.read_text(encoding="utf-8").split(f"\n## {SECTION}\n", 1)[1].split("\n## ", 1)[0]
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
