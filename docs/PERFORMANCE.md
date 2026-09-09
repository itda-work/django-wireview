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
    "SYNC_TRANSITION_WARNING_THRESHOLD": 2,  # 깊이 2 초과면 경고
    "SYNC_TRANSITION_ERROR_THRESHOLD": 3,    # 깊이 3 초과면 오류
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
        return await Post.objects.filter(user=self.user)[:5]

# 좋음: joined()에서 미리 읽는다
class UserProfile(Component):
    posts: list[Post] = []

    async def joined(self):
        self.posts = await sync_to_async(list)(
            Post.objects.filter(user=self.user)[:5]
        )
```

### 2. 컴포넌트 안에서는 `asend_to()`를 쓴다

async 컨텍스트에서는 async 함수를 쓴다.

```python
# 나쁨: 내부적으로 async_to_sync를 쓴다
from wireview.utils import send_notification

class ChatRoom(Component):
    async def send_message(self, text: str):
        # async에서 부르면 중첩 전환이 생긴다
        send_notification("chat_room_1", message=text)

# 좋음: 순수 async, 전환 없음
from wireview.utils import asend_notification

class ChatRoom(Component):
    async def send_message(self, text: str):
        await asend_notification("chat_room_1", message=text)
```

### 3. 데이터베이스 질의를 묶는다

`sync_to_async` 호출 횟수 자체를 줄인다.

```python
# 나쁨: sync_to_async를 여러 번
async def joined(self):
    self.user = await sync_to_async(User.objects.get)(pk=self.user_id)
    self.posts = await sync_to_async(list)(self.user.posts.all())
    self.comments = await sync_to_async(list)(self.user.comments.all())

# 좋음: prefetch와 함께 한 번에
async def joined(self):
    @sync_to_async
    def load_user_data():
        user = User.objects.prefetch_related('posts', 'comments').get(pk=self.user_id)
        return user, list(user.posts.all()), list(user.comments.all())

    self.user, self.posts, self.comments = await load_user_data()
```

### 4. 큰 리스트에는 Streams를 쓴다

리스트 전체를 다시 렌더하지 않는다.

```python
class ItemList(Component):
    items: list[Item] = []

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
        self.wire.skip_render()
```

## 튜닝

### HTML diff 설정

> 부분 diff는 `render_with_markers()`가 남기는 주석 마커에 의존한다.
> `USE_HMIN`은 HTML 주석을 제거하므로 **부분 diff를 조용히 끈다.**
> diff 형식과 실측 페이로드는 [features/html-diff.md](./features/html-diff.md)에 있다.

```python
WIREVIEW = {
    "USE_HTML_DIFF": True,   # 전체 HTML이 아니라 변경분만 보낸다
    "USE_HMIN": True,        # HTML 압축 (django-hmin 필요)
}
```

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

```python
import time
import logging

log = logging.getLogger("wireview.profiling")

class ProfiledComponent(Component):
    async def render_diff(self, *args, **kwargs):
        start = time.perf_counter()
        result = await super().render_diff(*args, **kwargs)
        duration = time.perf_counter() - start
        if duration > 0.1:  # 느린 렌더만 기록한다
            log.warning(f"Slow render: {self._name} took {duration:.3f}s")
        return result
```

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
| 채널 브로드캐스트 | < 10ms | Redis pub/sub |

### 부하 테스트

WebSocket 부하는 `locust`로 잰다.

```python
from locust import HttpUser, task
from locust_plugins.users import SocketIOUser

class WireviewUser(SocketIOUser):
    @task
    def join_component(self):
        self.send('{"command": "join", "name": "Counter"}')
```

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
