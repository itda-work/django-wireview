# 테스트 헬퍼

> `wireview.testing`이 주는 것 전체. 처음 배우는 순서는 [튜토리얼 09](../tutorials/09-testing-components.md)가 낫고,
> 이 문서는 레퍼런스다.

## 개요

`mount()`로 WebSocket 없이 컴포넌트를 띄우고, 돌아온 `MountedComponent`로 검사한다.

```python
import pytest
from wireview import mount


@pytest.mark.asyncio
async def test_increment():
    view = await mount(XCounter, count=0)   # mount 훅과 joined()까지 실행된다
    await view.call("inc")
    assert view.component.count == 1
```

`mount(component_class, user=None, params=None, session=None, session_key=None, live_session=None, **initial_state)`.
이 여섯 이름은 `mount()`가 쓰므로 같은 이름의 컴포넌트 필드에는 전달되지 않는다.
`ComponentTestCase`를 상속하면 pytest·unittest 클래스 안에서 `self.mount(...)`으로 같은 것을 쓴다.

**거절된 마운트는 freeze된다.** `Meta.on_mount` 훅이 halt하거나 `Meta.live_sessions`가 그 페이지를
허용하지 않으면 서버는 아무것도 그리지 않는다. `mount()`도 그렇게 답한다 — `view.render()`가
`None`이거나 리다이렉트 메타뿐이고, `view.is_frozen`이 참이다. 컴포넌트 인스턴스는 그대로
돌려주므로 훅이 무엇을 했는지는 검사할 수 있다.

**`view.render()`는 페이지의 첫 응답(HTTP 렌더)과 같게 그린다.** 템플릿의 `{% component %}`와
`{% live_component %}` 자식도 그 자리에 함께 그려지고, 자식의 `Meta.on_mount` 훅도 돈다. 첫 응답이
그렇듯 **LiveComponent 자식의 `joined()`·`update()`는 돌지 않는다** — 소켓에서 그것을 돌리는 것은
컨슈머이고 `mount()`에는 컨슈머가 없다. 그래서 자식은 부모가 넘긴 props와 기본값으로 그려진다.
`render()`를 부를 때마다 처음부터 다시 그리므로, 부모가 바뀐 뒤의 `render()`는 바뀐 props로 그린다.

## MountedComponent

| 무엇 | 설명 |
|------|------|
| `await view.call("handler", **kwargs)` | 핸들러 호출. 클라이언트가 보내는 것과 같은 경로 |
| `view.component` | 컴포넌트 인스턴스 |
| `view.render()` | 렌더된 HTML (freeze됐으면 `None`). 페이지의 첫 응답처럼 그린다 — 아래 참고 |
| `await view.render_diff()` | 다음 라이브 렌더가 클라이언트에 보낼 diff. 건너뛰었거나(`skip_render()`) 바뀐 것이 없으면 `None`. **약속하는 것은 `None`인지 아닌지뿐이고**, diff의 모양은 와이어 프로토콜이라 공개가 아니다. 첫 호출은 전체 렌더이고 그 뒤는 앞 호출 대비다. 컨슈머처럼 끝나면 `temporary_assigns`를 비운다. LiveComponent 자식은 참조(`{"c": id}`)로만 나온다. 컨슈머와 같은 경로로 렌더하므로 pytest-django에서는 `django_db` 표시가 필요하다 |
| `view.is_frozen` | `freeze()` 여부 |
| `view.redirected_to` | `wire.redirect_to()`로 간 URL. 없으면 `None`. 단언은 `assert_redirected_to()`가 낫다 — 실패하면 일어난 이동을 나열한다 |
| `view.sent_messages` | 클라이언트로 나간 메시지 목록 (원본. 항목의 모양은 공개가 아니다) |
| `view.dom_actions` | 서버가 지시한 DOM 조작 (항목의 모양은 공개가 아니다). **스트림은 여기 없다** |
| `view.broadcasts` | 이 컴포넌트가 낸 브로드캐스트 |
| `view.presence_broadcasts` | 그중 `PresenceMixin`이 낸 것(입장·퇴장·타이핑). 항목마다 `kwargs`에 `action` |
| `view.clear_messages()` / `view.clear_dom_actions()` | 다음 단계 전에 비운다 |

