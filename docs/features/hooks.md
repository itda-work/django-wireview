# JavaScript 훅

서드파티 JS 라이브러리(Chart.js, Mapbox, CodeMirror 등)를 컴포넌트에 붙이는 장치다. Phoenix
LiveView의 훅과 같은 모양이다.

## 빠른 시작

### 1. 훅을 정의한다

```javascript
window.wireview.hooks.ChartHook = {
  mounted() {
    // 엘리먼트가 페이지에 들어왔을 때
    const config = JSON.parse(this.el.dataset.config);
    this.chart = new Chart(this.el, config);
  },

  updated() {
    // DOM morph가 끝난 뒤
    this.chart.update();
  },

  destroyed() {
    // 엘리먼트가 사라질 때
    this.chart.destroy();
  }
};
```

### 2. 템플릿에서 쓴다

```html
<div wire-hook="ChartHook" data-config='{"type": "line", "data": {...}}'>
</div>
```

## 훅 파일을 언제 싣나

**`{% wireview_header %}` 뒤에 `defer`로 싣는다.**

```html
{% wireview_header %}
<script defer src="{% static 'myapp/hooks.js' %}"></script>
```

`defer` 스크립트는 문서 순서대로 실행되므로 그때 `window.wireview`는 이미 있다. 그리고
wireview는 **`defer` 스크립트가 전부 실행된 뒤에야 컴포넌트를 join한다** — 훅 파일이 조금
늦게 도착해도 첫 렌더 때 훅이 자리에 있다는 뜻이다.

이 보장이 필요한 이유는 실패가 조용하기 때문이다. 등록되지 않은 훅은 오류를 내지 않고
콘솔 경고 한 줄(`Hook "X" not registered`)만 남기며, 컴포넌트는 정상으로 보인다.

인라인 `<script>`는 `defer`가 안 되므로 **번들보다 먼저 실행된다.** 훅 등록을 인라인으로
쓰면 `window.wireview`가 없어서 실패한다. 파일로 빼서 `defer`로 싣는다.

동작하는 예제는 [`examples/hooks/`](../../examples/hooks/)에 있다. 훅 파일을 프로젝트가
직접 싣지 않아도 되게 만드는 일은 [GAP-032](../design/colocated-hooks.md)다.

## 훅 수명주기

| 콜백 | 언제 | 쓰임새 |
|------|------|--------|
| `mounted()` | 엘리먼트가 들어오고 첫 렌더가 끝난 뒤 | 라이브러리 초기화 |
| `beforeUpdate()` | DOM morph 직전 (동기) | 스크롤 위치·선택 영역 저장 |
| `updated()` | DOM morph가 끝난 뒤 | 상태 복원, 라이브러리 갱신 |
| `destroyed()` | 엘리먼트가 DOM에서 빠질 때 | 자원 정리 |
| `disconnected()` | WebSocket이 끊겼을 때 | 오프라인 표시 |
| `reconnected()` | WebSocket이 다시 붙었을 때 | 데이터 새로고침 |

## 훅 컨텍스트

콜백 안의 `this`가 주는 것.

| 속성 | 타입 | 뜻 |
|------|------|-----|
| `this.el` | `HTMLElement` | `wire-hook` 속성이 붙은 DOM 엘리먼트 |

| 메서드 | 뜻 |
|--------|-----|
| `this.pushEvent(event, payload, callback)` | 서버로 이벤트를 보낸다 |
| `this.handleEvent(event, callback)` | 서버가 보내는 이벤트를 받는다 |

## 서버와 주고받기

### 서버로 보내기 (pushEvent)

```javascript
window.wireview.hooks.InfiniteScroll = {
  mounted() {
    this.observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting) {
        this.loadMore();
      }
    });
    this.observer.observe(this.el.querySelector('.sentinel'));
  },

  loadMore() {
    // 콜백과 함께 서버로 보낸다
    this.pushEvent("load_more", { page: this.page }, (response) => {
      console.log("Server response:", response);
      if (response.hasMore) {
        this.page++;
      } else {
        this.observer.disconnect();
      }
    });
    this.page = (this.page || 1) + 1;
  },

  destroyed() {
    this.observer.disconnect();
  }
};
```

