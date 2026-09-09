# 세션 분리 설계 (GAP-027, #60)

> 상태: 설계 정리. 2026-09-09. **착수 대상이 아니다** — 기준은 5절.
> 배경: `docs/design/transport-abstraction.md` 4~6절, `docs/implementation/wire-protocol.md`

## 1. 무엇을 분리하는가

지금 `WireviewConsumer`는 두 가지를 겸한다.

1. **Channels WebSocket 어댑터** — 연결 수명, `scope`, `channel_layer`, JSON 프레이밍.
2. **세션 로직** — join/leave, 이벤트 디스패치, 렌더와 diff, 구독 관리, 업로드, 컴포넌트가
   보내는 명령(`component_*`)의 처리.

2번은 WebSocket과 아무 관계가 없다. `Outbound`(보내는 쪽)와 컴포넌트 저장소만 있으면 성립한다.
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

`_upload_registries`가 이 이슈의 진짜 난제다. 나머지는 옮기면 끝나지만 이것은 **HTTP 업로드
엔드포인트와 WebSocket 세션이 같은 프로세스에 있다는 가정**을 코드에 박아 둔 것이다. 다중 프로세스
배포에서 스티키 라우팅 없이 업로드가 되는지 아무도 검증하지 않았다. 분리 작업의 첫 단계는
"이게 지금 다중 프로세스에서 깨지는가"를 실측하는 일이어야 한다.

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
분리하면 같은 프로세스 안에서는 왕복이 사라진다 — **부수적인 성능 이득이지만 이걸 근거로 삼지
않는다.** 실측 전에는 주장하지 않는다.

## 4. 단계

| 단계 | 내용 | 검증 |
|---|---|---|
| 0 | 업로드 레지스트리가 다중 프로세스에서 깨지는지 실측 | 프로세스 2개 + 스티키 없는 라우팅에서 업로드 E2E |
| 1 | `WireviewSession` 추출, 컨슈머는 어댑터로 | 기존 테스트 전부 통과. 새 테스트는 세션을 소켓 없이 직접 구동 |
| 2 | 세션 상태 export/import | 컴포넌트 상태 + `Rendered` 스냅샷 + 구독 집합 + 쿼리스트링. wire-protocol §6 |
| 3 | 업로드 레지스트리를 세션 상태나 외부 저장소로 | 0단계에서 확인한 실패가 사라지는지 |
| 4 | 프런트 어댑터(Go 또는 Elixir) | 착수 시 결정 |

1단계만으로도 값이 있다. **세션을 소켓 없이 테스트할 수 있게 된다** — 지금은 join → 이벤트 →
렌더의 전 구간을 단위 테스트로 돌릴 방법이 없고, 그래서 GAP-014(`stream(limit=)`)가 컨슈머 hop에서
`TypeError`로 죽는 것을 아무도 못 잡았다(#65에서 브라우저로 발견). 그 구멍은 1단계가 닫는다.

## 5. 착수 기준 (갱신)

기존 기준: "프로세스당 동시 연결이 수천을 넘고 유휴 연결이 많은 워크로드가 실제로 생길 때."

**2026-09-09에 이 기준이 뒤로 밀렸다.** uvicorn의 연결당 211 KB 중 159 KB가 permessage-deflate였고
(`transport-abstraction.md` §5-2-1), 끄면 51 KB다. 같은 메모리로 **약 4배**의 유휴 연결을 든다.
연결 10,000개가 2 GB에서 0.5 GB가 된다.

그래서 착수 기준을 메모리에서 다른 축으로 옮긴다.

| 축 | 기준 |
|---|---|
| ~~메모리~~ | ~~유휴 연결의 RSS~~ — deflate를 끄면 당분간 병목이 아니다 |
| **CPU** | 이벤트 처리량이 프로세스 확장으로 안 되는 지점. 지금은 프로세스 4개에서 이벤트 3배로 선형에 가깝다 |
| **테스트 가능성** | 1단계 단독. 위 4절의 이유로 이건 워크로드와 무관하게 값이 있다 |
| **업로드 정합성** | 0단계에서 다중 프로세스 업로드가 깨진다고 확인되면, 그건 성능이 아니라 **버그**다. 그때는 기준과 무관하게 3단계를 한다 |

즉 지금 할 수 있는 일은 **0단계(실측)** 이고, 그 결과에 따라 1단계와 3단계의 우선순위가 정해진다.
2·4단계는 여전히 워크로드가 생길 때까지 열어만 둔다.

## 6. 하지 않기로 한 것

- **프로세스 격리형 중첩 LiveView.** BEAM 프로세스가 주는 성질이고 ASGI에는 그 단위가 없다.
  `docs/FEATURE-GAP.md` 2.10에 설계상 제외로 적었다.
- **세션을 무조건 외부 저장소로.** 상태를 프로세스 밖에 두면 매 이벤트가 직렬화·역직렬화를 탄다.
  2단계는 "필요할 때 옮길 수 있게 export/import를 갖추는 것"이지 "항상 밖에 두는 것"이 아니다.