아래 헬퍼는 전부 `sent_messages` 위에 있다. 직접 뒤져도 되지만, 그러면 **테스트가 wire
프로토콜의 메시지 모양을 알게 된다** — 그건 라이브러리 내부지 사용자 API가 아니다. `sent_messages`,
`dom_actions`, `render_diff()`의 diff, 스트림 검사가 돌려주는 항목은 [호환성 정책](../COMPATIBILITY.md)의
와이어 프로토콜에 속한다. 마이너 릴리스에서 모양이 바뀔 수 있으니 단언은 헬퍼로 한다.

## 내비게이션 단언

```python
await view.call("next_page")

view.assert_pushed_to("/products/", params={"page": "2"})
```

| 헬퍼 | 무엇을 단언하나 |
|------|-----------------|
| `view.assert_pushed_to(url=None, *, params=None)` | `wire.push_to()` |
| `view.assert_replaced_to(url=None, *, params=None)` | `wire.replace_to()` |
| `view.assert_redirected_to(url=None, *, params=None)` | `wire.redirect_to()` |
| `view.assert_no_navigation(command=None)` | URL을 건드리지 않았다 |
| `view.navigations` | 일어난 순서대로의 `Navigation` 목록 |

- `url`에 쿼리를 붙이면 그 부분은 **파라미터로** 비교한다. `"/items/?a=1&b=2"`와
  `"/items/?b=2&a=1"`은 같은 이동에 맞는다.
- `params`는 **통째로** 비교한다. `{"page": "2"}`는 `?page=2&sort=name`에 맞지 않는다 —
  붙어 온 파라미터 하나가 테스트가 잡아야 할 바로 그것이기 때문이다. 쿼리가 없다는 단언은 `params={}`.
- 값은 컴포넌트가 `params`로 받는 것과 같게 읽는다. `.json`으로 끝나는 키는 컴포넌트가
  받을 모습 그대로 디코드되어 보인다.
- 실패 메시지는 **실제로 일어난 이동을 전부 나열한다.**

`Navigation`의 필드:

| 필드 | 뜻 |
|------|----|
| `command` | `"push"`, `"replace"`, `"redirect"` |
| `url` | 컴포넌트가 준 URL 그대로 |
| `path` | 경로 부분. `?page=2`처럼 쿼리만 있으면 `""` |
| `params` | 쿼리를 컴포넌트가 받는 모습으로 읽은 dict (`.json` 키는 디코드) |

`assert_no_navigation()`이 따로 있는 이유는 하나다. "이동했다"와 "여기로 이동했다"는 통과하는
테스트만 보면 구별되지 않는다. 이동하면 안 되는 경로에는 짝을 지어 둔다.

```python
async def test_a_rejected_form_stays_put():
    view = await mount(XForm)
    await view.call("submit", email="not-an-email")

    assert view.component.errors
    view.assert_no_navigation()
```

## 리다이렉트를 따라가기

```python
landed = await view.follow_redirect(XDashboard)

assert landed.component.greeting == "환영합니다"
```

리다이렉트는 **페이지 로드**다. 브라우저가 대상을 새로 받아 오고 그 페이지의 컴포넌트가 새로
마운트되므로, `follow_redirect()`는 이어붙이기가 아니라 새 `mount()`다. 다만 테스트가 매번 다시
적어야 할 것들을 대신 가져간다.

- **대상 URL의 쿼리 문자열이 params가 된다.** `params=`로 덮어쓸 수 있다.
- **user와 session이 따라간다.** 쿠키가 하는 일과 같다.
- **대상의 live_session은 URLconf에서 읽는다.** 여기 있던 경계를 물려주지 않는다 — 페이지가
  경계를 바꾸는 방법이 바로 리다이렉트이기 때문이다.
- **대상 경계가 이 사용자를 거절하면 여기서도 거절한다** (`AssertionError`). 서버라면 로그인
  리다이렉트나 403을 돌려주고 컴포넌트를 그리지 않는다. 조용히 마운트하면 헬퍼가 서버와
  다른 답을 내는 것이고, 그런 헬퍼는 없느니만 못하다.

대상이 이 프로젝트의 URLconf에 없으면 무엇을 해야 하는지 알려 주며 실패한다. 의도한 것이라면
`live_session=None`(또는 특정 경계)을 명시한다.

## push를 따라가기

```python
await view.call("next_page")          # wire.push_to("?page=2")
await view.follow_push()              # 클라이언트가 하는 나머지 절반

assert view.component.page == 2
```

`push_to()`·`replace_to()`는 절반만이다. 나머지 절반은 클라이언트가 주소창을 바꾸고 **새 params를
서버에 알리는 것**이고, 그것이 `params_changed()`를 돌린다. 손으로 하면 테스트가 프로토콜을 알아야
하고, 그러고도 저장소의 params는 옛것으로 남는다. `follow_push()`가 둘 다 한다.

