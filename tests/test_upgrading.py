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


# What changed since 1.0.0rc4, entry by entry. A key is a phrase of one CHANGELOG entry of the
# release after 1.0.0rc4 ([Unreleased] until 1.0 is cut); its value names the bullet of
# "1.0.0rc4에서 1.0으로" that tells an upgrading project what to do -- a phrase of its bold lead --
# and a phrase its text, sub-bullets included, says about this entry. Under NO_UPGRADE_NOTE the
# value says why nothing needs telling. The UPGRADING section was written from the entries one
# track at a time and missed the StrEnum and stream container changes -- both silent. An entry
# with neither fails here, so each new one is a decision. Nine entries share the "문서 예시"
# bullet; matching its lead alone let every sub-bullet go and still pass.
UPGRADE_NOTE = {
    "`wireview.W018`: `manage.py check`": ("`wireview.W018`", "`wireview.W018`"),
    "a component's `mutation()` receives saves": ("`mutation()`이 받은 `instance`", "`save()`"),
    "transition no longer declares a `to` key": ("`to` 키", "`to` 키"),
    "refuses a modifier the client does not run": ("수정자를", "`ValueError`"),
    "a binding to a method the dispatcher would": ("부를 수 없는 이름에", "`AssertionError`"),
    "are `StrEnum`s": ("`StrEnum`", "`StrEnum`"),
    "`signed_cookies` session backend": ("토스트 채널이", "`signed_cookies`"),
    "[GHSA-4v8p-p6p8-78pj]": ("토스트 채널이", "`cached_db`: 테이블과 캐시 둘 다"),
    "no longer announces a fixture load": ("픽스처 로드", "픽스처 로드"),
    "`uvicorn <project>.asgi:application`": ("`asgi.py`", "`ASGIStaticFilesHandler`"),
    "shipped skill now introduces toasts": ("에이전트 스킬", "토스트"),
    "A generated `.pyi` imported": ("`.pyi`", "wireview_stubs"),
    "still described the 1.0.0rc1 contract": ("에이전트 스킬", "`handle_async`"),
    "formatted its arguments into markup with an f-string": ("문서 예시", "`format_html`"),
    "stand-in channel layer": ("구독 이름", "`TypeError`"),
    "`mount` is a framework name": ("문서 예시", "`async def mount(self)`"),
    "server-side helpers without a leading `_`": ("문서 예시", "`notify_user`"),
    "reconnect recovery example": ("`wireview.W018`", "async def validate"),
    "declared with `public=False`": ("`public=False`", "`name=`"),
    "quiz example and tutorial 13": ("문서 예시", "`{% class {...} %}`"),
    "bound Ctrl+Enter and Escape to `keypress`": ("문서 예시", "`keypress`"),
    "removes or moves the focused element": ("포커스 칸", "`blur`"),
    "toggle and an edit with `QuerySet.aupdate()`": ("문서 예시", "`QuerySet.aupdate()`"),
    "nested components without an `id`": ("문서 예시", "`id`를 준다"),
    "never set `AUTO_BROADCAST.senders`": ("문서 예시", "`AUTO_BROADCAST.senders`"),
    "the parent handler `send_to_parent` calls": ("문서 예시", "`send_to_parent`"),
    "last-seen helper starts with": ("문서 예시", "Presence API 심화"),
    "stream of the same name share no list": ("스트림 연산", "`dom_id`"),
    "[GHSA-8q8p-x4w4-p745]": ("`on_upload_complete`는", "consume_uploads"),
    "[GHSA-pv9v-gqcj-f42x]": ("함수 컴포넌트 템플릿 안의 `{% component %}`", "`Meta.live_sessions`를 선언하고"),
    "What a part kept for a reset temporary assign holds from elsewhere": (
        "초기화된 temporary assign",
        "`{% live_component %}`",
    ),
    "A `{% component %}` a live render draws": ("join되고 `joined()`가 돈다", "`mount()`나 핸들러"),
    "binding inside a LiveComponent calls that LiveComponent's handler": (
        "join되고 `joined()`가 돈다",
        "LiveComponent 안의 `wire-viewport-*`",
    ),
    "`leaving()` pairs with `joined()`": ("`leaving()`은 `joined()`가 돈 인스턴스만", "boost 이동"),
}

