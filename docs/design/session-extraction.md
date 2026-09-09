# 세션 분리 설계 (GAP-027, #60)

> 상태: 설계 정리. 2026-09-09. **착수 대상이 아니다** — 기준은 5절.
> 배경: `docs/design/transport-abstraction.md` 4~6절, `docs/implementation/wire-protocol.md`

## 1. 무엇을 분리하는가

지금 `WireviewConsumer`는 두 가지를 겸한다.

1. **Channels WebSocket 어댑터** — 연결 수명, `scope`, `channel_layer`, JSON 프레이밍.
2. **세션 로직** — join/leave, 이벤트 디스패치, 렌더와 diff, 구독 관리, 업로드, 컴포넌트가
   보내는 명령(`component_*`)의 처리.

2번은 WebSocket과 아무 관계가 없다. 다만 `Outbound`와 저장소만으로 성립하지는 **않는다** —
인증 문맥(scope의 user), 세션 주소, `Broker`(fan-out과 자기 세션 메일 수신), 그리고 이벤트가
겹치지 않게 하는 직렬 처리 규약이 함께 입력이다. "WebSocket 전송으로부터 독립적으로 만들 수
있다"와 "Outbound만 주입하면 된다"는 다른 말이다.
분리의 결과물은 `WireviewSession`이고, 컨슈머는 "소켓에서 받은 것을 세션에 넣고, 세션이 내놓은
것을 소켓으로 보내는" 어댑터로 남는다.

## 2. 지금 세션 상태가 어디에 흩어져 있나

`WireviewConsumer` 인스턴스에 붙은 것(`wireview/consumer.py`):

| 이름 | 내용 | 분리 후 |
|---|---|---|
| `self.repo` | 컴포넌트 인스턴스들(`ComponentRepository`) | 세션 |
| `self.subscriptions` | 이 연결이 구독한 채널 집합 | 세션 |
| `self.query_string` | 현재 URL의 쿼리스트링 | 세션 |
| `self.outbound` | `ChannelsOutbound` | 어댑터가 주입 |
| `self.scope` | user, cookies, session | 어댑터가 세션 생성 시 전달 |
| `self.channel_name` / `channel_layer` | fan-out 주소 | `Broker`/`Outbound` 뒤로 |

프로세스 전역에 있는 것:

| 이름 | 내용 | 문제 |
|---|---|---|
| `wireview/views.py`의 `_upload_registries` | 컴포넌트 id → `UploadRegistry` | **연결이 아니라 프로세스에 묶인다.** 업로드 HTTP 요청이 WebSocket과 다른 프로세스로 가면 지금도 실패한다 |

