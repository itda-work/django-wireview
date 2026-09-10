# 테스트

`mount()`로 WebSocket 없이 컴포넌트를 띄운다. 이것이 wireview 앱 테스트의 기본형이다.

```python
import pytest
from wireview import mount

from myapp.live import XCounter


@pytest.mark.asyncio
async def test_increment():
    view = await mount(XCounter, amount=0)      # joined()까지 실행된다
    await view.call("inc")
    assert view.component.amount == 1
```

`mount(component_class, user=None, params=None, session=None, session_key=None, live_session=None, **initial_state)`.
`user`를 주면 인증된 사용자로, `params`를 주면 URL 쿼리 파라미터가 있는 상태로,
`session={"k": v}`·`session_key="s1"`을 주면 세션이 있는 상태로, `live_session="admin"`을 주면
그 경계 안의 페이지에 뜬다. 이 여섯 이름은 `mount()`가 쓰므로 같은 이름의 컴포넌트 필드에는
전달되지 않는다.

## MountedComponent가 주는 것

| 속성 · 메서드 | 무엇 |
|---|---|
| `await view.call("handler", **kwargs)` | 핸들러 호출. 클라이언트가 보내는 것과 같은 경로 |
| `view.component` | 컴포넌트 인스턴스. 상태를 직접 검사한다 |
| `view.render()` | 렌더된 HTML 문자열 |
| `view.sent_messages` | 클라이언트로 나간 메시지 목록 |
| `view.dom_actions` | 서버가 지시한 DOM 조작 목록. **스트림은 여기 안 들어간다** (아래 참조) |
| `view.redirected_to` | 리다이렉트 대상 URL (없으면 `None`) |
| `view.assert_pushed_to(url, params=...)` | push 단언. `assert_replaced_to`·`assert_redirected_to`도 같은 모양 |
| `view.assert_no_navigation()` | URL을 건드리지 않았다 |
| `await view.follow_redirect(NextComponent)` | 리다이렉트를 따라가 대상 컴포넌트를 마운트 |
| `await view.follow_push()` | push·replace 뒤에 클라이언트가 하는 `params_changed`를 돌린다 |
| `view.stream_html(name)` | 스트림으로 나간 아이템 HTML (`stream_items`·`stream_ops`도 있다) |
| `view.is_frozen` | `freeze()` 여부 |
| `view.wire.broadcasts` | 이 컴포넌트가 낸 브로드캐스트 |
| `view.clear_messages()` / `view.clear_dom_actions()` | 다음 단계 전에 비운다. 필터 전환처럼 `stream()`을 다시 부르는 핸들러를 검사하기 전에 필수 |

`ComponentTestCase`를 상속하면 pytest·unittest 클래스 안에서 같은 유틸을 쓸 수 있다.

## 스트림을 테스트할 때

스트림 아이템은 `view.render()`에도 `view.dom_actions`에도 없다. 컴포넌트 템플릿은 빈
컨테이너만 렌더하고, 아이템 HTML은 별도 메시지로 간다. `view.stream_html(name)`이 그것을
모아 준다.

```python
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_filter_hides_read_items():
    view = await mount(XBookmarkList)
    view.clear_messages()          # joined()의 초기 stream()을 비우지 않으면 그 아이템까지 잡힌다
    await view.call("set_filter", filter="unread")

    html = view.stream_html("bookmarks")
    assert "안 읽은 것" in html
    assert "읽은 것" not in html
```

`stream()`을 다시 부르는 핸들러(필터·정렬·페이지 전환)는 이전 메시지 위에 **누적**된다.
`clear_messages()`를 빼면 방금 걸러 낸 아이템이 앞선 메시지에 남아 있어, 통과해야 할
테스트가 실패하거나 반대로 오탐 통과한다.

## URL을 바꾸는 핸들러를 테스트할 때

```python
@pytest.mark.asyncio
async def test_paging():
    view = await mount(XProductList)
    await view.call("next_page")

    view.assert_pushed_to("/products/", params={"page": "2"})
    await view.follow_push()               # 클라이언트가 하는 나머지 절반
    assert view.component.page == 2
```

- `params`는 **통째로** 비교한다. 붙어 온 파라미터 하나가 테스트가 잡아야 할 바로 그것이다.
- **이동하면 안 되는 경로에는 `view.assert_no_navigation()`을 짝지어 둔다.** "이동했다"와
  "여기로 이동했다"는 통과하는 테스트만 보면 구별되지 않는다.
- 리다이렉트는 페이지 로드다. `await view.follow_redirect(NextComponent)`가 대상 페이지의
  컴포넌트를 마운트하며, 대상의 `live_session`을 URLconf에서 읽어 그 경계가 거절하면 함께
  거절한다.
- 경계를 넘는 push는 전체 페이지 로드라 `params_changed`가 가지 않는다. 그 경우
  `follow_push()`는 실패하고, 대상 페이지를 새로 마운트하라고 알려 준다.

## DB를 건드리는 테스트

**`@pytest.mark.django_db`는 async ORM 호출을 롤백하지 못한다.** 동기 ORM은 정상적으로
롤백되지만, `acreate`·`asave`·`adelete`로 쓴 레코드는 테스트가 끝나도 남아 다음 테스트에
보인다. wireview 핸들러는 async라 사실상 모든 컴포넌트 테스트가 여기에 걸린다.

```python
    # 이렇게 쓰지 않는다 — 앞선 테스트가 남긴 레코드까지 센다
    assert await Bookmark.objects.acount() == 0

    # 이렇게 쓴다 — 이 테스트가 만든 레코드만 본다
    assert not await Bookmark.objects.filter(pk=bookmark.pk).aexists()
```

전역 개수·목록 비교를 피하고 pk로 범위를 좁힌다. 격리가 꼭 필요하면
`@pytest.mark.django_db(transaction=True)`를 쓰되 느려진다.
배경: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/09-testing-components.md

## 무엇을 테스트하나

- **핸들러의 상태 전이**: 이벤트 → 필드 값. 가장 값싸고 가장 많이 잡는다.
- **권한**: 남의 객체 id를 넘겼을 때 거부되는지. 클라이언트 인자는 신뢰할 수 없다.
- **렌더 결과**: `view.render()`에 기대하는 텍스트·클래스가 있는지.
- **브로드캐스트**: 알림을 보내야 하는 핸들러가 실제로 보냈는지 (`view.wire.broadcasts`).

브라우저가 실제로 필요한 것(idiomorph 갱신, 업로드 진행률, JS Hook)만 Playwright E2E로 남긴다.

## 컴포넌트 밖의 방어선

테스트 이전에 `manage.py check`가 조용한 실패를 잡는다 — 핸들러가 async가 아님, 컴포넌트
이름 충돌, JS 미로드, 다중 프로세스에서 깨지는 InMemory 레이어. CI에 넣어 둔다.

상세: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/09-testing-components.md