DOCS_ONLY = "문서·저장소만 바뀌었고 라이브러리 동작은 같다"
FIXED_ONLY = "결함 수정이고, 기대어 쓸 수 있던 동작이 아니었다"
LOUD_COPY = "그 예시를 베낀 코드는 이미 오류로 드러났다"
NO_UPGRADE_NOTE = {
    "ships a `.gitignore`": "새로 만드는 프로젝트만 받는다",
    "The changelog names the advisory": DOCS_ONLY,
    "opens with a table from the version you run": "버전 범위는 `버전 범위` 절이 말한다",
    "`SECURITY.md` supports the newest": DOCS_ONLY,
    "says `django` has no upper bound": "의존성 선언은 그대로이고 지원 범위를 문서로 적었다",
    "`docs/FEATURE-GAP.md` agrees with itself": DOCS_ONLY,
    "`docs/ROADMAP.md`: the modifier list": "수정자 거절은 수정자 항목이 다룬다",
    '"Redis cluster" example': DOCS_ONLY,
    "which nothing linked": DOCS_ONLY,
    "name all four flags": "맨 위 보안 상자가 다룬다",
    "table ends every row": DOCS_ONLY,
    "`tests/test_doc_links.py` checks": DOCS_ONLY,
    "each get an `instance` of their own": "한 컴포넌트의 편집이 다른 컴포넌트에 새던 결함이고, 기댈 동작이 아니었다",
    "Streams example now works as copied": LOUD_COPY,
    "list of `JS()` commands gives their real": LOUD_COPY,
    "performance section recommended": "`wireview.utils`의 함수는 그대로 있다",
    "table of contents opened the wrong": DOCS_ONLY,
    '"~10KB"': DOCS_ONLY,
    "hook example now says where the file goes": DOCS_ONLY,
    "running the examples left out": DOCS_ONLY,
    "overview picture are absolute": DOCS_ONLY,
    "generated type stubs are explained": DOCS_ONLY,
    "proxy configurations in `docs/DEPLOYMENT.md`": "그 설정은 첫 배포에서 번들이 404라 드러났다",
    "named two private repository methods": DOCS_ONLY,
    "Smaller reference errors": DOCS_ONLY,
    "`docs/ARCHITECTURE.md` drew": DOCS_ONLY,
    "the canon of the message shapes": DOCS_ONLY,
    "pre-#83 `conn:comp:config:ref`": "동작은 0.5부터 같고 문서만 바로잡았다",
    "without importing it (`NameError`)": LOUD_COPY,
    "needs `f` to be a handler": DOCS_ONLY,
    "deprecation warning of the `wireview.component`": "경고 문장만 바뀌었다",
    "does not show in a production log": DOCS_ONLY,
    "set `loading_more` and reset": DOCS_ONLY,
    "without `myself=True` goes to the parent": DOCS_ONLY,
    "used `NotificationType` without importing": LOUD_COPY,
    "settings and test blocks import what": DOCS_ONLY,
    "production must use Redis": DOCS_ONLY,
    "binds `cancel_file`": LOUD_COPY,
    "connection id is issued": DOCS_ONLY,
    "CI runs the examples on every push": DOCS_ONLY,
    "`live.pyi` appears next to `live.py`": DOCS_ONLY,
    "What a new component sends from `joined()` reaches it": FIXED_ONLY,
    "The hooks of a component a render brings in mount": FIXED_ONLY,
    "A stream list survives a render that adds an element ahead of it": FIXED_ONLY,
    "An element the morph reuses for another no longer keeps its old viewport binding": FIXED_ONLY,
    "a handler aims at what its own render reveals": FIXED_ONLY,
    "A hook a parent's patch draws inside a nested component": FIXED_ONLY,
    "A join that replaces a component's instance": FIXED_ONLY,
    "Two components on a page whose uploads share a name": FIXED_ONLY,
    "Test harness: `./tests/e2e.sh": DOCS_ONLY,
    "is drawn even when another component's patch runs first": FIXED_ONLY,
    "Infinite scroll asks for one page at a time after a reconnect": FIXED_ONLY,
    "A LiveComponent shown again after a reconnect starts from its defaults": FIXED_ONLY,
    "Nothing reaches a component whose join failed": FIXED_ONLY,
    "A LiveComponent placed in a slot stays on the page": "화면에서 사라지던 결함이고, 기댈 동작이 아니었다",
    "A temporary assign of a nested `{% component %}` stays": "목록이 화면에서 사라지던 결함이고, 기댈 동작이 아니었다",
    "draws no slot. A boosted visit to another": "앞 페이지의 슬롯이 잠깐 실리던 결함이고, 기댈 동작이 아니었다",
    "keeps the LiveComponents in it when the work lands": FIXED_ONLY,
    "before its parent's render ran its `joined()`": FIXED_ONLY,
    "stops drawing because of a prop its drawer": FIXED_ONLY,
    "hides or shows when the slot's owner": FIXED_ONLY,
    "in a function component's template is the page's in a live render": FIXED_ONLY,
    "An editor extension for VS Code in `editors/vscode/`": "라이브러리 밖의 새 도구이고 라이브러리 동작은 같다",
    "writes metadata version 1.1": "키만 더했다. 1.0을 읽던 도구는 major만 보면 그대로 읽는다",
    "listed a handler only when its name was all lowercase": FIXED_ONLY,
    "A function component that takes `**kwargs` could not render": FIXED_ONLY,
    "template directories were read off `TEMPLATES`": "W011의 새 경고는 전부터 있던, 보지 못한 문제다",
    "named a tag that does not exist": DOCS_ONLY,
    "The sdist took every `README.md`": "sdist에서 저장소 파일이 빠졌을 뿐 패키지는 같다",
    "Two runs of `manage.py wireview_lsp` over the same project": FIXED_ONLY,
}


def _entries_since_rc4() -> list[str]:
    """Every entry of the release after 1.0.0rc4, whitespace folded."""
    text = CHANGELOG.read_text()
    release = re.split(r"^## \[", text.split("\n## [1.0.0rc4]", 1)[0], flags=re.MULTILINE)[-1]
    return [" ".join(entry.split()) for entry in re.split(r"^- ", release, flags=re.MULTILINE)[1:]]


def _rc4_bullets() -> list[str]:
    section = UPGRADING.read_text().split("\n## 1.0.0rc4에서 1.0으로\n", 1)[1].split("\n## ", 1)[0]
    return [" ".join(bullet.split()) for bullet in re.split(r"^- ", section, flags=re.MULTILINE)[1:]]


def test_every_change_since_rc4_is_accounted_for():
    entries = _entries_since_rc4()
    keys = [*UPGRADE_NOTE, *NO_UPGRADE_NOTE]

    unaccounted = [entry[:100] for entry in entries if not any(key in entry for key in keys)]
    ambiguous = {key: n for key in keys if (n := sum(key in entry for entry in entries)) != 1}
    claimed_twice = [entry[:100] for entry in entries if sum(key in entry for key in keys) > 1]

    assert (unaccounted, ambiguous, claimed_twice) == ([], {}, [])


def test_every_rc4_note_names_a_change_and_every_named_note_exists():
    bullets = _rc4_bullets()
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
