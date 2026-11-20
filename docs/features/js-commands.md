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
- 선택자 자리에 `JS.inner(선택자)`를 넣으면 **바인딩이 달린 요소 안**의 맞는 요소 전부, `JS.closest(선택자)`면 그 요소와
  조상 중 **가장 가까운** 하나다. 같은 템플릿을 목록의 항목마다 그려도 각 버튼이 자기 카드만 건드린다. 맞는 것이 없으면
  아무것도 하지 않는다.

```python
@property
def expand(self) -> JS:
    # 누른 버튼이 든 카드. 카드 안의 카드면 안쪽 것
    return JS().toggle_attr(JS.closest(".card"), "aria-expanded", ("true", "false"))
```

"바인딩이 달린 요소"는 이벤트가 난 요소가 아니라 `{% on %}`이 붙은 요소다 — 그룹에 단 바인딩은 안의 버튼을 눌러도
그룹이 기준이다. `push_js`로 서버가 보낸 체인은 컴포넌트의 루트, `exec`가 돌린 체인은 그 속성을 가진 요소가 기준이다.
`push(target=)`은 컴포넌트를 고르는 문서 전체의 선택자라 상대 대상을 받지 않는다(`TypeError`).

## 속성 토글

`toggle_attr(선택자, 속성)`은 속성이 있으면 지우고 없으면 빈 값으로 둔다(`hidden`·`open`·`disabled`처럼 있음이 뜻인 속성).
`value="x"`면 없을 때 `x`로 둔다. `value=("true", "false")`처럼 두 값을 주면 첫 값이면 둘째로, 그 밖(없음 포함)이면 첫 값으로
바꾼다 — `aria-expanded`·`aria-pressed`. 바꾼 속성은 `set_attr`처럼 다음 렌더 뒤에도 남는다(아래).

## 포커스

`focus(선택자)`는 맞는 첫 요소에, `focus_first(선택자)`는 그 안에서 **키보드로 갈 수 있는** 첫 요소에 포커스를 준다.
`focus_first`가 보는 것은 `href`가 있는 링크, 버튼, 입력 칸(hidden 제외), select, textarea, `summary`, `contenteditable`,
`tabindex`가 -1이 아닌 요소이고, 그중 disabled이거나 `inert` 안이거나 보이지 않는(`hidden`·`display: none`·
`visibility: hidden`, 숨은 조상 포함) 것은 건너뛴다. `input_only=True`면 input(hidden 제외)과 textarea만이다. 찾기는 다음
프레임에, 곧 같은 체인의 `show`와 그 렌더의 패치 뒤에 한다. 그 사이 boost 이동이 있었으면 `focus`·`focus_first`는 아무것도 하지
않는다(sticky 컴포넌트 안의 요소는 한다).

### 돌아갈 곳: push_focus·pop_focus

모달을 닫으면 연 버튼으로 포커스가 돌아가야 한다. `push_focus()`가 바인딩이 달린 요소(`push_focus(선택자)`면 맞는 첫 요소)를
기억하고, `pop_focus()`가 마지막에 기억한 것에 포커스를 주고 잊는다. 모달 안에서 또 모달을 열면 차례로 쌓이고 닫을 때마다
하나씩 돌아간다.

```python
@property
def open_settings(self) -> JS:
    return JS().push_focus().show("#settings").focus_first("#settings")

@property
def close_settings(self) -> JS:
    return JS().hide("#settings").pop_focus()
```

`pop_focus`는 지금 포커스를 받을 수 없는 요소를 건너뛰고(그것도 잊는다) 그 전에 기억한 것으로 간다:

- 페이지에서 사라진 요소, 숨겨지거나 disabled·`inert`가 된 요소.
- boost 이동 전의 페이지에서 기억한 요소. morph가 같은 id의 요소를 다음 페이지에 그대로 둬도 그것은 다음 페이지의 것이다.
  sticky 컴포넌트 안의 요소만 이동을 건너 남는다.

**돌아갈 요소에는 `id`를 둔다.** 렌더가 그 요소를 지우면, id가 없는 형제는 morph가 같은 요소 객체를 다른 버튼으로 고쳐 쓸 수 있다
— 그러면 그 객체는 페이지에 남아 있어 엉뚱한 버튼으로 돌아간다. 스택은 페이지(탭)마다 하나이고 가장 최근의 32개만 남는다.
선택자 없는 `push_focus()`는 바인딩이 달린 요소가 포커스를 받을 수 없으면(클릭을 받는 `div`, `push_js`로 보낸 체인의
기준인 컴포넌트 루트) 지금 포커스가 있는 요소를 기억한다.

### 가두기: wire-focus-wrap

