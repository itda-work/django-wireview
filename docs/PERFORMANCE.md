# 성능 가이드

## async/sync 전환 이해하기

### 문제

Django Channels는 async 컨텍스트에서 돌지만 Django 템플릿과 ORM은 동기다. 그래서 전환이 생긴다.

```
ASYNC (Consumer)
  → sync_to_async (템플릿 렌더)
    → SYNC (Django 템플릿)
```

전환이 중첩되면 성능이 나빠진다.

```
ASYNC (Consumer)
  → sync_to_async
    → SYNC (렌더)
      → async_to_sync (async 프로퍼티)  # 추가 비용!
        → ASYNC (프로퍼티 코루틴)
```

`async_to_sync` 호출마다 이벤트 루프가 새로 만들어지고, 대략 0.5~2ms가 붙는다.

### 지금의 동작

wireview는 동기 컨텍스트로 들어가기 **전에** async 프로퍼티를 먼저 해소한다.

```
ASYNC (Consumer)
  → async 프로퍼티를 await  # 직접 await, 추가 비용 없음
  → sync_to_async (템플릿 렌더)
    → SYNC (Django 템플릿)  # 컨텍스트는 이미 해소됨
```

## 문제를 찾아내기

### 전환 추적 켜기

개발 중에는 감지를 켜 둔다.

```python
# settings.py
WIREVIEW = {
    "DEBUG_SYNC_TRANSITIONS": True,
    "DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD": 2,  # 깊이 2 초과면 경고
    "DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD": 3,    # 깊이 3 초과면 오류
}
```

중첩 전환이 감지되면 이런 경고가 남는다.

```
WARNING wireview.sync_detector: Nested sync context detected (total depth=3)
at _run_coro. This may cause performance degradation.
```

### 운영에서는 끈다

```python
WIREVIEW = {
    "DEBUG_SYNC_TRANSITIONS": False,  # 꺼 두면 비용 0
}
```

## 권장 사항

### 1. 데이터는 `joined()`에서 미리 읽는다

async 프로퍼티 대신 라이프사이클 메서드에서 읽는다.

```python
# 나쁨: 렌더 중에 접근하는 async 프로퍼티
class UserProfile(Component):
    @property
    async def recent_posts(self):
        return [post async for post in Post.objects.filter(user=self.user)[:5]]

# 좋음: joined()에서 미리 읽는다
class UserProfile(Component):
    posts: list[Post] = []  # 서명 상태에는 pk 목록만 실린다

    async def joined(self):
        self.posts = [post async for post in Post.objects.filter(user=self.user)[:5]]
```

### 2. 컴포넌트 안에서는 `abroadcast()`를 쓴다

async 컨텍스트에서는 async 함수를 쓴다.

```python
# 나쁨: 내부적으로 async_to_sync를 쓴다
from wireview import broadcast

class ChatRoom(Component):
    async def send_message(self, text: str):
        # async에서 부르면 중첩 전환이 생긴다
        broadcast("chat_room_1", message=text)

# 좋음: 순수 async, 전환 없음
from wireview import abroadcast

class ChatRoom(Component):
    async def send_message(self, text: str):
        await abroadcast("chat_room_1", message=text)
```

### 3. 데이터베이스 질의를 묶는다

`sync_to_async` 호출 횟수 자체를 줄인다.

```python
# 나쁨: sync_to_async를 여러 번
async def joined(self):
    profile = await sync_to_async(Profile.objects.get)(user=self.user)
    self.posts = await sync_to_async(list)(profile.posts.all())
    self.comments = await sync_to_async(list)(profile.comments.all())

# 좋음: 한 번의 전환 안에서 전부 읽는다
async def joined(self):
    @sync_to_async
    def load_profile_data():
        profile = Profile.objects.prefetch_related("posts", "comments").get(user=self.user)
        return list(profile.posts.all()), list(profile.comments.all())

    self.posts, self.comments = await load_profile_data()
```

### 4. 큰 리스트에는 Streams를 쓴다

리스트 전체를 다시 렌더하지 않는다.

