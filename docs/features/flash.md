# 플래시와 토스트

> 동작하는 예제: [examples/notifications/](../../examples/notifications/) — 토스트를 다른 사용자에게 보낸다.

## 개요

화면 한쪽에 잠깐 떴다 사라지는 메시지는 누가 일으켰느냐로 둘로 나뉜다.

| | 플래시 | 토스트 |
|---|---|---|
| 일으키는 쪽 | 그 화면의 사용자 자신 ("저장했습니다") | 다른 곳 — 다른 사용자, 뷰, 백그라운드 작업 ("bob이 리뷰를 요청했습니다") |
| 경로 | 이벤트를 처리한 연결로 바로 | 받는 사람의 채널 → 레이아웃의 `{% wireview_toasts %}` → 그 연결 |
| 코드 | 핸들러에서 `await self.put_flash(...)` | 보내는 쪽 `toast(user, ...)`·`await atoast(user, ...)`, 받는 쪽은 레이아웃에 `{% wireview_toasts %}` 한 줄 |

**토스트는 다른 곳에서 보낸 플래시다.** 화면에 그리는 일은 `put_flash()` 하나가 하고, 토스트가 더하는 것은
"누구의 어느 연결로 가는가"뿐이다. 그래서 스타일·자동 닫힘·닫기 버튼이 둘 사이에 어긋나지 않는다.

둘 다 저장되지 않는다. 지금 열려 있는 페이지에만 뜨고, 나중에 연 페이지는 보지 못한다. 사용자가
나중에 확인해야 하는 것이면 모델에 저장하는 알림이 맞다 — 예제의 `Notification`이 그 모양이다.

## 작동 방식

`put_flash()`는 그 컴포넌트의 연결로 `flash` 메시지를 보낸다. 클라이언트는 페이지에서
`[wire-flash]` 요소를 찾아 그 안에 메시지 요소를 붙인다.

```html
<div wire-flash>
  <!-- put_flash()가 붙이는 요소 -->
  <div id="flash-…" class="wireview-flash wireview-flash-success wireview-flash-enter"
       role="alert" data-flash-type="success">
    <span class="wireview-flash-message">저장했습니다</span>
    <button type="button" class="wireview-flash-dismiss" aria-label="Dismiss">×</button>
  </div>
</div>
```

| 클래스 | 붙는 때 |
|--------|---------|
| `wireview-flash` | 항상 |
| `wireview-flash-<flash_type>` | `flash_type` 값 그대로 (`success`, `error`, `info`, `warning` 등) |
| `wireview-flash-enter` | 붙은 다음 프레임. 등장 애니메이션을 여기에 건다 |
| `wireview-flash-exit` | 사라지기 시작할 때. `animationend`에 지워지고, 애니메이션이 없어도 500ms 뒤 지워진다 |

wireview는 스타일을 제공하지 않는다. 위 클래스에 CSS를 건다.

## 사용법

### 플래시

```python
class ProductForm(Component):
    async def save(self):
        await Product.objects.acreate(name=self.name)
        await self.put_flash("success", "저장했습니다")
```

| 인자 | 기본값 | 뜻 |
|------|--------|----|
| `flash_type` | — | 클래스 이름에 들어가는 종류 |
| `message` | — | 텍스트. HTML로 해석하지 않는다 |
| `timeout` | `5000` | 자동으로 닫히는 시간(ms). `0`이면 닫지 않는다 |
| `dismissible` | `True` | 닫기 버튼을 붙인다 |

`await self.clear_flash()`는 떠 있는 것을 모두 닫는다. 플래시 하나하나의 id는 브라우저가 띄울 때 만들므로 서버는
그 id를 알지 못한다 — `clear_flash(flash_id)`는 브라우저 쪽에서 얻은 id를 받았을 때만 쓸 수 있다.

### 컨테이너

```html
<body>
  {% component 'Header' %}
  <div class="toasts" wire-flash></div>
  ...
</body>
```

**컨테이너는 컴포넌트 밖에 둔다.** 컴포넌트 템플릿 안에 두면 그 컴포넌트가 다시 렌더될 때 morph가
서버 HTML에 없는 메시지 요소를 지운다.

