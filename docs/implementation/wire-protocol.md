# Wire Protocol

> 브라우저와 서버 세션 사이, 그리고 세션들 사이를 오가는 메시지의 정본. 연결 계층을 바꾸더라도 이 형태는 유지한다.

---

## 1. 구조

```
Browser tab  ──(1) inbound command──▶  Session (WireviewConsumer)
             ◀─(2) outbound command──  │
                                       │ (3) session mail  ──▶ 자기 세션 (message_from_component)
                                       │ (4) fan-out       ──▶ 토픽을 구독한 모든 세션
```

| 종류 | 형태 | 코드 지점 |
|------|------|-----------|
| (1) inbound | `{"command": str, "payload": {...}}` JSON 프레임 | `WireviewConsumer.receive_json` → `command_<name>(**payload)` |
| (2) outbound | `{"command": str, "payload": {...}}` JSON 프레임 | `Outbound.send_command` (`wireview/core/transport.py`) |
| (3) session mail | `{"type": "message_from_component", "command": str, "kwargs": {...}}` | `Broker.send_to_session` → `WireviewConsumer.message_from_component` → `component_<command>(**kwargs)` |
| (4) fan-out | `{"type": <아래 표>, "channel": str, ...}` | `Broker.publish` → 컨슈머의 `type` 핸들러 |

세션 식별자는 Channels에서 `channel_name`이다. 토픽은 채널 레이어 그룹 이름이다. 다른 연결 계층은 `Outbound`와 `Broker` 두 인터페이스만 구현하면 된다.

## 2. Inbound (브라우저 → 세션)

| command | payload | 처리 |
|---------|---------|------|
| `join` | `name`, `state` (서명 상태, `wireview.core.state`), `children: {id: [name, state]}` | 컴포넌트 복원, `joined()`, 첫 render, `params_changed`. `children`에는 중첩된 일반 Component와 LiveComponent의 상태가 함께 실린다. **LiveComponent는 자기 join을 보내지 않는다** — 부모가 소유하며, 이미 등록된 LiveComponent id로 join이 오면 서버는 무시한다 |
| `leave` | `id` | `leaving()`, 그 아래 LiveComponent에 cascade, 업로드 레지스트리 해제, 구독 재계산, 컴포넌트 제거 |
| `user_event` | `id`, `command`, `implicit_args` (폼 직렬화), `explicit_args` | 핸들러 호출 후 render |
| `hook_event` | `component_id`, `hook_id`, `event`, `payload`, `ref?` | `handle_hook_event()`, `ref`가 있으면 `hook_reply` |
| `params_changed` | `params`, `uri` | 모든 컴포넌트에 `params_changed()` |
| `query_string` | `qs` | 저장소 params 갱신 |
| `upload_register` | `id`, `name`, `entries: [{ref, name, size, type}]` | 검증 후 `upload_op` 응답 |
| `upload_cancel` | `id`, `name`, `ref` | 항목 취소 |
| `upload_complete` | `id`, `name`, `ref` | 항목 완료 처리 |

## 3. Outbound (세션 → 브라우저)