**경계를 넘는 push는 이것이 아니다.** 그 이동은 전체 페이지 로드가 되고 `params_changed`는 아예
가지 않는다(#58). 그 경우 `follow_push()`는 콜백을 돌리는 대신 실패한다 — 대상 페이지를 새로
마운트하라는 뜻이다.

`?page=2`처럼 쿼리만 있는 목적지도 그대로 쓸 수 있다. 세 내비게이션 메서드 모두 `?`나 `#`으로
시작하는 문자열은 URL로 그대로 두고, 나머지는 Django의 `resolve_url`이 처리한다(뷰 이름, 모델,
경로).

## 스트림 검사

스트림 아이템은 `view.render()`에도 `view.dom_actions`에도 없다. 템플릿은 빈 컨테이너만 렌더하고
아이템 HTML은 별도 메시지로 간다.

| 헬퍼 | 반환 |
|------|------|
| `view.stream_html(stream=None)` | 보내진 아이템 HTML 전부를 이어 붙인 문자열 |
| `view.stream_items(stream=None)` | `{"id", "html"}` 목록 |
| `view.stream_ops(stream=None)` | 원본 연산 목록 (`op`, `stream`, `items`, `at`, `limit`). 와이어 메시지 그대로라 모양은 공개가 아니다 — 단언은 위 둘로 한다 |

```python
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_filter_hides_read_items():
    await Bookmark.objects.acreate(title="새 글", url="https://example.com/new")
    await Bookmark.objects.acreate(title="지난 글", url="https://example.com/old", is_read=True)

    view = await mount(XBookmarkList)
    view.clear_messages()          # joined()의 초기 stream()을 비운다
    await view.call("set_filter", filter="unread")

    html = view.stream_html("bookmarks")
    assert "새 글" in html
    assert "지난 글" not in html
```

`not in` 단언에 쓰는 문자열은 남아야 할 항목의 HTML에 부분 문자열로 들어 있지 않아야 한다.
"읽은 것"은 "안 읽은 것" 안에 있으므로 그 단언은 필터가 옳아도 실패한다.

**`clear_messages()`를 잊지 않는다.** `stream()`을 다시 부르는 핸들러(필터·정렬·페이지 전환)는
이전 메시지 위에 누적되므로, 비우지 않으면 방금 걸러 낸 아이템이 앞선 메시지에 남아 있다.

## 경계 안에서 마운트하기

```python
view = await mount(XAdminPanel, user=staff, live_session="admin")
```

`live_session=`에는 `LiveSession` 객체나 그 이름을 준다. 주지 않으면 **경계를 선언하지 않은
페이지**에 마운트한 것이고, 그것은 `Meta.live_sessions`를 선언한 컴포넌트가 거절되는 상황이다.
경계의 `on_mount` 훅도 이때 돈다. 자세한 것은 [live_session](./live-session.md).

## 주의사항

- **`@pytest.mark.django_db`는 async ORM 호출을 롤백하지 못한다.** `acreate`·`asave`·`adelete`로
  쓴 레코드는 다음 테스트에 보인다. 개수 단언 대신 이 테스트가 만든 레코드를 직접 집는다.
- **마커는 등록해서 쓴다.** `--strict-markers`에서는 등록하지 않은 마커가 수집 오류다(등록 방법은
  [튜토리얼 09](../tutorials/09-testing-components.md#테스트-마커)). 이 저장소의 테스트는 `unit`·`integration`·`slow`·`e2e`
  중 하나를 반드시 붙인다.
- `mount()`에는 컨슈머가 없고 채널 레이어는 흉내 낸 것이다. 렌더는 첫 응답의 것이라 자식이
  그려지지만, 컨슈머 경로 전체(join, 재접속, 자식 LiveComponent의 `joined()`·`update()`·`leaving()`)를
  검사하려면 `tests/test_live_component_render.py`나 `tests/test_live_session_contract.py`처럼
  컨슈머를 직접 만든다. `view.call()`은 부모의 핸들러만 부른다.
- 자식은 `mount()`가 만든 것이 아니라 렌더가 만든 것이다. 자식이 낸 브로드캐스트는
  `view.broadcasts`에 남지 않는다.

## 관련 기능

- [튜토리얼 09 테스트 가이드](../tutorials/09-testing-components.md)
- [live_session](./live-session.md)
- [Streams 튜토리얼](../tutorials/06-streams-api.md)