### 서버에서 받기 (handleEvent)

```javascript
window.wireview.hooks.Notification = {
  mounted() {
    // 서버가 push하는 이벤트의 핸들러를 등록한다
    this.handleEvent("show_toast", ({ message, type }) => {
      this.showToast(message, type);
    });

    this.handleEvent("highlight", ({ color }) => {
      this.el.style.backgroundColor = color;
      setTimeout(() => {
        this.el.style.backgroundColor = '';
      }, 1000);
    });
  },

  showToast(message, type) {
    // 토스트 구현
  }
};
```

### 서버 쪽 핸들러

```python
from wireview import Component


class Dashboard(Component):
    _template_name = "dashboard.html"

    items: list = []
    page: int = 1

    async def handle_hook_event(self, hook_id: str, event: str, payload: dict):
        """Handle events from JavaScript hooks.

        Args:
            hook_id: Unique identifier of the hook instance
            event: Event name sent by the hook
            payload: Event data from the hook

        Returns:
            Response data sent to the hook's callback (or None)
        """
        if event == "load_more":
            page = payload.get("page", 1)
            new_items = await self.fetch_items(page)
            self.items.extend(new_items)

            return {
                "hasMore": len(new_items) == 20,
                "count": len(new_items)
            }

        return None

    async def notify_user(self, message: str):
        """Push an event to every hook in this component."""
        await self.push_event("show_toast", {
            "message": message,
            "type": "success"
        })

    async def highlight_item(self, hook_id: str):
        """Push an event to one specific hook."""
        await self.push_event(
            "highlight",
            {"color": "yellow"},
            hook_id=hook_id
        )
```

## 한 엘리먼트에 훅 여러 개

이름을 공백으로 나열한다.

```html
<div wire-hook="Sortable Draggable Tooltip">
  <!-- 내용 -->
</div>
```

각 훅은 자기 인스턴스와 수명주기를 갖는다.

## 예제

### Chart.js

```javascript
window.wireview.hooks.Chart = {
  mounted() {
    const config = JSON.parse(this.el.dataset.config);
    this.chart = new Chart(this.el, config);

    // 서버가 보내는 데이터 갱신을 받는다
    this.handleEvent("update_data", ({ datasets }) => {
      this.chart.data.datasets = datasets;
      this.chart.update();
    });
  },

  beforeUpdate() {
    // morph 전에 차트 상태를 저장한다
    this.chartState = {
      animation: this.chart.options.animation
    };
  },

  updated() {
    // morph 후에 애니메이션을 복원한다
    this.chart.options.animation = this.chartState.animation;
  },

  destroyed() {
    this.chart.destroy();
  }
};
```

### CodeMirror

```javascript
window.wireview.hooks.CodeEditor = {
  mounted() {
    this.editor = CodeMirror(this.el, {
      mode: this.el.dataset.mode || "javascript",
      lineNumbers: true
    });

    // 변경을 디바운스해서 서버로 보낸다
    let timeout;
    this.editor.on("change", () => {
      clearTimeout(timeout);
      timeout = setTimeout(() => {
        this.pushEvent("content_changed", {
          content: this.editor.getValue()
        });
      }, 300);
    });

    // 서버가 보내는 내용을 받는다
    this.handleEvent("set_content", ({ content }) => {
      this.editor.setValue(content);
    });
  },

  destroyed() {
    this.editor.toTextArea();
  }
};
```

### 스크롤 위치 보존

```javascript
window.wireview.hooks.PreserveScroll = {
  beforeUpdate() {
    // morph 전에 저장
    this.scrollTop = this.el.scrollTop;
  },

  updated() {
    // morph 후에 복원
    this.el.scrollTop = this.scrollTop;
  }
};
```

### 오프라인 표시