| command | payload |
|---------|---------|
| `render` | `id`, `diff`, `children?` — `diff`는 전체 `{"s", "d", "f"}` 또는 부분 `{"<index>": value}`, 또는 자식만 바뀌었을 때 `null`. value는 문자열, comprehension `{"s", "d"}`, 항목 갱신 `{"u", "n"}`, 블록 `{"r", "d"}`, 블록 부분 갱신 `{"p"}`, LiveComponent 참조 `{"c": id}` ([html-diff](../features/html-diff.md)). `children`은 이 렌더와 함께 렌더된 LiveComponent들의 `{id: diff}` 평면 맵이다(손자식 포함). 클라이언트는 DOM을 건드리기 전에 이들을 먼저 등록하고, 부모 HTML을 만들 때 참조 자리에 자식의 현재 HTML을 넣는다 |
| `remove` | `id` |
| `append`, `prepend`, `insert_after`, `insert_before`, `replace_with` | `id`, `html` |
| `stream_op` | `op`, `stream`, `items`, `at` |
| `exec_js` | `id`, `commands` |
| `push_event` | `component_id`, `hook_id`, `event`, `payload` |
| `hook_reply` | `ref`, `response` |
| `url_change` | `command` (`redirect`, `replace`, `push`), `url` |
| `set_query_string` | `qs` |
| `title` | `title` |
| `flash` | `flash_id`, `flash_type`, `message`, `timeout`, `dismissible` |
| `clear_flash` | `flash_id` |
| `scroll_into_view` | `id`, `behavior`, `block`, `inline` |
| `focus_on` | `selector` |
| `upload_op` | `op` (`registered`, `progress`, `error`, `complete` 등), `upload`, `ref?`, 그 외 op별 필드 |
| `dispatch_event` | `command`, `id`, `args`, `kwargs` — 지연 호출 |

## 4. Session mail (컴포넌트 → 세션)

`WireviewMeta.send(command, **kwargs)`가 `Broker.send_to_session(channel_name, ...)`로 보낸다. 컨슈머는 `component_<command>`로 받아 대부분 같은 이름의 outbound 명령으로 바꿔 보낸다. `command` 값은 3절의 outbound 명령 이름과 같고, 추가로 다음이 있다.

| command | kwargs | 처리 |
|---------|--------|------|
| `dispatch_event` | `id`, `command`, `args`, `kwargs` | 세션 안에서 핸들러를 다시 호출하고 render |
| `send_render` | `id` | 강제 render |
| `update_live_component` | `parent_id`, `live_component_id`, `assigns` | LiveComponent `update()` 후 render |
| `dom_action` | `action`, `id`, `html` | `action` 이름의 outbound 명령으로 전달 |

## 5. Fan-out (세션들 사이)

| type | 필드 | 발행 지점 | 수신 처리 |
|------|------|-----------|-----------|
| `notification` | `channel`, `kwargs` | `broadcast()`, `abroadcast()`, `send_notification()`, `WireviewMeta.queue_broadcast()`, Presence | 구독 컴포넌트의 `notification()` 후 render |
| `model_mutation` | `channel`, `action`, `instance` (직렬화된 모델) | `auto_broadcast.notify_mutation` (Django signals) | 구독 컴포넌트의 `mutation()` 후 render |
| `upload.progress` | `upload`, `ref`, `progress`, `bytes_received` | `UploadView` | `upload_op progress` 전송 |
| `upload.error` | `upload`, `ref`, `errors` | `UploadView` | `upload_op error` 전송 |

토픽 이름은 `Component._subscriptions`의 값(모델 라벨 `app.model` 또는 임의 채널 이름)과 `wireview_upload_<component_id>`다. 컨슈머는 render 뒤마다 저장소의 구독 집합과 자기 구독을 맞춘다(`update_to_which_channels_im_subscribed_to`).

## 6. 세션 상태

세션 하나가 Python 메모리에 들고 있는 것.

| 상태 | 위치 | 외부화 |
|------|------|--------|
| 컴포넌트 인스턴스와 필드 | `ComponentRepository.components` | `data-state`로 이미 클라이언트에 복제됨 |
| 마지막 렌더 스냅샷 (`Rendered`) | `WireviewMeta._last_rendered` | `Rendered.to_dict()` / `from_dict()` |
| 구독 집합 | `WireviewConsumer.subscriptions` | 토픽 이름 목록 |
| 쿼리스트링 | `WireviewConsumer.query_string` | 문자열 |
| 업로드 레지스트리 | `views._registries` (프로세스 전역) | 미지원 |

## 7. 버전

- 2026-09-08: `render` 부분 diff 값에 comprehension과 블록 형태 추가 (GAP-025).
- 2026-09-08: 첫 정본. 코드에서 추출했으며, 이후 명령을 더하거나 필드를 바꾸면 이 문서와 `CHANGELOG.md`에 남긴다.
