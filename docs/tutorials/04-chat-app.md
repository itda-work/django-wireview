# 04. Chat 앱

> 동작하는 전체 코드: [examples/chat/](../../examples/chat/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 실시간 채팅 앱을 만들며 Streams API와 Presence API를 학습합니다.

## 학습 목표

- Streams API로 대규모 리스트 효율적 처리
- Presence API로 온라인 사용자 추적
- 타이핑 표시 구현
- `leaving()` 라이프사이클 훅 활용
- `push_js()`로 클라이언트 사이드 명령 전송

## 완성 앱 미리보기

- 실시간 메시지 전송/수신
- 온라인 사용자 목록
- 타이핑 표시 ("User is typing...")
- 새 메시지 시 자동 스크롤
- 메모리 효율적 메시지 리스트

## Part 1: 기본 설정

### 모델 정의

`chat/models.py`:

```python
from django.db import models


class Room(models.Model):
    """채팅방"""
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Message(models.Model):
    """채팅 메시지"""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=100)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender}: {self.text[:50]}"
```

### 기본 구조

앱 구조:
```
chat/
├── models.py
├── live.py
├── views.py
├── urls.py
└── templates/chat/
    ├── room.html           # 페이지 템플릿
    ├── room_component.html # 채팅방 컴포넌트
    ├── message_item.html   # 메시지 아이템
    └── online_users.html   # 온라인 사용자 목록
```

## Part 2: Streams API - 메시지 리스트

### 왜 Streams를 사용하나요?

일반 리스트 렌더링:
- 전체 리스트를 상태에 저장
- 변경 시 전체 HTML 다시 렌더링
- 메모리 사용량 증가

Streams:
- 아이템 단위로 독립적 렌더링
- 변경된 아이템만 전송
- 메모리 효율적

### 메시지 리스트 컴포넌트

`chat/live.py`:

```python
from wireview import Component
from .models import Room, Message


class XMessageList(Component):
    """메시지 리스트 컴포넌트 - Streams API 사용"""

    class Meta:
        template_name = 'chat/message_list.html'

    room_id: int

    async def joined(self):
        """컴포넌트 연결 시 메시지 로드"""
        messages = await self._load_messages()
        # stream()으로 초기화 - 아이템별로 렌더링
        await self.stream("messages", messages, template="chat/message_item.html")

    async def _load_messages(self, limit: int = 50):
        """최근 메시지 로드"""
        messages = Message.objects.filter(
            room_id=self.room_id
        ).order_by('-created_at')[:limit]
        # 역순으로 반환 (오래된 것부터)
        return list(reversed([m async for m in messages]))
```

### 메시지 리스트 템플릿

`chat/templates/chat/message_list.html`:

```html
{% load wireview %}
<div {% tag_header %} class="message-list">
  <ul wire-stream="messages"></ul>
</div>
```

**핵심**: `wire-stream="messages"` 속성이 stream 컨테이너를 지정합니다. 항목은 컴포넌트 상태가 아니므로
템플릿이 반복문으로 그리지 않는다. `joined()`의 `stream()`이 이 컨테이너를 채운다.

### 메시지 아이템 템플릿

`chat/templates/chat/message_item.html`:

```html
<li id="messages-{{ item.pk }}" class="message">
  <span class="sender">{{ item.sender }}</span>
  <span class="text">{{ item.text }}</span>
  <span class="time">{{ item.created_at|time:"H:i" }}</span>
</li>
```

**핵심**: `id="messages-{{ item.pk }}"` - stream 이름과 pk로 DOM ID 생성

항목 템플릿이 받는 컨텍스트는 `item`(그 항목)과 `this`(스트림을 보낸 컴포넌트) 둘뿐이다. `template=`을 생략하면
`{컴포넌트 템플릿}_item.html`(여기서는 `chat/message_list_item.html`)을 찾는다. 이 튜토리얼은 뒤의
`XChatRoom`도 같은 항목 템플릿을 쓰므로 매번 `template=`으로 지정한다.

### 메시지 추가

```python
class XMessageList(Component):
    # ... 기존 코드 ...

    async def add_message(self, sender: str, text: str):
        """새 메시지 추가"""
        message = await Message.objects.acreate(
            room_id=self.room_id,
            sender=sender,
            text=text.strip()
        )

        # stream_insert로 아이템 추가 (append)
        await self.stream_insert("messages", message, at=-1, template="chat/message_item.html")

        # 새 메시지로 스크롤
        await self.scroll_into_view(f"messages-{message.pk}", behavior="smooth")
```

### Stream 메서드 정리

| 메서드 | 용도 | at 파라미터 |
|--------|------|-------------|
| `stream(name, items, template=None)` | 초기화/리셋 | - |
| `stream_insert(name, item, at=-1, template=None)` | 삽입 | -1: 끝, 0: 처음, n: 인덱스 |
| `stream_delete(name, dom_id)` | 삭제 | - |

## Part 3: Presence API - 온라인 사용자

### Presence 개념

- **PresenceMixin**: 자신의 상태를 브로드캐스트 (Producer)
- **PresenceTrackerMixin**: 다른 사용자 상태 추적 (Consumer)

### 채팅방 컴포넌트 (Producer)

```python
from wireview import Component, PresenceMixin, JS
from .models import Room, Message


class XChatRoom(PresenceMixin, Component):
    """채팅방 메인 컴포넌트"""

    class Meta:
        template_name = 'chat/room_component.html'

    room_id: int
    room_name: str
    username: str

    # Presence 설정
    def _presence_topic(self) -> str:
        return f"chat.room.{self.room_id}"

    def _presence_user_id(self) -> str:
        return self.username

    def _presence_username(self) -> str:
        return self.username

    async def joined(self):
        """입장 알림"""
        await self.presence_join()

    async def leaving(self):
        """퇴장 알림 - 연결 종료 시 자동 호출"""
        await self.presence_leave()

    async def send_message(self, text: str):
        """메시지 전송"""
        if not text.strip():
            return

        await Message.objects.acreate(
            room_id=self.room_id,
            sender=self.username,
            text=text.strip()
        )

        # 입력 필드 초기화
        await self.push_js(JS().set_value("input[name=text]", ""))

    async def on_typing(self):
        """타이핑 표시 - 자동으로 3초 후 해제"""
        await self.presence_set_typing(True)
```

### 온라인 사용자 컴포넌트 (Consumer)

```python
from wireview import PresenceTrackerMixin


class XOnlineUsers(PresenceTrackerMixin, Component):
    """온라인 사용자 목록 컴포넌트"""

    class Meta:
        template_name = 'chat/online_users.html'

    room_id: int
    username: str  # 현재 사용자 (자기 자신 식별용)

    def _presence_topic(self) -> str:
        return f"chat.room.{self.room_id}"

    def _presence_my_user_id(self) -> str:
        return self.username

    def get_subscriptions(self) -> set[str]:
        # Presence 채널 구독
        return {self._presence_channel()}

    async def joined(self):
        # 자기 자신을 추적 목록에 추가하고 다른 사용자에게 sync 요청
        await self.presence_track_self(username=self.username)
```

### 온라인 사용자 템플릿

`chat/templates/chat/online_users.html`:

```html
{% load wireview %}
<div {% tag_header %} class="online-users">
  <h3>Online ({{ this.presence_online_count }})</h3>

  <ul>
    {% for user in this.presence_users %}
      <li class="{% if user.is_typing %}typing{% endif %}">
        <span class="username">{{ user.username }}</span>
        {% if user.is_typing %}
          <span class="typing-indicator">typing...</span>
        {% endif %}
      </li>
    {% empty %}
      <li class="empty">No one online</li>
    {% endfor %}
  </ul>
</div>
```

### Presence 속성/메서드 정리

**PresenceMixin (Producer)**:
| 메서드 | 용도 |
|--------|------|
| `presence_join()` | 입장 알림 |
| `presence_leave()` | 퇴장 알림 |
| `presence_set_typing(bool)` | 타이핑 상태 설정 |

**PresenceTrackerMixin (Consumer)**:
| 속성/메서드 | 용도 |
|-------------|------|
| `presence_users` | 모든 사용자 리스트 |
| `presence_online_count` | 온라인 사용자 수 |
| `presence_typing_users` | 타이핑 중인 사용자 |
| `presence_track_self(username)` | 자기 자신 등록 |

## Part 4: 완성된 코드

### 전체 live.py

```python
from wireview import Component, PresenceMixin, PresenceTrackerMixin, JS
from .models import Room, Message


class XChatRoom(PresenceMixin, Component):
    """채팅방 메인 컴포넌트"""

    class Meta:
        template_name = 'chat/room_component.html'

    room_id: int
    room_name: str
    username: str

    def _presence_topic(self) -> str:
        return f"chat.room.{self.room_id}"

    def _presence_user_id(self) -> str:
        return self.username

    def _presence_username(self) -> str:
        return self.username

    async def joined(self):
        # 메시지 로드
        messages = await self._load_messages()
        await self.stream("messages", messages, template="chat/message_item.html")

        # 입장 알림
        await self.presence_join()

    async def leaving(self):
        await self.presence_leave()

    async def _load_messages(self, limit: int = 50):
        messages = Message.objects.filter(
            room_id=self.room_id
        ).order_by('-created_at')[:limit]
        return list(reversed([m async for m in messages]))

    async def send_message(self, text: str):
        if not text.strip():
            return

        message = await Message.objects.acreate(
            room_id=self.room_id,
            sender=self.username,
            text=text.strip()
        )

        await self.stream_insert("messages", message, at=-1, template="chat/message_item.html")
        await self.scroll_into_view(f"messages-{message.pk}", behavior="smooth")
        await self.push_js(JS().set_value("input[name=text]", ""))

    async def on_typing(self):
        await self.presence_set_typing(True)


class XOnlineUsers(PresenceTrackerMixin, Component):
    """온라인 사용자 목록"""

    class Meta:
        template_name = 'chat/online_users.html'

    room_id: int
    username: str

    def _presence_topic(self) -> str:
        return f"chat.room.{self.room_id}"

    def _presence_my_user_id(self) -> str:
        return self.username

    def get_subscriptions(self) -> set[str]:
        return {self._presence_channel()}

    async def joined(self):
        await self.presence_track_self(username=self.username)
```

### room_component.html

```html
{% load wireview %}
<div {% tag_header %} class="chat-room">
  <header class="chat-header">
    <h2>{{ room_name }}</h2>
  </header>

  <div class="chat-body">
    <div class="messages-container">
      <ul wire-stream="messages" class="message-list"></ul>
    </div>

    <aside class="sidebar">
      {% component 'XOnlineUsers' room_id=room_id username=username %}
    </aside>
  </div>

  <footer class="chat-footer">
    <form class="message-form">
      <input
        type="text"
        name="text"
        placeholder="Type a message..."
        autocomplete="off"
        {% on "input.debounce.100" "on_typing" %}
        {% on "keypress.enter.prevent" "send_message" %}
      >
      <button type="button" {% on "click" "send_message" %}>Send</button>
    </form>
  </footer>
</div>
```

### message_item.html

```html
<li id="messages-{{ item.pk }}" class="message {% if item.sender == this.username %}own{% endif %}">
  <div class="message-content">
    <span class="sender">{{ item.sender }}</span>
    <p class="text">{{ item.text }}</p>
    <span class="time">{{ item.created_at|time:"H:i" }}</span>
  </div>
</li>
```

컴포넌트 필드는 `this`로 읽는다. 스트림 항목의 컨텍스트에 `username`은 없다.

### online_users.html

```html
{% load wireview %}
<div {% tag_header %} class="online-users">
  <h3>
    <span class="status-dot"></span>
    Online ({{ this.presence_online_count }})
  </h3>

  <ul class="user-list">
    {% for user in this.presence_users %}
      <li class="user {% if user.is_typing %}is-typing{% endif %}">
        <span class="avatar">{{ user.username|slice:":1"|upper }}</span>
        <span class="name">{{ user.username }}</span>
        {% if user.is_typing %}
          <span class="typing-dots">
            <span></span><span></span><span></span>
          </span>
        {% endif %}
      </li>
    {% endfor %}
  </ul>
</div>
```

### CSS 예제

```css
.chat-room {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.chat-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.message-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.message {
  margin-bottom: 1rem;
  padding: 0.5rem 1rem;
  background: #f0f0f0;
  border-radius: 8px;
  max-width: 70%;
}

.message.own {
  background: #007bff;
  color: white;
  margin-left: auto;
}

.sidebar {
  width: 200px;
  border-left: 1px solid #ddd;
  padding: 1rem;
}

.chat-footer {
  padding: 1rem;
  border-top: 1px solid #ddd;
}

.message-form {
  display: flex;
  gap: 0.5rem;
}

.message-form input {
  flex: 1;
  padding: 0.5rem;
}

/* 타이핑 애니메이션 */
.typing-dots span {
  animation: typing 1s infinite;
  display: inline-block;
  width: 4px;
  height: 4px;
  background: #666;
  border-radius: 50%;
  margin: 0 1px;
}

.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}
```

## Part 5: 고급 기능

### 메시지 실시간 수신 (다른 사용자)

모델 구독을 추가해 다른 사용자의 메시지도 실시간 수신한다. 자동 브로드캐스트는 `Message`가 저장될 때
`chat.message`(모델 전체) 말고도 외래 키가 가리키는 쪽의 채널 `chat.room.{room_pk}.messages`
(`{관계 모델}.{pk}.{related_name}`)에 알린다. 이 채널을 쓰려면 설정에서 `related`를 켠다:

```python
WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True, related=True, senders={("chat", "Message")}),
}
```

```python
from wireview import ModelAction


class XChatRoom(PresenceMixin, Component):
    # ...

    def get_subscriptions(self) -> set[str]:
        # 이 방의 메시지만 받는다
        return super().get_subscriptions() | {f"chat.room.{self.room_id}.messages"}

    async def mutation(self, channel: str, action: ModelAction, instance):
        """다른 사용자의 메시지 수신 (내 메시지는 send_message가 이미 넣었다)"""
        if action == ModelAction.CREATED and instance.sender != self.username:
            await self.stream_insert("messages", instance, at=-1, template="chat/message_item.html")
            await self.scroll_into_view(f"messages-{instance.pk}", behavior="smooth")
```

### 메시지 로드 더 보기

처음 불러온 메시지 중 가장 오래된 것이 기준점이다. `joined()`에서 기록해 두지 않으면 첫 "더 보기"가
이미 보이는 최신 메시지를 다시 불러온다.

```python
class XChatRoom(PresenceMixin, Component):
    # ...
    oldest_message_id: int | None = None
    has_more: bool = True

    async def joined(self):
        messages = await self._load_messages()
        await self.stream("messages", messages, template="chat/message_item.html")
        if messages:
            self.oldest_message_id = messages[0].id
        self.has_more = len(messages) == 50
        await self.presence_join()

    async def load_more(self):
        """이전 메시지 로드"""
        if not self.has_more or self.oldest_message_id is None:
            return

        qs = Message.objects.filter(room_id=self.room_id, id__lt=self.oldest_message_id)
        # QuerySet은 await할 수 없다. async for로 모은다. 최신 것부터 20개
        messages = [m async for m in qs.order_by('-id')[:20]]

        if len(messages) < 20:
            self.has_more = False

        # 최신 것부터 하나씩 맨 앞에 붙이면 가장 오래된 것이 맨 위에 온다
        for message in messages:
            await self.stream_insert("messages", message, at=0, template="chat/message_item.html")
        if messages:
            self.oldest_message_id = messages[-1].id
```

## 연습 문제

1. **메시지 삭제**: 자신의 메시지를 삭제하는 기능 추가
2. **이미지 전송**: 이미지 URL을 감지해 미리보기 표시
3. **읽음 표시**: 메시지 읽음 상태 추적
4. **다이렉트 메시지**: 1:1 채팅 기능

## 다음 단계

Chat 앱을 통해 Streams API와 Presence API의 핵심 개념을 학습했습니다.

다음 튜토리얼에서는 AsyncResult를 사용한 비동기 데이터 로딩과 복합 컴포지션을 배워봅니다.

[← 이전: 13. Quiz 앱](13-quiz-app.md) | [목차](README.md) | [다음: 05. Dashboard →](05-dashboard.md)
