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

작업 코루틴을 만드는 메서드는 `_`로 시작한다. 밑줄 없는 메서드는 클라이언트가 이벤트로 부를 수 있는 핸들러가 된다.

```python
from wireview import Component, AsyncResult


class Dashboard(Component):
    class Meta:
        template_name = "dashboard.html"

    stats: AsyncResult[dict] | None = None

    async def joined(self):
        # 즉시 loading 상태의 AsyncResult를 받고,
        # 끝나면 success/error로 바뀐다
        self.stats = await self.assign_async(self._load_stats())

    async def _load_stats(self):
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
| `state` | `AsyncState` | 위 넷을 하나로: `AsyncState.PENDING`·`LOADING`·`SUCCESS`·`ERROR` (`str` 열거형이라 `"loading"`과도 같다) |
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
    class Meta:
        template_name = "search.html"

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
        await self.start_async("search", self._do_search(query))

    async def _do_search(self, query: str) -> list[dict]:
        import asyncio
        await asyncio.sleep(0.5)  # 디바운스
        return await SearchService.search(query)

    async def handle_async(self, name: str, result):
        """start_async가 끝나면 호출된다."""
        if name == "search":
            self.loading = False
            if result.ok:
                self.results = result.result
            else:
                self.error = result.error_message
                self.results = []

    async def clear_search(self):
        # 진행 중인 검색을 취소한다
        await self.cancel_async("search")
        self.query = ""
        self.results = []
        self.loading = False
```

### handle_async() 콜백

`start_async` 작업이 끝나면 호출된다. `result`는 `assign_async`가 채우는 것과 같은 `AsyncResult`이고,
끝난 상태(`ok` 또는 `failed`)로만 온다. 취소된 작업은 `handle_async`를 부르지 않는다.

```python
from wireview import AsyncResult

async def handle_async(self, name: str, result: AsyncResult) -> None:
    # name: start_async에 넘긴 이름
    if name == "load_data":
        if result.ok:
            self.data = result.result
        else:
            self.error = f"Failed to load: {result.error_message}"  # 예외는 result.error
```

### 작업 이름

아무 문자열이나 되지만, 무엇을 하는지 드러나게 짓는다.

```python
# 좋음: 설명적인 이름
await self.start_async("load_user_profile", self._fetch_profile(user_id))
await self.start_async("search_products", self._search_products(query))
await self.start_async(f"load_page_{page}", self._fetch_page(page))

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
    await self.start_async("search", self._do_search(query))
```

앞의 작업이 이미 결과를 내고 `handle_async`에 있어도 취소된다. `handle_async`가 결과를 반영하기 전에
`await`하는 동안(보강 조회, 저장) 새 검색이 시작되면, 낡은 결과는 반영되지 않고 새 결과만 화면에
남는다. `cancel_async`도 같다.

