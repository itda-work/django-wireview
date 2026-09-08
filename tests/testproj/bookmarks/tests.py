import pytest

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
