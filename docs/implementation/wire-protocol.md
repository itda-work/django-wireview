# Wire Protocol

> 브라우저와 서버 세션 사이, 그리고 세션들 사이를 오가는 메시지의 정본. 연결 계층을 바꾸더라도 이 형태는 유지한다.

---

## 1. 구조

```
Browser tab  ──(1) inbound command──▶  Session (WireviewSession, via WireviewConsumer)
             ◀─(2) outbound command──  │
                                       │ (3) session mail  ──▶ 자기 세션 (message_from_component)
                                       │ (4) fan-out       ──▶ 토픽을 구독한 모든 세션
```

| 종류 | 형태 | 코드 지점 |
|------|------|-----------|
| (1) inbound | `{"command": str, "payload": {...}}` JSON 프레임 | `WireviewConsumer.receive_json` → `WireviewSession.handle_message` → `command_<name>(**payload)` |
| (2) outbound | `{"command": str, "payload": {...}}` JSON 프레임 | `Outbound.send_command` (`wireview/core/transport.py`) |
| (3) session mail | `{"type": "message_from_component", "command": str, "kwargs": {...}}` | `Broker.send_to_session` → `WireviewConsumer.message_from_component` → `component_<command>(**kwargs)` |
| (4) fan-out | `{"type": <아래 표>, "channel": str, ...}` | `Broker.publish` → 컨슈머의 `type` 핸들러 |

브라우저는 `/__wireview__?vsn=<n>`으로 연결한다. `vsn`은 클라이언트가 적용할 수 있는 형태의 버전이고(`PROTOCOL_VERSION`, 번호별 내용은 7절의 표), 서버는 그보다 새 형태를 보내지 않는다. 없거나 잘못된 값(정수 아님, 음수, 여러 번)은 0으로 읽는다. 규칙은 7절에 있다.

세션 식별자는 Channels에서 `channel_name`이다. 토픽은 채널 레이어 그룹 이름이다. 다른 연결 계층은 `Outbound`와 `Broker` 두 인터페이스만 구현하면 된다.

## 2. Inbound (브라우저 → 세션)

