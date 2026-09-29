# 14. Notifications - 알림 센터

> 동작하는 전체 코드: [examples/notifications/](../../examples/notifications/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 사용자마다 따로 받는 알림 센터를 만들며 채널과 broadcast, JS 명령어를 학습합니다.

## 학습 목표

- `get_subscriptions()`와 `self.user`로 **한 사용자에게만** 가는 채널 만들기
- 자동 브로드캐스트의 관계 채널(`{관계 모델}.{pk}.{related_name}`)
- 두 가지 알림: 저장하는 **알림**(`mutation()`)과 저장하지 않는 **토스트**(`notification()` + `put_flash()`)
- `self.broadcast()`, 모듈 수준 `broadcast()` / `abroadcast()`
- 브라우저가 보낸 id를 믿지 않는 핸들러
- Streams API와 `JS()` 명령어 체이닝

## 완성 미리보기

- alice와 bob이 각자 로그인한 창을 연다 (쿠키가 다른 창 — 한쪽은 시크릿 창)
- alice가 bob에게 **알림**을 보내면 bob의 목록과 벨 배지가 바뀌고, alice 쪽은 아무것도 바뀌지 않는다
- alice가 bob에게 **토스트**를 보내면 bob의 화면에 잠깐 떴다 사라진다. 어디에도 남지 않는다

## 두 가지 알림

| | 알림 (패턴 A) | 토스트 (패턴 B) |
|---|---|---|
| 저장 | DB 행 | 없음 |
| 나중에 연 페이지 | 목록에 보인다 | 보지 못한다 |
| 전달 | 행을 저장하면 자동 브로드캐스트가 알린다 → `mutation()` | 채널에 직접 브로드캐스트 → `notification()` |
| 화면 | Streams 목록, 벨 배지 | `put_flash()` |

토스트는 **다른 곳에서 보낸 플래시**다. 사용자가 자기 행동의 결과로 보는 메시지("저장했습니다")는
핸들러에서 `self.put_flash()`를 부르면 되고 채널이 필요 없다. 둘의 경계는
[플래시와 토스트](../features/flash.md)에 있다.

## 1. 모델

`notifications/models.py`:

```python
from django.conf import settings
from django.db import models


class NotificationType(models.TextChoices):
    INFO = "info", "Information"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class Notification(models.Model):
    """한 사용자의 알림. 읽거나 지울 때까지 남는다"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
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

`user` 외래 키가 알림을 사용자별로 만든다. 자동 브로드캐스트의 `related`를 켜면 행이 저장될 때 외래 키가
가리키는 쪽의 채널 `auth.user.{user_pk}.notifications`(`{관계 모델}.{pk}.{related_name}`)에도 알린다:

```python
WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True, related=True),
}
```

`model=True`는 모델 전체 채널 `notifications.notification`에도 알린다. 모든 사용자의 알림이 그리로
가므로 **사용자별 컴포넌트는 그 채널을 구독하지 않는다.**

## 2. 채널 이름과 보내는 함수

채널 이름은 문자열일 뿐이라 누구에게 가는지는 이름이 정한다. 받을 사람의 pk를 넣고, 이름은 한곳에서
만든다. `notifications/services.py`:

```python
from wireview import abroadcast, broadcast

from .models import Notification, NotificationType


def notifications_channel(user) -> str:
    # 정하는 이름이 아니라 자동 브로드캐스트가 쓰는 이름이다 (Notification.user의 관계 채널)
    return f"auth.user.{user.pk}.notifications"


def refresh_channel(user) -> str:
    # aupdate()는 시그널을 보내지 않으므로 자동 브로드캐스트가 모르는 변경용
    return f"notifications-refresh.user.{user.pk}"


def toast_channel(user) -> str:
    return f"toasts.user.{user.pk}"


def notify(user, title, message="", type=NotificationType.INFO):
    """알림 (패턴 A): 행을 저장하면 받는 사람의 열린 페이지가 알아서 듣는다"""
    return Notification.objects.create(user=user, title=title, message=message, type=type)


async def anotify(user, title, message="", type=NotificationType.INFO):
    return await Notification.objects.acreate(user=user, title=title, message=message, type=type)


def toast(user, message, type=NotificationType.INFO):
    """토스트 (패턴 B): 지금 열린 페이지에만 뜨고 저장하지 않는다. 동기 코드용"""
    broadcast(toast_channel(user), flash_type=type, message=message)


