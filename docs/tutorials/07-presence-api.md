# 07. Presence API 심화

Presence API의 고급 사용법과 스케일링 전략을 다룹니다.

## 학습 목표

- Presence 아키텍처 이해
- 커스텀 메타데이터 활용
- 다중 방 지원
- 타이핑 타임아웃 커스터마이징
- 스케일링 고려사항

## Presence 아키텍처

### 두 가지 Mixin

| Mixin | 역할 | 사용 시점 |
|-------|------|----------|
| `PresenceMixin` | 자신의 상태 브로드캐스트 | 입력 컴포넌트, 채팅 입력창 |
| `PresenceTrackerMixin` | 다른 사용자 추적 | 사용자 목록, 온라인 표시 |

### 데이터 흐름

```
PresenceMixin (User A)
    │
    ├─ presence_join() ──────┐
    ├─ presence_set_typing() │
    └─ presence_leave() ─────┤
                             │
                             ▼
                    Broadcast Channel
                             │
                             ▼
             PresenceTrackerMixin (User B)
                    │
                    ├─ presence_users
                    ├─ presence_online_count
                    └─ presence_typing_users
```

## 커스텀 메타데이터

### 기본 정보 외 추가 데이터

```python
class XChatInput(PresenceMixin, Component):
    username: str
    avatar_url: str
    role: str  # admin, moderator, user

    def _presence_metadata(self) -> dict:
        """추가 메타데이터 반환"""
        return {
            "avatar_url": self.avatar_url,
            "role": self.role,
            "joined_at": timezone.now().isoformat(),
        }
```

### 메타데이터 사용

```html
{% for user in this.presence_users %}
  <div class="user">
    <img src="{{ user.metadata.avatar_url }}" alt="{{ user.username }}">
    <span class="name">{{ user.username }}</span>
    {% if user.metadata.role == 'admin' %}
      <span class="badge admin">Admin</span>
    {% endif %}
  </div>
{% endfor %}
```

## 다중 방 지원

### 동적 토픽

```python
class XChatRoom(PresenceMixin, Component):
    room_id: int

    def _presence_topic(self) -> str:
        # 방마다 다른 토픽
        return f"chat.room.{self.room_id}"
```

### 여러 토픽 구독

```python
class XGlobalPresence(PresenceTrackerMixin, Component):
    """여러 방의 사용자를 동시에 추적"""

    room_ids: list[int]

    @property
    def _subscriptions(self):
        # 여러 채널 구독
        channels = set()
        for room_id in self.room_ids:
            channels.add(f"presence.chat.room.{room_id}")
        return channels

    def _presence_topic(self) -> str:
        # 기본 토픽 (필수 구현)
        return f"chat.room.{self.room_ids[0]}" if self.room_ids else "global"
```

## 타이핑 타임아웃 커스터마이징

### 기본 설정

```python
from wireview.features.presence import PresenceConfig

class XChatInput(PresenceMixin, Component):
    _presence_config = PresenceConfig(
        typing_timeout=3.0,  # 3초 후 자동 해제
    )
```

### 긴 타임아웃

문서 편집 등 긴 작업:

```python
class XDocEditor(PresenceMixin, Component):
    _presence_config = PresenceConfig(
        typing_timeout=10.0,  # 10초
    )
```

### 수동 타이핑 해제

```python
async def on_input(self, text: str):
    if text:
        await self.presence_set_typing(True)
    else:
        # 입력이 비면 즉시 해제
        await self.presence_set_typing(False)
```

## 상태 동기화

### sync_on_join

새 사용자가 입장하면 기존 사용자 정보 요청:

```python
_presence_config = PresenceConfig(
    sync_on_join=True,  # 기본값
)
```

### 동기화 흐름

1. User B 입장
2. User B가 `presence_sync_request` 브로드캐스트
3. User A가 요청 수신 → `presence_sync_response` 전송
4. User B가 User A 정보 수신

## PresenceUser 객체

### 속성

```python
@dataclass
class PresenceUser:
    user_id: str       # 고유 식별자
    username: str      # 표시 이름
    state: PresenceState  # ONLINE, TYPING, OFFLINE
    last_active: float # 마지막 활동 시간
    metadata: dict     # 커스텀 데이터
```

### 메서드

```python
user.is_typing()  # 타이핑 중인지
user.is_online()  # 온라인인지 (OFFLINE 제외 모든 상태)
```

### 템플릿에서 사용

```html
{% for user in this.presence_users %}
  <li class="{% if user.is_typing %}typing{% endif %}">
    {{ user.username }}

    {% if user.is_typing %}
      <span class="status">typing...</span>
    {% elif user.is_online %}
      <span class="status online">online</span>
    {% endif %}
  </li>
{% endfor %}
```

## 스케일링 고려사항

### Redis Channel Layer 필수

프로덕션에서는 반드시 Redis 사용:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("127.0.0.1", 6379)],
        },
    },
}
```

### 대규모 방 처리

수천 명이 있는 방에서는:

```python
class XLargeRoom(PresenceTrackerMixin, Component):
    # 상위 N명만 표시
    @property
    def top_users(self) -> list:
        return self.presence_users[:50]

    @property
    def total_count(self) -> int:
        return self.presence_online_count
```

### 비활성 사용자 정리

```python
import time

class XOnlineUsers(PresenceTrackerMixin, Component):
    @property
    def active_users(self) -> list:
        # 5분 이내 활동한 사용자만
        cutoff = time.time() - 300
        return [
            u for u in self.presence_users
            if u.last_active > cutoff
        ]
```

## 트러블슈팅

### 온라인 사용자가 표시되지 않음

1. **구독 확인**
   ```python
   @property
   def _subscriptions(self):
       return {self._presence_channel()}  # 필수
   ```

2. **토픽 일치 확인**
   - Producer와 Consumer의 `_presence_topic()` 반환값 동일해야 함

3. **Channel Layer 확인**
   - InMemoryChannelLayer는 다른 프로세스와 공유 안 됨

### 타이핑이 사라지지 않음

타임아웃 로직 확인:
```python
async def on_typing(self):
    await self.presence_set_typing(True)
    # 이후 자동으로 타임아웃됨
```

### 퇴장이 감지되지 않음

`leaving()` 훅 구현 확인:
```python
async def leaving(self):
    await self.presence_leave()  # 필수
```

## 고급 패턴

### 상태 표시 (자리비움 등)

```python
from enum import Enum

class UserStatus(str, Enum):
    ONLINE = "online"
    AWAY = "away"
    DND = "dnd"  # Do Not Disturb

class XStatusIndicator(PresenceMixin, Component):
    status: UserStatus = UserStatus.ONLINE

    def _presence_metadata(self) -> dict:
        return {"status": self.status.value}

    async def set_status(self, status: str):
        self.status = UserStatus(status)
        # 상태 변경 브로드캐스트
        await self.presence_join()
```

### 마지막 활동 시간

```python
class XOnlineUsers(PresenceTrackerMixin, Component):
    def format_last_seen(self, user) -> str:
        from django.utils import timezone
        from django.utils.timesince import timesince

        last = datetime.fromtimestamp(user.last_active)
        return timesince(last)
```

## 다음 단계

[← 이전: 06. Streams API 심화](06-streams-api.md) | [다음: 08. File Uploads 심화 →](08-file-uploads.md)
