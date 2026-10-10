# Presence API 심화

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

## 탭이 여럿인 사용자

Presence는 사용자가 아니라 **연결**(브라우저 탭 하나의 WebSocket)마다 기록한다. Phoenix Presence와 같은 모양이다.
트래커는 사용자 아래에 연결마다 항목을 하나씩 두고, 템플릿이 읽는 목록은 사용자마다 한 항목으로 합친다.

- **떠남**: 같은 사용자가 탭 둘을 열었으면 하나를 닫아도 목록에 남는다. 마지막 탭이 떠날 때 빠진다.
- **상태**: 연결 중 하나라도 입력 중이면 그 사용자는 입력 중이다. 그렇지 않고 하나라도 온라인이면 온라인이다.
  입력 자동 해제 타이머도 연결마다 따로 돈다 — 한 탭이 멈춰도 다른 탭이 입력 중이면 입력 중으로 남는다.
- **이름과 메타데이터**: 가장 최근에 소식이 온 연결의 `username`·`metadata`·`last_active`를 쓴다.

한 페이지의 컴포넌트들은 같은 연결이므로, 같은 페이지에 있는 프로듀서(`PresenceMixin`)와 트래커
(`PresenceTrackerMixin`)는 한 항목을 함께 말한다. 연결을 가리키는 값은 메시지의 `presence_ref`에 실린다. 연결 id를
그대로 보내지 않고 거기서 만든 요약값이다 — 연결 id는 그 연결의 업로드를 가르는 값이기 때문이다.

트래커만 있는 페이지(같은 연결에 `presence_join()`을 부르는 프로듀서가 없다)는 자기를 목록에 보이지만 다른 사람의
목록에는 오르지 않는다. 들어왔다고도 떠났다고도 알리지 않는 연결이라, 올리면 내릴 수 없다. 보는 사람도
목록에 올리려면 그 페이지에 프로듀서를 둔다.

롤링 배포로 이 동작 이전(1.5 이하)의 프로세스와 섞이면, 옛 프로세스의 메시지에는 `presence_ref`가 없다. 새
프로세스는 그런 메시지를 그 사용자의 연결 하나로 받는다. 옛 프로세스에 붙은 탭끼리는 예전처럼 하나로 셈해지고,
새 프로세스에 붙은 탭과는 따로 남는다. 할 일은 [업그레이드 가이드](../UPGRADING.md#15에서-16으로)에 있다.

## 커스텀 메타데이터

### 기본 정보 외 추가 데이터

```python
from django.utils import timezone


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

    def get_subscriptions(self) -> set[str]:
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
from wireview import PresenceConfig

class XChatInput(PresenceMixin, Component):
    class Meta:
        presence = PresenceConfig(
            typing_timeout=3.0,  # 3초 후 자동 해제
        )
```

### 긴 타임아웃

문서 편집 등 긴 작업:

```python
class XDocEditor(PresenceMixin, Component):
    class Meta:
        presence = PresenceConfig(
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
class Meta:
    presence = PresenceConfig(
        sync_on_join=True,  # 기본값
    )
```

### 동기화 흐름

1. User B 입장
2. User B가 `presence_sync_request` 브로드캐스트
3. 이미 있는 연결의 트래커가 요청 수신 → 자기 연결의 항목으로 `presence_sync_response` 전송.
   User A가 탭 둘을 열었으면 두 탭이 각각 답한다. 자기 프로듀서가 아직 입장을 알리지 않은 연결은 답하지 않는다
4. User B가 연결마다 받아 사용자 단위로 합친다. 이미 소식을 들은 연결의 답은 버린다 — 연결이 상태를 바꿀
   때마다 직접 알리므로, 요청이 큐에서 기다리는 동안 바뀐 상태를 옛 답이 덮지 않게 한다

## PresenceUser 객체

### 속성

`presence_users`·`presence_typing_users`·`presence_get_user()`가 돌려주는 것은 사용자의 연결들을 합친 항목이다
([탭이 여럿인 사용자](#탭이-여럿인-사용자)).

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

### 프로세스를 잇는 채널 레이어

Presence는 채널 레이어로 퍼진다. InMemory 레이어는 프로세스 하나 안에서만 통하므로, 서버 프로세스를 여러 개
띄우면 다른 프로세스에 붙은 사용자는 오류 없이 목록에서 빠진다. 여러 프로세스로 운영할 때는 프로세스를 잇는
레이어를 쓴다. 이 프로젝트가 겨냥하는 것은 [channels-nats](https://github.com/itda-work/channels-nats)이고,
Redis가 이미 있다면 channels_redis도 된다. 둘의 성능은 대등하다.

```python
import os

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_nats.NatsChannelLayer",
        "CONFIG": {"servers": [os.environ["NATS_URL"]]},
    }
}
```

설정과 선택 기준은 [배포 가이드의 채널 레이어](../DEPLOYMENT.md#채널-레이어)에 있다.

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
   def get_subscriptions(self) -> set[str]:
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
from enum import StrEnum

class UserStatus(StrEnum):
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
from datetime import UTC, datetime

from django.utils.timesince import timesince


class XOnlineUsers(PresenceTrackerMixin, Component):
    @property
    def last_seen(self) -> list[tuple]:
        """(사용자, "3 minutes") 목록. 템플릿은 인자를 받는 메서드를 부를 수 없으므로 property로 만든다"""
        return [(user, self._format_last_seen(user)) for user in self.presence_users]

    def _format_last_seen(self, user) -> str:
        # `_`가 없으면 클라이언트가 부를 수 있는 핸들러로 노출된다
        return timesince(datetime.fromtimestamp(user.last_active, tz=UTC))
```

템플릿은 property를 그대로 돈다:

```html
<ul>
  {% for user, ago in this.last_seen %}
    <li>{{ user.username }} · {{ ago }} 전</li>
  {% endfor %}
</ul>
```

## 다음 단계

[← 이전: Streams API 심화](06-streams-api.md) | [목차](README.md) | [다음: File Uploads 심화 →](08-file-uploads.md)
