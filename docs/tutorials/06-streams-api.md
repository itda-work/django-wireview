# 06. Streams API 심화

Streams API의 고급 사용법과 성능 최적화를 다룹니다.

## 학습 목표

- Streams의 내부 동작 이해
- DOM ID 전략
- 커스텀 템플릿 사용
- 성능 최적화 기법
- 트러블슈팅

## Streams vs 일반 렌더링

### 일반 렌더링의 문제

```python
class BadExample(Component):
    items: list = []  # 1000개 아이템

    async def add_item(self, item):
        self.items.append(item)
        # 전체 리스트 HTML 다시 렌더링 후 전송
```

문제점:
- 매번 전체 HTML 생성 (~50KB)
- 대역폭 낭비
- DOM 전체 업데이트
- 메모리 사용량 증가

### Streams의 해결책

```python
class GoodExample(Component):
    items: list = []

    async def add_item(self, item):
        # 새 아이템 HTML만 전송 (~500B)
        await self.stream_insert("items", item)
```

장점:
- 변경된 부분만 전송
- 최소한의 DOM 업데이트
- 메모리 효율적
- 빠른 응답

## DOM ID 전략

### 기본 규칙

Stream 아이템의 DOM ID는 `{stream_name}-{pk}` 형식입니다:

```html
<li id="messages-123">...</li>
<li id="messages-124">...</li>
```

### 커스텀 DOM ID

#### 함수로 지정

```python
async def joined(self):
    await self.stream(
        "items",
        items,
        dom_id=lambda item: f"item-{item.uuid}"
    )
```

#### UUID 사용

`stream()`과 `stream_insert()`는 같은 `dom_id`를 써야 한다. 삽입과 초기 로딩이 다른 규칙으로 id를 만들면
같은 항목이 두 번 들어간다. 헬퍼 하나로 묶어 둔다. 이름은 `_`로 시작해야 한다 — `stream_insert`를
오버라이드하면 자기 자신을 부르게 된다.

```python
async def _insert(self, name, item, **kwargs):
    await self.stream_insert(
        name,
        item,
        dom_id=lambda i: f"{name}-{i.uuid}",
        **kwargs,
    )
```

### DOM ID 충돌 방지

같은 페이지에 여러 스트림이 있을 때:

```python
class XList1(Component):
    async def joined(self):
        # 컴포넌트 ID 포함
        await self.stream(
            "items",
            items,
            dom_id=lambda i: f"{self.id}-items-{i.pk}"
        )
```

## 커스텀 템플릿

### 기본 템플릿 경로

컴포넌트 템플릿 이름에 `_item`을 붙인 것이다. 스트림 이름과는 무관하다.

```
{컴포넌트 template_name에서 확장자를 뺀 것}_item.html
```

예: 컴포넌트의 `template_name`이 `chat/x_chat.html`이면 `chat/x_chat_item.html`. 한 컴포넌트에 스트림이
둘 이상이면 기본 경로를 함께 쓰게 되므로 `template=`으로 나눈다. 항목 템플릿의 컨텍스트 변수는 `item`이다.

### 명시적 템플릿 지정

```python
await self.stream(
    "messages",
    messages,
    template="chat/custom_message.html"
)

await self.stream_insert(
    "messages",
    message,
    template="chat/highlighted_message.html"
)
```

### 조건부 템플릿

```python
async def add_message(self, message):
    template = (
        "chat/system_message.html"
        if message.is_system
        else "chat/user_message.html"
    )
    await self.stream_insert("messages", message, template=template)
```

## 삽입 위치 제어

### at 파라미터

| 값 | 동작 |
|----|------|
| `-1` (기본) | 끝에 추가 (append) |
| `0` | 처음에 추가 (prepend) |
| `n` | n번째 위치에 삽입 |

### 사용 예

```python
# 새 메시지를 끝에 추가
await self.stream_insert("messages", message, at=-1)

# 새 알림을 맨 위에 추가
await self.stream_insert("notifications", notif, at=0)

# 특정 위치에 삽입
await self.stream_insert("items", item, at=5)
```

### 이미 화면에 있는 항목이면 제자리 갱신

`stream_insert`의 dom id가 이미 DOM에 있으면 `at`과 무관하게 **그 자리에서 교체**됩니다.
생성과 갱신을 한 갈래로 쓸 수 있습니다.

```python
async def mutation(self, channel, action, instance):
    if action == ModelAction.DELETED:
        await self.stream_delete("items", f"items-{instance.pk}")
    else:
        # 새 항목이면 맨 위에, 이미 있는 항목이면 제자리 갱신
        await self.stream_insert("items", instance, at=0)
```

**핸들러와 `mutation()` 양쪽에서 넣지 마세요.** 모델을 구독하고 있으면 저장 신호가 자기
연결로도 돌아옵니다. 두 곳에서 넣으면 같은 항목이 두 번 들어갑니다 — 구독 중이라면
삽입은 `mutation()` 한 곳에서만 하고 핸들러는 저장만 합니다.

### 재렌더는 스트림을 지우지 않는다

