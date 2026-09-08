# 14. Notifications - 알림 센터

> 동작하는 전체 코드: [examples/notifications/](../../examples/notifications/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 알림 센터를 만들며 broadcast와 JS 명령어를 학습합니다.

## 학습 목표

- `broadcast()` / `abroadcast()` 알림 전송
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
from wireview.component import Component
from wireview.js import JS
from wireview.schemas import ModelAction

from .models import Notification


class XNotificationBell(Component):
    """알림 벨 아이콘"""

    _template_name = "notifications/notification_bell.html"
    _subscriptions = {"notification", "notifications-refresh"}

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
                    transition="fade-in 200ms"
                )
            )
        else:
            await self.push_js(
                JS().hide(
                    f"#{self.id} .notification-dropdown",
                    transition="fade-out 150ms"
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

    _template_name = "notifications/notification_list.html"
    _subscriptions = {"notification"}

    async def joined(self):
        """초기 알림 로드"""
        notifications = list(await Notification.objects.all()[:20])
        await self.stream("notifications", notifications)

    async def mutation(self, channel, action, instance: Notification):
        """알림 변경 처리"""
        if action == ModelAction.CREATED:
            # 새 알림을 맨 위에 추가
            await self.stream_insert("notifications", instance, at=0)
            # 펄스 애니메이션
            await self.push_js(
                JS().transition(f"#notifications-{instance.id}", "pulse 500ms")
            )
        elif action == ModelAction.DELETED:
            await self.stream_delete("notifications", instance.id)

    async def dismiss(self, notification_id: int):
        """알림 삭제"""
        # 슬라이드 아웃 애니메이션
        await self.push_js(
            JS().transition(
                f"#notifications-{notification_id}",
                "slide-out-right 200ms"
            )
        )
        await Notification.objects.filter(id=notification_id).adelete()
        await self.abroadcast("notifications-refresh")

    async def mark_as_read(self, notification_id: int):
        """읽음 표시"""
        await Notification.objects.filter(id=notification_id).aupdate(is_read=True)
        await self.push_js(
            JS().add_class(f"#notifications-{notification_id}", "is-read")
        )
        await self.abroadcast("notifications-refresh")

    async def mark_all_read(self):
        """전체 읽음"""
        await Notification.objects.filter(is_read=False).aupdate(is_read=True)
        await self.push_js(
            JS().add_class(f"#{self.id} .notification-item", "is-read")
        )
        await self.abroadcast("notifications-refresh")

    async def clear_all(self):
        """전체 삭제"""
        await Notification.objects.all().adelete()
        await self.stream("notifications", [])  # 목록 비우기
        await self.abroadcast("notifications-refresh")
```

## 4. 핵심 개념: broadcast

### broadcast() vs abroadcast()

```python
# 동기 (모델 시그널 등에서)
self.broadcast("notifications-refresh")

# 비동기 (async 메서드에서)
await self.abroadcast("notifications-refresh")
```

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
| `transition(sel, effect)` | 애니메이션 |
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

  <div class="notification-list" wire-stream="notifications">
    {% for notification in notifications %}
      {% include "notifications/notification_list_item.html" %}
    {% endfor %}
  </div>
</div>
```

`notifications/notification_list_item.html`:

```html
<div
  id="notifications-{{ notification.id }}"
  {% class {'notification-item': True, 'is-read': notification.is_read} %}
  {% on 'click' 'mark_as_read' notification_id=notification.id %}
>
  <div class="notification-icon {{ notification.type }}">...</div>
  <div class="notification-content">
    <div class="notification-title">{{ notification.title }}</div>
    <div class="notification-message">{{ notification.message }}</div>
  </div>
  <button
    class="dismiss-btn"
    {% on 'click.stop' 'dismiss' notification_id=notification.id %}
  >×</button>
</div>
```

## 7. 알림 생성 컴포넌트

```python
class XNotificationCreator(Component):
    """알림 생성 폼 (데모용)"""

    _template_name = "notifications/notification_creator.html"

    title: str = ""
    message: str = ""
    type: str = "info"

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
        await self.push_js(
            JS()
            .set_value(f"#{self.id} input[name=title]", "")
            .set_value(f"#{self.id} textarea[name=message]", "")
            .focus(f"#{self.id} input[name=title]")
        )
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