async def atoast(user, message, type=NotificationType.INFO):
    await abroadcast(toast_channel(user), flash_type=type, message=message)
```

뷰나 시그널에서도 같은 함수를 쓴다:

```python
from notifications.services import notify, toast


def approve_order(request, order_id):
    order = Order.objects.get(pk=order_id)
    order.status = "approved"
    order.save()
    notify(order.customer, "주문이 승인되었습니다", f"주문 #{order.pk}가 곧 배송됩니다.", "success")
    toast(request.user, "승인했습니다", "success")
    return redirect("orders:list")
```

동기 `broadcast()`는 트랜잭션이 커밋된 뒤에 나간다.

## 3. 벨 아이콘 컴포넌트

`notifications/live.py`:

```python
from wireview import Component, JS, ModelAction

from .models import Notification
from .services import notifications_channel, refresh_channel, toast_channel


class XNotificationBell(Component):
    """알림 벨 아이콘. 토스트도 여기서 받는다"""

    class Meta:
        template_name = "notifications/notification_bell.html"

    is_open: bool = False

    def get_subscriptions(self) -> set[str]:
        # 로그인한 사용자의 채널만. 익명 방문자는 아무것도 듣지 않는다
        if not self.user.is_authenticated:
            return set()
        return {notifications_channel(self.user), refresh_channel(self.user), toast_channel(self.user)}

    @property
    def unread_count(self):
        if not self.user.is_authenticated:
            return 0
        return Notification.objects.filter(user=self.user, is_read=False).count()

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

    async def mutation(self, channel, action, instance):
        """내 알림이 바뀌면 배지를 다시 센다"""
        self.force_render()

    async def notification(self, channel: str, **kwargs):
        if channel == toast_channel(self.user):
            # 토스트는 띄우고 잊는다. 벨 자체는 바뀌지 않았다
            await self.put_flash(kwargs["flash_type"], kwargs["message"])
            self.skip_render()
        elif channel == refresh_channel(self.user):
            self.force_render()
```

구독은 곧 접근 제어다. 벨은 `self.user`의 채널만 이름으로 부르므로 다른 사람에게 보낸 것은 이
연결에 오지 않는다. 벨이 모든 페이지의 헤더에 있으니 토스트를 받는 자리로도 알맞다.

## 4. 알림 목록 컴포넌트

```python
class XNotificationList(Component):
    """내 알림 목록 (Streams 사용)"""

    class Meta:
        template_name = "notifications/notification_list.html"

    def get_subscriptions(self) -> set[str]:
        if not self.user.is_authenticated:
            return set()
        return {notifications_channel(self.user)}

    def _mine(self):
        """내 알림. 모든 핸들러가 여기서 시작한다"""
        if not self.user.is_authenticated:
            return Notification.objects.none()
        return Notification.objects.filter(user=self.user)

    async def joined(self):
        # QuerySet은 await할 수 없다. async for로 모은다
        notifications = [n async for n in self._mine()[:20]]
        await self.stream("notifications", notifications)

    async def mutation(self, channel, action, instance: Notification):
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
        await self.push_js(
            JS().transition(
                f"#notifications-{notification_id}",
                ("slide-out-right", 200)
            )
        )
        # 삭제는 내 채널로 알려지므로 벨도 따로 알릴 필요가 없다
        await self._mine().filter(id=notification_id).adelete()

    async def mark_as_read(self, notification_id: int):
        """읽음 표시"""
        if not await self._mine().filter(id=notification_id).aupdate(is_read=True):
            return
        await self.push_js(
            JS().add_class(f"#notifications-{notification_id}", "is-read")
        )
        # aupdate()는 시그널을 보내지 않는다. 벨에 직접 알린다
        await self.broadcast(refresh_channel(self.user))

    async def mark_all_read(self):
        """전체 읽음"""
        if not await self._mine().filter(is_read=False).aupdate(is_read=True):
            return
        await self.push_js(
            JS().add_class(f"#{self.id} .notification-item", "is-read")
        )
        await self.broadcast(refresh_channel(self.user))

    async def clear_all(self):
        """전체 삭제"""
        await self._mine().adelete()
        await self.stream("notifications", [])  # 목록 비우기
