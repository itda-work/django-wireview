# live_session 설계 (GAP-009, #58)

> 상태: 설계 초안. 2026-09-09. 구현 착수 전 합의용.
> 선행: [#75](https://github.com/itda-work/django-wireview/issues/75) (`_on_mount`가 호출되지 않는다), [#68](https://github.com/itda-work/django-wireview/issues/68) (세션 접근)

## 0. 먼저 확인된 사실

**`_on_mount` 훅은 지금 실행되지 않는다.** `_run_on_mount_hooks()`는 정의만 있고 호출부가 없다(#75).
`docs/features/lifecycle-hooks.md`가 인증 훅을 첫 예제로 드는데, 그 코드를 쓴 사람은 인증이
걸린 줄 알지만 아무 검사도 일어나지 않는다. **이 문서의 설계는 #75가 고쳐진 뒤에만 의미가 있다.**

## 1. 무엇을 푸는 문제인가

Phoenix의 `live_session`은 라우터에서 LiveView 묶음에 이름을 붙이고 두 가지를 준다.

1. **선언의 위치.** `on_mount` 훅을 뷰마다 쓰지 않고 라우팅 계층에서 한 번 선언한다.
2. **경계.** 같은 live_session 안에서는 live navigation이 연결을 유지한 채 일어나고, 경계를
   넘으면 **전체 페이지 로드**가 강제된다. 이것이 보안 성질이다 — 인증 가정이 바뀌는 지점에서
   반드시 새 요청과 새 핸드셰이크를 타게 만든다.

wireview에서 1번은 `_on_mount`가 (고쳐지면) 이미 담당한다. 그러나 **컴포넌트마다 붙인다.**
한 페이지에 컴포넌트가 다섯이면 다섯 곳에 같은 목록을 적어야 하고, 하나를 빠뜨려도 아무 신호가 없다.

2번은 wireview에 대응물이 아예 없다.

## 2. wireview의 구조가 Phoenix와 다른 지점

이 차이를 무시하고 Phoenix를 그대로 옮기면 맞지 않는다.

| | Phoenix | wireview |
|---|---|---|
| 라우팅 단위 | LiveView가 곧 라우트 | Django 뷰가 라우트, 컴포넌트는 페이지에 **박히는 조각** |
| 한 페이지의 LiveView 수 | 보통 하나(+ 중첩) | 여럿이 기본 |
| 내비게이션 | live navigation이 LiveView를 교체 | boost가 **body를 morph**하고 컴포넌트가 leave/join |
| 연결 수명 | LiveView 수명과 사실상 같다 | 연결은 페이지보다 오래 산다(boost 이동을 건너 유지) |

즉 wireview의 "경계"는 LiveView 사이가 아니라 **페이지(Django 뷰) 사이**에 그어져야 한다.

## 3. 제안

### 3-1. 정책은 페이지가 선언한다

`live_session`은 이름 붙은 정책이다. 훅 목록과, 그 경계 안에 들어올 수 있는 조건을 담는다.

```python
# myapp/live_sessions.py
from wireview import live_session

admin = live_session("admin", on_mount=[RequireStaff, AuditTrail])
public = live_session("public", on_mount=[])
```

뷰가 어느 세션에 속하는지 선언한다. 데코레이터와 클래스 속성 둘 다 받는다.

```python
@admin.view
def dashboard(request):
    return render(request, "admin/dashboard.html")
```

렌더된 페이지는 `{% wireview_header %}`가 세션 이름과 서명을 메타로 심는다. 이름은 서명되어야
한다 — 클라이언트가 `admin`을 자칭할 수 있으면 정책 전체가 장식이 된다.

### 3-2. join이 정책을 받는다

지금 `command_join`은 **그 상태가 어느 페이지에서 왔는지 모른다.** 서명된 `data-state`는 내용의
위조만 막고, A 페이지에서 얻은 상태를 B 페이지의 연결에서 join하는 것은 막지 않는다. 컴포넌트마다
붙인 인증 훅이 각자 방어할 뿐이다.

live_session은 여기에 **페이지 단위 정책**을 준다. join 시 세션 이름을 함께 보내고, 서버는

1. 서명을 검증하고,
2. 그 세션의 `on_mount` 훅을 컴포넌트의 것보다 **먼저** 돌리고,
3. 훅이 halt하면 join을 거절한다(리다이렉트는 나간다).

컴포넌트는 `_live_sessions = {"admin"}`으로 "나는 이 세션에서만 산다"를 선언할 수 있다. 선언이
없으면 어디서나 살 수 있다(기존 동작). 이 선언이 있으면 다른 세션에서의 join은 거절된다.

### 3-3. 경계를 넘는 이동은 전체 로드다

boost가 링크를 가로챌 때 새 문서의 세션 이름을 비교한다. 다르면 morph하지 않고 **평범한 페이지
이동**을 한다. 그러면 WebSocket이 끊기고 새 핸드셰이크가 새 쿠키·새 인증으로 다시 선다.

이 규칙이 없으면 다음이 성립한다: 로그인 상태로 관리자 페이지를 열고, 로그아웃하고, boost로
관리자 페이지로 이동한다 → 연결은 로그인 시점에 세운 그대로다. 지금 wireview가 정확히 이 상태다.

구현은 GAP-028에서 morph를 건너뛴 방식과 같은 자리다(`wireview-boost.js`의 클릭 인터셉터,
`isStreamContainer` 옆).

## 4. `_on_mount`와의 경계 (AC3)

| | `_on_mount` | `live_session` |
|---|---|---|
| 붙는 곳 | 컴포넌트 클래스 | 페이지(Django 뷰) |
| 답하는 질문 | "이 컴포넌트가 마운트될 때 무엇을 먼저 하나" | "이 페이지에 들어올 수 있는가, 그리고 어디서 나갈 때 연결을 끊어야 하나" |
| 실행 순서 | 세션 훅 다음 | 컴포넌트 훅보다 먼저 |
| 없으면 | 컴포넌트마다 반복 선언 | 페이지 경계에서 인증이 재검증되지 않는다 |

`live_session`은 `_on_mount`를 **대체하지 않고 그 위에 층을 얹는다.** 세션 훅과 컴포넌트 훅은
같은 훅 프로토콜(`on_mount(component, params, session) -> {"cont"|"halt"}`)을 쓴다. 새 개념을
만들지 않는 것이 이 설계의 목표 중 하나다.

## 5. 열린 질문

1. **세션 이름의 서명 수명.** 페이지 렌더 시점에 서명하면 만료를 어떻게 두나. `data-state` 서명과
   같은 `Signer`를 쓰되 세션 이름 + 사용자 pk를 함께 서명하면, 로그아웃 후 재사용이 막힌다.
   대신 익명 사용자에서 로그인으로 바뀌는 경우도 서명이 깨지므로 재로드가 강제된다 — 이것이
   바람직한 동작인지 확인이 필요하다.
2. **`_live_sessions` 선언을 강제할 것인가.** 기본을 "어디서나 허용"으로 두면 기존 코드가 안
   깨지지만, 정책이 옵트인이라 실수로 빠뜨리기 쉽다. `manage.py check`가 "세션 훅이 있는데
   `_live_sessions`를 선언하지 않은 컴포넌트"를 경고하는 선이 적당해 보인다.
3. **여러 세션이 한 페이지에.** Phoenix에서는 불가능하다(LiveView 하나 = 라우트 하나). wireview는
   한 페이지에 컴포넌트가 여럿이므로 "이 컴포넌트만 다른 정책"이 기술적으로 가능하다. 허용하면
   경계의 의미가 흐려진다. **금지**를 제안한다 — 페이지 하나에 세션 하나.
4. **#68(세션 접근)과의 순서.** 훅 시그니처의 `session` 인자를 채우는 일은 #68이다. live_session이
   먼저 나가면 그 인자는 계속 빈 dict다. #68 → #75 → #58 순서를 제안한다.

## 6. 인수 조건 재정의 (#58의 AC를 이 설계로 구체화)

- AC1: 페이지 단위로 선언한 인증 훅이 그 페이지의 **모든** 컴포넌트 join에 적용된다.
- AC2: 세션 경계를 넘는 boost 이동이 전체 페이지 로드가 되고, WebSocket이 새로 선다. E2E로 증명한다.
- AC3: 위조된 세션 이름으로 join하면 거절된다. 서명 검증 테스트를 포함한다.
- AC4: `_on_mount`와의 역할 구분을 `docs/features/lifecycle-hooks.md`에 적는다(4절 표).
- AC5: 완료 정의 준수.
