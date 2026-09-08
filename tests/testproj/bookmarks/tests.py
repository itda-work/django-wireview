import asyncio
import threading
import time
from random import randint
from time import sleep

import pytest
from channels.routing import get_default_application
from django.test import override_settings
from uvicorn.config import Config as UvicornConfig
from uvicorn.main import Server as Uvicorn

from wireview import mount
from wireview.schemas import ModelAction

from .live import XBookmarkList
from .models import Bookmark


def _stream_html(view) -> str:
    """view.sent_messages에 쌓인 stream_op 메시지의 아이템 HTML을 모아 돌려준다.

    stream_insert()로 넣은 아이템은 view.render()의 전체 템플릿 출력에는 포함되지
    않는다 (컨테이너만 정적으로 렌더된다) — 개별 아이템은 클라이언트로 보내는
    stream_op 메시지에만 담긴다. testing.md의 표에는 있었지만("view.dom_actions |
    스트림·DOM 조작 목록") 실제로는 view.dom_actions가 아니라 view.sent_messages에
    stream_op 타입으로 쌓였다.
    """
    html = ""
    for message in view.sent_messages:
        if message.get("type") == "stream_op":
            for item in message.get("items", []):
                html += item.get("html", "")
    return html


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_add_bookmark_transitions_state():
    view = await mount(XBookmarkList)
    await view.call("add", title="장고 문서", url="https://docs.djangoproject.com")

    assert await Bookmark.objects.filter(title="장고 문서").aexists()
    bookmark = await Bookmark.objects.aget(title="장고 문서")
    assert bookmark.is_read is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_toggle_read_flips_flag():
    bookmark = await Bookmark.objects.acreate(title="예제", url="https://example.com")
    view = await mount(XBookmarkList)

    await view.call("toggle_read", bookmark_id=bookmark.pk)
    await bookmark.arefresh_from_db()
    assert bookmark.is_read is True

    await view.call("toggle_read", bookmark_id=bookmark.pk)
    await bookmark.arefresh_from_db()
    assert bookmark.is_read is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_filter_unread_excludes_read_items():
    await Bookmark.objects.acreate(title="이미읽음", url="https://example.com/1", is_read=True)
    await Bookmark.objects.acreate(title="아직안읽음", url="https://example.com/2", is_read=False)

    view = await mount(XBookmarkList)
    view.clear_messages()  # joined()의 초기 stream() 메시지(필터 적용 전 전체 목록)를 비운다
    await view.call("set_filter", filter="unread")

    assert view.component.filter == "unread"
    stream_html = _stream_html(view)
    assert "아직안읽음" in stream_html
    assert "이미읽음" not in stream_html


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_delete_removes_bookmark():
    bookmark = await Bookmark.objects.acreate(title="삭제될 것", url="https://example.com")
    view = await mount(XBookmarkList)

    await view.call("delete", bookmark_id=bookmark.pk)

    assert not await Bookmark.objects.filter(pk=bookmark.pk).aexists()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_mutation_from_other_tab_streams_new_bookmark():
    view = await mount(XBookmarkList)

    bookmark = await Bookmark.objects.acreate(title="다른 탭에서 추가", url="https://example.com")
    # 테스트 DB에서 AUTO_BROADCAST 신호가 실제로 오는지는 확실치 않아, 모델 변경
    # 알림을 컴포넌트의 mutation() 라이프사이클 메서드를 직접 호출해 흉내낸다.
    await view.component.mutation("bookmarks.bookmark", ModelAction.CREATED, bookmark)

    assert "다른 탭에서 추가" in _stream_html(view)


# --- E2E ---------------------------------------------------------------------
#
# The mount() tests above never touch a template. They cannot tell whether a form
# submit actually delivers its `name` fields as handler arguments, or whether a
# stream item ever reaches the DOM — both of which the skill claims. These do.


