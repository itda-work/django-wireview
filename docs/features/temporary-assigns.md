# temporary_assigns - 메모리 최적화

> 렌더링 후 지정된 필드를 자동으로 초기화하여 서버 메모리를 절약합니다.

---

## 개요

### 문제: 대용량 데이터의 메모리 점유

일반적인 컴포넌트에서 대용량 리스트를 로드하면, 해당 데이터는 컴포넌트가 살아있는 동안 계속 메모리에 남아있습니다:

```python
class MessageList(Component):
    messages: list[Message] = []

    async def joined(self):
        # 10,000개의 메시지 로드 → ~10MB 메모리 점유
        self.messages = [message async for message in Message.objects.all()[:10000]]
        # 이 데이터는 컴포넌트가 종료될 때까지 메모리에 유지됨
```

WebSocket 연결이 유지되는 동안(사용자가 페이지에 머무르는 동안) 이 메모리는 해제되지 않습니다.

### 해결: temporary_assigns

`Meta.temporary_assigns`를 사용하면 **렌더링이 완료된 직후** 지정된 필드가 기본값으로 자동 초기화됩니다:

```python
class MessageList(Component):
    class Meta:
        temporary_assigns = {"messages"}  # 이 필드는 렌더 후 초기화됨

    messages: list[Message] = []

    async def joined(self):
        self.messages = [message async for message in Message.objects.all()[:10000]]
        # 렌더링 완료 후 → self.messages = [] (메모리 해제)
```

클라이언트에는 이미 HTML이 전송되었으므로 화면에는 10,000개의 메시지가 표시됩니다.

다음 렌더가 목록과 무관한 이벤트 때문이어도 목록은 화면에 남는다. **초기화는 변경이 아니기
때문이다.** Phoenix와 같다. 초기화된 필드만 읽은 부분은 다시 보내지 않으므로, 브라우저는 마지막으로
받은 HTML을 그대로 둔다. 자세한 규칙은 아래 "다음 렌더에서"에 있다.

서명 상태(`data-state`)에는 이 필드가 실리지 않는다. 그래서 재접속으로 다시 join하면 필드는 기본값에서
시작한다. **불러오는 코드는 `joined()`에 둔다.** 위 예제가 그렇다.

---

## 작동 방식

### 초기화 시점

`Meta.temporary_assigns`에 지정된 필드는 **매 렌더링 직후** 초기화됩니다:

```
이벤트 발생 (예: joined, 버튼 클릭)
    ↓
컴포넌트 상태 변경
    ↓
템플릿 렌더링 (messages 데이터 사용)
    ↓
HTML diff 계산 및 클라이언트 전송
    ↓
★ Meta.temporary_assigns 필드 초기화 ★  ← 이 시점
    ↓
다음 이벤트 대기
```

### 다음 렌더에서

초기화된 필드는 핸들러가 다시 대입하기 전까지 *그대로인* 상태다. 다음 렌더에서 템플릿의 각 동적
부분(`{{ … }}`, `{% for %}`, `{% if %}`)은 무엇을 읽었는지에 따라 이렇게 다뤄진다.

| 그 부분이 읽은 것 | 다음 렌더에서 |
|---|---|
| 초기화된 필드만 (`{% for m in messages %}`, `{{ messages\|length }}`, `this.messages`, 그 필드만으로 계산하는 property) | 마지막 모습 그대로. 보내지 않는다 |
| 초기화된 필드와 다른 필드 | 지금 가진 값으로 다시 그린다. 초기화된 필드는 기본값으로 읽힌다 |
| 초기화된 필드를 읽지 않음 | 평소처럼 |

- **다시 대입하면 변경이다.** `self.messages = [...]`는 새 목록을 보낸다. 일부러 비우려면
  `self.messages = []`처럼 대입한다.
- **제자리 변경도 변경이다.** `self.messages.append(...)`처럼 고친 목록은 기본값과 달라지므로 다시 그린다.
- **`{% if messages %}` 블록**은 조건 말고 가지(branch) 안의 이름까지 본다. 가지 어디에든 다른 필드가
  있으면(`{% if messages %}<b>{{ count }}</b>{% endif %}`) 블록을 다시 그린다. 그러면 조건이 빈 목록으로
  평가되어 가지가 빠진다. 다른 필드는 블록 밖에 둔다.
- **다른 목록의 항목 안에서 읽으면**(`{% for r in rows %}{{ messages|length }}{% endfor %}`) 그 루프는
  `rows`도 읽으므로 다시 그려진다.