```python
class ItemList(Component):
    # 항목은 상태 필드에 두지 않는다. 스트림으로 보낸 항목은 클라이언트 DOM에만 있다
    async def add_item(self, name: str):
        item = await Item.objects.acreate(name=name)
        # 리스트 전체가 아니라 새 항목만 보낸다
        await self.stream_insert("items", item, at=0)
```

### 5. 필요 없는 렌더를 건너뛴다

```python
class Counter(Component):
    count: int = 0

    async def increment_silent(self):
        self.count += 1
        # UI를 갱신할 필요가 없으면 렌더를 건너뛴다
        self.skip_render()
```

## 튜닝

### HTML diff 설정

> 부분 diff는 `render_with_markers()`가 남기는 주석 마커에 의존한다. HTML 압축기로 주석을 지우면 부분 diff가
> 꺼져 바뀔 때마다 HTML 전체가 나간다. 그래서 django-hmin 연동(`USE_HMIN`)은 #100에서 없앴다. diff 형식과 실측
> 페이로드는 [features/html-diff.md](./features/html-diff.md)에 있다.
>
> WebSocket 압축(permessage-deflate)은 **기본으로 끄기를 권한다**([배포 가이드](./DEPLOYMENT.md#권장-uvicorn--uvloop)).
> 연결마다 zlib 컨텍스트로 약 159KB를 들고, 작은 diff는 16% 남짓밖에 줄지 않는다. 첫 렌더나 스트림이 큰 HTML을
> 자주 보내는 앱만 켜고, 켜기 전에 메모리와 전송량을 둘 다 잰다.

### 템플릿

1. **템플릿을 잘게 나눈다.** 작을수록 렌더가 빠르다
2. **복잡한 템플릿 로직을 피한다.** Python 쪽으로 옮긴다
3. **템플릿 컴파일 캐시는 Django가 기본으로 한다**

### 데이터베이스

1. **`select_related()`·`prefetch_related()`를 쓴다**
2. **필터에 쓰는 필드에 인덱스를 건다**
3. **커넥션 풀링을 쓴다** (`CONN_MAX_AGE`)

### 채널 레이어

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("redis", 6379)],
            "capacity": 1500,      # 메모리에 둘 최대 메시지 수
            "expiry": 10,          # 메시지 만료(초)
        },
    },
}
```

## 프로파일링

### Django Debug Toolbar

개발 환경에 설치한다.

```python
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE
```

### 직접 재기

렌더·diff·이벤트 핸들러의 소요 시간은 옵트인 telemetry 시그널로 받는다. 컴포넌트를 오버라이드할 필요가 없다.
렌더 경로는 `Component`가 아니라 컨슈머와 `WireviewMeta`에 있어서, 컴포넌트에 메서드를 덮어써도 불리지 않는다.

```python
# settings.py
WIREVIEW = {"TELEMETRY": True}
```

```python
# myapp/telemetry_receivers.py (AppConfig.ready()에서 import한다)
import logging

from django.dispatch import receiver

from wireview import telemetry

log = logging.getLogger("wireview.profiling")


@receiver(telemetry.component_rendered)
def log_slow_renders(sender, component_name, duration_ms, **kwargs):
    if duration_ms > 100:  # 느린 렌더만 기록한다
        log.warning("Slow render: %s took %.1fms", component_name, duration_ms)