| command | payload | 처리 |
|---------|---------|------|
| `join` | `name`, `state` (서명 상태, `wireview.core.state`), `children: {id: [name, state]}`, `ref?` | 컴포넌트 복원, `joined()`, 첫 render, `params_changed`. `state`는 v2 봉투 `{"v":2,"n":<클래스 FQN>,"s":<live_session>,"a":<인증 세대>,"d":<상태>}`를 `TimestampSigner`로 서명한 값이다(`s`와 `a`는 경계 안의 페이지에만 실린다). 서버는 `STATE_MAX_AGE`(기본 14일)로 만료를 검사하고, `name`이 가리키는 클래스와 봉투 안의 클래스가 같은지, 봉투의 `live_session`이 이 연결의 것과 같은지, `a`가 이 연결의 인증 세대와 같은지 확인한다. 루트 상태가 거절되면 아무것도 마운트하지 않고 `reload`를 보낸다. `children`의 항목 하나가 거절되면(서명 실패, 또는 다른 경계·다른 인증 세대의 봉투) 그 항목만 복원 맵에서 빠지고 join은 계속된다(#76, #58). `children`에는 중첩된 일반 Component와 LiveComponent의 상태가 함께 실린다. **LiveComponent는 자기 join을 보내지 않는다** — 부모가 소유하며, 이미 등록된 LiveComponent id로 join이 오면 서버는 무시한다. `ref`(정수)는 그 join에 답하는 render와 `error`, 그리고 `remove`(마운트가 halt됨)·`reload`(상태 거절)·`joined`에 그대로 돌아온다. 같은 id로 join을 다시 보낸 페이지는 그것으로 이전 join의 늦은 응답을 가려 버린다(#139, #146). `user_event`의 `ref`와 한 카운터를 쓰므로 번호가 겹치지 않는다. 클라이언트는 서버가 join 응답에서 `vsn` 6 이상을 알렸을 때만 싣는다 — 연결의 첫 join들에는 실리지 않는다 |
| `leave` | `id` | `leaving()`, 그 아래 LiveComponent에 cascade, 업로드 레지스트리 해제, 구독 재계산, 컴포넌트 제거. **LiveComponent id로는 보내지 않는다** — 부모 렌더가 이미 그 자식을 떠나보냈고, 늦게 온 `leave`는 그 사이 다시 보인 새 인스턴스를 지웠다. 서버는 LiveComponent id의 `leave`를 무시한다(옛 번들 방어, #140) |
| `user_event` | `id`, `command`, `implicit_args` (폼 직렬화), `explicit_args`, `ref?` | 핸들러 호출 후 render. `ref`(정수)는 확정 액션(#92)과 로딩 표시를 거는 이벤트(#118)에 실리고, 서버는 그 이벤트의 render(또는 `error`)에 그대로 돌려준다. 클라이언트는 그 답으로 입력값 보존과 로딩 표시를 정리한다. 클라이언트는 서버가 join 응답에서 `vsn` 3 이상을 알렸을 때만 싣는다 |
| `hook_event` | `component_id`, `hook_id`, `event`, `payload`, `ref` | `handle_hook_event()`, `ref`가 `null`이 아니면 `hook_reply`. `ref`는 문자열 `hook-<n>`(훅이 콜백을 넘겼을 때) 또는 `null`이다. join·`user_event`의 정수 `ref`와 다른 카운터다 |
| `params_changed` | `params`, `uri` | 모든 컴포넌트에 `params_changed()` |
| `upload_register` | `id`, `name`, `entries: [{ref, name, size, type}]` | 검증 후 `upload_op` 응답 |
| `upload_cancel` | `id`, `name`, `ref` | 항목 취소 |
| `upload_complete` | `id`, `name`, `ref` | 항목 완료 처리 |

표에 없는 command, 또는 payload가 그 명령의 인자와 맞지 않는 메시지는 WARNING 로그를 남기고 버린다. 연결은 닫지 않는다(#94). 전에는 예외가 컨슈머 밖으로 나가 소켓이 닫혔고, 한 릴리스 어긋난 번들이 그런 메시지를 보낼 때마다 페이지의 모든 컴포넌트가 다시 join했다.

`user_event`의 `id`가 이 연결에 등록된 컴포넌트가 아니면(모르는 id, 경계가 거절한 id, 이미 떠난 id) 아무것도 보내지 않는다. `command`가 핸들러가 아니면(밑줄로 시작, 없는 이름, 프레임워크 메서드) 아무것도 호출하지 않고 `diff: null`인 render로 답한다. 로딩 상태를 풀고 `ref`를 정리하게 하기 위해서다.

## 3. Outbound (세션 → 브라우저)

| command | payload |
|---------|---------|
| `render` | `id`, `diff`, `children?`, `ref?`, `vsn?`, `instances?` — `ref`는 이 render가 답하는 `user_event`의 것(`vsn`이 실린 join 응답이면 그 join의 것), `vsn`은 join에 답하는 render에만 실리는 서버의 프로토콜 버전이다. 같은 id로 보낸 join의 응답을 기다리는 동안 클라이언트는 그 join의 `ref`를 실은 render만 적용한다. `id`가 LiveComponent면(자식 단독 render) 그 루트의 join으로 가른다 — 자식의 인스턴스는 루트의 join이 만든다(#146). `instances`는 `{id: 번호}`로, 이 render가 첫 렌더인 인스턴스(join의 응답이면 그 컴포넌트, 이 render에서 join된 LiveComponent)의 번호다. 번호는 53비트 무작위 정수라 세션이 다른 프로세스로 옮겨도 연결 안에서 겹치지 않고(#141), 클라이언트는 `===`로만 비교하며 같은 번호를 실은 `upload_op config`만 받는다(#137). `diff`는 전체 `{"s", "d", "f"}` 또는 부분 `{"<index>": value}`, 또는 자식만 바뀌었거나 사용자 이벤트가 아무것도 바꾸지 않았을 때 `null`(후자는 이벤트가 끝났다는 알림이라 클라이언트가 로딩 상태를 지운다). value는 문자열, comprehension `{"s", "d"}`, 항목 갱신 `{"u", "n"}`, 항목 재배열 `{"k": [[시작, 길이] \| {"d": [...]}, ...]}`(`vsn` 2 이상에만), 블록 `{"r", "d"}`, 블록 부분 갱신 `{"p"}`, LiveComponent 참조 `{"c": id}` ([html-diff](../features/html-diff.md)). `children`은 이 렌더와 함께 렌더된 LiveComponent들의 `{id: diff}` 평면 맵이다(손자식 포함). 클라이언트는 DOM을 건드리기 전에 이들을 먼저 등록하고, 부모 HTML을 만들 때 참조 자리에 자식의 현재 HTML을 넣는다 |
| `remove` | `id`, `ref?` — 요소를 지운다. join의 마운트가 halt되었으면(`on_mount`, `live_session`) 그 join의 `ref`를 싣고, `wire.destroy()`가 보낸 것은 싣지 않는다. 같은 id로 보낸 join의 응답을 기다리는 동안 클라이언트는 그 join의 `ref`가 아닌 `remove`를 대체된 인스턴스의 것으로 보고 버린다. `id`가 LiveComponent면 render처럼 그 루트의 join으로 가른다(#146) |
| `joined` | `id`, `ref?` — `ref`는 그 join의 것이다(#146). 그 join과 `joined()`가 쌓아 둔 작업(스트림의 첫 페이지, 제목 등)이 모두 나갔다. 그 작업들과 같은 세션 큐로 보내 맨 뒤에 도착한다. 클라이언트는 이때부터 `wire-viewport-*`를 판단한다(#112). `vsn` 5 이상의 클라이언트에만 보낸다. 옛 서버에서는 클라이언트가 그 컴포넌트의 첫 render를 신호로 쓴다 |
| `error` | `id`, `during` (`event` 또는 `join`), `ref?` — `ref`는 `event`면 그 이벤트의 것, `join`이면 그 join의 것이다. 서버 코드가 이 컴포넌트를 처리하다 예외를 던졌다(#94). `vsn` 4 이상의 클라이언트에만 보낸다. `event`: 핸들러, 브로드캐스트 수신, `params_changed`, 훅 이벤트, 업로드 콜백, LiveComponent `update()`, 렌더 중 하나가 던졌다. 서버는 인스턴스를 버렸고(`leaving()`을 부른다), `id`는 루트 컴포넌트다(LiveComponent가 던졌으면 그 루트). 클라이언트는 렌더 상태를 비우고 요소의 `data-state`로 다시 join한다. 그 상태는 이벤트 전의 것이라 핸들러가 던지기 전에 바꾼 값은 남지 않는다. `ref`는 그 이벤트의 것이고, 답이 render로 오지 않으므로 클라이언트는 여기서 정리한다. `join`: join이 첫 렌더까지 가지 못했거나 첫 렌더 뒤의 `params_changed`가 던졌다(이때 join 하나가 render와 `error` 두 응답을 받는다). 다시 시도하지 않고, 클라이언트는 요소를 그대로 둔 채 `wireview-error` 클래스를 붙이고 컴포넌트 등록에서 뺀다. 두 경우 모두 요소에서 버블링되는 `wireview:error` 이벤트(`detail: {id, during}`)를 보낸다. `vsn` 3 이하 클라이언트에는 `event`면 소켓을 코드 1011로 닫고(전부 다시 join), `join`이면 `remove`를 보낸다 — 둘 다 이전의 동작이다 |
| `reload` | `id` (알 수 없으면 `null`), `reason` (`expired`, `invalid`, `live_session`), `ref?` — join의 루트 서명 상태를 쓸 수 없어 아무것도 마운트하지 않았다. `ref`는 그 join의 것이고, 같은 id의 다른 join을 기다리는 페이지는 이것을 버린다(#146). 클라이언트는 전체 페이지 로드로 복구하며, 30초 안에 두 번 반복되면 `sessionStorage["wireview:last-reload"]` 가드가 막고 경고만 남긴다 |
| `stream_op` | `op` (`reset`, `insert`, `delete`), `stream`, `items: [{id, html}]`, `at`, `limit?`, `id?` — `limit`은 0이 아닐 때만 실린다. `id`는 스트림을 보낸 컴포넌트다. 클라이언트는 그 요소 안에서 `wire-stream` 컨테이너를 찾고(그 안에 중첩된 컴포넌트의 것보다 자기 것을 먼저), `delete`할 항목도 그 컨테이너 안에서 찾는다. 컨테이너가 아직 없는 연산(새 컴포넌트의 `joined()`가 보낸 것은 그 요소를 그리는 `render`의 패치보다 먼저 온다)은 다음 애니메이션 프레임, 곧 이미 예약된 패치 뒤에 다시 찾고, 같은 컴포넌트(`id`)가 그 뒤에 보낸 `stream_op`·`exec_js`·`push_event`도 함께 기다려 도착 순서대로 적용된다. 다른 컴포넌트의 것은 기다리지 않는다. 그때도 없으면 버린다(`targets.mjs`). `id`가 없는 메시지(이전 서버)는 페이지 전체에서 처음 나오는 같은 이름의 컨테이너로 간다. 옛 번들은 `id`를 읽지 않으므로 새 서버와도 전처럼 돈다. 기다리는 동작은 클라이언트만의 것이고 메시지 형태를 바꾸지 않는다. 그래서 `PROTOCOL_VERSION`은 그대로다 |
| `exec_js` | `id`, `commands` — `id`의 요소에서 돈다. 요소가 아직 없으면 `stream_op`처럼 다음 프레임까지 그 컴포넌트의 줄에서 기다린다 |
| `push_event` | `component_id`, `hook_id`, `event`, `payload` — 그 컴포넌트의 훅에 간다. 요소가 아직 없으면(훅도 아직 없다) `stream_op`처럼 다음 프레임까지 그 컴포넌트의 줄에서 기다린다 |
| `hook_reply` | `ref`, `response` |
| `url_change` | `command` (`redirect`, `replace`, `push`), `url` |
| `set_query_string` | `qs` |
| `title` | `title` |
| `flash` | `flash_type`, `message`, `timeout`, `dismissible` — id는 없다. 클라이언트가 만든다 |
| `clear_flash` | `flash_id` — `null`이면 전부. 플래시 id는 클라이언트가 만들어 서버는 알지 못하므로, 서버 코드가 보내는 것은 사실상 `null`뿐이다 |
| `scroll_into_view` | `id`, `behavior`, `block`, `inline` |
| `focus_on` | `selector` |
| `upload_op` | `op` (`config`, `registered`, `progress`, `error`, `complete` 등), `upload`, `ref?`, 그 외 op별 필드. `config`는 업로드를 클라이언트에 만드는 op라 소유 컴포넌트의 `id`를 싣는다. `config`는 페이지를 live로 만드는 `render`보다 채널 레이어 한 번 왕복만큼 늦게 온다 — 그 사이에 고른 파일은 클라이언트가 들고 있다가 `config`가 오면 등록한다. `config`는 보낸 인스턴스의 번호 `instance`를 싣는다. 세션은 떠났거나 같은 id의 새 join이 대신한 인스턴스가 보낸 op를 넘기지 않고, 클라이언트는 그 id에 대해 `render`의 `instances`로 마지막에 알림받은 번호와 다른 `config`를 버린다. 같은 id로 보낸 새 join의 응답을 기다리는 동안에는 이전 join의 render를 적용하지 않으므로 그 `instances`도 받지 않는다(#139). `instance`가 없는 `config`(#137 이전 서버)는 그대로 받는다(#137) |

## 4. Session mail (컴포넌트 → 세션)

`WireviewMeta.send(command, **kwargs)`가 `Broker.send_to_session(channel_name, ...)`로 보낸다. 컨슈머는 `component_<command>`로 받아 대부분 같은 이름의 outbound 명령으로 바꿔 보낸다. `command` 값은 3절의 outbound 명령 이름과 같고, 추가로 다음이 있다.

| command | kwargs | 처리 |
|---------|--------|------|
| `dispatch_event` | `id`, `command`, `args`, `kwargs` | 세션 안에서 핸들러를 다시 호출하고 render |
| `send_render` | `id` | 강제 render |
| `crashed` | `id` | 컴포넌트의 백그라운드 작업(`start_async`의 `handle_async`)이 던졌다. 핸들러가 던진 것과 같이 복구한다(outbound `error`, #94) |
| `update_live_component` | `parent_id`, `live_component_id`, `assigns` | LiveComponent `update()` 후 render |

## 5. Fan-out (세션들 사이)

| type | 필드 | 발행 지점 | 수신 처리 |
|------|------|-----------|-----------|
| `notification` | `channel`, `kwargs` | `broadcast()`, `abroadcast()`, `send_notification()`, `WireviewMeta.queue_broadcast()`, Presence | 구독 컴포넌트의 `notification()` 후 render |
| `model_mutation` | `channel`, `action`, `instance` (직렬화된 모델) | `auto_broadcast.notify_mutation` (Django signals) | 구독 컴포넌트의 `mutation()` 후 render |
| `upload.progress` | `component`, `upload`, `ref`, `progress`, `bytes_received` | `UploadView` | 엔트리 갱신, 소유 컴포넌트 render, `upload_op progress` 전송 |
| `upload.completed` | `component`, `upload`, `ref`, `bytes_received`, `path` | `UploadView` (마지막 청크) | 엔트리를 완료로 올린다. 브라우저에는 보내지 않는다 — 브라우저는 자기 `upload_complete`로 알고, `on_upload_complete`도 거기서 한 번만 돈다 |
| `upload.error` | `component`, `upload`, `ref`, `errors` | `UploadView` | 엔트리를 오류로, 소유 컴포넌트 render, `upload_op error` 전송 |
| `session_invalidated` | `reason` | `invalidate_authentication()` (로그아웃, 재로그인) | 소켓을 코드 4001로 닫는다 |

토픽 이름은 `Meta.subscriptions`의 값(모델 라벨 `app.model` 또는 임의 채널 이름), `wireview_upload_<connection_id>`, `wireview.auth.<인증 세대 지문>`(경계 안의 연결만)이다. 채널 레이어 그룹 이름이므로 영숫자·`-`·`_`·`.`만, 100자 미만이어야 한다. 컨슈머는 render 뒤마다 저장소의 구독 집합과 자기 구독을 맞춘다(`update_to_which_channels_im_subscribed_to`).

업로드 진행 통지 그룹은 **연결마다 하나**이며 컴포넌트 구독과 수명이 다르다. 첫 레지스트리가 등록될 때 한 번 가입하고 `disconnect()`에서 탈퇴하므로 `self.subscriptions` 집합에는 들어가지 않는다. 업로드가 없는 페이지는 `group_add`를 한 번도 하지 않는다. 클라이언트는 payload의 `upload`와 `ref`로 대상을 가른다. 업로드 HTTP 엔드포인트도 같은 연결 id를 첫 세그먼트로 받는다: `/__wireview_upload__/<connection_id>/<component_id>/<upload_name>/`.

## 6. 세션 상태

세션 하나가 Python 메모리에 들고 있는 것.

| 상태 | 위치 | 외부화 |
|------|------|--------|
| 컴포넌트 인스턴스와 필드 | `ComponentRepository.components` | `data-state`로 이미 클라이언트에 복제됨 |
| 마지막 렌더 스냅샷 (`Rendered`) | `WireviewMeta._last_rendered` | `Rendered.to_dict()` / `from_dict()` |
| 구독 집합 | `WireviewSession.subscriptions` | 토픽 이름 목록 |
| 쿼리스트링 | `WireviewSession.query_string` | 문자열 |
| 업로드 레지스트리 | 컴포넌트의 `_upload_registry` (연결에 묶인다, #77) | 컴포넌트와 함께 옮긴다. 청크 파일은 `UPLOAD_TEMP_DIR`에 있고 엔드포인트는 서명 토큰만 보므로 프로세스 전역 상태는 없다(#83) |
| 페이지 경계 | `WireviewSession.live_session_name` | 이름 문자열. 첫 join이 정하고 이후 join은 일치해야 한다 |
| 인증 세대 | `WireviewSession.auth_fingerprint` | 지문 문자열. connect 때 계산한다 |
| 인증 토픽 구독 | `WireviewSession._auth_topic` | 토픽 이름. 경계 안에서만 생긴다 |
| 세션 재확인 여부 | `WireviewSession._auth_revalidated` | bool. **거절된 연결이 다시 물어 통과하지 못하게 하는 값이다** |
| 업로드 인스턴스 번호 | `WireviewMeta.instance`, `WireviewMeta.instance_announced` | 정수와 bool. 컴포넌트와 함께 옮긴다. **새 인스턴스는** 번호를 무작위로 뽑으므로, 다른 프로세스에서 만들어져도 옛 번호와 겹치지 않는다 — 프로세스마다 처음부터 세는 카운터였다면 겹쳐서, 옛 인스턴스가 늦게 보낸 `config`를 페이지가 새 인스턴스의 것으로 받는다(#141). **옮긴 인스턴스는** 번호와 `instance_announced`를 둘 다 그대로 옮긴다. 번호를 새로 뽑으면 같은 인스턴스가 새 인스턴스로 취급된다 — 다음 render가 새 번호를 알리면 페이지가 그 id의 업로드 manager를 버려 진행 중인 업로드가 끝나고, 옛 번호로 오는 `config`(옮기기 전에 시작한 태스크가 보낸 것)는 세션이 버린다. 새로 뽑으면서 `instance_announced`를 `True`로 두면 페이지가 새 번호를 끝내 알지 못해 이후 `config`를 전부 버린다 |
| 프로토콜 버전 | `ComponentRepository.vsn` | 정수. connect 때 소켓 URL에서 읽고, 페이지 쿼리스트링(`query_string`)과는 섞지 않는다. 0으로 복원하면 옛 형태만 보낼 뿐이라 안전하다 |

아래 넷은 #58이 더했고 **외부화 목록의 일부다**. 세션을 프로세스 밖으로 옮기면서 이것을 빠뜨리면
경계가 조용히 사라진다 — 새 워커가 경계 없는 연결로 세션을 이어받고, 그 연결에는 정책도 인증 세대도
로그아웃 구독도 붙지 않는다. `_auth_revalidated`를 `False`로 복원하면 재확인이 한 번 더 도는 것뿐이라
안전하지만, `True`로 복원하면 그 검사를 건너뛴다.

## 7. 버전

**diff 형태의 규칙: 클라이언트가 이해한다고 말한 형태만 보낸다.** 클라이언트는 연결 URL의 `vsn`으로 자기 버전을 말하고, 버전 n의 클라이언트는 n 이하의 모든 형태를 이해한다. 서버의 기본값은 0("아무것도 모르는 클라이언트")이다. 새 형태를 더하면 `wireview/core/rendered.py`와 `rendered.mjs`의 `PROTOCOL_VERSION`을 함께 올린다(`tests/test_comprehension_moves.py`가 둘이 같은지 본다).

| vsn | 더한 형태 |
|----:|-----------|
| 0 | 버전 신호 이전의 전부: 전체 `{"s","d","f"}`, 문자열, `{"s","d"}`, `{"u","n"}`, `{"r","d"}`, `{"p"}`, `{"c"}` |
| 1 | 없음(번호만 비워 둔다) |
| 2 | 항목 재배열 `{"k"}` (GAP-030) |
| 3 | `user_event`의 `ref`와 그것을 돌려주는 render의 `ref`, join 응답의 `vsn` (#92) |
| 4 | outbound `error` (#94) |
| 5 | outbound `joined` (#112) |
| 6 | `join`의 `ref`와 그것을 돌려주는 render·`error`·`remove`·`reload`·`joined`의 `ref` (#139, #146) |

`vsn` 3과 6은 방향이 반대다. `ref`는 클라이언트가 서버로 보내는 필드라, 옛 서버(모르는 인자에 TypeError)에 보내면 안 된다. 그래서 서버가 먼저 join 응답의 `vsn`으로 자기 버전을 알리고, 클라이언트는 그 연결에서만 `ref`를 싣는다. `vsn` 6의 join `ref`도 같다 — 옛 서버는 모르는 인자가 붙은 join을 통째로 버린다(#94). 옛 클라이언트는 render의 모르는 필드를 무시한다.

구버전이 섞이면: 옛 클라이언트와 새 서버는 옛 클라이언트가 `vsn`을 보내지 않으므로 지금까지와 바이트까지 같은 메시지를 받는다. 새 클라이언트와 옛 서버는 옛 서버가 `vsn`을 읽지 않고 옛 형태만 보내며, 새 클라이언트는 그것을 그대로 읽는다. 버전 신호가 없었다면 옛 클라이언트는 `{"k"}`를 모르는 값으로 슬롯에 넣고 `[object Object]`를 그렸을 것이다 — 롤링 배포 중 옛 JS로 열린 페이지가 새 서버에 재연결하는 흔한 경우다.

- 2026-10-01: 요소가 아직 없는 `exec_js`·`push_event`도 `stream_op`와 한 줄에서 기다리고, 그 줄은 컴포넌트마다 따로다. 클라이언트만의 변경이고 메시지 형태가 그대로라 `vsn`을 올리지 않는다.
- 2026-09-30: `remove`·`reload`·`joined`도 그 join의 `ref`를 싣는다. `vsn`을 올리지 않는다 — `vsn` 6은 아직 릴리스되지 않았고(v1.0.0rc3가 5), 이 변경은 6의 뜻을 넓힐 뿐이다. 그래서 클라이언트는 "`ref`가 없는 응답은 받는다"로 읽지 않고, 기다리는 join이 있으면 `ref`가 없는 것도 대체된 인스턴스의 것으로 버린다 — render와 같은 규칙이다. 혼합: 옛 클라이언트는 join에 `ref`를 싣지 않으므로 새 서버의 응답이 바이트까지 이전과 같다. 새 클라이언트와 옛 서버(`vsn` 5 이하를 알림)에서는 join에 `ref`가 없어 모든 응답을 오는 대로 받는다. `vsn` 6을 알리면서 이 필드들을 싣지 않는 서버는 릴리스된 적이 없다 (#146).
- 2026-09-30: `vsn` 6. `join`의 `ref`, 그 join에 답하는 render와 `error`의 `ref`. 새 클라이언트와 옛 서버(`vsn` 5 이하를 알림)에서는 `ref`를 싣지 않고 응답을 오는 대로 받는다 — 이전과 같다. 옛 클라이언트는 `ref`를 보내지 않으므로 새 서버의 응답도 이전과 바이트까지 같다 (#139).
- 2026-09-30: 클라이언트가 LiveComponent id로 `leave`를 보내지 않고, 서버는 그런 `leave`를 무시한다. 보내던 옛 번들은 무시될 뿐이라 `vsn`을 올리지 않는다 (#140).
- 2026-09-30: 인스턴스 번호가 프로세스 카운터에서 53비트 무작위 정수로 바뀌었다. 형태는 그대로 JSON 정수라 `vsn`을 올리지 않는다 (#141).
- 2026-09-30: `render`의 `instances`와 `upload_op config`의 `instance`. 세션은 떠났거나 대체된 인스턴스의 `upload_op`를 버린다. 둘 다 새 필드일 뿐 diff 형태가 아니고, 옛 클라이언트는 render의 모르는 필드를 무시하며 `config`의 남는 필드는 설정에 섞여도 읽지 않으므로 `vsn`을 올리지 않는다. 새 클라이언트는 `instance`가 없는 `config`를 옛 서버의 것으로 보고 그대로 받는다 (#137).
- 2026-09-29: `vsn` 5. outbound `joined` (#112).
- 2026-09-29: `user_event`의 `ref`가 로딩 표시를 거는 이벤트에도 실린다. `vsn` 3 이상의 서버가 이미 받던 필드라 `vsn`을 올리지 않는다 (#118).
- 2026-09-26: inbound `query_string`(클라이언트가 보내지 않은 지 오래된 명령)과 `reload`의 `legacy` 사유를 없앴다. v2 이전 서명 상태는 `invalid`다 (#99).
- 2026-09-26: `vsn` 4. outbound `error`. 표에 없는 inbound 메시지와 핸들러가 아닌 `user_event`는 연결을 닫지 않는다 (#94).
- 2026-09-19: `vsn` 3. `user_event`의 `ref`, render의 `ref`와 `vsn` (#92).
- 2026-09-19: 사용자 이벤트가 아무것도 바꾸지 않아도 `render`(`diff: null`)를 보낸다. `upload_op config`에 `id` (#90). 둘 다 옛 클라이언트가 이미 읽는 모양이라 `vsn`을 올리지 않는다.
- 2026-09-19: 연결 URL의 `vsn`과 위의 규칙. `render` 부분 diff 값에 항목 재배열 `{"k"}` 추가, 세션 상태에 프로토콜 버전 (GAP-030, #69).
- 2026-09-09: `join`의 서명 상태가 v1 봉투가 되고 만료 검사가 붙었다. 거절 시 새 outbound 명령 `reload` (#76).
- 2026-09-10: 6절의 세션 상태에 페이지 경계·인증 세대·인증 토픽·재확인 여부를 더했다. #58이 만든 연결당 상태이고, 세션 외부화(GAP-027)가 함께 옮겨야 하는 것들이다.
- 2026-09-09: 봉투가 v2가 되어 페이지의 `live_session`과 인증 세대를 싣는다. `reload`에 `live_session` 사유가 붙었고, 서버가 인증 세대 토픽으로 보내는 `session_invalidated`가 그 세대의 소켓을 닫는다 (#58).
- 2026-09-08: `render` 부분 diff 값에 comprehension과 블록 형태 추가 (GAP-025).
- 2026-09-08: 첫 정본. 코드에서 추출했으며, 이후 명령을 더하거나 필드를 바꾸면 이 문서와 `CHANGELOG.md`에 남긴다.
