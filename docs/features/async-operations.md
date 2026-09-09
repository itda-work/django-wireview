# 비동기 작업

UI를 막지 않고 데이터를 읽어 오는 장치다. 읽는 동안 로딩 상태를 보여 주고, 성공·실패를 따로
다룬다.

| 메서드 | 용도 |
|--------|------|
| `assign_async()` | 간단한 비동기. `AsyncResult`가 상태를 대신 추적한다 |
| `start_async()` | 이름 붙은 비동기. 취소·교체할 수 있다 |
| `cancel_async()` | 진행 중인 작업을 취소한다 |
| `handle_async()` | `start_async` 결과를 받는 콜백 |

## assign_async()

가장 단순한 방법이다. 로딩·성공·실패를 추적하는 `AsyncResult`를 돌려준다.

```python
from wireview import Component
from wireview.async_result import AsyncResult


class Dashboard(Component):
    _template_name = "dashboard.html"

    stats: AsyncResult[dict] | None = None

    async def joined(self):
        # 즉시 loading 상태의 AsyncResult를 받고,
        # 끝나면 success/error로 바뀐다
        self.stats = await self.assign_async(self.load_stats())

    async def load_stats(self):
        # 느린 작업을 흉내 낸다
        import asyncio
        await asyncio.sleep(1)
        return {"users": 100, "revenue": 50000}
```

템플릿:

```html
{% load wireview %}

<div {% tag_header %}>
  {% if this.stats.loading %}
    <div class="skeleton">통계를 불러오는 중...</div>
  {% elif this.stats.failed %}
    <div class="error">오류: {{ this.stats.error_message }}</div>
  {% elif this.stats.ok %}
    <div class="stats">
      <p>사용자: {{ this.stats.result.users }}</p>
      <p>매출: {{ this.stats.result.revenue }}</p>
    </div>
  {% endif %}
</div>
```

### AsyncResult 속성

| 속성 | 타입 | 뜻 |
|------|------|-----|
| `loading` | bool | 진행 중 |
| `ok` | bool | 성공으로 끝났다 |
| `failed` | bool | 오류로 끝났다 |
| `done` | bool | 끝났다 (성공이든 실패든) |
| `pending` | bool | 아직 시작하지 않았다 |
| `result` | T | 결과값 (`ok`일 때) |
| `error` | Exception | 예외 (`failed`일 때) |
| `error_message` | str | 사람이 읽을 오류 메시지 |

### AsyncResult 메서드

```python
# 상태 만들기
AsyncResult.loading_state()  # 로딩 상태
AsyncResult.success(value)   # 성공 상태
AsyncResult.failure(error)   # 오류 상태

# 결과 다루기
result.map(lambda x: x * 2)  # 성공일 때만 변환
result.get_or(default)       # 결과 또는 기본값
result.get_or_raise()        # 결과 또는 예외 발생
```

## start_async() / cancel_async()

더 세밀하게 다뤄야 하면 이름 붙은 작업을 쓴다. 취소하거나 교체할 수 있다.

```python
class Search(Component):
    _template_name = "search.html"

    query: str = ""
    results: list[dict] = []
    loading: bool = False
    error: str | None = None

    async def search(self, query: str):
        self.query = query
        self.loading = True
        self.error = None

        # 이름 붙은 비동기 작업을 시작한다.
        # "search"가 이미 돌고 있으면 그것은 취소된다
        await self.start_async("search", self.do_search(query))

    async def do_search(self, query: str) -> list[dict]:
        import asyncio
        await asyncio.sleep(0.5)  # 디바운스
        return await SearchService.search(query)

    async def handle_async(self, name: str, result):
        """start_async가 끝나면 호출된다."""
        if name == "search":
            self.loading = False
            if result[0] == "ok":
                self.results = result[1]
            else:
                self.error = str(result[1])
                self.results = []

    async def clear_search(self):
        # 진행 중인 검색을 취소한다
        await self.cancel_async("search")
        self.query = ""
        self.results = []
        self.loading = False
```

### handle_async() 콜백

`start_async` 작업이 끝나면 호출된다.

```python
async def handle_async(
    self,
    name: str,
    result: tuple[Literal["ok"], Any] | tuple[Literal["exit"], Exception],
) -> None:
    # name: start_async에 넘긴 이름
    # result: ("ok", 값) 또는 ("exit", 예외)

    if name == "load_data":
        status, value = result
        if status == "ok":
            self.data = value
        else:
            self.error = f"Failed to load: {value}"
```