컴포넌트 템플릿은 **빈** 컨테이너만 렌더합니다. 그래서 `wire-stream` 컨테이너는 DOM 패치
대상에서 제외되고, 상태를 바꾼 뒤 다시 스트리밍하는 핸들러(필터·정렬 전환)가 정상 동작합니다.

```python
async def set_filter(self, filter: str):
    self.filter = filter                             # 재렌더가 일어나도
    await self.stream("items", self.queryset)        # 이 결과가 남는다
```

컨테이너 자체의 속성(class 등)은 이 때문에 서버 렌더로 갱신되지 않습니다. 컨테이너 속성을
바꿔야 한다면 바깥 엘리먼트에 두세요.

## 성능 최적화

### 1. 배치 삽입

여러 아이템을 한 번에 추가:

```python
# 비효율적
for item in items:
    await self.stream_insert("items", item)

# 효율적 - stream()으로 리셋
await self.stream("items", items)
```

### 2. 초기 로딩 최적화

```python
async def joined(self):
    # 최근 50개만 로드
    messages = [m async for m in Message.objects.order_by('-id')[:50]]
    await self.stream("messages", list(reversed(messages)))

async def load_more(self):
    # 추가 로딩
    older = await self._load_older()
    for msg in older:
        await self.stream_insert("messages", msg, at=0)
```

### 3. 스크롤 최적화

```python
async def add_message(self, message):
    await self.stream_insert("messages", message)
    # 부드러운 스크롤
    await self.scroll_into_view(
        f"messages-{message.pk}",
        behavior="smooth",
        block="end"
    )
```

### 4. 불필요한 렌더링 방지

```python
async def add_item(self, item):
    await self.stream_insert("items", item)
    self.skip_render()  # 컴포넌트 전체 렌더링 방지
```

## 트러블슈팅

### 아이템이 표시되지 않음

1. **wire-stream 속성 확인**
   ```html
   <ul wire-stream="items">  <!-- 이름 일치 확인 -->
   ```

2. **DOM ID 확인** — 항목 템플릿에 `id`를 적지 않아도 된다. 클라이언트가 `dom_id`(기본
   `{name}-{item.pk}`)로 붙인다. `pk`가 없는 항목(dict 등)이면 `dom_id=`를 넘겨야 한다.

3. **템플릿 경로 확인**
   - 기본은 컴포넌트 템플릿 이름 + `_item.html` (예: `chat/x_chat.html` → `chat/x_chat_item.html`)

### 순서가 잘못됨

`at` 파라미터 확인:
```python
# 최신이 위로
await self.stream_insert("items", item, at=0)

# 최신이 아래로
await self.stream_insert("items", item, at=-1)
```

### 메모리 누수

화면에 남길 개수는 `limit`으로 정한다. 넘치면 클라이언트가 반대쪽 끝부터 지운다. 서버는 항목을 들고
있지 않으므로 컴포넌트 상태에 목록을 따로 둘 필요가 없다.

```python
async def add_message(self, message):
    # 최신 1000개만 화면에 남긴다
    await self.stream_insert("messages", message, limit=1000)
```

## 무한 스크롤 (`wire-viewport-top` / `wire-viewport-bottom`)

목록 아래(또는 위)에 둔 요소가 화면에 들어오면 그 속성에 적은 핸들러가 불린다. 아래로 스크롤하다
바닥 요소가 보이면 `wire-viewport-bottom`, 위로 스크롤하다 꼭대기 요소가 보이면 `wire-viewport-top`이다.

```html
<ul wire-stream="rows"></ul>
<div wire-viewport-bottom="load_more">불러오는 중...</div>
```

```python
async def load_more(self):
    self.page += 1
    async for row in Row.objects.all()[self.page * 20:(self.page + 1) * 20]:
        await self.stream_insert("rows", row)
```

스크롤 방향과 맞을 때만 불린다. 페이지를 열었을 때 이미 보이는 요소는 부르지 않지만, 요소가 화면
가운데를 이미 지나 있으면(목록이 짧을 때) 방향과 무관하게 부른다. 그 구분이 필요하면 핸들러가
`_overran` 인자를 받는다. 받지 않는 핸들러에는 넘어가지 않는다.

## 고급 패턴

### Virtual Scrolling 준비

```python
class XVirtualList(Component):
    visible_items: list = []
    scroll_top: int = 0
    item_height: int = 50

    async def on_scroll(self, scroll_top: int):
        self.scroll_top = scroll_top
        start = scroll_top // self.item_height
        end = start + 20  # 화면에 20개

        # 표시할 아이템만 로드
        self.visible_items = await self._load_range(start, end)
```

### 드래그 앤 드롭 순서 변경

```python
async def reorder(self, item_id: int, new_index: int):
    item = await Item.objects.aget(id=item_id)

    # DB 순서 업데이트
    await self._update_order(item, new_index)

    # 스트림에서 제거 후 새 위치에 삽입
    await self.stream_delete("items", item_id)
    await self.stream_insert("items", item, at=new_index)
```

## 다음 단계

[← 이전: 05. Dashboard](05-dashboard.md) | [다음: 07. Presence API 심화 →](07-presence-api.md)
