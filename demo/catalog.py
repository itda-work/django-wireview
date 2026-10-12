"""The examples the demo shows, in examples/README.md's reading order.

``url`` is the URL name of the example's first page. ``media`` is #213's screenshot of the
example, ``examples/<slug>/media/<slug>.webp``, which the settings collect as a static file
under ``demo/media/`` (``STATICFILES_DIRS``); the list shows it when it is there.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse

SOURCE = "https://github.com/itda-work/django-wireview/tree/main/examples/"


@dataclass(frozen=True)
class Example:
    slug: str
    title: str
    concept: str
    url: str
    signs_in: bool = False
    two_windows: bool = False

    @property
    def href(self) -> str:
        return reverse(self.url)

    @property
    def source(self) -> str:
        return f"{SOURCE}{self.slug}"

    @property
    def media(self) -> str | None:
        path = f"demo/media/{self.slug}.webp"
        return static(path) if finders.find(path) else None


EXAMPLES = (
    Example("todo", "Todo", "모델 구독으로 여러 탭이 같은 목록을 함께 본다", "todo:todo", two_windows=True),
    Example("poll", "Poll", "쓰기는 핸들러가, 다시 그리기는 브로드캐스트가", "poll:index", two_windows=True),
    Example("rating", "Rating", "잠깐 쓰는 상태와 남는 상태를 갈라 둔다", "rating:index"),
    Example("search", "Live Search", "디바운스한 입력, 주소에 남는 검색어, 키보드로 고르는 결과", "search:index"),
    Example("quiz", "Quiz", "컴포넌트 상태로 굴리는 상태 머신", "quiz:index"),
    Example("chat", "Chat", "Streams와 Presence", "chat:index", two_windows=True),
    Example(
        "chat_lv2",
        "Chat 2단계",
        "누가 말하는가를 서버가 정한다 (로그인 사용자, 방 멤버십, live_session)",
        "chat_lv2:index",
        signs_in=True,
        two_windows=True,
    ),
    Example(
        "chat_lv3",
        "Chat 3단계",
        "위로 스크롤해 과거 불러오기, 내 메시지 수정·삭제",
        "chat_lv3:index",
        signs_in=True,
        two_windows=True,
    ),
    Example(
        "chat_lv4",
        "Chat 4단계",
        "한 사람에게만 가는 소식 (방 목록의 안 읽은 메시지 배지)",
        "chat_lv4:index",
        signs_in=True,
        two_windows=True,
    ),
    Example("dashboard", "Dashboard", "AsyncResult로 느린 조회를 미룬다", "dashboard:index"),
    Example(
        "notifications",
        "Notifications",
        "알림은 한 사용자에게 간다. 저장하는 알림과 저장하지 않는 토스트",
        "notifications:index",
        signs_in=True,
        two_windows=True,
    ),
    Example("livecomp", "LiveComponent", "연결을 공유하는 중첩 컴포넌트", "livecomp_dashboard"),
    Example("slots", "Slots", "내용을 호출자가 채우는 레이아웃 컴포넌트", "slots:gallery"),
    Example("hooks", "Hooks", "브라우저만 할 수 있는 일을 컴포넌트에 붙인다", "hooks_page"),
)