```

### 브라우저가 보낸 id를 믿지 않는다

`dismiss(notification_id=...)`의 id는 브라우저가 보낸다. 화면에는 내 알림만 있지만, 브라우저는 아무
id나 보낼 수 있다. `Notification.objects.filter(id=notification_id)`로 지우면 남의 알림도 지워진다.
그래서 모든 핸들러가 `_mine()`, 곧 **소유자로 거른 쿼리에서 시작한다.** 채널을 사용자별로 나눈 것은
"누가 무엇을 듣는가"이고, 이것은 "누가 무엇을 바꾸는가"다. 둘은 따로 지켜야 한다.

## 5. 핵심 개념: broadcast

### self.broadcast()와 모듈 수준 broadcast() / abroadcast()

```python
# 컴포넌트 메서드 안에서. async다
await self.broadcast(refresh_channel(self.user))
```

컴포넌트 밖에서는 모듈 수준 함수를 쓴다. `self.abroadcast()`는 없다.

```python
from wireview import abroadcast, broadcast

# 동기 코드 (뷰, 모델 시그널, 관리 명령 등)
broadcast(toast_channel(user), flash_type="info", message="다시 오신 것을 환영합니다")

# 비동기 코드
await abroadcast(toast_channel(user), flash_type="info", message="다시 오신 것을 환영합니다")
```

`aupdate()`·`abulk_create()` 같은 대량 쿼리는 `post_save`를 보내지 않으므로 모델 채널로 알림이 가지
않는다. 위 `mark_as_read`가 `refresh_channel`로 직접 보내는 이유다.

### notification() 훅

`broadcast()`의 키워드 인자가 그대로 `**kwargs`로 온다. 한 컴포넌트가 여러 채널을 들으면 `channel`로
가른다.

```python
async def notification(self, channel: str, **kwargs):
    if channel == toast_channel(self.user):
        await self.put_flash(kwargs["flash_type"], kwargs["message"])
```

## 6. 토스트를 띄울 자리

`put_flash()`는 페이지의 `[wire-flash]` 요소에 메시지를 붙인다. 이 요소는 **컴포넌트 밖**에 둔다.
컴포넌트 안에 두면 그 컴포넌트가 다시 렌더될 때 morph가 서버 HTML에 없는 메시지를 지운다.

`notifications/base.html`:

```html
<body>
  <header class="header">
    <h1>Notification Center Demo</h1>
    {% if user.is_authenticated %}
      {% component 'XNotificationBell' id="bell" %}
    {% endif %}
  </header>
  <div class="toasts" wire-flash></div>
  <main class="main">{% block content %}{% endblock %}</main>
