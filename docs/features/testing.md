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
| `await view.call("handler", **kwargs)` | 핸들러 호출. 클라이언트가 보내는 것과 같은 경로. 템플릿의 바인딩은 보지 않는다 — 그것까지 보려면 아래 행 |
| `await view.click(selector, *, text=None)` 외 `change`·`input`·`submit`·`keydown`·`keyup`·`focus`·`blur` | 렌더의 요소를 골라 그 이벤트를 보낸다. 요소의 `wire-on-*` 바인딩대로 핸들러가 불린다 — 아래 "페이지의 요소로 이벤트 보내기" |
| `view.component` | 컴포넌트 인스턴스 |
| `view.render()` | 렌더된 HTML (freeze됐으면 `None`). 페이지의 첫 응답처럼 그린다 — 아래 참고 |
| `await view.render_diff()` | 다음 라이브 렌더가 클라이언트에 보낼 diff. 건너뛰었거나(`skip_render()`) 바뀐 것이 없으면 `None`. **약속하는 것은 `None`인지 아닌지뿐이고**, diff의 모양은 와이어 프로토콜이라 공개가 아니다. 첫 호출은 전체 렌더이고 그 뒤는 앞 호출 대비다. 단 컴포넌트가 알림을 받으면 그 렌더도 기준선을 옮긴다 — 페이지가 이미 받은 것이므로 알림이 그린 것은 `sent_messages`의 `render`에 있고, 그 뒤의 `render_diff()`는 알림 뒤의 변화만 본다(바뀐 것이 없으면 `None`, 아래 "알림 받기"). `render_diff()`로 기준선을 잡은 뒤 `call()`한 이벤트가 있으면, 다음 알림이 그 이벤트의 렌더를 먼저 해 `render`로 남긴다(세션이 이벤트마다 렌더하듯. `skip_render()`했으면 남기지 않는다). 컨슈머처럼 끝나면 `temporary_assigns`를 비운다. LiveComponent 자식은 참조(`{"c": id}`)로만 나온다. 컨슈머와 같은 경로로 렌더하므로 pytest-django에서는 `django_db` 표시가 필요하다. `Meta.shared_render`를 선언한 컴포넌트는 `DEBUG`와 상관없이 검사한다 — 렌더가 `user`·`session`·`request` 등을 읽으면 `SharedRenderError`다(`VERIFY_SHARED_RENDER = False`로 끈다, [shared-render](./shared-render.md#틀린-선언-잡기)) |
| `view.is_frozen` | `freeze()` 여부 |
| `view.redirected_to` | `wire.redirect_to()`로 간 URL. 없으면 `None`. 단언은 `assert_redirected_to()`가 낫다 — 실패하면 일어난 이동을 나열한다 |
| `view.sent_messages` | 클라이언트로 나간 메시지 목록 (원본. 항목의 모양은 공개가 아니다) |
| `view.broadcasts` | 이 컴포넌트가 낸 브로드캐스트. 기록만 하지 않고 페이지의 것처럼 발행한다: 같은 프로세스에서 마운트한 컴포넌트(자기 자신 포함)와 그 프로세스의 채널 레이어로 연결된 소켓에 닿는다(아래 "알림 받기"). **테스트 설정의 채널 레이어로도 나가므로**, 그 레이어가 redis·nats면 서버가 떠 있어야 한다 — 없으면 `call()`이 레이어의 연결 오류로 실패한다(실제 연결도 그렇게 실패한다). 단위 테스트 설정은 InMemory 레이어이거나 `CHANNEL_LAYERS` 없이 둔다. 채널 레이어가 거절하는 이름(`room:42`)은 기록하지 않고 레이어와 같은 `TypeError`를 던진다. 구독(`Meta.subscriptions`·`get_subscriptions()`)도 세션처럼 `mount()` 끝과 `call()`·`follow_push()`마다 맞추므로, 그런 이름의 구독은 `mount()`나 그 `call()`·`follow_push()`가 같은 `TypeError`로 실패한다 |
| `view.presence_broadcasts` | 그중 `PresenceMixin`이 낸 것(입장·퇴장·타이핑). 항목마다 `kwargs`에 `action` |
| `view.clear_messages()` | 다음 단계 전에 비운다 |
| `async with view.queries() as q:` | 블록 안의 `call()`·`render_diff()`·`render()`와 그것이 만든 작업이 실행한 SQL. `q.count`는 몇 건인지, `q.assert_no_repeats(threshold=2)`는 같은 템플릿 줄·property에서 같은 SQL이 두 번 이상이면 실패한다(N+1). 설정과 무관하게 모은다. 행과 실패 메시지의 모양은 공개가 아니다 ([render-queries](./render-queries.md#테스트에서-단언하기)) |

아래 헬퍼는 전부 `sent_messages` 위에 있다. 직접 뒤져도 되지만, 그러면 **테스트가 wire
프로토콜의 메시지 모양을 알게 된다** — 그건 라이브러리 내부지 사용자 API가 아니다. `sent_messages`,
`render_diff()`의 diff, 스트림 검사가 돌려주는 항목은 [호환성 정책](../COMPATIBILITY.md)의
와이어 프로토콜에 속한다. 마이너 릴리스에서 모양이 바뀔 수 있으니 단언은 헬퍼로 한다.

`wireview.testing`을 import한 프로세스는 렌더 쿼리를 편집기용 파일에 쓰지 않는다. 테스트가 별도 프로세스로 띄우는
서버도 쓰지 않게 하려면 환경 변수 `WIREVIEW_RENDER_QUERIES_DIR=off`를 둔다([render-queries](./render-queries.md#켜기와-끄기)).

## 페이지의 요소로 이벤트 보내기

`call("remove", id=3)`은 템플릿에서 그 버튼의 `{% on %}`을 지우거나 다른 핸들러에 걸어도 통과한다 — 브라우저에서는 아무
버튼도 `remove`를 부르지 않는데도. 아래 넷은 사용자가 하는 일을 한다: 지금 렌더(`view.render()`)에서 요소를 고르고 그
요소에 DOM 이벤트를 보낸다. 핸들러와 인자는 이벤트가 만나는 `wire-on-*` 바인딩에서 읽으므로, 바인딩이 없으면 실패한다.

```python
from wireview import mount


async def test_remove_button_deletes_the_row(item):
    view = await mount(XTodoItem, item=item)

    await view.click("input.toggle")                   # 체크 → {% on "change" "completed" %}
    html = await view.click("button.destroy")          # {% on "click" "delete" %}

    assert not await Item.objects.filter(pk=item.pk).aexists()
```

| 헬퍼 | 하는 일 |
|------|---------|
| `await view.click(selector, *, text=None, ctrl=False, alt=False, shift=False, meta=False)` | 클릭. checkbox·radio는 클릭 동안 체크가 바뀌고, 막히지 않으면 `input`·`change`가 뒤따른다. 폼의 제출 버튼이면 click 바인딩 뒤 그 폼에 `submit`이 간다. `<label>`이면 그 칸으로 클릭이 이어진다. `ctrl=True` 등은 그 키를 누른 채 클릭한다(`.ctrl` 같은 수정자) |
| `await view.change(selector, value, *, text=None)` | 칸에 `value`를 넣고 `change`. 텍스트 칸을 떠날 때·select·checkbox가 보내는 것 |
| `await view.input(selector, value, *, text=None)` | 같은 값 규칙으로 `input`. 키를 누를 때마다 보내는 것 |
| `await view.submit(selector="form", *, values=None, submitter=None, text=None)` | 폼 제출. `values`는 이름 → 사용자가 넣은 값, `submitter`는 누른 제출 버튼의 선택자 |
| `await view.keydown(selector, key, *, text=None, ctrl=False, alt=False, shift=False, meta=False, composing=False)` | 키를 누른다. `key`는 브라우저의 `event.key`(`"Enter"`, `"Escape"`, `" "`, `"a"`, `"ArrowUp"`). 폼의 텍스트 칸에서 Enter는 브라우저처럼 폼을 제출한다(아래) |
| `await view.keyup(selector, key, ...)` | 같은 인자로 키를 뗀다. `{% on "keyup.enter" "send" %}` 같은 채팅 입력창 |
| `await view.focus(selector, *, text=None)` | 포커스를 준다: `focus`(그 요소의 바인딩만) 뒤 `focusin`(조상으로 올라간다) |
| `await view.blur(selector, *, text=None)` | 포커스를 뺀다: `blur` 뒤 `focusout` |
| `await view.hook_event(selector, event, payload=None, *, hook=None, text=None)` | 그 요소의 훅이 `this.pushEvent(event, payload)`로 보내는 것을 보낸다. **돌려주는 것은 렌더가 아니라 응답**이다 — 아래 "훅 이벤트" |

모두 이벤트를 보낸 뒤의 `view.render()`를 돌려준다(Phoenix의 `render_click`처럼). 핸들러는 `call()`과 같은 길로
돈다 — `handle_event` 훅, 클라이언트가 부를 수 있는 메서드인지의 검사, `validate_call`, 그 뒤의 `sent_messages`와
내비게이션 단언까지.

**요소 고르기.** `selector`는 CSS의 실용적인 부분이다: 태그, `*`, `#id`, `.class`, `[attr]`, `[attr=값]`(`~=`·`^=`·
`$=`·`*=`·`|=`도), 자손(공백)·자식(`>`) 결합자, 쉼표. `data-testid`는 `[data-testid="save"]`로 고른다. 그 밖의 문법
(`:nth-child`, `+`, `~` …)은 `ValueError`다. `text=`는 맞은 것 중 텍스트(공백을 하나로 접은 것)에 그 문자열이 든 것만
남긴다. 정규식(`re.compile(...)`)이면 `search`로 본다. **정확히 하나가 남아야 한다** — 없거나 여럿이면 맞은 요소를
나열하며 실패한다. 더 좁은 선택자나 `text=`로 고른다.

**스트림 항목도 페이지에 있다.** 렌더는 `wire-stream` 컨테이너를 비워서 그리지만, 헬퍼가 읽는 페이지에는 마운트 뒤
보낸 스트림 연산(삽입·삭제·`limit`·`reset`·`update_only`)이 보낸 순서대로 클라이언트와 같은 규칙으로 들어가 있다 —
`click("#messages-12 button.delete")`가 그대로 된다. `clear_messages()`는 그 기록을 지우지 않는다(메시지를 비워도 화면의
항목은 남는다). 연산은 지금 렌더의 컨테이너에 다시 적용하므로, 렌더 사이에 컨테이너가 사라졌다 다시 생겨 브라우저가
항목을 잃은 경우는 따라 하지 않는다. 클라이언트처럼 항목 HTML의 첫 요소만 들어가고, 표의 행(`<tr>`)으로 시작하는 항목은
브라우저의 파서가 행을 버리듯 셀의 내용만 남는다.

**값.** `change`·`input`의 `value`와 `submit`의 `values`는 사용자가 칸에 남긴 것이다.

| 칸 | 값 |
|----|----|
| 텍스트 칸·textarea | `str` |
| select | 고를 option의 값 `str`. 없는 option·disabled option은 실패 |
| select multiple | 고를 값의 `list[str]` |
| checkbox | 체크는 `True`, 해제는 `False`. `values`에서 같은 이름의 묶음이면 체크할 값의 `list[str]` |
| radio | `True`(고르기). `values`에서는 고를 값 `str` |
| 같은 이름의 텍스트 칸 여럿 | `values`에서 페이지 순서대로 `list[str]` |

`value`를 주지 않으면 칸은 렌더의 값 그대로 이벤트만 간다. **헬퍼는 부를 때마다 렌더에서 다시 시작한다** — 앞 호출에서
넣은 값을 기억하지 않으므로 제출에 필요한 값은 `submit(values=...)`에 모두 준다(브라우저에서도 `change` 바인딩의 답이
폼을 렌더의 값으로 되돌린다. 값을 상태에 두지 않은 폼은 거기서 이미 잃는다, [입력 중인 값](./html-diff.md#입력-중인-값)).

**훅 이벤트.** 훅은 JavaScript라 헬퍼가 돌리지 않지만, 훅이 서버로 보내는 것은 보낼 수 있다(Phoenix의 `render_hook`).

```python
reply = await view.hook_event("#chart", "point_clicked", {"x": 3})   # <canvas id="chart" wire-hook="Chart">
assert reply == {"handled": True}            # handle_hook_event가 돌려준 것: 훅의 콜백·Promise가 받는 값
assert "3번 점" in view.render()
```

고른 요소에 `wire-hook`이 있어야 하고, `hook=`은 그 이름 중 하나여야 한다(`wire-hook="Chart Tooltip"`처럼 여럿이면
꼭 준다). 훅은 그 요소를 품은 컴포넌트의 것이라 그것이 마운트한 컴포넌트여야 한다(중첩 컴포넌트의 훅이면 그 컴포넌트를
마운트하라며 실패한다). `handle_hook_event(hook_id, event, payload)`가 세션에서처럼 돈다 — `hook_id`는
`"<컴포넌트 id>:<훅 이름>:test"`이고, `payload`와 응답은 JSON을 지난 값이다(튜플은 리스트가 된다). JSON으로 보낼 수 없는
응답은 소켓에서처럼 실패한다(`TypeError`). 훅이 이벤트를 보내는지, 언제 보내는지는 JavaScript의 일이라 브라우저 E2E로 본다.

**클라이언트와 같은 규칙.** 무엇이 가는지는 클라이언트가 정한 그대로다.

- 이벤트는 요소에서 조상으로 올라가며 그 이벤트의 바인딩을 모두 실행한다(위임). `.stop`이 있는 요소에서 멈춘다.
- 바인딩이 있는 요소가 속한 컴포넌트(가장 가까운 컴포넌트 요소, `myself=True`면 그 LiveComponent)에게 간다.
- 실린 폼 필드: 바인딩 요소의 폼이 그 컴포넌트 안에 있으면 그 폼, 아니면 컴포넌트 전체의 `name` 있는 요소.
  중첩 컴포넌트의 것은 빠진다. checkbox·radio는 체크된 것만(값이 없으면 `"on"`), select multiple은 목록,
  **disabled 칸과 이름 있는 버튼은 모두** 실린다(누른 버튼만이 아니다 — 클라이언트가 지금 그렇게 보낸다,
  GAP-048, [기능 갭](../FEATURE-GAP.md)). 핸들러는 같은 이름의 마지막 값을 받는다.
- 수정자: `.prevent`·`.stop`은 그대로 따른다. `.ctrl`·`.enter`·`.key_code.65`처럼 조건인 수정자는 헬퍼가 보낸
  이벤트(누른 키, `ctrl=` 같은 수정 키)로 판단한다. 성립하지 않으면 그 바인딩은 건너뛰고 조상으로 계속 간다 — 브라우저의
  평범한 클릭이 `.ctrl` 바인딩을 지나치는 것과 같다. `keyCode`는 브라우저가 채우는 값이다(글자는 대문자의 코드, Enter 13,
  Escape 27, 모르는 키는 0). `composing=True`는 IME가 조합 중인 키라 키 조건(`.enter`·`.key_code`)이 지나가지 않는다 —
  한글을 조합하며 누른 Enter는 보내지 않는다. `.debounce`는 시간이
  지난 것으로 보고 보낸다(브라우저처럼 같은 이벤트의 다른 바인딩 뒤에), `.throttle`은 매번 창이 지난 것으로 본다.
  여러 번의 입력이 하나로 합쳐지는 것은 시간의 일이라 브라우저 E2E로 본다.
- `focus`·`blur`는 올라가지 않는다: 그 요소의 바인딩만 돈다. 조상에서 받으려면 `focusin`·`focusout`에 건다.
  헬퍼는 포커스를 기억하지 않으므로 `focus(b)`가 앞의 칸에 `blur`를 보내지 않는다. 입력한 칸을 떠나는 것은 브라우저에서
  `change` 뒤 `blur`다 — `change()` 다음 `blur()`로 한다.
- `<label>`을 누르면(막히지 않았으면) 브라우저처럼 그 label의 칸에 클릭이 한 번 더 간다: `for`가 있으면 그 id의 칸,
  없으면 label 안의 첫 칸. checkbox·radio는 체크와 `input`·`change`, 제출 버튼은 제출이다. label 안의 칸 자신이나
  버튼·링크를 누르면 이어지지 않는다. `for`가 없는 id를 가리키거나 칸이 disabled면 아무 데도 가지 않는다.
- 키의 기본 동작은 하나만 따른다: 폼의 텍스트 칸에서 막히지 않은 Enter `keydown`은 폼의 첫 제출 버튼을 누른다(그 버튼이
  disabled면 아무것도 하지 않는다). 제출 버튼이 없으면 텍스트 칸이 하나뿐인 폼만 제출한다(HTML의 암묵적 제출).
  글자를 치면 칸에 들어가는 것, 버튼에서 Enter·Space가 클릭이 되는 것은 하지 않는다 — 값은 `input()`·`change()`로, 클릭은
  `click()`으로.

**실패가 계약이다.** 다음은 아무 일도 하지 않고 통과하는 대신 이유를 말하며 `AssertionError`로 실패한다.

- 선택자에 맞는 요소가 없다, 여럿이다.
- 이벤트가 만난 바인딩 중 서버 핸들러를 부르는 것이 없다. 수정자 조건 때문에 건너뛴 바인딩은 함께 나열한다.
- 포커스를 받을 수 없는 요소에 `focus`·`blur`를 했다(칸·버튼·`href` 있는 링크·`tabindex`·`contenteditable` 밖).
- 요소가 disabled다(자신, 감싼 `<button disabled>`, `<fieldset disabled>` — 그 첫 `<legend>` 안은 아니다). readonly·
  `type=hidden` 칸에 값을 넣으려 했다. 칸이 아닌 요소에 값을 주었다. 값의 타입이 칸과 맞지 않다.
- 바인딩이 다른 컴포넌트(중첩 `{% component %}`, LiveComponent)의 것이다. `mount()`는 마운트한 컴포넌트의 핸들러만
  돌리므로, 그 컴포넌트를 따로 `mount()`해 시험한다. `mount()`의 `render()`는 페이지의 첫 응답처럼 렌더마다 자식을
  새로 그리고 자식의 `joined()`·`update()`·`leaving()`을 돌리지 않는다 — 자식의 핸들러가 바꾼 상태는 다음 렌더에
  남지 않고, `send_to_parent()`는 부모에게 가지 않는다. 그래서 부모를 마운트한 채 자식의 요소를 누르는 것은 지원하지
  않는다(그러려면 `mount()`가 소켓의 세션처럼 저장소를 렌더 사이에 지니고 자식의 수명주기를 돌려야 한다, #252).
  자식과 부모가 함께 움직이는 것(`send_to_parent`, 부모의 다시 그리기가 자식에 주는 props)은 브라우저 E2E로 본다.
- `submit`이 막히지 않는다(`.prevent`가 없다). 브라우저는 그 폼을 `action`으로 보내 페이지를 떠난다. 폼 안의
  `<button>`(기본 type이 submit)에 `{% on "click" %}`만 걸고 `.prevent`를 잊은 경우도 여기서 잡힌다.
- `JS()` 체인이 `push`로 서버 이벤트를 보낸다. 헬퍼는 JavaScript를 돌리지 않으므로 무엇이 갈지 말할 수 없다.

**하지 않는 것.** JavaScript는 돌지 않는다: `JS()` 체인(push가 없으면 건너뛴다), 훅의 코드(`hook_event()`는 훅이 보낼
것을 대신 보낼 뿐이다), 로딩 클래스, `wire-disabled-with`는
없다. 브라우저의 폼 검증(`required`·`pattern`)도 하지 않는다. 중첩 컴포넌트에 보내기, 폼의 HTTP 제출, 업로드는 아직 없다(#252 2단계, GAP-057). 그런 것은 `call()`과 브라우저 E2E로 본다.

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
  테스트가 `render_diff()`로 기준선을 먼저 잡았다면, 그 뒤 `call()`한 이벤트의 렌더는 세션이 이벤트마다 하듯
  알림보다 먼저 따로 한다 — 이벤트가 바꾼 것은 그 `render`에 남고 알림의 `render`에 섞이지 않으며, 이벤트가
  `skip_render()`했으면 그 렌더는 아무것도 남기지 않고 알림의 렌더는 그대로 남는다.
- **다른 테스트로 새지 않는다.** 마운트한 컴포넌트는 그것을 만든 것이 끝나면 더 듣지 않는다. pytest에서는
  wireview와 함께 설치되는 플러그인(`wireview`, `pytest11` 진입점)이 끊는다 — 켤 것이 없다. 이벤트
  루프를 여러 테스트가 함께 쓰는 설정(pytest-asyncio의 `loop_scope="module"`·`"session"`)에서도 그렇다.
  - 테스트 본문이나 함수 범위 픽스처에서 마운트한 것은 그 테스트가 끝날 때 끊는다.
  - 테스트보다 넓은 픽스처(모듈·클래스·세션 범위, unittest의 `setUpClass` — pytest는 그것을 클래스 범위
    픽스처로 돌린다)가 마운트한 것은 그 픽스처가 정리될 때 끊는다. 그 픽스처를 함께 쓰는 테스트는 모두 받는다.
  - `setUpClass`에서 `async_to_sync(mount)(...)`로 만든 것은 자기 루프에서 마운트되었으므로, 테스트의 루프에서
    발행한 알림은 받지 않고 `Broadcast` 패치만 받는다. 알림까지 받게 하려면 테스트 안이나 테스트와 같은 루프의
    픽스처에서 마운트한다.

  `-p no:wireview`로 플러그인을 끄거나 pytest가 아닌 러너(`manage.py test`)에서는 이벤트 루프만이 가른다:
  마운트한 루프에서 발행한 알림만 받으므로 테스트마다 새 루프인 Django의 async `TestCase`·`IsolatedAsyncioTestCase`
  에서는 알림이 새지 않지만, 루프를 함께 쓰는 테스트 사이에서는 앞 테스트의 인스턴스가 사라질 때까지 듣는다.
  플러그인을 읽으면 패키지 `wireview`(그 `__init__`, Django는 읽지 않는다)가 import되고, pytest는 진입점으로 읽은
  플러그인의 최상위 패키지를 assertion rewriting 대상으로 표시한다 — `-p no:wireview`로 꺼도 그렇다. 둘 다
  피하려면 환경 변수 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`로 진입점 플러그인을 읽지 않게 한다(그러면 다른
  플러그인도 `-p`로 직접 켜야 한다).
- **모델 변경과 동기 `broadcast()`는 커밋 뒤에 발행한다(`on_commit`).** async ORM(`acreate()`·`asave()`)은
  워커 스레드의 연결에서 곧바로 커밋하므로 바로 발행되고, 그 행이 다음 테스트에 남지 않게 테스트는
  `@pytest.mark.django_db(transaction=True)`로 표시한다(아래 "주의사항"). 원자 블록 안에서 쓴 행 —
  `transaction.atomic()` 안이나, 그냥 `django_db` 테스트의 테스트 스레드에서 쓴 동기 ORM — 은 그 블록이 커밋될
  때에야 발행되고, 롤백되면 발행되지 않는다. `abroadcast()`·`self.broadcast()`는 곧바로 발행한다.
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

## Windows에서 브라우저 테스트

`daphne`를 `INSTALLED_APPS`에 둔 프로젝트(스타터 프로젝트가 그렇다)에서 pytest-playwright로 브라우저 테스트를 쓰면,
Windows에서는 모든 브라우저 테스트가 픽스처에서 `NotImplementedError`로 멈춘다. daphne는 import될 때 Twisted를 위해
selector 루프 정책을 거는데, Windows의 selector 루프는 서브프로세스를 띄우지 못한다. 그런데 Playwright는 드라이버를
서브프로세스로 띄운다. 프로젝트 루트의 `conftest.py`에서 daphne를 import해 정책 걸기를 끝낸 뒤 정책을 기본값으로 되돌린다. pytest-django는 보통
이 파일보다 먼저 Django를 설정해 daphne를 import하지만, 어느 쪽이 먼저든 마지막에 걸리는 정책은 이 파일의 것이다.

```python
# conftest.py
import asyncio
import sys
import warnings

if sys.platform == "win32":
    import daphne  # noqa: F401  -- imported before or after Django's, the policy set below is the last

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # Python 3.14 deprecates the policy API
        asyncio.set_event_loop_policy(None)  # the default: the proactor loop, which starts subprocesses
```

daphne 서버 자체는 따로 띄운 프로세스에서 돌고 그 프로세스는 정책을 스스로 걸므로, 테스트 프로세스의 정책을 되돌려도
서버에는 영향이 없다. `mount()`만 쓰는 테스트에는 필요 없다. 이 저장소의 루트 `conftest.py`가 같은 일을 한다(#217).

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
- **`mount()`는 CSRF 쿠키를 가진 페이지다.** 컴포넌트 템플릿의 `{% csrf_token %}`은 브라우저처럼
  `csrfmiddlewaretoken` 입력을 그린다(#206). 시크릿은 `mount()`마다 새것이다. `render()`는 응답 하나라 부를 때마다 새로
  마스킹하고, 라이브 렌더(`render_diff()`, 이벤트·알림 뒤)는 연결처럼 토큰 하나를 함께 쓴다. 스트림 항목은 삽입마다
  새로 마스킹한다. 값은 테스트마다 다르므로 HTML을 통째로 비교하는 단언은 그 입력을 빼고 비교한다
  ([폼의 CSRF 토큰](./dead-view.md#폼의-csrf-토큰)).
- 자식은 `mount()`가 만든 것이 아니라 렌더가 만든 것이다. 자식이 낸 브로드캐스트는
  `view.broadcasts`에 남지 않는다.

## 관련 기능

- [튜토리얼: 테스트 가이드](../tutorials/09-testing-components.md)
- [live_session](./live-session.md)
- [Streams 튜토리얼](../tutorials/06-streams-api.md)

## Phoenix LiveView와 다른 점

[LiveViewTest](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveViewTest.html)의 모든 헬퍼가 있는 것은 아니다.
`mount`·`call`·렌더·내비게이션 단언·스트림 검사, 선택자로 고른 요소의 바인딩을 읽는 `click`·`change`·`input`·`submit`
(Phoenix의 `element`·`render_click`·`render_change`·`render_submit`, submitter 포함)은 지원한다. 다음은 공개 헬퍼가 없다.

- key·focus·blur 이벤트, `render_hook`, 중첩 컴포넌트에 보내기(`find_live_child`), 폼의 HTTP 제출(`submit_form`)
- `file_input`·`preflight_upload`·`render_upload`로 업로드 프로토콜을 구동하는 헬퍼
- `render_async`처럼 관리 중인 비동기 작업이 끝날 때까지 기다리는 헬퍼
- `assert_push_event`·`assert_reply`·page title 전용 단언, hook 이벤트·자식 탐색·HTTP action 제출 헬퍼

`call`은 DOM에 핸들러가 연결됐는지 검증하지 않는다 — 그것은 `click` 등이 한다. 이들은 JavaScript를 돌리지 않으므로
`JS()` 체인·훅·debounce의 시간은 브라우저 E2E로 본다. `render`는 자식의 라이브 수명주기를 구동하지 않는다.
이 부분은 컨슈머 통합 테스트와 브라우저 E2E로 확인한다. `sent_messages` 목록은 공개지만 그 메시지 형식은 내부 프로토콜이다.
Phoenix 1.2의 중복 ID·컴포넌트·복구 폼 ID 누락별 `raise`/`warn`/`ignore` 설정도 제공하지 않는다.