예외는 `handle_async` **자신**이 제 이름을 다시 부를 때 하나다. `handle_async` 안에서 같은 이름으로
다시 시작해도(재시도, 폴링) 지금 돌고 있는 `handle_async`는 취소되지 않고 끝까지 돌며 렌더도 나간다(#147).
같은 까닭에 `handle_async` 안에서 제 이름으로 `cancel_async`를 부르면 취소하지 않고 `False`를 돌려준다.
작업 코루틴이 아직 결과를 내기 전에 제 이름으로 다시 시작하면 여느 교체처럼 취소된다.

## 사용 예

### 타입어헤드 검색

```python
class Typeahead(Component):
    class Meta:
        template_name = "typeahead.html"

    query: str = ""
    suggestions: list[str] = []
    loading: bool = False

    async def on_input(self, query: str):
        self.query = query

        if len(query) < 2:
            self.suggestions = []
            # 취소된 작업은 handle_async를 부르지 않으므로 로딩 표시는 여기서 끈다
            await self.cancel_async("suggest")
            self.loading = False
            return

        self.loading = True
        await self.start_async("suggest", self._fetch_suggestions(query))

    async def _fetch_suggestions(self, query: str) -> list[str]:
        import asyncio
        await asyncio.sleep(0.3)  # 자연스러운 디바운스
        return await SuggestionService.get(query)

    async def handle_async(self, name, result):
        if name == "suggest":
            self.loading = False
            if result.ok:
                self.suggestions = result.result
```

### 병렬 로딩

```python
class Dashboard(Component):
    class Meta:
        template_name = "dashboard.html"

    users: AsyncResult[list[User]] | None = None
    orders: AsyncResult[list[Order]] | None = None
    stats: AsyncResult[dict] | None = None

    async def joined(self):
        # 세 가지를 동시에 읽는다
        self.users = await self.assign_async(self._load_users())
        self.orders = await self.assign_async(self._load_orders())
        self.stats = await self.assign_async(self._load_stats())

    async def _load_users(self):
        # QuerySet은 await할 수 없다. async for로 목록을 만든다.
        # 결과의 모델 인스턴스는 서명 상태에 pk로 실리고, 다시 join할 때 AsyncResult[list[User]]를 따라 다시 읽힌다
        return [user async for user in User.objects.all()[:10]]

    async def _load_orders(self):
        return [order async for order in Order.objects.filter(status="pending")[:10]]

    async def _load_stats(self):
        return {"total_users": await User.objects.acount()}
```

### 취소할 수 있는 파일 처리

```python
class FileProcessor(Component):
    class Meta:
        template_name = "processor.html"

    progress: int = 0
    processing: bool = False
    result: str | None = None

    async def process_file(self, file_id: str):
        self.processing = True
        self.progress = 0
        await self.start_async("process", self._do_process(file_id))

    async def _do_process(self, file_id: str):
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
            if result.ok:
                self.result = result.result
```

## 작업의 수명

작업은 그것을 시작한 **컴포넌트보다 오래 살지 않는다**(#95). 컴포넌트가 떠나면 `leaving()`이 끝난 뒤
남은 `start_async`·`assign_async` 작업이 모두 취소된다. 컴포넌트가 떠나는 경우는 다음과 같다.

- 탭이 닫히거나 연결이 끊긴다
- 요소가 DOM에서 사라진다(`leave`). 부모가 더 이상 그리지 않는 LiveComponent도 여기에 든다
- 내비게이션으로 같은 id의 새 인스턴스가 join한다
- 핸들러가 예외를 던져 인스턴스가 버려진다([errors](./errors.md))

`handle_async`를 돌고 있는 작업도 취소된다. 작업이 아직 결과를 내는 중이면 다음 `await`에서 취소된다.

취소된 작업에는 `handle_async`가 불리지 않고 렌더도 요청되지 않는다. `handle_async` 도중에 취소되면
그 다음 `await`부터는 돌지 않고 렌더도 요청되지 않는다. `cancel_async()`로 직접 취소할 때도 같다. 작업 안에서 정리가 필요하면 `asyncio.CancelledError`를 받아 처리하고 다시 던진다.

```python
async def export(self):
    try:
        await self._write_rows()
    except asyncio.CancelledError:
        await self._remove_partial_file()
        raise
```

재연결하면 진행 중이던 작업은 이어지지 않는다. 다시 join한 인스턴스는 서명 상태에서 복원된 새
인스턴스이고, 작업은 옛 인스턴스와 함께 취소됐다. 재연결 뒤에도 필요한 작업이면 `joined()`에서 다시
시작한다.

## 작업과 렌더

라이브 렌더는 property를 읽고 템플릿을 그리는 일을 워커 스레드에서 하고(#120), 그동안 이벤트 루프는
계속 돈다. `start_async`·`assign_async`가 시작한 작업은 그 루프에서 돈다. 그래서 작업은 **컴포넌트의
렌더와 겹치지 않게** 돈다(#138). 렌더가 진행 중이면 작업의 다음 단계(두 `await` 사이의 코드)는 그
렌더가 끝날 때까지 기다린다. 작업 코루틴 자신, 결과가 `AsyncResult`에 들어가는 일, `handle_async`가
모두 그렇다. `start_async`·`assign_async`의 작업에 대해서는 한 프레임의 `data-state`와 본문이 같은
상태를 말한다. 위 `FileProcessor`처럼 작업 안에서 `self.progress`를 바꿔도 된다.

렌더를 하는 쪽은 둘이다. 이벤트·메시지를 처리하는 연결의 렌더와, 작업이 `stream_insert`·`stream`으로
그리는 스트림 항목이다. 둘은 겹칠 수 있고 끝나는 순서도 정해져 있지 않다. 작업의 다음 단계는
**다른 쪽의 렌더가 하나도 남지 않았을 때** 시작한다. 작업이 스스로 시작한 렌더는 그 작업을 막지 않는다.
한 메시지가 `stream()`으로 항목을 여럿 그리면 작업은 그 항목들을 다 그릴 때까지 기다린다(항목 수에
비례한다). 작업이 요청하는 렌더와 스트림 조작은 어차피 연결이 그 메시지를 다 처리한 뒤에 나가므로
이 기다림으로 늦어지지 않는다. 늦어지는 것은 작업의 다음 단계다. 그 단계가 쿼리나 HTTP 요청을
시작한다면 그 요청은 그 메시지를 처리하는 시간만큼 늦게 시작하고, 그 결과가 화면에 오는 것도 그만큼
늦다(#147).

기다리는 것은 그 컴포넌트의 작업뿐이다. 루프는 막히지 않으므로 다른 컴포넌트와 다른 연결은 그대로
돈다. 렌더 도중 작업을 취소하면 작업은 그 렌더가 끝난 뒤에 `CancelledError`를 받는다.

한계는 다음과 같다.

- **`start_async`·`assign_async` 밖의 코드는 해당하지 않는다.** 아래 코드가 `self`를 바꾸면 워커
  스레드가 렌더하는 도중에 바뀔 수 있다. 그러면 서명은 옛 상태로, 본문은 새 상태로 나가는 프레임이
  생긴다. 컴포넌트를 바꾸는 백그라운드 작업은 `start_async`로 시작한다.
  - `asyncio.create_task()`로 직접 띄운 코루틴
  - 루프에 따로 붙인 그 밖의 코드(콜백, 타이머 등)
  - 작업 안이라도 thread-sensitive가 아닌 실행기에서 도는 코드: `loop.run_in_executor()`,
    `sync_to_async(thread_sensitive=False)`. 이 코드는 렌더와 다른 스레드에서 동시에 돈다.
    `db()`는 렌더와 같은 스레드에서 차례로 돌므로 안전하다.

  라이브러리가 스스로 띄우는 태스크 중 게이트를 거치지 않는 것은 둘이다. `PresenceMixin`의 타이핑
  만료 타이머와 `allow_upload()`의 설정 전송이다. 둘 다 렌더가 읽는 상태를 바꾸지 않는다. 앞의 것은
  서명 상태에 들지 않는 내부 표시를 끄고 브로드캐스트를 보내고, 뒤의 것은 메시지 하나를 보낸다(#147).
  게이트 안에서 임의의 코루틴을 돌리는 공개 API는 없다. 컴포넌트를 바꾸는 작업은 `start_async`로 시작한다.
- **async property가 그 컴포넌트의 작업을 기다리면 멈춘다.** property는 렌더 안에서 기다려지고,
  작업은 그 렌더가 끝나기를 기다린다. 작업의 결과는 property에서 기다리지 말고 `handle_async`나
  `AsyncResult` 필드로 받는다. 예외도 취소도 나지 않으므로, 렌더가 async property를 10초 넘게
  기다리는 동안 그 컴포넌트의 작업이 렌더를 기다리고 있으면 `wireview` 로거에 경고를 한 번 남긴다(#147).
  워커 스레드에서 읽는 동안(느린 쿼리)은 작업을 기다릴 수 없으므로 경고하지 않는다. property가 제
  작업을 기다리지 않고 그저 10초 넘게 걸려도 경고는 나온다. 그동안 작업이 멈춰 있는 것은 사실이다.

## 오류 처리

### assign_async

```python
async def joined(self):
    self.data = await self.assign_async(
        self._load_data(),
        on_error=lambda e: logger.error(f"Load failed: {e}")
    )
```

### start_async

```python
async def handle_async(self, name, result):
    if name == "critical_task":
        if result.failed:
            logger.error(f"Critical task failed: {result.error}")
            # 필요하면 재시도한다
            await self.start_async("critical_task", self._retry_task())
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
| 떠날 때 | 프로세스와 함께 종료 | `leaving()` 뒤 취소 |
