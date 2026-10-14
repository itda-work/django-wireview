# JS 명령

`JS()`는 서버 왕복 없이 브라우저에서 바로 도는 DOM 명령의 체인이다 — 보이기·숨기기, 클래스, 속성, 포커스,
트랜지션, 그리고 서버로 이벤트를 보내는 `push`. Phoenix LiveView의 `Phoenix.LiveView.JS`에 해당한다.
명령 목록과 인자는 [README의 사용 가능한 명령](../../README.md#사용-가능한-명령)이 정본이다.

```python
from wireview import JS, Component


class Panel(Component):
    @property
    def open_js(self) -> JS:
        # 템플릿은 인자를 받는 호출을 쓸 수 없으므로 체인은 컴포넌트의 속성이 만든다
        return JS().show("#panel").add_class("#opener", "active").push("opened")

    async def opened(self):
        ...

    async def close(self):
        # 서버에서도 보낼 수 있다
        await self.push_js(JS().hide("#panel").remove_class("#opener", "active"))
```

```html
<button id="opener" {% on "click" this.open_js %}>열기</button>
<div id="panel" hidden>...</div>
```

## 대상

- 선택자를 비우면 명령이 붙은 요소 자신이다. `push_js`로 보낸 명령은 그 컴포넌트의 루트 요소다.
- 선택자가 있으면 문서에서 **그것과 맞는 모든 요소**다. `JS().add_class("#list .item", "read")`는 항목 전부에
  붙는다.
- `focus`·`focus_first`만 맞는 요소 중 첫 요소 하나다 — 포커스는 한 곳에만 있을 수 있다.

## 렌더 뒤에도 남는다

명령이 요소에 바꾼 것은 그 요소를 다시 그리는 렌더 뒤에도 남는다(LiveView의 sticky와 같다, #219). 렌더는 요소를
템플릿의 HTML로 morph하는데, 그 HTML은 브라우저에서 일어난 일을 모른다. 그래서 wireview는 명령이 바꾼 것을 요소마다
기록해 두고, 그 요소의 morph에서 들어오는 HTML에 다시 얹는다. 서버가 같은 요소에 바꾼 다른 것(텍스트, 다른 속성)은
평소처럼 들어온다.

| 명령 | 렌더 뒤에 남는 것 |
|------|-------------------|
| `add_class`·`remove_class`·`toggle_class` | 명령이 실제로 더하거나 뺀 클래스 |
| `set_attr`·`remove_attr` | 그 속성의 마지막 값, 또는 없음 |
| `show`·`hide`·`toggle` | 마지막으로 보이거나 숨긴 상태 |
| `transition`, 명령의 `transition=` | 양수 시간을 지정한 트랜지션의 클래스. 시간이 끝나면 빠진다. 시간을 생략한 경우는 아래 차이 참고 |

- **클래스는 명령이 만든 차이만 기록한다.** 이미 있던 클래스를 `add_class`하면 아무것도 기록하지 않는다. 그래서 뒤의
  렌더가 그 클래스를 빼면 빠진다. 앞서 명령으로 뺀 클래스를 다시 더하면 그 기록이 지워지고, 요소는 다시 서버의 HTML을
  따른다.
- `set_attr("class", ...)`는 그 앞의 클래스 명령을, `set_attr("hidden" | "style", ...)`은 그 앞의 보이기·숨기기를
  대신한다.
- `set_value`·`focus`·`dispatch`·`navigate`·`push`는 남길 상태가 없다. 입력값이 렌더를 건너는 규칙은
  [입력값](../design/input-values.md)의 것이다.

### 남지 않는 경우

- **렌더가 요소를 새것으로 바꾸면** 기록도 없다. `{% if %}`가 요소를 지웠다가 다시 그리거나, 짝지을 단서가 없는 목록에서
  자리가 바뀐 경우다. 기록은 선택자가 아니라 요소에 붙는다.
- **boost 이동**의 morph는 이전 페이지의 기록을 지운다. 새 페이지의 요소는 같은 id로 짝지어져도 새 페이지의 것이다.
  [sticky 컴포넌트](./boost.md)는 이동을 건너 요소째 남으므로 그 안의 기록도 남는다.
- 스트림 항목은 예외다: 같은 dom id로 다시 삽입해 항목을 갱신하면 새 요소가 옛 요소의 자리를 잇고, 옛 요소와 그 안의
  id 있는 요소의 기록을 물려받는다.

### 서버의 상태와 겹칠 때

명령이 남긴 것은 서버의 다음 렌더보다 우선한다. 서버가 같은 요소의 같은 클래스·속성·표시를 필드로 그리고 있다면,
명령을 보낸 뒤로는 그 필드가 바뀌어도 그 부분은 화면에 반영되지 않는다. 둘 중 하나로 정한다:

- 서버가 정한다 — 템플릿이 필드로 그리고, 명령은 쓰지 않는다. 즉시 반응이 필요하면 [로딩 클래스](./optimistic-ui.md)를
  쓴다.
- 브라우저가 정한다 — 명령으로만 바꾸고, 서버가 바꿔야 할 때는 핸들러에서 `self.push_js(...)`로 되돌리는 명령을 보낸다.

**"응답이 올 때까지 로딩"은 명령으로 하지 않는다.** `JS().add_class("#btn", "loading").push("save")`의 `loading`은
응답 뒤에도 남는다. 서버 왕복 동안만의 표시는 `wireview-click-loading` 같은 [로딩 클래스](./optimistic-ui.md#로딩-클래스)와
`wire-disabled-with`가 맡는다.

## 체인의 순서

명령은 적은 순서로 돈다. 시간을 준 트랜지션(`("fade-out", 300)`)은 끝날 때까지 다음 명령을 기다리게 한다 — 서버
왕복을 트랜지션과 함께 시작하려면 `push`를 앞에 둔다. 선택자가 여러 요소와 맞으면 그 요소들의 트랜지션은 함께 돈다.
트랜지션을 기다린 명령(`hide(transition=...)`, `add_class(..., transition=...)`)은 끝날 때의 요소에 마저 적용된다 —
그 사이 스트림 갱신이 항목을 바꿨으면 새 항목에, boost 이동이 있었으면 새 페이지의 것이므로 아무것도 하지 않는다.
체인의 나머지 명령도 boost 이동 뒤에는 돌지 않는다. sticky 컴포넌트 안에서 시작한 체인만 이동을 건너 이어진다.

`{% on %}` 하나에 명령과 서버 호출을 함께 묶는 이유는 [Optimistic UI](./optimistic-ui.md#즉시-반응은-js-명령으로)에 있다.

## Phoenix LiveView와 다른 점

[Phoenix의 명령·옵션](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.JS.html) 중 다음은 지원하지 않는다.

| 항목 | 현재 wireview |
|------|---------------|
| `toggle_attribute` | `set_attr`·`remove_attr`만 있다. 속성 유무 또는 두 값의 토글 명령은 없다 |
| `exec`·`concat` | 다른 속성에 든 명령 실행·기존 체인 결합 빌더는 없다. 전역 `wireview.exec`와 구분한다 |
| `push_focus`·`pop_focus` | 포커스 저장·복원 스택은 없다 |
| `ignore_attributes` | 특정 속성·와일드카드만 패치에서 제외하는 명령은 없다 |
| `to: {:inner, ...}`·`{:closest, ...}` | 선택자는 문서 전체의 CSS 문자열이며 상대 범위 선택은 없다 |
| `push`의 `loading`·`page_loading` | 임의 요소 로딩·잠금과 페이지 로딩 이벤트 옵션은 없다. `target`은 첫 일치 컴포넌트 루트를 고른다 |
| `patch` | 별도 JS 명령이 없다. 서버의 `push_to`·`replace_to`와 `navigate`를 구분한다 |
| `dispatch`의 `blocking`·`done`·`dispatcher` | 제공하지 않는다. click도 `MouseEvent`가 아닌 `CustomEvent`로 보낸다 |

트랜지션은 클래스 문자열과 시간만 받는다. Phoenix의 세 클래스 묶음(transition/start/end), `blocking` 옵션,
show/hide 시작·종료 이벤트는 없다. 시간을 생략하면 기본 200ms 후 제거하는 것이 아니라 클래스가 남으며 다음 렌더가
덮어쓸 수 있다. 시간 있는 트랜지션은 **다음 `push`까지 지연**시킨다. Phoenix는 명령을 순서대로 시작하되 트랜지션
완료를 기다려 다음 push를 시작하지 않는다([실행부](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/assets/js/phoenix_live_view/js.js)).

표시 판정도 다르다. wireview의 `toggle`은 요소의 `hidden`을 검사한다. CSS로만 감춘 요소를 Phoenix의 가시성 판정과
같다고 가정하지 않는다. `focus_first`의 탐색 대상도 input·textarea·select·button·tabindex 요소로 한정되어 있어,
`href`만 가진 링크나 숨은 조상을 가진 요소까지 포함하는 일반적인 접근성 포커스 탐색과 같지 않다.

Phoenix 1.2는 JS 명령을 이벤트 payload에 JSON으로 넣을 수 있다. wireview도 `to_json`과 서버의
`js` 명령 전송을 제공한다. 다만 JS 객체를 임의 payload에 넣었을 때 자동 인코딩하는 규약은 없으며,
Phoenix의 명령 배열과 wireview의 명령 객체 배열은 서로 다른 형식이다.

`show`·`toggle`의 `display` 지정은 지원한다. 생략하면 inline display를 비워 CSS에 맡기므로
Phoenix의 block/table-row/table-cell 기본 선택과 다르다. CSS가 계속 숨기면 show만으로 보이지 않을 수 있다.
