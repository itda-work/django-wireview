# 세션 진입점의 의무를 한 관문으로 — 접근 검토 (#232)

> 2026-11-12. **검토 메모 — 결정 아님.** 기준은 `main`의 7037b96이다. 이 메모를 쓰는 동안 다른 작업이
> `wireview/session.py`의 일부를 고치고 있다(#230 인바운드 상한, #231 상태 복원 실패). 줄 번호는 7037b96의 것이고,
> 그 두 작업이 들어오면 어긋난다. 원자료(이력 표, 재현 테스트)는 커밋하지 않고 작업 사본의 `build/agent/`에 두었다.

## 0. 묻는 것과 답

**물음.** "컴포넌트 코드를 부르는 어떤 길도 같은 의무를 진다"를 사람의 기억(`CLAUDE.md` 「함정」)이 아니라
구조와 계약 테스트가 지키게 할 수 있는가.

**답의 요약.**

1. 의무 열한 가지 중 **여섯은 이미 구조가 진다** — 진입점이 아니라 그것들이 결국 지나는 하나의 싱크에 있다
   (`send_render`의 `refused`, `render_diff`의 `shared_render.render`, `register_component`의 `wire.identity`,
   어댑터 `dispatch`의 `aclose_old_connections`, 작업의 `render_gate.run`, `stream()` 안의 `patch_gate`).
   진입점마다 손으로 거는 것은 **다섯**이다: 예외 회복(`_crashed`, 그 안의 `ConnectionGone` 통과), 인스턴스 찾기
   (`reachable`, 메일이면 인스턴스 번호까지), 렌더와 그 뒤의 `after_mutation_chores`, `has_joined`, `joined()` 둘레의 패치 잡기.
2. 진입점 18개 × 그 다섯을 대조해 **빠진 칸 넷을 실패하는 테스트로 보였다**(§1-3). 넷 모두 "다른 진입점에는 있는 의무"다.
3. 그런데 이력은 "빠뜨린 의무가 결함의 주된 원인"이라는 가설을 **반박한다.** `v1.0.0..HEAD`의 fix 34개 중 주 원인이
   빠뜨린 의무인 것은 1개(3%), 부차까지 5개(15%)다. 새 기능 자체의 결함이 19개(56%), 순서·경합이 11개(32%)다(§2).
   대신 fix 23개(68%)가 **새 의무를 만들었고**, 그중 7개는 모든 진입점·렌더 경로에 손으로 퍼뜨려야 하는 종류였다.
   관문을 정당화하는 것은 과거의 누락이 아니라 의무가 늘어나는 속도다.
4. **권고(§5): 큰 구조 변경은 하지 않는다.** 진입점을 코드에서 열거하는 계약 테스트(§4)를 먼저 두고, 남은 다섯 의무 중
   둘(예외 회복 + 뒤처리, 인스턴스 찾기)만 세션의 작은 도우미 둘로 모은다(후보 B). 선언형 표·타입으로 좁힌 핸들은 지금은 버린다.

## 1. 진입점 × 의무

### 1-1. 진입점

`WireviewSession`(`wireview/session.py`)에서 컴포넌트 코드 — 핸들러, `joined()`·`leaving()`·`update()`·`params_changed()`·
`notification()`·`mutation()`, `on_mount` 훅, 업로드 콜백, 템플릿과 property(렌더), `get_subscriptions()` — 를 부르는 길은 18개다.
`command_*` 10개 중 9개, `component_*` 19개 중 5개, 채널 레이어 메시지 4개다. 나머지(`command_sync`, `component_title` 같은
중계, `wireview_patch`)는 컴포넌트 코드를 부르지 않는다 — Broadcast 패치는 렌더도 핸들러도 없이 쓰인다(`session.py:2644`).

| 갈래 | 진입점 | 부르는 컴포넌트 코드 |
|---|---|---|
| 인바운드 | `command_join` `:612` | `on_mount`, `mount`, `joined()`, `params_changed()`, 렌더 |
| | `command_leave` `:1139` | `leaving()` |
| | `command_navigated` `:1236`, `command_params_changed` `:1270` | `params_changed()`, 렌더 |
| | `command_user_event` `:1299` | `handle_event` 훅, 핸들러, 렌더(묘비면 핸들러만, `:1334`) |
| | `command_hook_event` `:1394` | `handle_hook_event()`, 렌더 |
| | `command_upload_register` `:1441` | `external` 콜백, 렌더 |
| | `command_upload_cancel` `:1545`, `command_upload_complete` `:1557` | `cancel_upload`, `on_upload_complete()`, 렌더 |
| 메일 | `component_dispatch_event` `:1691` | `wire.defer`·`send_to_parent`의 핸들러, 렌더 |
| | `component_send_render` `:1789` | 렌더(작업의 `send_render()`, `start_async`·`assign_async`의 끝) |
| | `component_crashed` `:1872` | `leaving()`(`handle_async`가 던진 뒤) |
| | `component_update_live_component` `:1928` | `update()`, 렌더 |
| | `component_deliver_unheard` `:1719` | 미뤄 둔 `notification()`·`mutation()`, 렌더 |
| 채널 | `notification`·`model_mutation` `:2147` | `core/notifications.deliver` — `wireview.testing`의 mount와 함께 쓴다 |
| | `upload_progress` `:1982`, `upload_error` `:2018` | 렌더(`_render_upload_owner` `:2039`) |
| 작업 | `start_async` `core/component.py:1060`, `assign_async` `:1208` | 연산과 `handle_async()` — 세션 밖, 결과는 메일(`send_render`·`crashed`)로 돌아온다 |

`stop()`(`:429`)의 `leaving()`과, 렌더 안에서 LiveComponent의 `joined()`·`update()`·`leaving()`을 부르는 `_render_level`(`:2270`)은
진입점이 아니라 위의 길들이 지나는 싱크다.

### 1-2. 의무, 그리고 어디에 걸려 있나

| 의무 | 걸린 곳 | 진입점마다? |
|---|---|---|
| 예외를 `_crashed`로(join은 `_join_failed`) | 진입점마다의 `try` | **예** |
| `ConnectionGone` 통과 | `_crashed` 안(`:548`), join(`:755`), 업로드 콜백(`:1504`), `component_dispatch_event`(`:1706`) | 대부분 `_crashed`가 진다. `_crashed`를 거치지 않는 `except Exception`만 손으로 |
| 인스턴스를 `repo.reachable`로 | 진입점마다. 이벤트는 `refused`를 먼저 묻고 `dispatch_event`가 `reachable`(`repository.py:1218`) | **예** |
| 메일의 인스턴스 번호 비교 | `component_deliver_unheard`·`component_release_patches`·`component_upload_op` | **예** |
| `refused`인 것을 따로 렌더하지 않기 | `send_render` 첫 줄(`:2197`) | 아니오 — 싱크 |
| `mount_halted` | `command_join`(`:721`), LiveComponent는 `_render_level`(`:2322`) | 길이 둘뿐 |
| 렌더는 `send_render`로 | 진입점마다 부른다 | **예**(부르는 것 자체) |
| 뒤처리 `after_mutation_chores` | 진입점마다 | **예** |
| `has_joined` 전에는 듣지 않기 | `params_changed`(`:1287`)·`navigated`(`:1261`)·`deliver` | **예**. 이벤트·훅·업로드는 클라이언트가 막는다(`wireview.js:3519`의 `isLive`) |
| `joined()` 둘레의 `_hold_patches`·`_let_patches_through` | `command_join`(`:719`,`:741`,`:752`), `_render_level`(`:2329`)·`send_render`(`:2225`,`:2231`) | `joined()`를 부르는 두 곳 |
| `wire.identity` | `ComponentRepository.register_component`(`repository.py:1076`), LiveComponent `:456` | 아니오 — 싱크 |
| `shared_render.render` | `WireviewMeta.render_diff`(`core/meta.py:478`·`:492`), 공유는 `deliver`의 `handling()` 안에서만 | 아니오 — 싱크 |
| `keep_connections()` | 동기 렌더 → async 다리(`core/meta.py:1048`) | 해당 없음 — 세션 진입점은 루프 위에서 돈다 |
| `aclose_old_connections` 트립 | 어댑터의 `dispatch`가 메시지마다(`consumer.py:164`, `in_turn`) | 아니오 — 어댑터 |
| `render_gate` | 작업이 `render_gate.run`으로 돈다(`core/component.py:1148`·`:1272`) | 세션 진입점은 해당 없음(차례 안에서 렌더를 기다린다) |

### 1-3. 칸마다

`✓`는 적용됨, `–`는 해당 없음, **빠짐**은 실패하는 테스트로 보인 것, *의심*은 깨지는 입력을 적었으나 테스트로 만들지 않은 것이다.
테스트는 `build/agent/test_gate_gaps.py`(5개 중 대조군 1개 통과, 4개 실패).

| 진입점 | 예외 회복 | 인스턴스 | 렌더·뒤처리 | `has_joined` |
|---|---|---|---|---|
| `command_join` | ✓ `_join_failed` `:761` | ✓ `retry_join`·`bury` | ✓ `:739`·`:746` | – (자기가 join) |
| `command_leave` | ✓ `_call_leaving`이 삼킨다 | – (LiveComponent는 무시) | ✓ `:1159` | ✓ `_call_leaving` `:1184` |
| `command_navigated` | ✓ `:1267` | ✓ `reachable_components` | ✓ | ✓ `:1261` |
| `command_params_changed` | ✓ `:1295` | ✓ | ✓ | ✓ `:1287` |
| `command_user_event` | ✓ `:1330` | ✓ `refused`·`dispatch_event` | **빠짐**: `after_mutation_chores`(`:1332`)가 `try` 밖이다 | – 클라이언트 |
| `command_hook_event` | ✓ `:1435` | ✓ `:1413` | 같은 모양(`:1437`), 같은 빠짐 | – 클라이언트 |
| `command_upload_register` | **빠짐**: 끝의 `send_render`(`:1543`)가 `try` 밖 | ✓ `:1452` | 뒤처리 없음(*의심*, 아래) | – |
| `command_upload_cancel` | ✓ `:1555` (대조군) | ✓ | 뒤처리 없음 | – |
| `command_upload_complete` | 오류 갈래의 `send_render`(`:1593`)가 `try` 밖 — 위와 같은 빠짐 | ✓ | 뒤처리 없음 | – |
| `component_dispatch_event` | ✓ `:1712` | ✓ `dispatch_event`. 인스턴스 번호 없음(*의심*) | ✓ | – |
| `component_send_render` | ✓ `:1795` | `repo.get` — `send_render`가 `refused`를 막아 무해 | – | – |
| `component_crashed` | – | **빠짐**: id만 본다. 같은 id로 다시 join한 새 인스턴스가 은퇴한다 | – | – |
| `component_update_live_component` | ✓ `:1973` | ✓ `:1948` | ✓ | – |
| `component_deliver_unheard` | ✓ `deliver_unheard` | ✓ 인스턴스 번호 `:1726` | ✓ | ✓ |
| `notification`·`model_mutation` | ✓ `deliver` | ✓ `components_subscribed_to` | ✓ | ✓ |
| `upload_progress`·`upload_error` | **빠짐**: `_render_upload_owner`(`:2048`)가 `try` 밖 | `repo.get`, 렌더는 싱크가 막음 | 뒤처리 없음 | – |

빠짐 넷의 깨지는 입력:

1. **업로드 경로의 렌더 예외** (`command_upload_register` `:1543`, `command_upload_complete` `:1593`, `upload_progress`·`upload_error` → `:2048`).
   업로드 목록을 그리는 property가 던진다 — 진행률 메시지 하나가 소켓을 닫는다(어댑터 `dispatch`는 `ConnectionGone`만 받는다,
   `consumer.py:166`). 페이지 전체가 다시 join한다. 이웃한 `command_upload_cancel`은 같은 렌더를 `try`/`_crashed` 안에서 하고,
   그 컴포넌트만 다시 join한다. 확신: **높음**(테스트 둘이 실패, 대조군 통과).
2. **`get_subscriptions()`가 던지면** — `after_mutation_chores`(`:2403`) → `update_to_which_channels_im_subscribed_to`(`:2412`)가
   모든 reachable 컴포넌트에 묻는데 어느 진입점에서도 `try` 밖이다. 핸들러가 바꾼 필드로 `get_subscriptions()`가 던지면
   그 컴포넌트가 아니라 연결이 끝난다. 확신: **높음**(테스트 실패). 다만 이것이 제품이 질 보장인지는 §5-3의 질문이다 —
   예제 열 곳 넘게 이 메서드를 덮어쓰지만 모두 순수한 집합 계산이다.
3. **낡은 인스턴스의 `crashed` 메일** (`component_crashed` `:1872`). `handle_async`가 던져 메일 `crashed`가 채널에 들어간 뒤,
   그보다 먼저 처리된 같은 id의 join(boost 이동의 새 DOM)이 새 인스턴스를 만든다. 메일은 새 인스턴스를 은퇴시키고 `error`를
   보내 페이지가 그것을 한 번 더 join한다. 같은 모양의 메일인 `release_patches`·`deliver_unheard`·`upload_op`은 `wire.instance`를
   비교한다(#137, #207). 확신: **중간** — 함수 단위로는 테스트가 실패하지만, 브라우저에서 그 순서가 나오려면 경합이 필요하고
   결과는 상태 손실이 아니라 불필요한 다시 join이다.
4. *의심*, 테스트 없음: `component_dispatch_event`도 id만 싣는다(`core/meta.py:746`). 떠난 인스턴스의 `wire.defer`가 새 인스턴스의
   핸들러를 부를 수 있다 — 단 떠날 때 작업이 취소되므로(`_call_leaving` `:1212`) 메일이 이미 채널에 있는 좁은 틈에서만.
   업로드 명령 셋이 `after_mutation_chores`를 하지 않는 것은 그 핸들러가 구독을 바꿀 일이 드물어 *의심*으로 둔다.

빠지지 않은 것도 적는다. "깨지는 입력 하나"(새 진입점이 `repo.get`으로 막힌 컴포넌트를 부른다)는 지금 렌더에 대해서는 이미
구조가 막는다 — `send_render`가 `refused`를 묻는다. 막히지 않는 것은 렌더가 아닌 컴포넌트 코드(핸들러·`update()`·콜백)이고,
그 길은 지금 모두 `reachable`로 찾는다. 관문이 없으면 다음 진입점이 그것을 빠뜨릴 수 있다는 것은 맞지만, **지금 빠진 칸은 없다.**

## 2. 이력

`git log v1.0.0..HEAD -- wireview/session.py` 52개 중 fix 34개(되돌린 412c41b 포함). 표 전체는 `build/agent/history.md`.

| 범주 | 수 | 대표 |
|---|---|---|
| A 빠뜨린 의무(다른 진입점에 이미 있던 것) | 1 | 572e051(#178): `joined()`에만 있던 패치 잡기가 핸들러·`params_changed`·`notification()`·`update()`의 reset에 없었다 |
| B 순서·경합 | 11 | 그중 6개가 #178 Broadcast hold(12f7b17, 53ac2ee, d308c50, fa132fd, f2a1775 …), cae1c12·b203a3c(#170), 7b9c8b9(#207), 9968cc7·354d05a(#211) |
| C 새 기능의 결함 | 19 | #204 다섯, #209 넷, #207·#202·#210·#206·#212 |
| D 기타 | 3 | 2623a7f(Channels가 `disconnect()`를 부르지 않는 종료), 05abd74·aaad0ca(레이어가 메시지를 버림) |

부차로 A 성격이 끼어 있는 것이 넷 더 있다: 22598bf·12f7b17(`reachable`을 다시 묻지 않음), 9968cc7(메일마다 `aclose_old_connections`),
b203a3c(join 경로만 LiveComponent에 params를 알리지 않음). 합쳐 5/34(15%).

**가설 판정: 반박.** 주 원인으로 3%, 부차까지 15%다. 그러나 다른 숫자가 같은 방향을 가리킨다.

- fix 23/34(68%)가 새 의무를 만들었다. 그중 진입점이나 렌더 경로마다 걸어야 하는 것이 7개다:
  `ConnectionGone` 통과(2253383, 한 커밋에서 네 곳에 같은 `except`), 메일 차례, `page_request.render`(562232c, 렌더 경로 다섯 곳),
  `wire.identity`, unheard 보류와 `has_joined`, CSRF 토큰(ae1c59f, 렌더 경로 다섯 곳), `patch_gate`.
- 그 일곱 중 다섯은 결국 **싱크로 갔다** — `identity`는 `register_component`, `page_request`·CSRF·`shared_render`는 `render_diff`,
  `patch_gate`는 `stream()`. 572e051은 진입점마다 거는 대신 `stream()` 안으로 모아 고쳤다. 진입점에 남은 것은
  `ConnectionGone`(대부분 `_crashed` 안)과 `has_joined`뿐이다.
- `wireview.testing`의 mount가 세션과 다르게 동작한 결함이 #178 리뷰에서만 세 번 나왔고(R5, S3, T4), f2f7e0a가 배달 규칙을
  `core/notifications.deliver`로 옮겨 사본을 없앴다.

이 저장소가 이미 해 온 일이 답의 모양이다: **새 의무는 진입점이 아니라 싱크에 단다.** 진입점에 손으로 거는 의무는 "부른 쪽의
문맥이 있어야 하는 것"만 남는다 — 무엇을 답할지(ref), 누구를 다시 join할지, 어떤 인스턴스였는지.

## 3. 후보

먼저 묻는다: 이 보장 전부를 제품이 져야 하는가, 어떤 길은 없애거나 합칠 수 있는가.

- **업로드 경로 넷은 합칠 수 있다.** `upload_progress`·`upload_error`의 렌더, `command_upload_*`의 렌더는 같은 일 —
  "그 컴포넌트를 다시 그린다, 던지면 회복한다" — 이다. 진입점을 없애지는 못하지만(브로커가 보내는 메시지다) 몸통은 하나가 된다.
- **`crashed` 메일은 없앨 수 있는가.** 작업은 세션 밖에서 돌고 회복은 세션이 해야 하므로 메일은 필요하다. 없앨 수 있는 것은
  "id만 싣는다"는 모양이다 — 인스턴스를 싣는 메일 셋과 같은 규칙으로 맞추면 의무가 아니라 형식이 된다.
- **`has_joined`를 서버가 질 것인가.** 지금은 클라이언트의 `isLive`가 진다. 서버 검사를 더하면 두 번 지고, 빼면 지금처럼
  클라이언트를 믿는다. §5-3의 질문이다.

### A. 아무것도 바꾸지 않는다 (「함정」 문서 유지)

- 강제되는 것: 지금의 싱크 여섯.
- 규율로 남는 것: 다섯 전부.
- 비용: 없음. 위험: §1-3의 빠짐 넷이 그대로 남고, 다음 진입점(#230·#231이 지금 손대는 곳)이 같은 실수를 할 수 있다.
  그런데 이력상 그 실수는 드물다(3%). 빠짐 넷을 하나씩 고치는 것만으로 이 후보의 위험 대부분이 사라진다.

### B. 세션의 도우미 둘 + 진입점 열거 계약 테스트 (권고)

1. `_running(component, ref=None)` — 비동기 컨텍스트 매니저. 몸통의 예외를 `_crashed(component, ref)`로 받고
   (`ConnectionGone`은 `_crashed`가 이미 다시 던진다), 정상으로 끝나면 `after_mutation_chores()`까지 그 안에서 돈다.
   진입점은 `async with self._running(component, answer): …핸들러…; await self.send_render(...)`가 된다. 빠짐 1·2가 이것으로 닫힌다.
2. `_target(id, instance=None)` — `repo.reachable(id)`에 인스턴스 번호 비교를 더한 것. 메일이 인스턴스를 싣게 하고
   (`crashed`, `send_render`, `dispatch_event`) 셋 다 이것으로 찾는다. 빠짐 3·4가 닫힌다.
3. 진입점을 코드에서 열거하는 계약 테스트(§4).

- 강제되는 것: 계약 테스트가 "새 `command_*`·`component_*`·채널 핸들러는 표에 행이 있어야 하고, 행이 말한 의무를 지켜야 한다"를
  지킨다. 도우미는 강제하지 않는다 — 쓰지 않은 진입점은 테스트가 잡는다.
- 규율로 남는 것: 도우미를 쓰는 것, `joined()` 둘레의 잡기(부르는 곳이 둘뿐이고 둘 다 이미 있다), `has_joined`.
- 단계: (1) 테스트만 — 빠짐 넷은 `xfail(strict=True)`로 표시. 회귀 위험 0. (2) 도우미 둘과 빠짐 넷 수정 — 수십 줄,
  `tests/test_errors.py`의 모양. (3) 이미 `try`/`_crashed`인 진입점 열 곳을 도우미로 옮긴다 — 기계적이고 되돌리기 쉽다.
  (3)은 생략해도 된다.
- mount와의 공유: `notifications.Host`가 이미 `_crashed`·`after_mutation_chores`를 프로토콜로 갖는다. `_running`을 Host의
  메서드로 두면 mount의 `call()`(`testing.py:965`)도 같은 모양이 된다. mount의 `_crashed`는 다시 던진다 — 테스트가 듣는다.
- 성능: 핫 경로(`user_event`)에 `async with` 한 겹. `send_render` 한 번(수백 µs, `docs/design/broadcast-fanout.md`)에 비해 측정할 수 없을 만큼 작다.

### C. 선언형 진입점 표 (이름 → 의무 플래그) + 하나의 디스패처

`handle_message`·`_take_mail`이 이미 이름으로 진입점을 찾는다(`:516`, `:1682`). 거기에 `{"user_event": Needs(target="refused",
recover=True, chores=True), …}`를 두고 디스패처가 의무를 건다.

- 강제되는 것: 표에 있는 플래그는 빠뜨릴 수 없다.
- 규율로 남는 것: 플래그를 맞게 고르는 것. 그리고 진입점 몸통의 모양이 제각각이라(join은 거절 사유 다섯과 `_join_failed`,
  `navigated`는 컴포넌트마다 회복, 묘비는 회복 없음) 플래그가 열을 넘거나 예외 행이 생긴다 — 그 예외가 지금의 규율과 같은 것이 된다.
- 단계: 진입점 29개의 몸통을 한 번에 바꿔야 의미가 있다. 2,700줄 파일의 큰 변경이고 #230·#231과 충돌한다.
- 성능: 디스패치마다 표 조회 한 번. 무시할 만하다.
- 판정: 지금은 버린다. 이득(빠짐을 막는 것)은 B의 계약 테스트가 같은 크기로 주고, 비용은 훨씬 크다.

### D. 인스턴스를 타입으로 좁히기 (`Reachable` 핸들만 컴포넌트 코드를 부른다)

`repo.reachable()`이 `Reachable[C]`를 돌려주고 `dispatch_event`·`send_render`가 그것만 받는다.

- 강제되는 것: pyright가 볼 수 있는 곳에서 `repo.get`의 결과로 코드를 부르는 것.
- 규율로 남는 것: 많다. `repo.get`은 정당하게 쓰이는 곳이 열다섯 넘는다(묘비·은퇴·`_forget_holds`·`_holds`의 동일성 비교).
  pyright는 `tests/`를 검사하지 않고, 컴포넌트 코드 대부분은 `component.update(...)`처럼 인스턴스의 메서드를 바로 부르므로
  핸들을 벗기는 순간 타입이 지키지 못한다. 그리고 지금 빠진 칸 넷 중 이것이 막는 것은 없다(넷은 인스턴스가 아니라 예외·번호의 문제다).
- 판정: 버린다.

### 비교

| | 지금 빠짐 넷 | 다음 진입점의 누락 | 변경 크기 | #230·#231과 충돌 |
|---|---|---|---|---|
| A 그대로 | 남는다 | 문서만 | 0 | 없음 |
| B 도우미 + 계약 테스트 | 닫힌다 | 테스트가 잡는다 | 작음, 단계별 | 거의 없음 |
| C 선언형 표 | 닫힌다 | 표가 잡는다(예외 행 제외) | 큼, 한 번에 | 큼 |
| D 타입 핸들 | 남는다 | 일부 | 중간 | 중간 |

## 4. 계약 테스트

`tests/test_live_session_contract.py`의 `CLIENT_COMMANDS`(`:1102`)는 손으로 쓴 사전이다 — 새 명령을 더하고 사전에 넣지 않으면
아무것도 실패하지 않는다. `tests/test_session_commands.py`는 반대로 **소스에서 열거한다**: `send("x")`가 있으면
`component_x`가 있어야 한다. 둘을 합친다.

```python
# tests/test_session_entry_contract.py (제안)
ENTRIES = {  # 이름 → (부르는 법, 그 진입점이 지는 의무)
    "command_user_event": (lambda s, c: s.command_user_event(c.id, "explode", {}, {}, ref=1), {"recovers", "chores"}),
    "command_upload_register": (..., {"recovers"}),
    "upload_progress": (..., {"recovers"}),
    "component_crashed": (lambda s, c: s.component_crashed(c.id, instance=c.wire.instance), {"instance"}),
    "command_sync": (None, set()),   # 컴포넌트 코드를 부르지 않는다: 이유를 주석으로
    ...
}

def test_every_entry_point_has_a_row():
    found = {name for name in dir(WireviewSession)
             if name.startswith(("command_", "component_")) or name in CHANNEL_HANDLERS}
    assert found == set(ENTRIES)
```

- 열거는 `dir(WireviewSession)`의 `command_`·`component_` 접두사와, 채널 레이어 메시지 타입(`notification`, `model_mutation`,
  `upload_progress`, `upload_completed`, `upload_error`, `session_invalidated`, `wireview_patch`)이다. 채널 쪽은 소스의
  `"type": "..."` 리터럴을 `test_session_commands.py`처럼 AST로 모으면 손 목록도 없앨 수 있다.
- 의무마다 하나의 탐침 컴포넌트와 하나의 단언:
  - `recovers` — 템플릿 property가 던지게 한 뒤 부른다. 예외가 올라오지 않고, `error`가 그 id로 한 번, 다른 컴포넌트는 그대로.
  - `chores` — 핸들러가 `get_subscriptions()`를 바꾼다. 부른 뒤 `outbound.topics`가 새 집합이다(던지는 경우는 §5-3의 답에 따라).
  - `reachable` — `test_live_session_contract.py`의 `TestARefusedIdAnswersNothing`을 그대로 옮긴다(거절된 id에는 답만).
  - `instance` — 같은 id로 새 인스턴스를 세운 뒤 옛 번호로 부른다. 새 인스턴스는 그대로.
  - 그리고 그 경로마다 **대조군** — 같은 진입점이 정상 컴포넌트에는 무언가를 한다(`OBSERVABLE`과 같은 이유: 아무것도 안 하는 것과
    맞게 거절하는 것은 한쪽에서 구별되지 않는다).
- mount 쪽은 `notifications.Host`를 지키는 행만 같은 표로 돈다: mount의 `_crashed`는 다시 던지는 것이 계약이다.
- 첫 판은 빠짐 넷을 `xfail(strict=True)`로 둔다. 고치면 strict가 xfail을 풀라고 실패한다.

## 5. 권고

### 5-1. 고른 것과 이유

**B.** 이유는 셋이다.

1. 이력이 말하는 결함의 대부분(88%)은 새 기능·순서의 것이고, 진입점 관문은 그것을 막지 못한다. 관문에 크게 투자할 근거가 없다.
2. 의무가 늘어나는 속도(fix의 68%가 새 의무)에는 이 저장소가 이미 맞는 답을 갖고 있다 — 싱크에 단다. 그 관행을 문서로 적고
   계약 테스트로 지키는 것이 새 구조보다 싸다.
3. 남은 다섯 의무 중 실제로 빠진 둘(예외 회복과 뒤처리, 인스턴스 번호)은 도우미 둘이면 형식이 된다. 나머지 셋(`reachable`,
   `joined()` 둘레, `has_joined`)은 지금 빠진 칸이 없고 부르는 곳이 적다.

### 5-2. 첫 PR과 그다음

1. **첫 PR — 테스트만.** `tests/test_session_entry_contract.py`: 진입점 열거, 행 없는 진입점 실패, `recovers`·`instance`·`reachable`
   세 의무의 탐침과 대조군, 빠짐 넷은 strict xfail. `wireview/`는 건드리지 않는다. 되돌리기는 파일 하나를 지우는 것이다.
2. **둘째 PR — 빠짐 넷.** `_running`·`_target` 도우미, 메일 `crashed`·`send_render`·`dispatch_event`에 인스턴스 번호, 업로드 경로의
   렌더를 회복 안으로. xfail이 풀린다. `CLAUDE.md` 「함정」의 "컴포넌트 코드를 부르는 새 경로는 예외를 `_crashed`로" 문장을
   "`_running` 안에서"로 고친다.
3. (선택) 셋째 PR — 이미 `try`/`_crashed`인 진입점을 도우미로 옮긴다. 동작은 같다.

### 5-3. 하지 말아야 할 것

- `session.py`를 진입점 표 하나로 다시 짜는 것(C). 지금의 빠짐은 그만한 변경을 요구하지 않고, #230·#231과 정면으로 겹친다.
- 타입으로 좁힌 핸들(D). pyright가 `tests/`를 보지 않고 빠짐 넷 중 어느 것도 막지 못한다.
- 서버에 `has_joined` 검사를 정책 결정 없이 더하는 것. 클라이언트의 `isLive`와 어긋나는 순간 "join 직후의 클릭이 사라진다"가 된다.
- 새 의무를 진입점에 다는 것. 싱크(`send_render`, `render_diff`, `register_component`, 어댑터 `dispatch`, `stream()`)가 없으면
  먼저 싱크를 만든다 — 이 문장은 「함정」 머리에 한 줄로 올릴 값이 있다.

### 5-4. 저장소 소유자에게 물을 것

1. **`get_subscriptions()`가 던지면** 그 컴포넌트만 잃을 것인가(`_crashed`), 프로그래밍 오류로 보고 연결을 닫을 것인가.
   전자면 `after_mutation_chores`가 컴포넌트마다 회복해야 해 뒤처리가 진입점의 `try` 밖에서도 회복을 진다.
2. **`has_joined`를 서버도 질 것인가.** 이벤트·훅·업로드는 지금 클라이언트만 막는다. 낡은 번들이나 손으로 보낸 프레임에 대한
   방어로 서버가 막으면, 막힌 이벤트에 무엇으로 답하는가(`gone`? 빈 render?).
3. **컴포넌트를 이름으로 부르는 모든 메일이 인스턴스 번호를 실을 것인가.** 내부 메일이라 `PROTOCOL_VERSION`과 무관하지만,
   배포 중 옛 프로세스가 보낸 메일(번호 없음)을 어떻게 다룰지 — 받아들일지 버릴지 — 정해야 한다.
4. **업로드 브로커 메시지의 렌더 실패**를 다른 진입점처럼 "그 컴포넌트를 다시 join"으로 다룰 것인가. 진행 중인 업로드는
   다시 join하면 끝난다(`_release_uploads`) — 진행률 표시 하나가 던졌다고 업로드를 끝내는 것이 맞는가, 아니면 렌더만 건너뛰고 로그를 남길 것인가.