`wire-focus-wrap` 속성을 단 요소 안에 포커스가 있으면 Tab이 그 안을 돈다 — 마지막 요소에서 Tab은 첫 요소로, 첫 요소에서
Shift-Tab은 마지막 요소로 간다. 요소 자신(`tabindex="-1"`)에 포커스가 있으면 Tab은 첫 요소, Shift-Tab은 마지막 요소다. 순서는
브라우저의 Tab 순서(양수 `tabindex`가 먼저)이고 대상은 `focus_first`와 같은 규칙이다. 라디오 그룹은 브라우저처럼 한 번 —
선택된 것, 없으면 첫 것 — 에 선다. 안에 양수 `tabindex`가 있으면 브라우저가 그 다음의 양수 `tabindex`로 밖에 나갈 수 있다 —
가두는 요소 안에서는 양수 `tabindex`를 쓰지 않는다.

```html
<div id="settings" role="dialog" aria-modal="true" wire-focus-wrap hidden>...</div>
```

포커스가 그 밖에 있을 때 끌어오지는 않는다 — 모달을 열 때 `focus_first`로 들여보낸다. 요소나 `document`에 단 keydown
리스너가 Tab을 `preventDefault()`하면 손대지 않는다(창에 단 리스너, `{% on "keydown.window" %}`는 그보다 늦다). IME가 조합 중인
Tab도 손대지 않는다. 옛 `focus_first`는 가두지 않았고 지금도 가두지 않는다: 가두기는 이 속성을 단 요소만이다.

## 체인 재사용: exec와 결합

`exec(속성, to=선택자)`는 그 요소의 속성에 든 체인을 **그 요소에서** 돌린다. 체인을 한 곳에 두고 여러 버튼이 부르게 할 때 쓴다.
속성에는 렌더한 `JS()` 그대로(`data-cancel="{{ this.cancel_js }}"`)나 바인딩의 속성(`wire-on-click`)을 둔다. `to`를 비우면
바인딩이 달린 요소 자신의 속성이다.

```python
@property
def cancel_js(self) -> JS:
    return JS().hide(JS.inner(".panel"))


@property
def cancel(self) -> JS:
    return JS().exec("data-cancel", to="#dialog")
```

```html
<div id="dialog" data-cancel="{{ this.cancel_js }}"><div class="panel">...</div></div>
<button {% on "click" this.cancel %}>취소</button>
<button {% on "keydown.window.esc" this.cancel %} hidden></button>
```

- 속성이 없으면 아무것도 하지 않는다. 체인이 아닌 값이면 콘솔에 오류를 남기고 건너뛴다.
- `exec`가 이미 돌고 있는 같은 요소·같은 속성으로 돌아오면 거기서 멈추고 콘솔에 오류를 남긴다. 체인의 나머지는 계속 돈다.
- 돌린 체인의 `push`는 그 속성을 가진 요소의 컴포넌트로 간다. 폼의 칸·커밋·로딩 표시는 사용자가 누른 요소의 것이다 — 폼의 submit이 `exec`로 돌린 `push`도 그 폼의 칸을 싣는다.
- 바인딩이 자기 속성을 `exec`하면(`exec("wire-on-click")`) 거기서 멈춘다.

`a.concat(b)` 또는 `a + b`는 두 체인을 이은 **새** 체인이다. `a`와 `b`는 바뀌지 않으므로 공통 부분을 속성 하나로 두고 여러 체인이
이어 쓸 수 있다.

```python
@property
def close(self) -> JS:
    return JS().hide("#modal")


@property
def save_and_close(self) -> JS:
    return self.close + JS().push("save")  # self.close는 여전히 hide 하나다
```

체인을 이벤트 인자나 `push`의 `value`에 넣으면 명령 목록으로 실린다 — `JS().push("save", value={"undo": JS().show("#m")})`의
`value`는 `{"undo": [{"cmd": "show", "to": "#m"}]}`이다. 템플릿에 `{{ this.chain }}`으로 렌더한 것과 같은 모양이라 그 값을 속성에
두면 `exec`가 읽는다.

### 페이지의 스크립트가 옛것일 때

`toggle_attr`·`exec`와 상대 대상은 프로토콜 15, `push_focus`·`pop_focus`는 16부터다. 서버가 보내는 체인(`push_js`, `Broadcast.js`)에 그것이 들어 있으면, 배포 전에 열어
옛 `wireview.js`를 가진 페이지에는 보내지 않는다 — `push_js`는 경고를 남기고, Broadcast는 그 연결이 쓰는 프레임에서 뺀다. 옛 스크립트는 모르는 명령을 건너뛰고
객체 대상에서 오류를 내기 때문이다. 템플릿 속성에 렌더된 체인은 이 확인을 거치지 않는다 — 그런 페이지가 새 템플릿의 렌더를 받으면
그 체인은 새로고침 전까지 돌지 않는다.