`_upload_registries`가 이 이슈의 진짜 난제다. 그리고 **이건 세션 분리를 기다릴 일이 아니라 지금
버그다**([#77](https://github.com/itda-work/django-wireview/issues/77)).

- 키가 컴포넌트 id 하나인데 그 id는 **페이지 안에서만** 고유하다. 같은 페이지를 탭 둘로 열면
  나중 연결이 앞선 연결의 레지스트리를 덮어쓰고, 앞선 탭의 leave가 남의 레지스트리를 pop하며
  `cleanup_all()`로 임시 파일까지 지운다. 다중 프로세스 이전에 **단일 프로세스에서 이미 깨진다.**
- 다중 프로세스에서는 HTTP 업로드가 다른 워커로 가면 404다. 재현했다(독립 인터프리터 둘,
  `Process B HTTP: 404 {"error": "Component not found"}`). `views.py:114-117`이 **토큰 검증보다
  먼저** 404를 내므로 유효한 토큰도 이 분기를 넘지 못한다.
- `disconnect()`는 업로드 레지스트리를 해제하지 않는다. 업로드 그룹은 `self.subscriptions`에
  들어가지 않아 그 정리 루프로도 지워지지 않는다.

따라서 순서가 바뀐다. 0단계는 "깨지는지 확인"이 아니라 **소유권을 연결 단위로 고치는 일**이고,
E2E는 버그 판정의 선행조건이 아니라 수정의 검증 수단이다.

단 **키를 바꾸는 것만으로는 다중 프로세스가 해결되지 않는다.** 다른 워커의 dict가 비어 있는 것은
키 모양과 무관하다. 두 갈래를 분리해서 본다.

| 갈래 | 무엇 | 이 이슈와의 관계 |
|---|---|---|
| 소유권·수명 | 연결 단위 키, 남의 것을 지우지 않는 삭제, leave·disconnect·재연결·프로세스 사망의 정리, 토큰(`component_id:config_name:ref`)에 소유권 결합 | 독립. 지금 고친다 |
| 분산 접근 | 소유 워커로의 라우팅/RPC 또는 공유 레지스트리 + 공유 파일 저장소 | 설계 선택이 필요하다. 세션 export/import 뒤로 미루지 않는다 |

후자를 "세션 상태로 옮기면 된다"로 적으면 안 된다. 레지스트리 객체는 이미 컴포넌트가 들고 있고
(`_upload_registry`), 전역 dict는 **HTTP가 그 객체를 찾는 인덱스**다. 세션 객체로 옮겨도 HTTP
요청이 그 세션을 가진 워커에 닿지 못하면 똑같이 404다.

> **진행 상황.** 소유권·수명 갈래는 #77에서, 분산 접근 갈래는 #83에서 닫혔다.
> **이 이슈의 진짜 난제였던 `_upload_registries`는 이제 없다.** 청크 엔드포인트가 업로드 상태를
> 하나도 들고 있지 않고 서명된 토큰만으로 판단하므로 인덱스 자체가 필요 없어졌다
> (`docs/design/distributed-uploads.md`). 프로세스 전역에 남은 것은 없고, 세션 분리는 컨슈머
> 인스턴스 상태만 다루면 된다.

## 3. 경계의 모양

```
브라우저 ──WebSocket── WireviewConsumer ──┐
                       (Channels 어댑터)   │
                                          ├──> WireviewSession ──> ComponentRepository
프런트(Go/Elixir) ──gRPC/NATS── 다른 어댑터 ┘        │
                                                   └──> Outbound (보내기) / Broker (fan-out)
```

세션이 받는 것은 `docs/implementation/wire-protocol.md`의 inbound 메시지, 내놓는 것은 outbound
메시지다. 어댑터는 그 둘을 자기 전송 방식으로 옮기기만 한다.

`command_*`는 inbound 핸들러(세션의 공개 API), `component_*`는 컴포넌트가 세션에게 보내는 mail의
핸들러다. 후자는 지금 채널 레이어를 한 번 왕복한다(`WireviewMeta.send` → 세션 채널 → 컨슈머).
분리해도 이 왕복은 **저절로 사라지지 않는다.** `WireviewMeta.send`(`core/meta.py:446`)는 pending이 아니면
`_do_send`를 거쳐 자기 세션에도 `Broker.send_to_session`으로 보내고, 메서드가 어느 클래스에 있든 그 호출은 그대로다.
로컬 mailbox 최적화는 추출과 **다른 변경**이고, 그때도 순서 의미를 먼저 봐야 한다 — `deffer()`는
후속 이벤트를 메일로 미루는 것이 곧 재진입 방지이고, 즉시 await로 바꾸면 현재 이벤트가 끝나기
전에 다음 핸들러가 들어온다.

## 4. 단계

| 단계 | 내용 | 검증 |
|---|---|---|
| 0 | 업로드 소유권을 연결 단위로 고친다 (#77). 세션 분리와 독립이다 | 같은 id의 연결 둘, disconnect 정리, 다중 프로세스 업로드 |
| 1 | `WireviewSession` 추출, 컨슈머는 어댑터로 | 기존 테스트 전부 통과. 새 테스트는 세션을 소켓 없이 직접 구동 |
| 2 | 세션 상태 export/import | 컴포넌트 상태 + `Rendered` 스냅샷 + 구독 집합 + 쿼리스트링. **이 목록으로는 부족하다** — 아래 4-1 |
| 3 | 업로드 분산 접근(라우팅 또는 공유 저장소) | 스티키 없는 다중 워커에서 업로드 성공 |
| 4 | 프런트 어댑터(Go 또는 Elixir) | 착수 시 결정 |

1단계의 값은 테스트 **비용**이지 가능성이 아니다. 소켓 없이 컨슈머 경로를 도는 방법은 지금도
있다 — `tests/test_streams.py`는 컨슈머를 직접 만들어 `component_stream_op`에 실제 페이로드를
넣고, `tests/test_nats_layer.py`는 `WebsocketCommunicator`로 connect→join→render를 돈다.
GAP-014(`stream(limit=)`)가 컨슈머 hop에서 죽은 것을 놓친 이유는 구조가 아니라 **그 hop을 아무도
테스트하지 않았기 때문**이다(#65에서 브라우저로 발견, #67에서 시그니처 가드 추가).

그러므로 이 구멍을 메우는 일은 1단계에 종속되지 않는다. 지금 당장 컨슈머 + 가짜 `Outbound`로
join→이벤트→render와 session mail을 덮는 테스트를 쓸 수 있고, 써야 한다. 1단계는 그 테스트를
**싸게** 만든다(Channels 의존 없이 세션만 세운다).

### 4-1. export/import가 덮지 못하는 것

wire-protocol §6의 목록(상태·`Rendered`·구독·쿼리스트링)은 **체크포인트로 충분하지 않다.**

- `user`와 `wire`는 상태에서 제외된다(`core/component.py:138`). 인증 문맥을 어디서 다시 얻을지 정해야 한다.
- 아직 join되지 않은 children, LiveComponent의 부모 id, 대기 중인 joined/update 큐(`repository.py`).
- `attach_hook`으로 붙인 동적 훅과 **실행 중인 asyncio task**(`start_async`/`assign_async`). task는 JSON으로 옮길 수 없다 — 취소할지, 완료를 기다릴지, 재실행할지 정해야 한다.
- `flush_pending()`이 들고 있는 대기 명령과 브로드캐스트.

그래서 2단계의 첫 결정은 직렬화 형식이 아니라 **"어느 시점이 이동 가능한 안전 지점인가"** 다.
import가 새 로그인 검증이나 정책 제한을 건너뛰는 통로가 되어서도 안 된다.

## 5. 착수 기준 (갱신)

기존 기준: "프로세스당 동시 연결이 수천을 넘고 유휴 연결이 많은 워크로드가 실제로 생길 때."

**2026-09-09에 이 기준이 뒤로 밀렸다.** uvicorn의 연결당 211.5 KB가 permessage-deflate를 끄면
50.6 KB다(`transport-abstraction.md` §5-2-1). 같은 조건에서 **약 4배**의 유휴 연결을 든다.

수치의 범위를 분명히 해 둔다. 이 값은 macOS 호스트, InMemory 레이어, 단일 프로세스, 항목 5개
컴포넌트, 연결 2,000개에서 잰 것이다. "연결 10,000개면 0.5 GB"는 이 기울기의 **외삽**이지 실측이
아니다. 벤치는 baseline과 join 후의 프로세스 RSS 차를 재므로 그 실험에서 실제로 할당된 버퍼는
포함되어 있다. 포함되지 않은 것은 프로세스 기본 RSS, 더 큰 컴포넌트, 운영 채널 레이어, 그리고
다른 부하·백프레셔에서 더 자랄 버퍼다. 실제 배포 구성의 목표 연결 수에서 다시 재야 한다.

그래서 착수 기준을 메모리에서 다른 축으로 옮긴다.

| 축 | 기준 |
|---|---|
| 메모리 | 목표 배포 구성과 목표 연결 수에서 다시 잰 값이 예산을 넘을 때. deflate를 끄면 연결당 증분이 약 1/4이 되지만, 그것이 "병목이 아니다"를 뜻하지는 않는다 |
| **CPU** | 이벤트 처리량이 프로세스 확장으로 안 되고, **그 원인이 연결 I/O로 확인된** 지점. 렌더·DB가 원인이면 프런트를 바꿔도 병목은 그대로다(`transport-abstraction.md` §2) |
| **테스트 가능성** | 1단계 단독. 위 4절의 이유로 이건 워크로드와 무관하게 값이 있다 |
| **업로드 정합성** | 기준이 아니다. 이미 깨져 있고(#77) 세션 분리와 독립으로 고친다 |

즉 지금 할 일은 **0단계(#77, 업로드 소유권)** 와 **컨슈머 경로 테스트 보강**이고, 둘 다 세션
분리를 기다리지 않는다. 1단계는 그 뒤에 비용 절감으로 하면 되고, 2·4단계는 워크로드가 생길
때까지 열어만 둔다.

> 이 절의 수정은 Codex(gpt-6-astra) 리뷰 두 라운드(2026-09-09)의 지적을 반영한 것이다. 원래는 "다중 프로세스
> 업로드가 깨지는지 실측"을 0단계로 두었고, "지금은 컨슈머 hop을 테스트할 방법이 없다"고 적었다.
> 둘 다 틀렸다.

## 6. 하지 않기로 한 것

- **프로세스 격리형 중첩 LiveView.** BEAM 프로세스가 주는 성질이고 ASGI에는 그 단위가 없다.
  `docs/FEATURE-GAP.md` 2.10에 설계상 제외로 적었다.
- **세션을 무조건 외부 저장소로.** 상태를 프로세스 밖에 두면 매 이벤트가 직렬화·역직렬화를 탄다.
  2단계는 "필요할 때 옮길 수 있게 export/import를 갖추는 것"이지 "항상 밖에 두는 것"이 아니다.
