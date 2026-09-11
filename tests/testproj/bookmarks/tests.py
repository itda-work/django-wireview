import pytest

from testproj.e2e_browser import expect_count, open_live
from testproj.e2e_server import serve
from wireview import mount
from wireview.schemas import ModelAction

from .live import XBookmarkList
from .models import Bookmark


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
    stream_html = view.stream_html()
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

    assert "다른 탭에서 추가" in view.stream_html()


# --- E2E ---------------------------------------------------------------------
#
# The mount() tests above never touch a template. They cannot tell whether a form
# submit actually delivers its `name` fields as handler arguments, or whether a
# stream item ever reaches the DOM — both of which the skill claims. These do.


@pytest.fixture(scope="function")
def bookmarks_server():
    """A live ASGI server for this module's E2E tests."""
    with serve() as base_url:
        yield base_url


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


def _wait_for_count(page, expected: int):
    expect_count(_items(page), expected)


@pytest.mark.e2e
class TestBookmarksE2E:
    @pytest.fixture(autouse=True)
    def setup_db(self, transactional_db):
        pass

    def test_form_submit_delivers_named_inputs_as_handler_arguments(self, page, bookmarks_server):
        """The skill claims a form field's `name` becomes the handler argument."""
        open_live(page, f"{bookmarks_server}/bookmarks/")
        _wait_for_count(page, 0)

        _add(page, "위키", "https://wikipedia.org")

        _wait_for_count(page, 1)
        assert "위키" in _items(page).first.inner_text()
        assert "https://wikipedia.org" in _items(page).first.inner_html()

    def test_stream_insert_reaches_the_dom(self, page, bookmarks_server):
        open_live(page, f"{bookmarks_server}/bookmarks/")

        _add(page, "첫째", "https://example.com/1")
        _add(page, "둘째", "https://example.com/2")

        _wait_for_count(page, 2)
        assert "둘째" in _items(page).first.inner_text(), "at=0 should prepend"

    def test_toggle_and_delete_round_trip(self, page, bookmarks_server):
        open_live(page, f"{bookmarks_server}/bookmarks/")
        _add(page, "토글대상", "https://example.com/t")
        _wait_for_count(page, 1)

        assert "안읽음" in _items(page).first.inner_text()
        page.click('li >> button:has-text("토글")')
        expect_count(page.locator('li span:text-is("읽음")'), 1)
        # streaming the same dom id again updates the item, it does not add one (#67)
        expect_count(_items(page), 1)

        page.click('li >> button:has-text("삭제")')
        _wait_for_count(page, 0)

    def test_filter_switch_resets_the_stream(self, page, bookmarks_server):
        """A handler that changes state and re-streams: the re-render must not
        wipe the container it just filled (#67)."""
        open_live(page, f"{bookmarks_server}/bookmarks/")
        _add(page, "이미읽음", "https://example.com/1")
        _add(page, "아직안읽음", "https://example.com/2")
        _wait_for_count(page, 2)

        # mark the first one read through the UI
        page.click('li:has-text("이미읽음") >> button:has-text("토글")')
        expect_count(page.locator('li:has-text("이미읽음") span:text-is("읽음")'), 1)

        page.click('.filters a:has-text("안읽음")')
        _wait_for_count(page, 1)
        assert "아직안읽음" in _items(page).first.inner_text()