- **초기화된 목록을 도는 루프**는 항목을 그대로 둔다. 항목 안의 다른 값(`{{ count }}`)도 마지막 값으로
  멈춘다. Phoenix의 `phx-update="append"`처럼 항목은 그린 순간의 모습이다.

### 다른 컴포넌트의 렌더 안에서

중첩된 `{% component %}`는 둘러싼 컴포넌트가 렌더할 때마다 그 렌더 안에서 다시 그려진다.
`{% component %}`를 쓴 쪽(호스트)도, 그 컴포넌트를 슬롯에 담아 그리는 쪽(`{% component_block %}`의
`{% render_slot %}`)도 그렇다. 그 렌더의 diff가 중첩 컴포넌트의 요소까지 덮어쓴다.

그 그림에서도 위 표가 그대로다. 초기화된 필드만 읽은 부분은 그 컴포넌트가 **스스로** 마지막으로 렌더한
모습으로 그려진다. 그래서 중첩 컴포넌트의 이벤트가 불러온 목록은 호스트의 무관한 렌더 뒤에도 화면에
남는다. 1.0 전에는 그 규칙이 컴포넌트 자신의 렌더에만 적용되어, 호스트가 렌더하면 목록이 사라졌다.

- 그 컴포넌트가 스스로 렌더한 적이 없으면 맞춰 볼 것이 없으므로 지금 값으로 그린다.
- 마지막 렌더와 다른 가지(`{% if %}`)에 있는 부분도 지금 값으로 그린다. 그 컴포넌트의 다음 자기
  렌더도 그렇게 그린다.
- 호스트가 넘긴 값(`{% component 'X' count=this.n %}`)처럼 다른 필드를 읽는 부분은 평소처럼 바뀐다.

### 초기화 값 결정 규칙

필드는 **Pydantic 모델에서 정의한 기본값**으로 초기화됩니다:

| 필드 정의 | 초기화 값 |
|-----------|----------|
| `items: list[str] = []` | `[]` (빈 리스트) |
| `data: dict = {}` | `{}` (빈 딕셔너리) |
| `count: int = 0` | `0` |
| `name: str = "default"` | `"default"` |
| `items: list[str] = Field(default_factory=list)` | `[]` (새 리스트 인스턴스) |

**주의**: 기본값이 없는 필드(`items: list[str]` - 기본값 없음)는 초기화되지 않습니다.

### 코드 내부 동작

```python
# wireview/core/component.py
def _clear_temporary_assigns(self) -> None:
    for field_name in self._meta.temporary_assigns:
        field_info = type(self).model_fields[field_name]
        if field_info.is_required():
            continue  # 기본값이 없으면 건드리지 않는다
        default_value = field_info.get_default(call_default_factory=True)
        object.__setattr__(self, field_name, default_value)
```

---

## 사용법

### 기본 사용

```python
from wireview import Component

class ProductList(Component):
    class Meta:
        template_name = "products/list.html"
        temporary_assigns = {"products"}  # set으로 필드명 지정

    products: list[Product] = []
    total_count: int = 0
    current_page: int = 1

    async def joined(self):
        self.products = [product async for product in Product.objects.all()[:100]]
        self.total_count = await Product.objects.acount()
```

**결과**:
- `products`: 렌더 후 `[]`로 초기화 (메모리 해제)
- `total_count`: 유지됨 (100)
- `current_page`: 유지됨 (1)

### 여러 필드 지정

```python
class Dashboard(Component):
    class Meta:
        temporary_assigns = {"orders", "analytics", "logs"}

    orders: list[Order] = []
    analytics: dict = {}
    logs: list[LogEntry] = []
    user_name: str = ""  # 이 필드는 유지됨
```

### 이벤트 핸들러에서 다시 로드

`temporary_assigns` 필드는 매번 새로 로드해야 합니다:

```python
class MessageList(Component):
    class Meta:
        temporary_assigns = {"messages"}

    messages: list[Message] = []
    page: int = 1

    async def joined(self):
        await self._load_messages()

    async def next_page(self):
        self.page += 1
        await self._load_messages()  # 페이지 변경 시 다시 로드

    async def _load_messages(self):
        offset = (self.page - 1) * 50
        self.messages = [message async for message in Message.objects.all()[offset:offset + 50]]
```

---

## 주의사항

### 1. 기본값 필수

`Meta.temporary_assigns`에 지정된 필드는 반드시 기본값이 있어야 합니다:

```python
# ✅ 올바른 사용
class Good(Component):
    class Meta:
        temporary_assigns = {"items"}

    items: list[str] = []  # 기본값 있음

# ❌ 작동하지 않음
class Bad(Component):
    class Meta:
        temporary_assigns = {"items"}

    items: list[str]  # 기본값 없음 - 초기화되지 않음
```

