# 14. Notifications - 알림 센터

> 동작하는 전체 코드: [examples/notifications/](../../examples/notifications/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 알림 센터를 만들며 broadcast와 JS 명령어를 학습합니다.

## 학습 목표

- `self.broadcast()`, 모듈 수준 `broadcast()` / `abroadcast()` 알림 전송
- `notification()` 훅 커스텀 이벤트 수신
- `push_js()` 고급 활용
- Streams API로 알림 목록 관리
- `JS()` 명령어 체이닝

## 완성 미리보기

실시간 알림 센터:
- 벨 아이콘에 읽지 않은 수 표시
- 드롭다운 토글 애니메이션
- 알림 추가/제거 실시간 반영
- 읽음 표시, 전체 삭제

## 1. 모델 정의

`notifications/models.py`:

```python
from django.db import models


class NotificationType(models.TextChoices):
    INFO = "info", "Information"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class Notification(models.Model):
    """알림"""
    title = models.CharField(max_length=100)
    message = models.TextField()
    type = models.CharField(
        max_length=20,
        choices=NotificationType.choices,
        default=NotificationType.INFO,
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
```

## 2. 벨 아이콘 컴포넌트

`notifications/live.py`:

```python
from wireview import Component, JS, ModelAction

from .models import Notification, NotificationType


class XNotificationBell(Component):
    """알림 벨 아이콘"""

    class Meta:
        template_name = "notifications/notification_bell.html"
        # 모델 채널 이름은 "<앱 label>.<모델 이름>" 소문자다
        subscriptions = {"notifications.notification", "notifications-refresh"}

    is_open: bool = False

    @property
    def unread_count(self):
        return Notification.objects.filter(is_read=False).count()

    async def toggle_dropdown(self):
        """드롭다운 토글 with 애니메이션"""
        self.is_open = not self.is_open

        if self.is_open:
            await self.push_js(
                JS().show(
                    f"#{self.id} .notification-dropdown",
                    transition=("fade-in", 200)
                )
            )
        else:
            await self.push_js(
                JS().hide(
                    f"#{self.id} .notification-dropdown",
                    transition=("fade-out", 150)
                )
            )

    async def notification(self, channel: str, **kwargs):
        """커스텀 broadcast 수신"""
        if channel == "notifications-refresh":
            self.force_render()

    async def mutation(self, channel, action, instance):
        """알림 변경 시 배지 업데이트"""
        self.force_render()
```

## 3. 알림 목록 컴포넌트

```python
class XNotificationList(Component):
    """알림 목록 (Streams 사용)"""

    class Meta:
        template_name = "notifications/notification_list.html"
        subscriptions = {"notifications.notification"}

    async def joined(self):
        """초기 알림 로드"""
        # QuerySet은 await할 수 없다. async for로 모은다
        notifications = [n async for n in Notification.objects.all()[:20]]
        await self.stream("notifications", notifications)

    async def mutation(self, channel, action, instance: Notification):
        """알림 변경 처리"""
        if action == ModelAction.CREATED:
            # 새 알림을 맨 위에 추가
            await self.stream_insert("notifications", instance, at=0)
            # 펄스 애니메이션. 시간은 문자열이 아니라 (클래스, 밀리초) 튜플로 준다
            await self.push_js(
                JS().transition(f"#notifications-{instance.id}", ("pulse", 500))
            )
        elif action == ModelAction.DELETED:
            await self.stream_delete("notifications", instance.id)

    async def dismiss(self, notification_id: int):
        """알림 삭제"""
        # 슬라이드 아웃 애니메이션
        await self.push_js(
            JS().transition(
                f"#notifications-{notification_id}",
                ("slide-out-right", 200)
            )
        )
        await Notification.objects.filter(id=notification_id).adelete()
        await self.broadcast("notifications-refresh")

    async def mark_as_read(self, notification_id: int):
        """읽음 표시"""
        await Notification.objects.filter(id=notification_id).aupdate(is_read=True)
        await self.push_js(
            JS().add_class(f"#notifications-{notification_id}", "is-read")
        )
        await self.broadcast("notifications-refresh")

    async def mark_all_read(self):
        """전체 읽음"""
        await Notification.objects.filter(is_read=False).aupdate(is_read=True)
        await self.push_js(
            JS().add_class(f"#{self.id} .notification-item", "is-read")
        )
        await self.broadcast("notifications-refresh")

    async def clear_all(self):
        """전체 삭제"""
        await Notification.objects.all().adelete()
        await self.stream("notifications", [])  # 목록 비우기
        await self.broadcast("notifications-refresh")
```

## 4. 핵심 개념: broadcast

### self.broadcast()와 모듈 수준 broadcast() / abroadcast()

```python
# 컴포넌트 메서드 안에서. async다
await self.broadcast("notifications-refresh")
```

컴포넌트 밖에서는 모듈 수준 함수를 쓴다. `self.abroadcast()`는 없다.

```python
from wireview import abroadcast, broadcast

# 동기 코드 (모델 시그널, 관리 명령 등)
broadcast("notifications-refresh")

# 비동기 코드
await abroadcast("notifications-refresh")
```

`aupdate()`·`abulk_create()` 같은 대량 쿼리는 `post_save`를 보내지 않으므로 모델 채널로 알림이 가지
않는다. 위 `mark_as_read`가 `notifications-refresh`를 직접 보내는 이유다.

### notification() 훅

```python
async def notification(self, channel: str, **kwargs):
    """커스텀 채널의 브로드캐스트 수신"""
    if channel == "notifications-refresh":
        self.force_render()
```

## 5. JS() 명령어 체이닝

```python
await self.push_js(
    JS()
    .hide("#modal")              # 모달 숨김
    .show("#success-message")    # 메시지 표시
    .transition("#btn", "pulse") # 애니메이션
    .focus("#next-input")        # 포커스 이동
)
```

### 주요 JS 명령어

| 명령어 | 설명 |
|--------|------|
| `show(sel, transition=)` | 요소 표시 |
| `hide(sel, transition=)` | 요소 숨김 |
| `toggle(sel)` | 토글 |
| `add_class(sel, cls)` | 클래스 추가 |
| `remove_class(sel, cls)` | 클래스 제거 |
| `toggle_class(sel, cls)` | 클래스 토글 |
| `transition(sel, effect)` | 애니메이션. 시간은 `(클래스, 밀리초)` 튜플 또는 `time=` |
| `set_value(sel, val)` | input 값 설정 |
| `focus(sel)` | 포커스 |
| `set_attr(sel, attr, val)` | 속성 설정 |
| `remove_attr(sel, attr)` | 속성 제거 |

## 6. 템플릿

`notifications/notification_list.html`:

```html
{% load wireview %}

<div {% tag_header %}>
  <div class="actions">
    <button {% on 'click' 'mark_all_read' %}>Mark all read</button>
    <button {% on 'click' 'clear_all' %}>Clear all</button>
  </div>

  {# 스트림 컨테이너는 비워 둔다. 항목은 stream()·stream_insert()가 채운다 #}
  <div class="notification-list" wire-stream="notifications"></div>
</div>
```

`notifications/notification_list_item.html` — 스트림 항목 템플릿이다. 기본 경로는 컴포넌트 템플릿 이름에
`_item`을 붙인 것이고, 항목은 `item`으로 들어온다. 항목 템플릿은 따로 렌더되므로 `{% load wireview %}`가
따로 필요하다. `id`는 적지 않아도 클라이언트가 `notifications-<pk>`로 붙인다 — 위 `JS()` 선택자가 그 id를 쓴다.

```html
{% load wireview %}
<div
  {% class {'notification-item': True, 'is-read': item.is_read} %}
  {% on 'click' 'mark_as_read' notification_id=item.id %}
>
  <div class="notification-icon {{ item.type }}">...</div>
  <div class="notification-content">
    <div class="notification-title">{{ item.title }}</div>
    <div class="notification-message">{{ item.message }}</div>
  </div>
  <button
    class="dismiss-btn"
    {% on 'click.stop' 'dismiss' notification_id=item.id %}
  >×</button>
</div>
```

## 7. 알림 생성 컴포넌트

```python
class XNotificationCreator(Component):
    """알림 생성 폼 (데모용)"""

    class Meta:
        template_name = "notifications/notification_creator.html"

    title: str = ""
    message: str = ""
    type: str = NotificationType.INFO

    async def set_title(self, title: str):
        self.title = title
        self.skip_render()  # 입력마다 다시 그릴 필요는 없다

    async def set_message(self, message: str):
        self.message = message
        self.skip_render()

    async def set_type(self, type: str):
        if type in NotificationType.values:
            self.type = type

    async def create(self):
        """알림 생성"""
        if not self.title.strip():
            return

        await Notification.objects.acreate(
            title=self.title.strip(),
            message=self.message.strip(),
            type=self.type,
        )

        # 폼 초기화
        self.title = ""
        self.message = ""
        self.type = NotificationType.INFO
        await self.push_js(
            JS()
            .set_value(f"#{self.id} input[name=title]", "")
            .set_value(f"#{self.id} textarea[name=message]", "")
            .focus(f"#{self.id} input[name=title]")
        )
```

`notifications/notification_creator.html`. 입력의 `name`이 핸들러 인자 이름과 같아야 값이 들어온다.

```html
{% load wireview %}

<div {% tag_header %}>
  <input type="text" name="title" value="{{ title }}" {% on 'input' 'set_title' %} />
  <textarea name="message" {% on 'input' 'set_message' %}>{{ message }}</textarea>
  <button type="button" {% on 'click' 'set_type' type='info' %}>Info</button>
  <button type="button" {% on 'click' 'set_type' type='warning' %}>Warning</button>
  <button type="button" {% cond {'disabled': not title} %} {% on 'click' 'create' %}>Create</button>
</div>
```

## 연습 문제

1. **알림 그룹**: 같은 유형 알림 그룹화
2. **자동 닫기**: 일정 시간 후 토스트 자동 닫기
3. **알림 필터**: 유형별 필터링

## 마무리

이것으로 wireview 튜토리얼 시리즈가 완료되었습니다!

학습한 내용:
- **초급**: 상태 관리, 이벤트, 렌더링 최적화
- **중급**: 모델 구독, 디바운스, 상태 머신
- **고급**: Streams, Presence, broadcast, JS 명령어

더 자세한 내용은 [심화 가이드](./06-streams-api.md)를 참고하세요.

---

[← 13. Quiz 앱](./13-quiz-app.md) | [목차](./README.md)