### 토스트

받는 쪽: 모든 페이지의 레이아웃에 `{% wireview_toasts %}`를 한 번 둔다. 보이지 않는 컴포넌트 하나가
그 연결의 사용자와 세션의 토스트 채널을 구독하고, 받은 것을 플래시로 띄운다.

```html
<body>
  <div class="toasts" wire-flash></div>
  {% wireview_toasts %}
  ...
</body>
```

보내는 쪽: 어디서든 받을 사람을 이름으로 부른다.

```python
from wireview import atoast, toast

# 뷰·시그널·관리 명령 같은 동기 코드. 트랜잭션이 커밋된 뒤에 나간다
toast(user, "다시 오신 것을 환영합니다")

# 컴포넌트 핸들러 같은 비동기 코드. 바로 나간다
await atoast(user, "지금 회의 들어와요", flash_type="warning", timeout=0)

# 로그인하지 않은 방문자: 세션 키로
toast(request.session.session_key, "장바구니에 담았습니다")
```

| 인자 | 기본값 | 뜻 |
|------|--------|----|
| 첫째 (받는 사람) | — | 저장된 사용자, 또는 세션 키 문자열. 세션이 아직 없으면(`None`, `""`) `ValueError` |
| `message` | — | 텍스트 |
| `flash_type`, `timeout`, `dismissible` | `"info"`, `5000`, `True` | `put_flash()`와 같다 |

**구독이 곧 접근 제어다.** 받는 컴포넌트는 자기 연결의 `user`와 세션의 채널만 구독하므로, 한 사람에게 보낸
토스트는 다른 사람의 페이지에 닿지 않는다. 채널 이름은 `toast_channel(user_or_session_key)`가 돌려준다 —
토스트를 자기 컴포넌트에서 직접 받고 싶으면 이 채널을 구독하고 `notification()`에서 `put_flash()`한다.
세션 키는 이름에 그대로 들어가지 않고 다이제스트로 들어간다. 세션 키는 그 세션의 자격 증명이고, `signed_cookies`
백엔드의 키는 `:`가 든 서명 쿠키 문자열이라 채널 레이어가 그룹 이름으로 받지 않는다. 사용자의 pk도 레이어가 받지 않는
문자(`@` 등)가 있으면 다이제스트가 된다. 이름의 모양은 공개가 아니니 늘 `toast_channel()`로 얻는다.

## 주의사항

- **`[wire-flash]`가 없으면 메시지는 버려진다.** 브라우저 콘솔에 경고 한 줄이 남을 뿐 서버는 모른다.
- **토스트는 `{% wireview_toasts %}`가 있는 열린 페이지에만 간다.** 그 태그가 없는 페이지, 닫힌 탭에는
  가지 않고 다시 오지도 않는다. 세션 키로 보낸 것은 그 세션이 연결될 때 이미 있던 키여야 한다 — 로그인하면
  Django가 세션 키를 바꾼다. `signed_cookies` 백엔드에서는 세션에 **무엇이든 쓸 때마다** 키가 바뀐다(키가 세션 내용의
  서명이다). 장바구니를 세션에 담고 `toast(request.session.session_key, …)`를 보내면 이미 열린 탭에는 닿지 않는다.
  세션 키 토스트가 필요하면 서버 저장형 백엔드를 쓴다. 같은 사용자의 탭이 여럿이면 모든 탭에 뜬다.
- **동기 `broadcast()`는 트랜잭션이 커밋된 뒤에 나간다.** 롤백되면 나가지 않는다.
- **리다이렉트 뒤에 보여줄 메시지는 플래시가 아니다.** 플래시는 연결에 가므로 전체 페이지 이동이
  끝나면 사라진다. 그 경우는 Django의 `messages` 프레임워크로 다음 페이지에 그린다.

## 관련 기능

- [튜토리얼: Notifications - 알림 센터](../tutorials/14-notifications.md) — 저장하는 알림과 토스트를 함께 쓴다
- [세션 읽기](./session.md) — 로그인하지 않은 방문자를 가리키는 값이 필요할 때