### 2. 렌더링마다 초기화됨

`skip_render()`를 호출해도 `Meta.temporary_assigns`는 초기화됩니다:

```python
async def some_handler(self):
    self.items = [1, 2, 3]
    self.skip_render()  # 렌더링 스킵
    # 하지만 items는 여전히 [] 로 초기화됨
```

그러면 `[1, 2, 3]`은 한 번도 그려지지 않는다. 다음 렌더에서 `items`는 초기화된 필드이므로 화면은
그 전의 모습으로 남는다.

### 3. Streams와의 차이

| 기능 | temporary_assigns | Streams |
|------|------------------|---------|
| **용도** | 단순 리스트 렌더링 후 메모리 해제 | 실시간 리스트 추가/삭제 |
| **데이터 위치** | 서버에서 렌더링 후 해제 | 클라이언트에서 관리 |
| **개별 항목 조작** | 불가능 (전체 재로드 필요) | 가능 (`stream_insert`, `stream_delete`) |
| **메모리** | 렌더링 시점에만 사용 | 서버 메모리 사용 안 함 |

**선택 기준**:
- 읽기 전용 목록 → `temporary_assigns`
- 실시간 추가/삭제 필요 → `Streams`

### 4. 상속 시 동작

```python
class Parent(Component):
    class Meta:
        temporary_assigns = {"items"}

    items: list = []

class Child(Parent):
    # Parent의 Meta.temporary_assigns 상속됨 (Meta는 키 단위로 상속된다)
    # items는 여전히 초기화됨
    pass

class ChildOverride(Parent):
    class Meta:
        temporary_assigns = {"items", "extra"}  # 재정의

    extra: list = []
```

---

## 실제 사용 예시

### 게시판 목록

```python
class BoardList(Component):
    class Meta:
        template_name = "board/list.html"
        temporary_assigns = {"posts"}

    posts: list[Post] = []
    page: int = 1
    total_pages: int = 1
    per_page: int = 20

    async def joined(self):
        await self._load_posts()

    async def go_to_page(self, page: int):
        self.page = page
        await self._load_posts()

    async def _load_posts(self):
        offset = (self.page - 1) * self.per_page
        self.posts = [post async for post in Post.objects.order_by("-created")[offset:offset + self.per_page]]
        total = await Post.objects.acount()
        self.total_pages = (total + self.per_page - 1) // self.per_page
```

### 대시보드 위젯

```python
class AnalyticsDashboard(Component):
    class Meta:
        template_name = "dashboard/analytics.html"
        temporary_assigns = {"chart_data", "recent_events"}

    chart_data: list[dict] = []
    recent_events: list[Event] = []
    summary: dict = {}  # 이건 유지 (작은 데이터)

    async def joined(self):
        await self._load_data()

    async def refresh(self):
        await self._load_data()

    async def _load_data(self):
        # 대용량 데이터
        self.chart_data = await self._fetch_chart_data()
        self.recent_events = [event async for event in Event.objects.order_by("-timestamp")[:100]]
        # 작은 요약 데이터
        self.summary = await self._calculate_summary()
```

---

## 메모리 절약 효과

### 측정 예시

```
10,000개 Message 객체 (각 1KB 가정)

temporary_assigns 미사용:
├── joined() 후: ~10MB
├── 1시간 후: ~10MB (유지)
└── 연결 종료 시: 해제

temporary_assigns 사용:
├── joined() 후: ~10MB
├── 렌더링 완료 후: ~0MB (즉시 해제)
└── GC 대상이 됨
```

줄어드는 것은 파이썬 객체(모델 인스턴스, dict)다. 렌더된 HTML 조각은 다음 diff를 위해 서버가
계속 들고 있다. 그래서 목록 1만 개면 그 HTML만큼은 남는다. 그것마저 줄여야 하면
[Streams](../tutorials/06-streams-api.md)를 쓴다.

### 동시 접속자 기준

```
동시 접속 1,000명, 각각 10,000개 메시지 표시 시:

미사용: 1,000 × 10MB = 10GB 메모리 필요
사용:   1,000 × ~0MB = 최소 메모리
```

---

## 관련 기능

- [Streams API](../tutorials/06-streams-api.md) - 실시간 리스트 조작
- [`skip_render()`](./component-api.md#부르는-것) - 불필요한 렌더링 방지
- [Performance Guide](../PERFORMANCE.md) - 성능 최적화 가이드

---

*이 기능은 Phoenix LiveView의 `temporary_assigns` 옵션을 참고하여 구현되었습니다.*
