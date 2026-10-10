# 테스트 헬퍼

> `wireview.testing`이 주는 것 전체. 처음 배우는 순서는 [테스트 가이드 튜토리얼](../tutorials/09-testing-components.md)이 낫고,
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

`mount(component_class, /, *, user=None, params=None, session=None, session_key=None, live_session=None, path=None, state=None, **initial_state)`.
옵션은 모두 키워드로 준다. `params`를 주면 페이지 로드의 join처럼 `joined()` 뒤에 `params_changed()`가 그 값으로
돈다. `LiveComponent`를 마운트해도 같다 — 페이지에서 처음 그려질 때 그 순서로 듣는다(#170). 마운트한 컴포넌트의
템플릿이 그리는 자식 LiveComponent의 수명주기는 돌지 않는다(`render()` 참고). `path`는 컴포넌트가 놓인 페이지의 경로(`"/items/"`)이고, `follow_push()`가 patch인지 가린다(아래).
필드 초깃값은 키워드(`count=0`)나 `state={"count": 0}`로 준다. 옵션과 이름이 같은
필드(`params` 같은)는 `state=`로만 줄 수 있다 — 이후 릴리스가 옵션을 더해도 그 필드는 `state=`로 계속 줄 수 있다.
같은 필드를 두 곳에 주면 `TypeError`다.
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
| `await view.render_diff()` | 다음 라이브 렌더가 클라이언트에 보낼 diff. 건너뛰었거나(`skip_render()`) 바뀐 것이 없으면 `None`. **약속하는 것은 `None`인지 아닌지뿐이고**, diff의 모양은 와이어 프로토콜이라 공개가 아니다. 첫 호출은 전체 렌더이고 그 뒤는 앞 호출 대비다. 단 컴포넌트가 알림을 받으면 그 렌더도 기준선을 옮긴다 — 페이지가 이미 받은 것이므로 알림이 그린 것은 `sent_messages`의 `render`에 있고, 그 뒤의 `render_diff()`는 알림 뒤의 변화만 본다(바뀐 것이 없으면 `None`, 아래 "알림 받기"). 컨슈머처럼 끝나면 `temporary_assigns`를 비운다. LiveComponent 자식은 참조(`{"c": id}`)로만 나온다. 컨슈머와 같은 경로로 렌더하므로 pytest-django에서는 `django_db` 표시가 필요하다. `Meta.shared_render`를 선언한 컴포넌트는 `DEBUG`와 상관없이 검사한다 — 렌더가 `user`·`session`·`request` 등을 읽으면 `SharedRenderError`다(`VERIFY_SHARED_RENDER = False`로 끈다, [shared-render](./shared-render.md#틀린-선언-잡기)) |
| `view.is_frozen` | `freeze()` 여부 |
| `view.redirected_to` | `wire.redirect_to()`로 간 URL. 없으면 `None`. 단언은 `assert_redirected_to()`가 낫다 — 실패하면 일어난 이동을 나열한다 |
| `view.sent_messages` | 클라이언트로 나간 메시지 목록 (원본. 항목의 모양은 공개가 아니다) |
| `view.broadcasts` | 이 컴포넌트가 낸 브로드캐스트. 기록만 하지 않고 페이지의 것처럼 발행한다: 같은 프로세스에서 마운트한 컴포넌트(자기 자신 포함)와 그 프로세스의 채널 레이어로 연결된 소켓에 닿는다(아래 "알림 받기"). 채널 레이어가 거절하는 이름(`room:42`)은 기록하지 않고 레이어와 같은 `TypeError`를 던진다. 구독(`Meta.subscriptions`·`get_subscriptions()`)도 세션처럼 `mount()` 끝과 `call()`·`follow_push()`마다 맞추므로, 그런 이름의 구독은 `mount()`나 그 `call()`·`follow_push()`가 같은 `TypeError`로 실패한다 |
| `view.presence_broadcasts` | 그중 `PresenceMixin`이 낸 것(입장·퇴장·타이핑). 항목마다 `kwargs`에 `action` |
| `view.clear_messages()` | 다음 단계 전에 비운다 |
| `async with view.queries() as q:` | 블록 안의 `call()`·`render_diff()`·`render()`와 그것이 만든 작업이 실행한 SQL. `q.count`는 몇 건인지, `q.assert_no_repeats(threshold=2)`는 같은 템플릿 줄·property에서 같은 SQL이 두 번 이상이면 실패한다(N+1). 설정과 무관하게 모은다. 행과 실패 메시지의 모양은 공개가 아니다 ([render-queries](./render-queries.md#테스트에서-단언하기)) |

아래 헬퍼는 전부 `sent_messages` 위에 있다. 직접 뒤져도 되지만, 그러면 **테스트가 wire
프로토콜의 메시지 모양을 알게 된다** — 그건 라이브러리 내부지 사용자 API가 아니다. `sent_messages`,
`render_diff()`의 diff, 스트림 검사가 돌려주는 항목은 [호환성 정책](../COMPATIBILITY.md)의
와이어 프로토콜에 속한다. 마이너 릴리스에서 모양이 바뀔 수 있으니 단언은 헬퍼로 한다.

`wireview.testing`을 import한 프로세스는 렌더 쿼리를 편집기용 파일에 쓰지 않는다. 테스트가 별도 프로세스로 띄우는
서버도 쓰지 않게 하려면 환경 변수 `WIREVIEW_RENDER_QUERIES_DIR=off`를 둔다([render-queries](./render-queries.md#켜기와-끄기)).

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

`push_to()`·`replace_to()`는 절반만이다. 나머지 절반은 브라우저가 한다. `follow_push()`는 브라우저와 같은
판단으로 그 절반을 한다(#169).

- **같은 경로**(`"?page=2"`처럼 쿼리나 조각만, 또는 `mount(..., path=)`로 준 경로와 같은 경로): patch다.
  클라이언트가 주소창을 바꾸고 새 params를 서버에 알리면 **같은 인스턴스**의 `params_changed()`가 돈다.
  인자 없이 부르고, 따라간 `Navigation`을 돌려준다. 이벤트로 바꾼 상태는 남는다.
- **다른 경로**: 브라우저는 그 페이지를 가져오고 컴포넌트는 새로 join한다. 그 페이지가 그리는 컴포넌트를
  넘긴다 — `landed = await view.follow_push(ProductDetail)`. `follow_redirect()`처럼 대상의 `live_session`을
  URLconf에서 읽고, 그 경계가 사용자를 거절하면 실패한다. 경계를 넘는 이동은 브라우저에서 전체 로드지만,
  새로 마운트된다는 점에서 결과는 같다. 떠난 페이지의 원래 인스턴스(`view`)는 브라우저에서처럼 사라진다 —
  얼고(`view.is_frozen`) 알림·`Broadcast`를 더 받지 않는다. 같은 `live_session` 안으로 가는 sticky 컴포넌트
  (`Meta.sticky`)만 boost 이동을 건너 남아 계속 받는다.

어느 쪽인지는 헬퍼가 정한다. 같은 경로에 컴포넌트를 넘기거나 다른 경로에 넘기지 않으면 실패한다. 경로가 있는
목적지(`"/items/?page=2"`)인데 `mount()`에 `path`가 없으면 판단할 수 없다. 그때는 1.1처럼 같은 인스턴스에
`params_changed()`를 돌리고 `WireviewDeprecationWarning`을 낸다. 2.0에서는 실패한다. 컴포넌트에 `path`라는 필드가
있으면 그 값은 `state={"path": ...}`로 준다 — `state=`에 없는 채 `path=`를 주면 `TypeError`다. 손으로 `params_changed()`를 부르면 테스트가
프로토콜을 알아야 하고, 그러고도 저장소의 params는 옛것으로 남는다.

`?page=2`처럼 쿼리만 있는 목적지도 그대로 쓸 수 있다. 세 내비게이션 메서드 모두 `?`나 `#`으로
시작하는 문자열은 URL로 그대로 두고, 나머지는 Django의 `resolve_url`이 처리한다(뷰 이름, 모델,
경로).

## 스트림 검사

스트림 아이템은 `view.render()`에 없다. 템플릿은 빈 컨테이너만 렌더하고
아이템 HTML은 별도 메시지로 간다.

| 헬퍼 | 반환 |
|------|------|
| `view.stream_html(stream=None)` | 보내진 아이템 HTML 전부를 이어 붙인 문자열 |
| `view.stream_items(stream=None)` | `{"id", "html"}` 목록 |
| `view.stream_ops(stream=None)` | 원본 연산 목록 (`op`, `stream`, `items`, `at`, `limit`). 와이어 메시지 그대로라 모양은 공개가 아니다 — 단언은 위 둘로 한다 |

`mount()`한 컴포넌트는 같은 프로세스에서 발행한 [`Broadcast`](./broadcast.md)를 연결처럼 받는다 — 정확히 그 클래스이고
그 토픽을 구독할 때만. 받은 스트림 연산·훅 이벤트·JS 명령은 컴포넌트 자신의 것처럼 위 헬퍼와 `sent_messages`에 남는다.

```python
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)   # acreate가 커밋한다. 아래 "주의사항"
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

## 알림 받기

`mount()`한 컴포넌트는 같은 프로세스에서 발행한 알림을 세션처럼 받는다(#201) — `abroadcast()`·`broadcast()`·
`self.broadcast()`·`atoast()`의 알림, auto-broadcast의 모델 변경 모두. 받는 것은 구독한 채널(`Meta.subscriptions`·
`get_subscriptions()`)에 온 것뿐이고, 받으면 세션과 같은 순서로 돈다: `notification()`(모델 변경은 `mutation()`)
→ 렌더 → 구독 다시 맞추기. 렌더가 보낸 것은 `sent_messages`에 `render`로 남고, 받는 메서드가 보낸 스트림 연산·
`push_event`·JS도 그 뒤에 남는다. 규칙은 세션과 같은 코드다(사본이 아니다). `component.notification()`을 직접
부르면 렌더와 구독 맞추기를 건너뛰므로 페이지와 어긋난다.

```python
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_new_notification_reaches_the_open_list(alice):
    view = await mount(XNotificationList, user=alice)   # examples/notifications

    # auto-broadcast의 model_mutation → mutation() → 렌더
    await Notification.objects.acreate(user=alice, title="새 알림", message="…")

    assert "새 알림" in view.stream_html("notifications")
```

- **발행이 돌아오기 전에 받는다.** `await abroadcast(...)`나 그것을 부른 `call()`이 끝나면 받은 쪽의
  `notification()`과 렌더도 끝나 있다. 기다릴 것이 없다.
- **핸들러가 도는 동안 온 알림은 기다린다.** 연결은 메시지를 하나씩 처리한다. 핸들러가 자기가 듣는
  채널에 발행하면, 그 알림은 핸들러가 그때까지 보낸 메일(스트림 연산·`push_event`·JS) 뒤에 서고 핸들러가
  끝난 뒤 처리된다(#205).
- **받는 메서드가 예외를 던지면 발행한 곳에서 올라온다.** 세션은 그 컴포넌트만 버리고 다시 join시키지만,
  테스트에는 그 예외를 알린다. 그래서 다른 컴포넌트의 `call()`이나 `abroadcast()`, auto-broadcast라면 행을 쓴
  `acreate()`·`save()`가 받는 쪽의 예외로 실패한다.
- **자기가 듣는 채널에 보내면 자기도 받는다.** 채팅의 "보내고 받기"가 브라우저처럼 돈다. 핸들러에서 목록에
  넣고 `notification()`에서 또 넣는 컴포넌트는 항목이 둘이 된다 — 브라우저에서도 그렇다.
  `joined()`에서 보낸 `self.broadcast()`도 구독을 맞춘 뒤에 나가므로 자기가 받는다(세션의 join과 같다).
- **알림의 렌더는 페이지가 가진 것 대비다.** 세션은 join이 이미 페이지 전체를 보냈으므로 알림 뒤에는 바뀐 것만
  보낸다. `mount()`는 그 기준선을 첫 알림을 처리하기 전에 잡는다(기록하지 않는다). 그래서 상태를 바꾸지 않는
  알림은 `render`를 남기지 않고, 알림 뒤의 `render_diff()`는 알림이 그린 것을 다시 내지 않는다.
- **다른 테스트로 새지 않는다.** 테스트가 끝나면 그 테스트가 마운트한 컴포넌트는 더 듣지 않는다. pytest에서는
  wireview와 함께 설치되는 플러그인(`wireview`, `pytest11` 진입점)이 테스트마다 끊는다 — 켤 것이 없다. 이벤트
  루프를 여러 테스트가 함께 쓰는 설정(pytest-asyncio의 `loop_scope="module"`·`"session"`)에서도 그렇다.
  `-p no:wireview`로 플러그인을 끄거나 pytest가 아닌 러너(`manage.py test`)에서는 이벤트 루프만이 가른다:
  마운트한 루프에서 발행한 것만 받으므로 테스트마다 새 루프인 Django의 async `TestCase`·`IsolatedAsyncioTestCase`
  에서는 새지 않지만, 루프를 함께 쓰는 테스트 사이에서는 앞 테스트의 인스턴스가 사라질 때까지 듣는다.
  모듈·세션 범위 픽스처에서 마운트한 컴포넌트도 그것을 처음 만든 테스트가 끝나면 듣지 않는다.
- **받은 쪽의 렌더는 `render_diff()`와 같은 경로다.** pytest-django에서는 알림을 받는 컴포넌트가 있는 테스트에
  `django_db` 표시가 필요하다.
- **같은 프로세스 안에서만이다.** 다른 프로세스의 연결로는 채널 레이어가 나른다. 마운트한 컴포넌트의
  `self.broadcast()`도 그 프로세스의 채널 레이어로 나가므로 같은 프로세스의 소켓(`WebsocketCommunicator`,
  라이브 서버)은 받는다.

## 핸들러의 메일과 Broadcast 패치의 순서

세션은 `Broadcast` 패치를 오는 대로 쓰고, 핸들러가 보낸 메일(스트림 연산·`push_event`·JS·내비게이션)은 핸들러가
끝난 뒤 쓴다. `mount()`도 그렇다(#205): `call()`·`joined()`·`follow_push()`의 `params_changed`·알림 처리가 보낸
메일은 그것이 끝날 때 `sent_messages`에 들어가고, 그 사이에 온 패치는 그 앞에 든다. 그래서 목록을 읽어
`stream_insert`하는 핸들러 도중에 그 항목의 삭제 패치가 오면, 브라우저처럼 `delete` 뒤에 `insert`가 남는다 —
항목이 되살아나는 결함이 테스트에도 보인다. `start_async`·`assign_async`의 작업은 핸들러가 아니다. 연결이 그것을
기다리지 않으므로 작업이 보낸 것은 바로 들어간다.

## 경계 안에서 마운트하기

```python
view = await mount(XAdminPanel, user=staff, live_session="admin")
```

`live_session=`에는 `LiveSession` 객체나 그 이름을 준다. 주지 않으면 **경계를 선언하지 않은
페이지**에 마운트한 것이고, 그것은 `Meta.live_sessions`를 선언한 컴포넌트가 거절되는 상황이다.
경계의 `on_mount` 훅도 이때 돈다. 자세한 것은 [live_session](./live-session.md).

## 주의사항

- **async ORM으로 쓰는 테스트는 `@pytest.mark.django_db(transaction=True)`.** 그냥 `django_db`의 롤백은
  테스트 스레드의 연결에만 걸리고, `acreate`·`asave`·`adelete`와 async 핸들러 안의 ORM 호출은 워커 스레드의
  연결에서 곧바로 커밋되어 다음 테스트에 남는다. `transaction=True`는 끝에 테이블을 비운다. 이 저장소에서 잰
  비용은 테스트 하나에 약 15ms다([테스트 가이드 튜토리얼](../tutorials/09-testing-components.md#모델과-함께-테스트)).
- **마커는 등록해서 쓴다.** `--strict-markers`에서는 등록하지 않은 마커가 수집 오류다(등록 방법은
  [테스트 가이드 튜토리얼](../tutorials/09-testing-components.md#테스트-마커)). 이 저장소의 테스트는 `unit`·`integration`·`slow`·`e2e`
  중 하나를 반드시 붙인다.
- `mount()`에는 컨슈머가 없고 채널 레이어는 흉내 낸 것이다(구독은 기록되고, 받는 것은 같은 프로세스의 알림·`Broadcast`다). 렌더는 첫 응답의 것이라 자식이
  그려지지만, 컨슈머 경로 전체(join, 재접속, 자식 LiveComponent의 `joined()`·`update()`·`leaving()`)를
  검사하려면 `tests/test_live_component_render.py`나 `tests/test_live_session_contract.py`처럼
  컨슈머를 직접 만든다. `view.call()`은 부모의 핸들러만 부른다.
- 자식은 `mount()`가 만든 것이 아니라 렌더가 만든 것이다. 자식이 낸 브로드캐스트는
  `view.broadcasts`에 남지 않는다.

## 관련 기능

- [튜토리얼: 테스트 가이드](../tutorials/09-testing-components.md)
- [live_session](./live-session.md)
- [Streams 튜토리얼](../tutorials/06-streams-api.md)