```

시그널과 인자 목록은 [features/telemetry.md](features/telemetry.md).

### py-spy

```bash
py-spy record -o profile.svg --pid <PID>
```

## 벤치마크

> 짐작하지 말고 잰다. `make bench`가 `bench/`의 인프로세스·WebSocket 벤치마크를 돌리고,
> `make bench-compare BASE=<ref>`가 과거 커밋을 현재 트리 옆에서 함께 잰다.
> 사용법과 해석은 [bench/README.md](../bench/README.md).

### 기대치

| 동작 | 목표 | 비고 |
|------|------|------|
| WebSocket 연결 | < 50ms | 최초 연결 |
| 컴포넌트 join | < 100ms | `joined()` 포함 |
| 이벤트 핸들러 | < 50ms | 사용자 조작 |
| 렌더 diff | < 20ms | HTML 생성 |
| 브로드캐스트 발행 | < 10ms | `abroadcast` 한 번이 브로커에 메시지를 넘기기까지. 받는 쪽 렌더는 들어 있지 않다 |

이 표는 동작 하나의 **목표**이고 벤치가 재는 값이 아니다. 특히 벤치의 `ws.broadcast_ms`는 다른 지표다 —
브로드캐스트 하나가 구독한 **모든 연결**의 재렌더까지 끝나는 벽시계 시간이라 연결 수와 항목 수에 비례한다.
macOS에서 연결 2,000개일 때 프로세스 4개(NATS·Redis)는 141~416ms, 단일 프로세스(InMemory)는 635~1,763ms였다
(`bench/results/a993181-*.json`, `663b3f7-*.json`). 정의는 [bench/README.md](../bench/README.md).

### 부하 테스트

wireview의 WebSocket은 Socket.IO가 아니라 `{"command", "payload"}` JSON 프레임을 주고받는 날 WebSocket이다
([wire-protocol](implementation/wire-protocol.md)). join에는 페이지가 발급한 서명 상태(`data-state`)가 필요하므로,
가상 사용자는 페이지를 먼저 받고 거기서 컴포넌트의 이름·id·상태를 읽는다. `locust`와 `websocket-client`로 쓰면 다음과 같다.

```python
import html
import json
import re
import time

import websocket  # pip install websocket-client
from locust import HttpUser, between, task

COMPONENT_TAG = re.compile(r"<[^>]*\bwireview-component\b[^>]*>")
ATTRIBUTE = re.compile(r'([\w-]+)="([^"]*)"')


class WireviewUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        page = self.client.get("/counter/")
        attrs = dict(ATTRIBUTE.findall(COMPONENT_TAG.search(page.text).group(0)))
        self.component_id = attrs["id"]
        cookie = "; ".join(f"{name}={value}" for name, value in self.client.cookies.items())
        self.ws = websocket.create_connection(self.host.replace("http", "ws", 1) + "/__wireview__", cookie=cookie)
        # 중첩 컴포넌트가 있는 페이지라면 children에 {id: [name, state]}를 채운다
        self.request("join", {"name": attrs["data-name"], "state": html.unescape(attrs["data-state"]), "children": {}})

    def on_stop(self):
        self.ws.close()

    @task
    def increment(self):
        event = {"id": self.component_id, "command": "increment", "implicit_args": {}, "explicit_args": {}}
        self.request("user_event", event)

    def request(self, command, payload):
        """명령 하나를 보내고 그에 답하는 render가 올 때까지의 시간을 잰다."""
        start = time.perf_counter()
        self.ws.send(json.dumps({"command": command, "payload": payload}))
        while (message := json.loads(self.ws.recv()))["command"] != "render":
            pass
        self.environment.events.request.fire(
            request_type="WS",
            name=command,
            response_time=(time.perf_counter() - start) * 1000,
            response_length=len(json.dumps(message)),
            exception=None,
            context={},
        )
```

핸들러가 아무것도 바꾸지 않아도 서버는 `diff: null`인 render로 답하므로 위의 대기는 끝난다. 연결 수당 메모리·join 처리량·
이벤트 처리량은 `make bench`의 WebSocket 벤치마크(`bench/ws.py`)가 같은 프로토콜로 이미 잰다.

## 문제 해결

### 최초 로딩이 느리다

1. `joined()`에 느린 질의가 있는지 본다
2. Django Debug Toolbar로 프로파일링한다
3. 큰 데이터셋은 지연 로딩을 고려한다

### 메모리를 많이 쓴다

1. 큰 리스트를 상태에 담지 말고 Streams를 쓴다
2. 순환 참조를 확인한다
3. 컴포넌트 인스턴스 수를 관찰한다

### 연결이 자주 끊긴다

1. WebSocket 프록시의 타임아웃 설정을 본다
2. Redis 연결이 안정적인지 확인한다
3. 서버 자원을 관찰한다

### async/sync 경고가 뜬다

"Nested sync context detected"가 보이면

1. 템플릿에서 쓰는 async 프로퍼티를 찾는다
2. 데이터 로딩을 `joined()`로 옮긴다
3. async 호출 사슬을 다시 본다

운영 설정은 [배포 가이드](DEPLOYMENT.md)에 있다.
