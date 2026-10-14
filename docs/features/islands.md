# 섬 (island)

컴포넌트 안에 페이지의 JavaScript가 그리는 영역을 두고, 서버의 상태를 props로 넘긴다(#221). React 트리, 지도, 차트처럼
DOM을 스스로 관리하는 것이 섬이다. 서버는 섬 안을 건드리지 않고, props가 바뀌면 섬에게 새 props를 건넨다.

[훅](./hooks.md)과 `wire-update="ignore"`로도 같은 일을 할 수 있다. 섬은 그 조합을 정해 둔 것이다. 수명(언제 마운트하고,
언제 갱신하고, 언제 떼는가)과 props를 넘기는 길이 정해져 있어 따로 짤 것이 없다.

## 템플릿

```django
{% load wireview %}

<div {% tag_header %}>
  <h2>{{ this.title }}</h2>
  {% island "Map" props=this.map_props class="h-96" %}
</div>
```

```python
from wireview import Component


class Explorer(Component):
    class Meta:
        template_name = "explorer.html"

    title: str = "지도"
    zoom: int = 10

    @property
    def map_props(self) -> dict:
        return {"zoom": self.zoom, "center": [55.27, 25.2]}

    async def zoom_in(self):
        self.zoom += 1
```

태그는 이렇게 그린다.

```html
<div wire-hook="wireview-island" wire-island="Map" class="h-96"
     wire-island-props="{&quot;zoom&quot;: 10, &quot;center&quot;: [55.27, 25.2]}"><div wire-update="ignore"></div></div>
```

- 첫 인자는 섬의 이름이다. 페이지가 `wireview.islands`에 같은 이름으로 등록한 것이 그린다.
- `props`는 Django의 JSON 인코더로 직렬화한다(날짜·`Decimal`·UUID 포함). 빼거나 `None`이면 `{}`이다. JSON이 될 수 없는 값이면
  `TypeError`, NaN·무한대면 `ValueError`다 — 브라우저의 `JSON.parse`가 받지 못하는 값을 서버에서 막는다. 값은 속성으로 이스케이프되므로 `<script>`가 들어 있어도 안전하다. 인라인 스크립트가 아니라 속성이므로
  [CSP](./csp.md)와도 맞는다.
- 다른 키워드 인자는 바깥 요소의 속성이다. `data_role="map"`은 `data-role="map"`이 된다.
- **props는 렌더 diff의 동적 부분 하나다.** props가 바뀐 렌더는 그 JSON만 보내고, 섬의 마크업은 다시 보내지 않는다.
  이름이나 다른 속성이 렌더마다 바뀌면 그렇지 않다(정적 부분이 바뀌어 컴포넌트 전체를 보낸다).
- `{% for %}` 안에서 여럿을 그리거나 섬 앞뒤로 요소가 나타났다 사라지면 `id`를 준다(`id=item.dom_id`). morph는 id가 없는 요소를
  자리로 짝짓는데, 짝이 바뀌면 다른 항목의 섬이 그 자리의 props를 받는다.

## 페이지의 JavaScript

```javascript
window.wireview.islands.Map = {
  mount(el, props, ctx) {
    // el: wire-update="ignore" 요소. 어떤 렌더도 이 안을 건드리지 않는다
    const map = new MapLibrary(el, props);
    return {
      update(props) {
        map.setZoom(props.zoom);
      },
      unmount() {
        map.remove();
      },
    };
  },
};
```

React라면 이렇다.

```javascript
import { createRoot } from "react-dom/client";
import { MapView } from "./MapView";

window.wireview.islands.Map = {
  mount(el, props, ctx) {
    const root = createRoot(el);
    const render = (next) => root.render(<MapView {...next} ctx={ctx} />);
    render(props);
    return { update: render, unmount: () => root.unmount() };
  },
};
```

- `mount(el, props, ctx)`는 섬 요소마다 한 번 불린다. 돌려준 객체의 `update(props)`·`unmount()`가 그 섬의 것이다. 둘 다 없어도 된다.
- **이름을 객체에 더한다.** `window.wireview.islands = {...}`로 객체를 바꾸면 등록이 들리지 않는다.
- 등록은 페이지가 그린 뒤에 와도 된다. 정의를 기다리던 섬은 등록될 때 그 요소의 지금 props로 마운트된다. 번들을
  `{% wireview_header %}` 뒤에 `defer`나 `type="module"`로 실으면 `window.wireview`가 이미 있다.
- 섬의 코드가 던진 예외는 콘솔에 보고되고 페이지는 계속 돈다.
- 섬이 떠나면(`unmount`) 그 섬이 `ctx.on`·`ctx.onStatus`로 단 것도 함께 떼어진다.
- `mount`가 받는 요소는 언제나 비어 있다. 마운트 전에 wireview가 그 요소의 자식과 `wire-update` 밖의 속성을 지운다 — 뒤로 가기가
  복원한 페이지 사본에는 옛 섬이 그린 DOM이 남아 있다.

### ctx

| 멤버 | 뜻 |
|------|-----|
| `ctx.el` | 섬의 바깥 요소(`wire-island`가 붙은 것) |
| `ctx.root` | 섬이 그리는 요소. `mount`의 `el`과 같다 |
| `ctx.props` | 마지막으로 건넨 props |
| `ctx.push(event, payload)` | 컴포넌트의 `handle_hook_event()`로 보낸다. 응답의 Promise를 돌려준다 — 그 이벤트의 렌더가 화면에 있고 새 props를 건넨 뒤 풀린다. 응답이 오지 않으면 `WireviewPushError`로 거절된다([훅의 Promise](./hooks.md#promise로-받기)) |
| `ctx.on(event, callback)` | 컴포넌트의 `push_event(event, ...)`를 듣는다. 떼는 함수를 돌려준다 |
| `ctx.onStatus(callback)` | 연결 상태를 듣는다: `"disconnected"`, `"reconnected"`. 떼는 함수를 돌려준다 |
| `ctx.status` | `"connected"` 또는 `"disconnected"` |

```python
class Explorer(Component):
    ...

    async def handle_hook_event(self, hook_id, event, payload):
        if event == "moved":
            self.zoom = payload["zoom"]
            await self.push_event("saved", {"zoom": self.zoom})
            return {"ok": True}
```

핸들러가 `push_event`로 보낸 이벤트는 그 응답 **뒤에** 온다. 세션은 이벤트를 다 처리한 다음에 그것을 보낸다.

## 수명

섬은 그 요소만큼 산다. 내장 훅 `wireview-island`가 수명을 잇는다.

| 언제 | 섬이 듣는 것 |
|------|-------------|
| 컴포넌트가 join했다 | `mount(el, props, ctx)` |
| 렌더가 props를 바꿨다 | `update(props)` 한 번. props가 그대로인 렌더에는 부르지 않는다 |
| 핸들러가 예외를 던져 컴포넌트가 다시 join했다 | 아무것도. 섬은 그대로이고, 뒤따르는 렌더가 바뀐 props를 건넨다 |
| 연결이 끊겼다 / 다시 붙었다 | `onStatus`의 `"disconnected"` / `"reconnected"`. 섬은 그대로이고, 다시 join한 렌더가 바뀐 props를 건넨다 |
| 개발 서버에서 템플릿이 바뀌어 다시 join했다(#180) | 아무것도. 위와 같다 |
| boost 이동이 요소를 가져갔다 | `unmount()` |
| boost 이동을 건넜다(sticky 컴포넌트) | 아무것도. 다음 페이지에서도 같은 섬이다 |
| boost 이동의 다음 페이지가 같은 id의 컴포넌트를 그린다(sticky 아님) | 아무것도. 요소와 훅이 남아 같은 섬이고, 다시 join한 렌더가 새 페이지의 props를 `update`로 건넨다 |
| morph가 같은 `id`의 섬 요소를 다른 컴포넌트에 넘겼다(앞 컴포넌트가 떠났든 남았든) | 앞 컴포넌트의 섬은 `unmount()`, 새 컴포넌트가 그 요소에 자기 섬을 `mount`한다 |
| 뒤로·앞으로 가기가 떠났던 페이지의 사본을 그렸다 | 새 `mount`. 사본에 남은 옛 섬의 DOM은 지우고 빈 요소에 그린다 |
| 렌더가 요소를 지웠다 | `unmount()` |

섬 안의 DOM은 어떤 렌더도 건드리지 않는다. 바깥 요소의 속성(`class`, props 등)은 렌더가 템플릿대로 맞추고, 그 자식은
morph하지 않는다 — 라이브러리가 그리는 요소에 `id`를 붙여도 렌더도 boost 이동도 그 요소를 바꾸지 않는다. 안쪽 요소의
`wire-update="ignore"`는 그 요소가 페이지의 것임을 표시한다.

## Phoenix LiveView와 다른 점

Phoenix에는 섬 태그가 없다. `phx-hook`과 `phx-update="ignore"`를 함께 쓰고, 훅의 `updated()`에서 `data-*` 속성을 읽어
라이브러리에 넘기는 것이 같은 일이다. wireview의 `wire-update="ignore"`는 요소의 속성까지 지키므로(`data-*`도),
그 요소에 props를 실으면 갱신되지 않는다 — 섬은 props를 바깥 요소에 두고 안쪽 요소만 지킨다.