### 작업 이름

아무 문자열이나 되지만, 무엇을 하는지 드러나게 짓는다.

```python
# 좋음: 설명적인 이름
await self.start_async("load_user_profile", self.fetch_profile(user_id))
await self.start_async("search_products", self.search(query))
await self.start_async(f"load_page_{page}", self.fetch_page(page))

# 취소되었는지 확인할 수 있다
if await self.cancel_async("search_products"):
    print("Search was cancelled")
```

### 자동 교체

같은 이름으로 `start_async`를 다시 부르면 앞의 작업이 자동으로 취소된다.

```python
async def search(self, query: str):
    # 키를 누를 때마다 새 검색이 시작되고
    # 앞선 검색은 저절로 취소된다
    await self.start_async("search", self.do_search(query))
```

## 사용 예

### 타입어헤드 검색

```python
class Typeahead(Component):
    _template_name = "typeahead.html"

    query: str = ""
    suggestions: list[str] = []
    loading: bool = False

    async def on_input(self, query: str):
        self.query = query

        if len(query) < 2:
            self.suggestions = []
            await self.cancel_async("suggest")
            return

        self.loading = True
        await self.start_async("suggest", self.fetch_suggestions(query))

    async def fetch_suggestions(self, query: str) -> list[str]:
        import asyncio
        await asyncio.sleep(0.3)  # 자연스러운 디바운스
        return await SuggestionService.get(query)

    async def handle_async(self, name, result):
        if name == "suggest":
            self.loading = False
            if result[0] == "ok":
                self.suggestions = result[1]
```

### 병렬 로딩

```python
class Dashboard(Component):
    _template_name = "dashboard.html"

    users: AsyncResult[list] | None = None
    orders: AsyncResult[list] | None = None
    stats: AsyncResult[dict] | None = None

    async def joined(self):
        # 세 가지를 동시에 읽는다
        self.users = await self.assign_async(self.load_users())
        self.orders = await self.assign_async(self.load_orders())
        self.stats = await self.assign_async(self.load_stats())

    async def load_users(self):
        return await User.objects.all()[:10]

    async def load_orders(self):
        return await Order.objects.filter(status="pending")[:10]

    async def load_stats(self):
        return {"total_users": await User.objects.acount()}
```

### 취소할 수 있는 파일 처리

```python
class FileProcessor(Component):
    _template_name = "processor.html"

    progress: int = 0
    processing: bool = False
    result: str | None = None

    async def process_file(self, file_id: str):
        self.processing = True
        self.progress = 0
        await self.start_async("process", self.do_process(file_id))

    async def do_process(self, file_id: str):
        import asyncio
        for i in range(100):
            await asyncio.sleep(0.1)
            self.progress = i + 1
            await self.send_render()  # 진행률을 갱신한다
        return "Processing complete!"

    async def cancel_processing(self):
        if await self.cancel_async("process"):
            self.processing = False
            self.progress = 0

    async def handle_async(self, name, result):
        if name == "process":
            self.processing = False
            if result[0] == "ok":
                self.result = result[1]
```

## 오류 처리

### assign_async

```python
async def joined(self):
    self.data = await self.assign_async(
        self.load_data(),
        on_error=lambda e: logger.error(f"Load failed: {e}")
    )
```

### start_async

```python
async def handle_async(self, name, result):
    if name == "critical_task":
        if result[0] == "exit":
            error = result[1]
            logger.error(f"Critical task failed: {error}")
            # 필요하면 재시도한다
            await self.start_async("critical_task", self.retry_task())
```

## Phoenix LiveView 대응

| 기능 | Phoenix LiveView | django-wireview |
|------|------------------|-----------------|
| 간단한 비동기 | `assign_async/3` | `assign_async()` |
| 이름 붙은 비동기 | `start_async/3` | `start_async()` |
| 취소 | `cancel_async/3` | `cancel_async()` |
| 결과 처리 | `handle_async/3` | `handle_async()` |
| 결과 래퍼 | `AsyncResult` 구조체 | `AsyncResult` dataclass |
| 자동 취소 | 같은 이름이면 교체 | 같은 이름이면 교체 |