</body>
```

모양은 `.wireview-flash`, `.wireview-flash-<종류>` 같은 클래스에 CSS로 준다. 클래스 목록은
[플래시와 토스트](../features/flash.md#작동-방식)에 있다.

## 7. JS() 명령어 체이닝

```python
await self.push_js(
    JS()
    .hide("#modal")                        # 모달 숨김
    .show("#success-message")              # 메시지 표시
    .transition("#btn", ("pulse", 300))    # 애니메이션
    .focus("#next-input")                  # 포커스 이동
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

## 8. 템플릿

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

## 9. 보내는 컴포넌트

받는 사람을 고르고 알림이나 토스트를 보내는 데모용 폼이다.

```python
from django.contrib.auth import get_user_model

from .services import anotify, atoast


class XNotificationCreator(Component):
    """알림·토스트 보내기 (데모용)"""

    class Meta:
        template_name = "notifications/notification_creator.html"

    recipient: str = ""  # 비우면 자기 자신
    title: str = ""
    message: str = ""
    type: str = NotificationType.INFO

    @property
    def usernames(self) -> list[str]:
        return list(get_user_model().objects.order_by("username").values_list("username", flat=True))

    @property
    def can_send(self) -> bool:
        return bool(self.title.strip())

    async def _recipient(self):
        if not self.recipient:
            return self.user if self.user.is_authenticated else None
        return await get_user_model().objects.filter(username=self.recipient).afirst()

    async def set_recipient(self, recipient: str):
        self.recipient = recipient

    async def set_title(self, title: str):
        could_send = self.can_send
        self.title = title
        # 입력란은 이미 친 글자를 보여준다. 대부분의 키 입력은 다시 그릴 필요가 없지만,
        # 보내기 버튼이 활성으로 바뀌는 입력은 그려야 한다
        if self.can_send == could_send:
            self.skip_render()

    async def set_message(self, message: str):
        self.message = message
        self.skip_render()

    async def set_type(self, type: str):
        if type in NotificationType.values:
            self.type = type

    async def create(self):
        """알림 (패턴 A)"""
        if not self.can_send:
            return
        recipient = await self._recipient()
        if recipient is None:
            await self.put_flash("error", "Choose who gets it.")
            return

        await anotify(recipient, self.title.strip(), self.message.strip(), self.type)

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

    async def send_toast(self):
        """토스트 (패턴 B). 아무것도 저장하지 않는다"""
        if not self.can_send:
            return
        recipient = await self._recipient()
        if recipient is None:
            await self.put_flash("error", "Choose who gets it.")
            return

        await atoast(recipient, self.title.strip(), self.type)
        self.skip_render()  # 보낸 쪽 폼은 바뀌지 않았다
```

`skip_render()`는 조심해서 쓴다. 이 폼의 버튼은 `{% cond {'disabled': not this.can_send} %}`로 제목이
있을 때만 켜지는데, `set_title`이 매번 렌더를 건너뛰면 버튼은 처음 그려진 비활성 상태로 남는다.
건너뛸 수 있는 것은 **화면이 달라지지 않는** 렌더뿐이다.

`notifications/notification_creator.html`. 입력의 `name`이 핸들러 인자 이름과 같아야 값이 들어온다.

```html
{% load wireview %}

<div {% tag_header %}>
  <select name="recipient" {% on 'change' 'set_recipient' %}>
    <option value="">Me</option>
    {% for username in this.usernames %}
      {% if username != this.user.username %}<option value="{{ username }}">{{ username }}</option>{% endif %}
    {% endfor %}
  </select>
  <input type="text" name="title" value="{{ title }}" {% on 'input' 'set_title' %} />
  <textarea name="message" {% on 'input' 'set_message' %}>{{ message }}</textarea>
  <button type="button" {% on 'click' 'set_type' type='info' %}>Info</button>
  <button type="button" {% on 'click' 'set_type' type='warning' %}>Warning</button>
  <button type="button" {% cond {'disabled': not this.can_send} %} {% on 'click' 'create' %}>Send Notification</button>
  <button type="button" {% cond {'disabled': not this.can_send} %} {% on 'click' 'send_toast' %}>Send Toast</button>
</div>
```

## 10. 테스트

사용자별 채널이 정말 사용자를 가르는지는 `mount(..., user=...)`로 확인한다.

```python
import pytest
from django.contrib.auth import get_user_model

from wireview import mount

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def alice():
    return get_user_model().objects.create(username="alice")


@pytest.fixture
def bob():
    return get_user_model().objects.create(username="bob")


@pytest.mark.asyncio
async def test_dismissing_deletes_only_the_users_own(alice, bob):
    theirs = await Notification.objects.acreate(user=bob, title="남의 것", message="")
    view = await mount(XNotificationList, user=alice)

    await view.call("dismiss", notification_id=theirs.pk)

    assert await Notification.objects.filter(pk=theirs.pk).aexists()
```

핸들러의 ORM 호출은 테스트와 다른 스레드의 연결에서 돈다. `transaction=True`가 아니면 픽스처가 쓴 행을
핸들러가 보지 못하거나, SQLite의 쓰기 잠금을 기다리다 멈춘다. 채널이 실제로 한 사람에게만 가는지,
브라우저 두 개에서 어떻게 보이는지는 예제의 `tests.py`에 있다.

## 연습 문제

1. **알림 그룹**: 같은 유형 알림 그룹화
2. **로그인 환영 토스트**: `user_logged_in` 시그널에서 `toast()`를 보내 보고, 왜 뜨지 않는지 설명하기
   (힌트: 그 순간 열린 페이지가 있는가)
3. **알림 필터**: 유형별 필터링

## 마무리

이것으로 wireview 튜토리얼 시리즈가 완료되었습니다!

학습한 내용:
- **초급**: 상태 관리, 이벤트, 렌더링 최적화
- **중급**: 모델 구독, 디바운스, 상태 머신
- **고급**: Streams, Presence, 사용자별 채널, broadcast, JS 명령어

더 자세한 내용은 [심화 가이드](./06-streams-api.md)를 참고하세요.

---

[← 13. Quiz 앱](./13-quiz-app.md) | [목차](./README.md)