## 트랜지션

`show`·`hide`·`toggle`·`add_class`·`remove_class`·`toggle_class`의 `transition=`과 `transition()`은 세 모양을 받는다.

| 모양 | 하는 일 |
|------|---------|
| `"shake"` | 클래스를 붙이고 끝난다. 시간이 없으므로 빼지 않는다 — 그 클래스를 갖지 않는 다음 렌더가 지운다 |
| `("shake", 400)` | 400ms 동안 붙였다가 뺀다 |
| `("ease-out duration-300", "opacity-0", "opacity-100")` | 세 단계(Phoenix의 `{transition, start, end}`). 시간을 생략하면 200ms, 네 번째 항목으로 준다 |

세 단계는 이렇게 돈다: `start`를 붙여 한 번 그리고, 다음 프레임에 `transition`(움직임을 정하는 클래스)을 더하고, 그
다음 프레임에 `start`를 빼며 `end`를 붙인다. 시간이 지나면 `transition`과 `end`를 뺀다. 그래서 CSS는 `start`에서
`end`로 움직이고, 끝난 요소에는 아무 클래스도 남지 않는다 — 끝 상태는 `show`·`hide`가 만든 것(보임·숨김)이다.

```python
JS().show("#modal", transition=("ease-out duration-300", "opacity-0", "opacity-100"))
JS().hide("#modal", transition=("ease-in duration-200", "opacity-100", "opacity-0", 200))
JS().transition("#card", {"transition": "ease", "start": "scale-95", "end": "scale-100", "time": 150})
```