class UvicornThread(threading.Thread):
    def __init__(self, application, host: str, port: int):
        super().__init__()
        self.host = host
        self.port = port
        self.application = application
        self.server: Uvicorn | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    @override_settings(DEBUG=True)
    def run(self):
        self.loop = asyncio.new_event_loop()
        config = UvicornConfig(self.application, host=self.host, port=self.port, log_level="warning")
        self.server = Uvicorn(config)
        self.server.install_signal_handlers = lambda *args, **kwargs: None
        self.loop.run_until_complete(self.server.serve())
        self.server = None

    @property
    def started(self) -> bool:
        return self.server is not None and self.server.started

    def terminate(self):
        if self.server:
            self.server.force_exit = True
            self.server.should_exit = True
            if self.loop:
                self.loop.create_task(self.server.shutdown())


@pytest.fixture(scope="function")
def bookmarks_server():
    host = "127.0.0.1"
    port = randint(9000, 40000)
    server = UvicornThread(get_default_application(), host, port)
    server.start()
    while not server.started:
        sleep(0.1)

    yield f"http://{host}:{port}"

    server.terminate()


def _wait_for_websocket(page, timeout: float = 5.0):
    page.wait_for_selector('[data-is-live="true"]', timeout=timeout * 1000)


def _items(page):
    return page.locator('ul[wire-stream="bookmarks"] li')


def _add(page, title: str, url: str):
    """Seed through the UI. Creating rows with the ORM from the test body trips
    asgiref's "AsyncToSync in the same thread as an async event loop"."""
    before = _items(page).count()
    page.fill('input[name="title"]', title)
    page.fill('input[name="url"]', url)
    page.click('button[type="submit"]')
    _wait_for_count(page, before + 1)


def _wait_for_count(page, expected: int, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _items(page).count() == expected:
            return
        time.sleep(0.1)
    assert _items(page).count() == expected, f"expected {expected} items, got {_items(page).count()}"


@pytest.mark.e2e
class TestBookmarksE2E:
    @pytest.fixture(autouse=True)
    def setup_db(self, transactional_db):
        pass

    def test_form_submit_delivers_named_inputs_as_handler_arguments(self, page, bookmarks_server):
        """The skill claims a form field's `name` becomes the handler argument."""
        page.goto(f"{bookmarks_server}/bookmarks/")
        _wait_for_websocket(page)
        _wait_for_count(page, 0)

        _add(page, "위키", "https://wikipedia.org")

        _wait_for_count(page, 1)
        assert "위키" in _items(page).first.inner_text()
        assert "https://wikipedia.org" in _items(page).first.inner_html()

    def test_stream_insert_reaches_the_dom(self, page, bookmarks_server):
        page.goto(f"{bookmarks_server}/bookmarks/")
        _wait_for_websocket(page)

        _add(page, "첫째", "https://example.com/1")
        _add(page, "둘째", "https://example.com/2")

        _wait_for_count(page, 2)
        assert "둘째" in _items(page).first.inner_text(), "at=0 should prepend"

    def test_toggle_and_delete_round_trip(self, page, bookmarks_server):
        page.goto(f"{bookmarks_server}/bookmarks/")
        _wait_for_websocket(page)
        _add(page, "토글대상", "https://example.com/t")
        _wait_for_count(page, 1)

        assert "안읽음" in _items(page).first.inner_text()
        page.click('li >> button:has-text("토글")')
        page.wait_for_selector('li span:text-is("읽음")', timeout=5000)
        # streaming the same dom id again updates the item, it does not add one (#67)
        assert _items(page).count() == 1

        page.click('li >> button:has-text("삭제")')
        _wait_for_count(page, 0)

    def test_filter_switch_resets_the_stream(self, page, bookmarks_server):
        """A handler that changes state and re-streams: the re-render must not
        wipe the container it just filled (#67)."""
        page.goto(f"{bookmarks_server}/bookmarks/")
        _wait_for_websocket(page)
        _add(page, "이미읽음", "https://example.com/1")
        _add(page, "아직안읽음", "https://example.com/2")
        _wait_for_count(page, 2)

        # mark the first one read through the UI
        page.click('li:has-text("이미읽음") >> button:has-text("토글")')
        page.wait_for_selector('li:has-text("이미읽음") span:text-is("읽음")', timeout=5000)

        page.click('.filters a:has-text("안읽음")')
        _wait_for_count(page, 1)
        assert "아직안읽음" in _items(page).first.inner_text()