```javascript
window.wireview.hooks.ConnectionStatus = {
  mounted() {
    this.updateStatus(true);
  },

  disconnected() {
    this.updateStatus(false);
  },

  reconnected() {
    this.updateStatus(true);
  },

  updateStatus(connected) {
    this.el.classList.toggle('connected', connected);
    this.el.classList.toggle('disconnected', !connected);
    this.el.textContent = connected ? '연결됨' : '다시 연결하는 중...';
  }
};
```

## 권장 사항

1. **`destroyed()`에서 반드시 정리한다.** observer 해제, 라이브러리 인스턴스 파괴, 이벤트 리스너 제거.
2. **상태 보존은 `beforeUpdate()`에서.** 스크롤 위치·포커스·선택 영역을 morph 전에 저장한다.
3. **초기화는 `mounted()`에서.** 엘리먼트가 페이지에 올라오기 전에 초기화하지 않는다.
4. **재연결을 다룬다.** 끊긴 동안 바뀌었을 데이터를 `reconnected()`에서 새로 읽는다.
5. **폴링 대신 `handleEvent()`를 쓴다.** 데이터가 바뀌면 서버가 밀어 준다.
6. **훅 하나는 한 가지만 한다.** 필요하면 한 엘리먼트에 여러 개를 붙인다.

## API

### Component.handle_hook_event()

```python
async def handle_hook_event(
    self,
    hook_id: str,
    event: str,
    payload: dict[str, Any],
) -> Any
```

클라이언트 훅이 `pushEvent()`로 보낸 이벤트를 받는다.

- `hook_id` — 훅 인스턴스의 고유 식별자
- `event` — 훅이 보낸 이벤트 이름
- `payload` — 훅이 보낸 데이터
- **반환** — 훅의 콜백으로 돌아갈 응답 데이터 (없으면 `None`)

### Component.push_event()

```python
async def push_event(
    self,
    event: str,
    payload: dict[str, Any] | None = None,
    hook_id: str | None = None,
) -> None
```

클라이언트 훅으로 이벤트를 보낸다.

- `event` — 보낼 이벤트 이름
- `payload` — 데이터 (기본: 빈 dict)
- `hook_id` — 특정 훅 인스턴스만 (`None`이면 전체)

## DOM morph 콜백

### wireview.dom.onBeforeElUpdated()

갱신 중 엘리먼트가 morph되기 직전에 실행되는 콜백을 등록한다. 서버 렌더 결과가 덮어써 버릴
클라이언트 쪽 속성이나 상태를 지키는 데 쓴다.

```javascript
wireview.dom.onBeforeElUpdated((fromEl, toEl) => {
  // fromEl: 지금 DOM에 있는 엘리먼트
  // toEl: 그것을 대체할 새 엘리먼트
});
```

**JS가 붙인 속성 지키기**

```javascript
// data-js-* 속성을 보존한다
wireview.dom.onBeforeElUpdated((fromEl, toEl) => {
  for (const attr of fromEl.attributes) {
    if (attr.name.startsWith('data-js-')) {
      toEl.setAttribute(attr.name, attr.value);
    }
  }
});
```

**Alpine.js 상태 보존**

```javascript
wireview.dom.onBeforeElUpdated((fromEl, toEl) => {
  if (fromEl._x_dataStack) {
    window.Alpine.clone(fromEl, toEl);
  }
});
```

**CSS 트랜지션 유지**

```javascript
wireview.dom.onBeforeElUpdated((fromEl, toEl) => {
  if (fromEl.hasAttribute('data-transitioning')) {
    toEl.setAttribute('data-transitioning', fromEl.getAttribute('data-transitioning'));
  }
});
```

### Phoenix LiveView 대응

| 기능 | Phoenix LiveView | django-wireview |
|------|------------------|-----------------|
| 설정 위치 | `LiveSocket` 생성자 옵션 | `wireview.dom.onBeforeElUpdated()` |
| 콜백 시그니처 | `(fromEl, toEl)` | `(fromEl, toEl)` |
| 반환값 | 무시 | 무시 |
| 호출 대상 | 모든 노드 | 엘리먼트 노드만 |