시간이 0이면 클래스를 하나도 붙이지 않고 명령의 변화만 바로 일어난다. 진행 중인 트랜지션의 클래스는 렌더를 건너
남고([렌더 뒤에도 남는다](#렌더-뒤에도-남는다)), 렌더가 요소를 지웠다 다시 그리면 새 요소에는 붙지 않는다. boost
이동 뒤의 새 페이지 요소에도 붙지 않는다.

### 기다리지 않는 체인

트랜지션이 있는 명령은 끝날 때까지 체인의 다음 명령을 기다리게 한다 — 1.7까지와 같은 기본값이다. `wait=False`를 주면
트랜지션은 그대로 돌고 다음 명령이 바로 시작한다.

```python
# 400ms 흔든 뒤에 저장 (기본)
JS().transition("#card", "shake", time=400).push("save")
# 흔들면서 바로 저장
JS().transition("#card", "shake", time=400, wait=False).push("save")
```

Phoenix는 언제나 `wait=False`처럼 돈다. 기다리는 의미에 기대는 앱 — `hide(transition=...)`가 끝나야 `push`가 가서
서버 렌더가 숨는 요소를 밀어내지 않던 것 — 이 있어 기본값은 바꾸지 않았다. 바꿀지는 2.0에서 정한다. 옮겨 가려면
트랜지션이 서버 왕복과 무관한 체인부터 `wait=False`를 적는다. `hide`·`remove_class`처럼 트랜지션 뒤에 바뀌는 명령은
`wait=False`여도 자기 변화는 트랜지션 뒤에 한다 — 기다리지 않는 것은 체인의 나머지다.

### show·hide의 이벤트

`show`·`hide`(와 `toggle`)는 대상 요소에서 `wireview:show-start`·`wireview:show-end`·`wireview:hide-start`·`wireview:hide-end`를
쏜다. 버블링하므로 `document`에서 받을 수 있다. `show-start`는 보이기 전, `show-end`는 트랜지션이 끝난 뒤, `hide-start`는
트랜지션 전, `hide-end`는 숨긴 뒤다. 트랜지션이 없으면 둘이 바로 이어진다.

```js
document.addEventListener("wireview:hide-end", (event) => event.target.querySelector("video")?.pause());
```

`toggle`은 전처럼 요소의 `hidden`을 보고 보일지 숨길지 정한다. CSS(`display: none` 클래스)로만 감춘 요소는 보이는 것으로
친다.

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
| `transition`, 명령의 `transition=` | 진행 중인 트랜지션의 클래스(세 단계면 그 단계의 것). 시간이 끝나면 빠진다. 시간 없는 한 묶음은 남기지 않는다 |

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
왕복을 트랜지션과 함께 시작하려면 그 명령에 `wait=False`를 주거나 `push`를 앞에 둔다([기다리지 않는 체인](#기다리지-않는-체인)). 선택자가 여러 요소와 맞으면 그 요소들의 트랜지션은 함께 돈다.
트랜지션을 기다린 명령(`hide(transition=...)`, `add_class(..., transition=...)`)은 끝날 때의 요소에 마저 적용된다 —
그 사이 스트림 갱신이 항목을 바꿨으면 새 항목에, boost 이동이 있었으면 새 페이지의 것이므로 아무것도 하지 않는다.
체인의 나머지 명령도 boost 이동 뒤에는 돌지 않는다. sticky 컴포넌트 안에서 시작한 체인만 이동을 건너 이어진다.

`{% on %}` 하나에 명령과 서버 호출을 함께 묶는 이유는 [Optimistic UI](./optimistic-ui.md#즉시-반응은-js-명령으로)에 있다.

## Phoenix LiveView와 다른 점

[Phoenix의 명령·옵션](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.JS.html) 중 다음은 지원하지 않는다.

| 항목 | 현재 wireview |
|------|---------------|
| `toggle_attribute` | `toggle_attr`. 두 값 토글에서 지금 값이 어느 쪽도 아니면 첫 값으로 간다 |
| `exec` | 같다. 다만 대상 `to`가 여럿이면 각 요소에서 차례로 돌고, 순환은 같은 요소·속성으로 돌아올 때 멈춘다. 전역 `wireview.exec`와 구분한다 |
| `push_focus`·`pop_focus` | 같다. 다만 돌아갈 수 없는 요소와 boost 이동 전 페이지의 요소(sticky 밖)는 건너뛴다 |
| `focus_wrap` | 컴포넌트가 아니라 속성 `wire-focus-wrap`이다. 경계용 요소를 그리지 않고 Tab 키를 가로챈다 |
| `ignore_attributes` | 특정 속성·와일드카드만 패치에서 제외하는 명령은 없다 |
| `to: {:inner, ...}`·`{:closest, ...}` | `JS.inner()`·`JS.closest()`. 기준은 바인딩이 달린 요소이고, `push(target=)`에는 쓸 수 없다 |
| `push`의 `loading`·`page_loading` | 임의 요소 로딩·잠금과 페이지 로딩 이벤트 옵션은 없다. `target`은 첫 일치 컴포넌트 루트를 고른다 |
| `patch` | 별도 JS 명령이 없다. 서버의 `push_to`·`replace_to`와 `navigate`를 구분한다 |
| `dispatch`의 `blocking`·`done`·`dispatcher` | 제공하지 않는다. click도 `MouseEvent`가 아닌 `CustomEvent`로 보낸다 |

트랜지션은 다음이 다르다.

- **체인이 기본으로 기다린다.** Phoenix는 명령을 차례로 시작하되 트랜지션 완료를 기다리지 않는다([실행부](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/assets/js/phoenix_live_view/js.js)).
  wireview는 `wait=False`를 준 명령만 그렇다.
- **클래스 하나짜리 문자열은 시간이 없으면 남는다.** Phoenix는 기본 200ms 뒤에 뺀다. 세 단계로 주면 wireview도 200ms다.
- **렌더를 막지 않는다(`blocking`이 없다).** Phoenix의 기본 `blocking: true`는 트랜지션이 도는 동안 그 요소의 패치를
  미룬다. wireview는 패치를 바로 적용하고 진행 중인 트랜지션의 클래스만 새 HTML에 다시 얹는다 — 서버가 보낸 내용이
  트랜지션 때문에 늦게 보이지 않는다. 트랜지션이 끝나기 전에 그 요소의 내용을 서버가 바꾸지 않아야 한다면 `push`를
  트랜지션 뒤에 둔다(기본 동작).

표시 판정도 다르다. wireview의 `toggle`은 요소의 `hidden`을 검사한다. CSS로만 감춘 요소를 Phoenix의 가시성 판정과
같다고 가정하지 않는다. `focus_first`는 Phoenix처럼 요소마다 포커스를 줘 보고 확인하지 않고 위의 규칙으로 고른다 —
`<details>`가 닫혀 있어 보이지 않는 칸처럼 규칙 밖의 경우는 다를 수 있다.

Phoenix 1.2는 JS 명령을 이벤트 payload에 JSON으로 넣을 수 있다. wireview도 이벤트 인자와 `push`의 `value`에 든 `JS()`를
명령 목록으로 싣는다([체인 재사용](#체인-재사용-exec와-결합)). Phoenix의 명령 배열과 wireview의 명령 객체 배열은 서로 다른 형식이다.
`concat` 대신 `+`도 쓴다.

`show`·`toggle`의 `display` 지정은 지원한다. 생략하면 inline display를 비워 CSS에 맡기므로
Phoenix의 block/table-row/table-cell 기본 선택과 다르다. CSS가 계속 숨기면 show만으로 보이지 않을 수 있다.
