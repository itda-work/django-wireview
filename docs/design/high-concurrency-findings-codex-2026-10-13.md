# 고동접 아키텍처 조사 결과

조사 기준일: **2026-10-13**. 외부 문서는 이날 열람했다. 별도 날짜가 없는 링크는 갱신형 공식 문서이며, 열람일을 릴리스일로 해석하지 않는다. 저장소는 읽기만 했고 새 벤치는 실행하지 않았다.

기준 저장소: `django-wireview 1.5.1`, HEAD `a4e957c4fc5333cf243f363c272a0894dae79ae2`. 아래 **저장소 실측**은 그 저장소에 보관된 과거 실험 결과, **외부 실측**은 해당 작성자가 공개한 결과, **추정/제안**은 이번 조사에서 도출한 판단이다. 제품 간 수치는 동일 조건 경쟁 벤치가 아니다.

## 먼저 바로잡을 전제

wireview가 언제나 연결마다 렌더하는 것은 아니다. 현재는 `shared_render`와 `Broadcast`가 이미 구현되어 있다. 특히 Broadcast는 Django에서 HTML·프레임을 한 번 만들고 수신 연결에서는 id만 끼운다. LiveView도 일반적인 `handle_info` 갱신에서는 각 view가 따로 렌더한다. 따라서 비교 단위는 언어 이름이 아니라 **공유 가능한 업데이트인가, 개인화 렌더인가, 유휴 연결인가**다.

또한 wireview에는 temporary assign 초기화에 대한 읽기 추적과 이전 출력 보존이 이미 있다. HEEx처럼 모든 일반 필드의 변경 의존성을 컴파일해 계산을 생략하는 엔진과는 다르다. keyed diff 역시 이미 구현된 기능이다. 이들을 새로 만들 기능으로 계산하면 선택지의 비용과 효과를 왜곡한다.

근거: [현재 프로젝트 지침](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/CLAUDE.md), [Broadcast 설계·구현 결과](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-patch.md), [temporary assign 결정](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/temporary-assigns-change-tracking.md).

### 저장소에서 확인한 기준선

| 부하·실험 | 저장된 결과 | 읽어야 할 의미 |
|---|---|---|
| Board 50개 요소, 1,000연결, uvicorn 1프로세스·InMemory, 5회, 2026-10-06 | shared_render OFF 429.0ms → ON 89.6ms; CPU/연결 436.8 → 109.0µs | 동일 렌더 공유 효과. 다른 실험군의 shared 중앙값은 125.7ms로 분포가 갈림. 89.6ms를 보편 상수로 쓰지 않음 |
| 동일 `<li>` stream 삽입, 1,000연결, uvicorn 1프로세스, 3회, 2026-10-06 | notification: InMemory 379.4 / Redis 742.6 / NATS 276.3ms; Broadcast: 18.8 / 19.7 / 18.0ms | 이미 수신자별 핸들러·렌더를 없앤 경로. Board 전체 갱신과 직접 비교 불가 |
| 위 Broadcast / FastAPI 기준 | Broadcast CPU 17.4–19.0µs/연결, FastAPI 11.5µs; NATS 18.0ms / FastAPI 12.0ms | 프레임도 205B / 80B로 다름. FastAPI와 차이 전부를 Python 렌더 탓으로 돌릴 수 없음 |
| ASGI 비교, 2,000연결·items=5 | uvicorn 압축 해제 약 61KB/연결, 기본 약 222.3KB; daphne 55.6KB, Granian 193.5KB | 언어보다 압축·WS 구현이 메모리에 큰 영향. 앱 상태 크기 바뀌면 수치도 바뀜 |
| NATS·uvicorn 1 → 4프로세스 | 3,056 → 9,784 events/s; 지연 712 → 161ms; 4프로세스 약 70KB/연결 | CPython 프로세스 확장은 이미 유효한 대조군. 단일 프로세스 GIL을 전체 서비스 코어 미활용과 동일시하면 안 됨 |

원본: [PERFORMANCE.md의 결과 파일·환경·실행 조건](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/PERFORMANCE.md). 대표 환경은 M5 Pro 18코어, macOS 27, Python 3.14.7, Django 6.0, Channels 4.3.2, asgiref 3.11.0, uvicorn 0.38.0, websockets 15.0.1이다. 개별 과거 실험에는 Python 3.12.14 등 다른 환경이 있으므로 결과 파일 단위로 비교해야 한다.

## 1. Phoenix LiveView 1.2.12의 이점과 경계

### HEEx는 출력 비교 전의 계산을 생략한다

**버전 사실:** LiveView 1.2.12 패키지 갱신일은 2026-09-16이다. [Hex 패키지](https://hex.pm/packages/phoenix_live_view/1.2.12).

HEEx 엔진은 정적 문자열과 동적 표현식을 나누고, 동적 부분이 읽는 assign을 컴파일 시 분석한다. `assign`/`update`가 관리하는 `__changed__`를 동적 함수가 받아, 관련 assign이 바뀌지 않으면 해당 위치를 `nil`로 돌려 계산과 전송을 생략한다. `@user.name` 같은 필드 접근은 관련 필드의 변경까지 구분한다. 정적 텍스트는 최초 전달 후 매번 보내지 않는다. 이는 HTML 전체 렌더 뒤 diff하는 방식보다 **변하지 않은 표현식의 실행 비용**을 줄인다. [1.2.12 엔진 소스](https://hex.pm/packages/phoenix_live_view/1.2.12/files/lib/phoenix_live_view/engine.ex).

다만 `Repo.all(User)`처럼 assign과 무관한 외부 DB 변화는 자동 감지하지 않는다. assign 변경으로 화면 갱신을 명시해야 한다. 임의 지역 변수는 추적을 방해하며, `if`/`case`/`for`가 도입한 변수와 구별해야 한다. `<.child {assigns}>`처럼 전체 assign을 넘기면 좁은 의존 추적의 이점을 잃는다. `Map.put`으로 assign 메타데이터를 우회하는 방식도 피해야 한다. 함수 내부의 DB·시간·외부 상태를 완전한 반응형 그래프로 분석하는 것은 아니다. [1.2.12 변경 추적 가이드](https://phoenix-live-view.hexdocs.pm/1.2.12/assigns-eex.html).

**추정:** 개인화 화면에서 필드 하나만 바꾸고 나머지 비싼 계산이 안정적이면 wireview보다 유리할 가능성이 크다. 반대로 매번 모든 assign이 바뀌거나 ORM 시간이 지배하면 이 이점은 작다. wireview와 같은 앱의 직접 측정은 확보하지 못했다.

### comprehension과 stream

1.2.12 comprehension은 반복되는 정적 구조를 공유하고 항목별 변경을 추적한다. 기본 항목 식별은 위치이므로 앞쪽 삽입은 뒤 항목 변경을 유발할 수 있다. 안정적인 `:key={item.id}`가 이를 줄인다. “LiveView는 리스트가 변하면 언제나 모든 항목을 다시 보낸다”는 구버전 설명을 적용하면 안 된다. 이 개선은 1.1 계열에서 도입됐다. 항목 비교용 상태는 서버 메모리를 사용한다. [1.2.12 가이드](https://phoenix-live-view.hexdocs.pm/1.2.12/assigns-eex.html), [1.1 릴리스 설명](https://www.phoenixframework.org/blog/phoenix-liveview-1-1-released).

Stream은 insert/delete 명령으로 DOM의 목록을 갱신하고, 전송한 항목을 서버의 stream assign에서 비운다. 대형 목록 전체를 view assign에 계속 보관하지 않아도 되지만, DB·별도 캐시에 같은 목록을 보관하면 총 메모리는 그대로 남는다. 클라이언트 목록 크기 제한도 별도다. 재접속 시 목록 재구성 책임은 앱에 있다. [1.2.12 stream 구현·API](https://hex.pm/packages/phoenix_live_view/1.2.12/files/lib/phoenix_live_view.ex).

### PubSub, Channels fastlane, LiveView 렌더는 서로 다르다

PubSub는 토픽 가입자에게 메시지를 분배한다. Channels fastlane은 가로채지 않는 broadcast를 channel callback마다 처리하지 않고 transport로 전달하며, 같은 serializer에 대해 인코딩 결과를 재사용한다. “클러스터 전체에서 반드시 단 한 번”이라는 뜻은 아니다. 노드·dispatcher·serializer 경계에 따라 인코딩이 반복될 수 있고 `intercept`된 이벤트는 연결별 처리를 거친다. [Channels fastlane의 고정 커밋 소스](https://github.com/phoenixframework/phoenix/blob/2ca60ffe811c0e585835cfc309b645c3a4190df1/lib/phoenix/channel/server.ex#L94-L122).

LiveView가 PubSub 메시지를 받아 각 view의 assigns를 바꾸면 **각 view의 HEEx 계산·diff는 남는다**. fastlane을 켠다고 이를 공유 렌더로 바꾸지 못한다. 현재 PubSub 문서는 v2.4.1이며 배포 노드 간 전파와 pool 설정을 다룬다. 과거 custom dispatcher 예제의 API를 그대로 가져오기 전에 현재 sender API 및 deprecation을 확인해야 한다. [Phoenix.PubSub v2.4.1](https://phoenix-pubsub.hexdocs.pm/Phoenix.PubSub.html).

**wireview와 비교:** Broadcast의 이미 직렬화된 `[prefix, suffix, min_vsn?]`는 수신 컴포넌트 id가 다르므로 Phoenix의 동일 바이트 fastlane에 그대로 들어맞지 않는다. id 삽입과 접근 가능성 필터를 하는 전용 gateway 경로가 필요하다. 그렇더라도 렌더를 연결마다 실행할 이유는 없다. [wireview Broadcast](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-patch.md).

### 메모리와 2M 벤치의 정확한 의미

Erlang 효율 가이드의 예제는 새 프로세스 327 words = 2,616 bytes, 초기 heap 233 words를 제시한다. 문서의 예제 런타임은 OTP 27이며 열람한 문서 사이트 버전과 구별해야 한다. 이것은 **빈 BEAM 프로세스 메모리**다. LiveView assigns, 렌더 상태, mailbox, WS transport, TLS, 커널 버퍼까지 합친 연결당 RSS가 아니다. [Erlang 프로세스 가이드](https://www.erlang.org/doc/system/eff_guide_processes.html).

2015-11-03 Phoenix 글의 2M은 40코어·128GB 서버, 별도 Tsung 부하 발생기 수십 대, OS 파일·소켓 한도 조정 조건이다. 클라이언트는 `rooms:lobby`에 가입하고 오래 대기한다. join broadcast도 제거했다. PubSub/ETS 샤딩 후 약 1초 broadcast를 보고했지만 지속적인 개인별 이벤트·DB·HEEx 렌더 벤치가 아니다. 당시 개발 중 최적화가 포함되었고 LiveView 등장 전 실험이다. 따라서 **LiveView 1.2.12가 200만 활성 UI를 처리한다는 근거가 아니다**. [원문·실험 설정](https://www.phoenixframework.org/blog/the-road-to-2-million-websocket-connections).

## 2. 연결 계층 분리의 선례

아래 구조 설명은 공식 문서 기준이다. 수치는 별도 표의 버전·부하 범위로 제한한다.

| 후보 | 상태 위치와 이벤트 왕복 | broadcast 경로 | 장애·재연결 |
|---|---|---|---|
| AnyCable | 공식 WS 구현은 **Go**인 `anycable-go`. 연결 식별자·connection/channel state는 gateway, 앱 객체는 RPC마다 복원. 브라우저 → Go → gRPC 또는 HTTP RPC 앱 → 응답·상태 차이 → Go → 브라우저 | 앱 → HTTP/broker → Go 노드의 구독자. 일반 이벤트 RPC와 공유 publish를 분리 | 앱 재배포와 소켓 수명 분리 가능. gateway 장애는 재연결 필요. reliable streams의 epoch/offset·보관 범위 안에서 복구; 영구 이벤트 원장과 다름 |
| Pushpin/Fanout GRIP | proxy가 연결·hold·subscription 보유. WS-over-HTTP 모드에서 OPEN/TEXT/CLOSE를 HTTP 이벤트 묶음으로 backend에 전달, 응답으로 제어·데이터 반환 | backend publish → Pushpin → WS/SSE/long-poll 연결 | `prev-id` 등 연속성 장치는 있지만 영구 exactly-once 아님. 앱 이력·cursor·snapshot 필요. **그냥 upstream WS proxy 모드면 backend 소켓도 유지** |
| Centrifugo | Go 서버가 연결·인증 결과·토픽 가입·presence 등을 보유; 도메인 DB는 앱. HTTP/gRPC proxy의 connect/subscribe/publish/RPC 또는 일반 앱 HTTP API 사용 | 앱 HTTP/gRPC publish → 메모리/Redis 등 broker → 노드 → 구독자 | epoch+offset 기반 제한된 history. `recovered=false`면 앱 재동기화. 기본 history는 비활성; 메모리 restart 손실과 Redis 보관 정책 구별 |
| Mercure | hub가 **SSE 하향 연결**과 구독·history를 보유; 명령은 브라우저 → Django HTTP → 응답, 업데이트는 별도 SSE | Django publish HTTP → hub → topic selector/JWT에 맞는 구독자 | EventSource 재접속·Last-Event-ID. history 범위를 벗어나면 snapshot. JWT 만료 후 인증 갱신과 EventSource 재생성은 앱 책임 |
| Phoenix 앞단 + Django | Phoenix 소켓/channel/actor 상태, Django 도메인·DB. 자체 HTTP/gRPC/queue 계약으로 명령 전달 | Django 완료 이벤트 → bus → Phoenix PubSub → 클라이언트 | BEAM supervision은 프로세스 복구이지 사라진 UI 상태의 자동 복구가 아님. cursor·idempotency·resnapshot 설계 필요 |

출처: [AnyCable architecture](https://docs.anycable.io/architecture), [RPC](https://docs.anycable.io/anycable-go/rpc), [broker/recovery](https://docs.anycable.io/anycable-go/broker); [Pushpin usage](https://pushpin.org/docs/usage/), [GRIP](https://pushpin.org/docs/protocols/grip/), [advanced](https://pushpin.org/docs/advanced/); [Centrifugo proxy](https://centrifugal.dev/docs/server/proxy), [history/recovery](https://centrifugal.dev/docs/server/history_and_recovery); [Mercure concepts](https://mercure.rocks/docs), [reconnection/history](https://mercure.rocks/docs/concepts/reconnection-and-history).

**버전 주의:** AnyCable RPC 문서에는 1.6.0 예제가 있으나 이를 2026 최신 버전으로 단정하지 않는다. Pushpin·Centrifugo 링크는 버전 고정 없는 현행 문서이고, Mercure 사이트는 Hub 1.0을 안내한다. AnyCable의 공식 참조 구조를 “Go/Rust WS 서버”라고 묶는 것은 부정확하다. 이 조사에서 공식 Rust AnyCable 서버 근거는 확인하지 못했다.

### 공개 수치와 비교의 한계

| 출처·날짜 | 공개 결과·조건 | 한계 |
|---|---|---|
| AnyCable 비교, 2026; 관련 Evil Martians 글 2026-06-24 | Pro 약 18KB/유휴 연결. 10,000구독자·40발행자·초당100 broadcast = 100만 전달/s 시험에서 Pro p50/p99 4/12ms, OSS 4/15ms | 공급자 자체 실험. 18KB는 Pro 수치. Rails RPC·템플릿 렌더 처리량이 아님 |
| Centrifuge/Centrifugo 글, 2020-02-10 | 100만 연결, 20pod·합계40CPU·27GB; 5Redis shard. 100B 메시지 약2,800만/분, p99 ≤200ms; 연결당 개인+10인그룹 두 채널, 분당20만 연결 교체 | 당시 **Centrifuge 라이브러리** 부하시험. 현대 Centrifugo 한 프로세스의 같은 성능으로 일반화 불가 |
| Centrifugo PRO, 2026-08-17 | 16vCPU EPYC Genoa KVM·30GB, 20만 유휴 연결·가입 없음·25초 ping. 기본27KB → 튜닝10.9KB/연결; CPU0.94 →0.45코어. 별도100만 실험 RSS11.3GB, CPU2.33코어, 커널까지 약15.5GB | PRO 옵션과 유휴 부하. 100만 사례는 단일 run. 메시지 전송·개인화 렌더 성능 제외 |
| Mercure HA 운영 문서, 열람 2026-10-13 | 공급자는 EC2 t3.micro에서 OSS 4만, HA 버전 20만 동시 연결을 제시 | 구체적 앱 갱신률·렌더·지연·모든 설정 미공개. 확장판 기능·비용을 OSS와 혼동하지 않음 |
| Phoenix 2015 | 앞 절의 2M 소켓 | LiveView 벤치 아님 |
| Pushpin/Fanout | 이번 조사에서 하드웨어·버전·부하를 함께 재현할 수 있는 적절한 수치 확보 못함 | 아키텍처 적합성과 미확인 처리량을 분리 |

출처: [AnyCable 비교](https://anycable.io/compare/nodejs-websocket/), [작성자 벤치 설명](https://evilmartians.com/chronicles/choose-your-fighter-benchmarking-5-websocket-servers-for-nodejs), [Centrifuge 2020 실험](https://centrifugal.dev/blog/2020/02/10/million-connections-with-centrifugo), [Centrifugo PRO 2026 유휴 튜닝](https://centrifugal.dev/blog/2026/08/17/tuning-idle-connections), [Mercure HA](https://mercure.rocks/docs/production/high-availability).

### Phoenix + Django의 공개 사례는 있으나 UI 성능 증거는 약하다

Five Niners의 EVCMS 사례는 Django/DRF를 ORM·업무 시스템으로, Phoenix를 OCPP 1.6/2.0.1 충전기 연결 gateway로 사용한다. 충전기별 supervised process와 Redis Streams를 두고, outbound는 충전기별 stream, inbound는 공유 lifecycle stream으로 연결했다. 공개 설명의 부하시험은 **충전기 200개·worker 2개에서 연결 오류 0**이다. 2026년 사례이나 하드웨어·상세 버전·tail latency·복구 보장이 충분히 공개되어 있지 않다. 협력 업체가 발행한 설계·납품 설명이며, 대규모 실제 운영을 독립 검증한 보고서는 아니다. [공급자 EVCMS 사례](https://fiveniners.com/EVCMS%20Case%20Study).

**추정:** “Django 도메인을 남기고 Phoenix가 장기 소켓을 맡는다”는 선례로는 유효하다. 10만 wireview 사용자나 HEEx 전환 효과의 근거로는 부족하다. 같은 구조를 선택할 때 도메인 메시지와 소켓 생명주기를 분리한다는 점만 가져와야 한다.

## 3. wireview용 하이브리드 설계

### 먼저 보존해야 할 현재 계약

- 서명 `data-state`는 단순 필드 JSON이 아니다. v2 envelope에는 클래스, live_session, 인증 세대, 로그인·CSRF 식별, state가 들어간다. TimestampSigner 만료·키 순환·재서명 정책도 있다. 기존 token을 gateway가 “이미 검증됨”으로 영구 신뢰하면 안 된다.
- `live_session` 경계는 HTTP와 join에서 동일하게 authorize하며, 인증 세대 `_wireview_auth_gen`은 세션 키와 별개다. 연결 identity snapshot과 최신 쿠키 차이는 renew/reload 흐름으로 처리한다. 권한 철회는 session group과 PatchHub auth 경로 양쪽에서 전달한다.
- `WireviewSession`에는 component state 외에 마지막 렌더, 하위 트리, 구독, query, tasks/mailbox, uploads, instance, vsn, hold 상태가 있다. 서명 상태만 보내는 RPC로 현재 세션을 완전히 재현할 수 없다.
- handler 안 Django ORM과 트랜잭션, sync/async bridge, DB 연결 정리 규칙을 유지해야 한다. `DJANGO_ALLOW_ASYNC_UNSAFE`로 편법 병렬화하면 안 된다.
- Broadcast는 정확한 클래스·topic·reachable을 확인한 후 id만 삽입한다. 일반 필드 변경과 일반 render를 금지하는 이유는 `_last_rendered`와 signed state의 일관성이다.
- join snapshot과 patch 순서에는 hold token이 있다. **실제 socket write 완료 후** 해제해야 한다. 현재 held+pending 한도 1,000frame 초과나 안전하지 않은 hold 만료는 close 1013 후 재동기화하며, 인증 철회는 4001 경로다.

근거: [프로젝트 계약](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/CLAUDE.md), [transport 분리](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/transport-abstraction.md), [wire protocol](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/implementation/wire-protocol.md), [Broadcast lifecycle](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-patch.md).

### (a) Phoenix는 소켓·fanout, Django는 기존 component/렌더

**제안 경로:** 브라우저 wireview → Phoenix 전용 WS endpoint → 연결별 순서를 보존한 다중화 RPC → Django `WireviewSession` → 렌더/서명/diff → Phoenix send queue → 브라우저. Backend에 사용자별 WS를 다시 열지 않고, worker별 적은 수의 RPC 연결을 공유한다. 일반 Phoenix Channels 프레임과 wireview raw JSON command/payload는 다르므로 endpoint/serializer adapter가 필요하다.

Django worker에 세션을 pin하면 변경량이 가장 작다. 이때 **Python session 메모리와 개인별 렌더 CPU는 남고**, Python WS/TLS/압축/heartbeat 스택만 앞단으로 이동한다. 기존 Outbound/Broker 분리와 `Rendered.to_dict/from_dict`가 출발점이지만 export/import는 완성된 무상태 세션 계약이 아니다. `session_id → worker, epoch` 라우팅과 worker 실패 시 rejoin이 필요하다. RPC 큐가 렌더 지연을 없애주지는 않는다. [현재 transport 분석](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/transport-abstraction.md).

Broadcast는 Django publisher에서 한 번 생성 → bus → Phoenix 각 노드 → local subscriber별 id 삽입으로 보낸다. Phoenix 쪽에 class/topic/id/instance/vsn/reachable/auth-generation/hold 정보를 동기화해야 한다. 모든 patch마다 Django RPC를 호출하면 분리의 목적을 잃는다. 그러나 메타데이터가 늦게 삭제되면 떠난 component나 철회된 사용자에게 전송할 수 있으므로 세션 epoch·subscription revision으로 오래된 메시지를 차단해야 한다.

join 응답을 RPC로 받았다는 사실은 브라우저에 썼다는 뜻이 아니다. Phoenix가 snapshot write 완료를 알리고 나서 해당 hold를 푸는 프로토콜이 필요하다. 제안하는 ACK는 **transport에 쓰기 완료**이지 브라우저 DOM 적용 보장이 아니다. gateway/worker 사이 timeout·중복·순서 역전의 상태 기계를 별도로 검증해야 한다.

`mount()`는 Django component 단위 계약으로 유지 가능하다. 기존 socket 없는 테스트가 새 RPC 라우팅·gateway queue·인증 철회를 검증하지는 않으므로 별도 adapter 통합 시험이 필수다. backend 장애에서는 작업 결과가 불명확한 명령을 무조건 재시도하지 않는다. command id와 업무 단위 멱등성, DB commit 이후 outbox 발행으로 중복 쓰기/유실 경계를 정한다. 이 문단들은 **설계 제안이며 구현·실측 없음**이다.

**메모리 추정의 한계:** 기존 문서의 daphne 미join 39.2KB, join5 46.7KB 차이만 보면 세션 증분은 약7KB다. 하지만 이는 특정 앱·서버의 과거 측정이다. 이를 근거로 총 RAM이 85% 줄어든다고 약속할 수 없다. gateway RSS, broker, RPC 버퍼, 남은 Python 상태, kernel까지 합쳐 측정해야 한다.

### (b) LiveView가 화면을 소유, Django는 도메인·ORM·관리자

**제안 경로:** 브라우저 → Phoenix LiveView event → Django 업무 API → DB transaction → 반환 DTO/업무 이벤트 → LiveView assign 변경 → HEEx diff. PubSub 수신 때도 필요한 DTO만 갱신한다. 화면 상태와 마지막 렌더는 BEAM에 두고, ORM 객체·QuerySet을 RPC로 넘기지 않는다. 단순 UI 이벤트는 Django 호출 없이 처리할 수 있다. 비싼 업무 이벤트는 여전히 Django·DB 용량에 제한된다.

장점은 개인화 렌더의 변경 추적과 scheduler 기반 코어 활용이다. 비용은 Python UI component·template·hook을 Elixir/HEEx로 옮기는 일이다. 기존 wireview signed state와 `mount()`는 LiveView 상태·LiveViewTest로 **대체**된다. Django 업무 API·ORM 테스트는 남지만 현재 UI 테스트를 그대로 돌릴 수 없다. Broadcast도 자동 호환되지 않는다. 공유 stream HTML을 고집하면 별도 DOM 소유권 경계가 필요하고, 기본 LiveView 경로에서는 view별 stream 명령/렌더 비용을 측정해야 한다.

**인증 제안:** Django를 인증 권한의 원천으로 유지한다. 같은 origin의 reverse proxy 아래에서 Django 로그인 후 짧은 수명의 Phoenix용 token을 발급한다. subject·tenant·audience·expiry·auth generation·허용 live_session을 담고 서명 키를 명시적으로 관리한다. 대안은 Phoenix가 Django에 session introspection하는 방식이다. Django cookie/session 저장 포맷을 Phoenix session과 동일하다고 간주하거나 Django SECRET_KEY를 무조건 공유하지 않는다. HTTP mount와 socket mount 양쪽 검증, 중요 업무 API 재인가, logout/세대 변경 bus에 의한 live socket 종료가 필요하다. LiveView의 `live_session`과 wireview의 동명 개념은 자동 호환 계약이 아니다. [LiveView 1.2.12 보안 모델](https://phoenix-live-view.hexdocs.pm/1.2.12/security-model.html).

**같은 페이지에 섞는 순서:** 가장 단순한 경계는 URL별 소유권 분리다. 동일 페이지가 필요하면 독립 Phoenix 문서를 iframe으로 넣는 방식이 인증·접근성·크기 조정 비용은 있어도 DOM 수명주기가 명확하다. 진짜 island 방식은 Phoenix가 생성한 초기 HTML 및 LiveView token을 전달하고 두 클라이언트가 같은 subtree를 morph하지 않도록 ignore 경계를 설정해야 한다. navigation·destroy·재mount·자산 버전도 조율한다. Django가 임의 HTML을 쓰고 LiveView JS를 붙이는 것으로 완성되지 않는다. 이 통합 방식들의 성능·개발비는 **추정**이다.

### (c) 더 좁고 실용적인 분리

1. **공유 피드만 외부 gateway:** Django에서 stream fragment를 한 번 만들고 Centrifugo/AnyCable/GRIP에 publish한다. 개인별 폼은 wireview에 남긴다. 수신 브라우저가 두 socket을 열면 총 연결 수가 늘 수 있으므로 읽기 전용 화면에서 wireview socket 자체를 없앨 때 효과가 크다.
2. **HTTP 명령 + SSE 피드:** Mercure/Pushpin을 사용하고 UI는 서버 HTML fragment를 적용한다. signed state를 HTTP 요청마다 검증하는 새 경로는 설계 가능하지만, 현재 wireview의 모든 양방향 이벤트·upload·renew 프로토콜을 그대로 지원하는 것은 아니다.
3. **렌더 worker pool만 분리:** socket/session은 현행대로 두고 순수 template 입력→출력만 process/subinterpreter pool로 보낸다. DB 조회·인증·state 변경은 원래 worker에서 끝낸다. 공유 가능한 렌더를 먼저 제거한 뒤 남은 개인화 CPU에만 의미가 있다.

앞의 세 방식은 공식 gateway 기능과 wireview 계약에서 도출한 **제안**이며 wireview 완성 통합체의 공개 실측은 찾지 못했다. [Centrifugo proxy](https://centrifugal.dev/docs/server/proxy), [Pushpin GRIP](https://pushpin.org/docs/protocols/grip/), [Mercure](https://mercure.rocks/docs), [wireview transport](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/transport-abstraction.md).

## 4. 다른 서버 주도 UI의 동접 구조

| 시스템·검토 버전 | 연결/UI 상태 | 렌더·팬아웃 및 확장 방법 | 공개 메모리·주의 |
|---|---|---|---|
| Turbo Streams + ActionCable, Rails 8.1 문서 | Cable connection·channel 객체는 서버, 일반 Turbo DOM 상태는 브라우저. LiveView 같은 전체 UI actor가 필수 아님 | publisher에서 partial을 한 번 렌더해 HTML stream broadcast. Cable 전용 프로세스와 pubsub, AnyCable로 연결층 분리 가능. refresh 방식은 각 브라우저 HTTP 재요청으로 렌더가 다시 늘어남 | 동일 조건 KB/연결 수치 확보 못함. MRI Ruby CPU는 프로세스 확장 필요; 공유 렌더 패턴은 wireview Broadcast와 가까움 |
| StimulusReflex 3.x 공식 가이드 | Reflex 인스턴스는 이벤트 단위로 재구성; 지속 상태는 DB/session/DOM dataset. Cable 연결 상태는 남음 | 브라우저 stimulus → Cable → Reflex → page/selector morph 또는 CableReady 연산. 전체 page morph는 요청별 렌더 부담. 독립 Cable/AnyCable 선택 시 호환 범위 확인 | 프레임워크 전체를 “연결별 화면 객체 상주”로 분류하면 틀림. 공통 KB/연결 수치 없음 |
| Livewire 3 | signed/checksummed snapshot의 memo·data를 브라우저가 보유. HTTP마다 hydrate→action→render→dehydrate; PHP component는 요청 사이 상주하지 않음 | request bundling으로 왕복 수 감소. Echo/Reverb 알림에 응답해 component를 다시 그리면 구독자별 Livewire HTTP 요청·PHP 렌더는 남음 | Livewire 자체 상주 연결당 UI 메모리는 없음. Reverb WS 메모리는 별도. 브라우저 snapshot·HTTP CPU로 비용 이동 |
| Blazor Server, ASP.NET Core 10 문서 | SignalR 연결과 별개로 circuit에 component tree·render 상태·scoped service 유지. 탭마다 상태 발생 | .NET thread pool과 노드 확장. SignalR 서비스 분리는 transport 확장이지 circuit 계산·메모리 제거가 아님. reconnect/affinity 또는 명시적 circuit 상태 보존 설계 | 보편적인 “250KB/circuit” 상수는 채택하지 않음. 실제 앱별 live+disconnected circuit와 GC heap 측정 필요 |
| Vaadin Flow, 현행 문서의 Java 25 예제 | JVM의 VaadinSession/UI 객체·component tree, 한 세션 내부 접근 동기화 | 세션 affinity, 다중 노드, 필요 시 지원되는 세션 복제. lazy data provider로 목록·UI 상태 제한 | 공식 form+lazy-grid 예제 retained heap 약290KB/세션(한 탭), 둘째 탭 +160KB. 1만행 eager 로딩은 탭당 약4배. JVM RSS·WS 비용과 동일하지 않음 |

출처: [Action Cable](https://guides.rubyonrails.org/action_cable_overview.html), [Turbo Streams](https://turbo.hotwired.dev/handbook/streams), [publisher render 소스](https://github.com/hotwired/turbo-rails/blob/37530c08780fa6f6dbb56a633de4b81169bdd174/app/channels/turbo/streams/broadcasts.rb#L98-L126), [StimulusReflex persistence](https://docs.stimulusreflex.com/guide/persistence), [Livewire 3 hydration](https://livewire.laravel.com/docs/3.x/hydration), [bundling](https://livewire.laravel.com/docs/3.x/bundling), [Echo 처리 소스](https://github.com/livewire/livewire/blob/eee4431ba2d0327876ca9801c4ddbd15c268f3ee/js/features/supportLaravelEcho.js), [Blazor 메모리](https://learn.microsoft.com/en-us/aspnet/core/blazor/host-and-deploy/server/memory-management?view=aspnetcore-10.0), [Vaadin capacity](https://vaadin.com/docs/latest/building-apps/architecture/capacity-and-failover).

Blazor의 자주 인용되는 성능 글은 **2019년 .NET Core 3.0** 실험이다. Azure D1v2(1vCPU·3.5GB) 5,000명 이상, D3v2(4vCPU·14GB) 20,000명 이상을 보고했고 메모리가 주요 제한이었다. 이를 .NET 10의 현행 동일 앱 수치로 쓰면 안 된다. 새 circuit 상태 보존 기능도 임의 객체·외부 연결 전체의 투명한 직렬화가 아니다. [2019 벤치](https://devblogs.microsoft.com/dotnet/blazor-server-in-net-core-3-0-scenarios-and-performance/), [현행 server state management](https://learn.microsoft.com/en-us/aspnet/core/blazor/state-management/server).

**추정:** 큰 UI 상태를 서버에 두는 Blazor/Vaadin은 Python GIL이 없어도 메모리로 동접 상한이 생긴다. 반대로 Turbo/Livewire는 idle 메모리에 유리하지만 구독자별 HTTP 렌더가 발생하는 설계에서는 총 CPU가 늘 수 있다. “서버 주도 UI”를 하나의 연결당 비용으로 묶을 수 없다.

## 5. Python 런타임: 2026-10 현재

### CPython 3.14t 지원과 확장성은 다른 질문이다

3.14에서 free-threaded build는 지원 단계로 승격됐지만 기본 빌드는 아니다. 공식 현행 문서의 pyperformance 평균 단일 스레드 오버헤드는 플랫폼에 따라 약 **macOS aarch64 1%–x86 Linux 8%**다. 과거 실험의 10–15%를 최신 보편 수치로 반복하면 안 된다. extension이 GIL을 다시 켤 수 있으므로 실제 imports·렌더를 거친 후 `sys._is_gil_enabled()`를 확인해야 한다. [PEP 779](https://peps.python.org/pep-0779/), [CPython free-threading 가이드](https://docs.python.org/3/howto/free-threading-python.html).

| 구성요소 | 확인된 상태 | wireview에 대한 결론 |
|---|---|---|
| Django | 6.1 릴리스(2026-08-05)는 Python 3.12–3.14 지원. 2026-03 이후 개발자 논의에서 free-threaded cache·Field counter·lookup contention을 다룸 | Python 3.14 지원을 모든 3.14t 구성의 correctness·선형 확장 보증으로 읽지 않는다. 저장소 실험은 Django 6.0이므로 최신 조합은 재측정 대상 |
| Channels 4.3.2 | 공식 release notes가 Django 6.0과 Python 3.14 지원을 확인 | 문서에 전체 free-threaded stack 인증은 명시되어 있지 않음. 이벤트 dispatch와 thread-sensitive ORM 직렬화는 별도 |
| asgiref 3.12.0/3.12.1, 2026-07-14 | free-threaded 3.13/3.14 CI와 thread context 상속 관련 개선. 3.12.1은 내부 partial 형태 호환 수정 | 기존3.11.0 실험만으로 최신 지원을 부정하면 안 됨. 하지만 `sync_to_async(thread_sensitive=True)`의 단일 executor 경로는 GIL을 꺼도 직렬 |
| uvicorn | 0.38.0(2025-10-18)에서 Python 3.14 지원 명시 | ASGI 서버·WS parser·event loop·확장 모듈까지 한 조합으로 검증해야 함. event loop 한 스레드만 사용하면 GIL 해제만으로 CPU 렌더가 병렬이 되지 않음 |
| uvloop | 0.22.0 release notes에 Python 3.14와 free-threading 지원 변경 명시 | “uvloop는 nogil 미지원”이라는 오래된 단정은 부정확. I/O 개선이며 Django 템플릿 CPU 제거가 아님. Windows에서는 사용 불가 |

출처: [Django 6.1](https://docs.djangoproject.com/en/6.1/releases/6.1/), [Django 개발자 free-threading 논의](https://forum.djangoproject.com/t/improve-free-threading-performance/44497), [Channels 4.3.2](https://channels.readthedocs.io/en/latest/releases/4.3.2.html), [asgiref changelog](https://github.com/django/asgiref/blob/main/CHANGELOG.txt), [sync executor 구현](https://github.com/django/asgiref/blob/main/asgiref/sync.py), [uvicorn release notes](https://www.uvicorn.org/release-notes/), [uvloop releases](https://github.com/MagicStack/uvloop/releases), [uvloop 지원 범위](https://github.com/MagicStack/uvloop).

**저장소 실측:** 기존 plain template는 GIL 빌드 240.9µs 대비 3.14t 1thread 255.8–321.9µs(약6–34% 느림), 4thread 145–280µs(자체1thread 대비 약1–1.8배), 8thread 300–342µs였다. 실행 환경에 한정된 결과이며 contention 원인도 확정되지 않았다. 공식 평균 오버헤드와 모순되지 않는다. 전체 서비스의 최신 Django/asgiref/uvloop 조합 결과로 일반화할 수 없다. [fanout 설계의 실험](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-fanout.md).

**현재 코드 확인:** `get_template_marker()`는 프로세스 전역 `TemplateMarker`를 반환하고, 여기에 mutable `MarkerContext`, depth·frames 등이 있다. `render_reads`가 thread-local이어도 이 전역 렌더 상태 전체가 안전해지는 것은 아니다. 현재 엔진을 여러 스레드에서 동시에 렌더하는 구조는 격리·동기화 검증 없이 채택할 수 없다. [template_engine.py](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/wireview/template_engine.py), [render_reads.py](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/wireview/core/render_reads.py).

### PEP 734 서브인터프리터

Python 3.14의 `concurrent.interpreters`와 `InterpreterPoolExecutor`는 별도 interpreter를 여러 OS thread에서 돌려 코어를 활용할 길을 제공한다. interpreter를 생성하는 것만으로 동시 실행되는 것은 아니다. 모듈·globals가 격리되며 일반 객체를 임의 공유할 수 없다. executor 경계의 함수·인자·결과는 pickle 등 전송 비용을 갖는다. 일부 공유 가능한 객체·queue가 있어도 ORM/request를 안전한 공유 객체로 바꾸지는 못한다. [PEP 734](https://peps.python.org/pep-0734/), [3.14 interpreter API](https://docs.python.org/3.14/library/concurrent.interpreters.html).

**제안:** main worker에서 인증·handler·ORM을 마친 뒤, 불변 render DTO와 template 식별자·locale·timezone·버전을 넘겨 pool에서 HTML/Rendered를 반환한다. 각 interpreter에서 Django 설정·template cache를 초기화한다. 이 방식은 전역 marker 분리에 도움이 되지만 template 안의 ORM 접근·custom tag 부작용을 금지하거나 통제해야 한다. C extension의 **subinterpreter 지원과 free-threading 지원은 별개**이며, worker별 초기화·메모리·직렬화 비용을 process pool 대조군과 비교해야 한다. 같은 프로세스이므로 네이티브 crash 격리는 process pool보다 약하다. 현재 wireview의 투명한 실행 모드로 구현된 것은 아니다.

### 다른 런타임·컴파일러·직렬화기

| 후보·시점 | 기대할 수 있는 범위 | 제약과 판단 |
|---|---|---|
| PyPy, 2026-10 공식 다운로드 | 장시간 반복되는 순수 Python 코드의 JIT 이득 가능. 사이트에 3.12/3.11 계열이 안내됨 | “PyPy는 3.11뿐”이라는 과거 정보로 배제하지 않되 정확한 배포 채널·플랫폼을 고정해야 함. wireview는 Python≥3.12. Pydantic core·DB driver wheel/cpyext, warmup·RSS를 포함한 전체 stack 검증 필요. 공개 일반 JIT 배수를 템플릿 배수로 인용하지 않음 |
| Cython, 열람 문서3.3.0 | typed parser·문자열 스캔 등 좁은 hot path에 적합 | Django template 전체를 컴파일한다고 Python 객체 호출·ORM 비용이 없어지지 않음. 3.1+ free-threading 지원은 문서상 실험적이며 호환 선언이 안전성 검증을 대신하지 않음. 배포 wheel·Windows 비용 |
| mypyc, stable 문서2.3.1 | 정적 타입이 잘 잡힌 순수 helper에 적합 | 동적 class·monkeypatch·런타임 template 확장 관행과 제한 충돌. Django 엔진 전체 대체보다 검증 가능한 작은 함수가 후보 |
| orjson, 현행 README | JSON 단계의 CPU 절감; bytes 반환을 포함한 API 차이 있음 | PyPy·subinterpreter 미지원 명시, free-threading은 지원 보장 문구가 아님. signed payload·NaN·정수·unicode·프레임 bytes 계약을 검증. 현재 주요 병목일 근거 약함 |
| msgspec 0.20.0, 2025-11-23 | Python3.14 및 free-threading 지원 명시; encode 경로 교체 실험 가능 | JSON encoder만 바꾸는 것과 Pydantic Component를 Struct로 옮기는 것은 비용이 다름. 후자는 validation·schema·state 계약 변경. subinterpreter 지원은 별도 확인 |

출처: [PyPy 다운로드](https://pypy.org/download.html), [Cython free-threading](https://docs.cython.org/en/latest/src/userguide/freethreading.html), [mypyc 차이](https://mypyc.readthedocs.io/en/stable/differences_from_python.html), [orjson 지원 범위](https://github.com/ijl/orjson), [msgspec changelog](https://github.com/msgspec/msgspec/blob/main/docs/changelog.md).

**우선순위 근거:** 과거 fanout profile의 CPU 679.6µs 중 template 323.2µs, marker parse 124.4µs, JSON 2.0µs였다. 그 비율이 유지된다는 가정에서 JSON을 10배 가속해도 전체 절감은 약0.27%다. 이 프로파일 이후 최적화가 들어갔으므로 현재 비율로 주장하지 않는다. djust의 Rust template 엔진은 Django 호환 영역을 크게 재구현한 선택이며, VDOM HTML parsing과 wireview marker parsing도 다르다. “Rust JSON/VDOM을 넣으면 템플릿이 해결된다”는 근거가 되지 않는다. [fanout profile](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-fanout.md), [djust 검토 2026-09-18](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/djust-vdom-review-2026-09-18.md).

## 6. 렌더 자체를 줄이는 선택지

### 상태 해시 캐시: 이미 있는 것과 확장할 것

현재 `shared_render`는 message id·클래스·component id·전체 필드·언어·timezone 등을 키로 한 event-loop 로컬 캐시다. 약1초·16MB·4,096렌더 상한을 두고, 공유 결과의 state slot에 연결별 서명을 채운다. handler·diff·hook·구독은 여전히 연결별이다. 제외 필드도 렌더에 영향을 줄 수 있어 키에 포함한다. 모델/QuerySet, viewer 문맥, LiveComponent·slot·temporary assign·live_session 등은 보수적 제한을 적용한다. W019는 include/extends/함수 템플릿을 살피며 동적 이름 등 확신할 수 없는 경우 공유를 거부한다. [현재 shared_render 설계](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-fanout.md).

**제안:** 메시지를 넘어 결과를 재사용하려면 state hash만으로 충분하지 않다. 키에 template/code version, locale/timezone, tenant·권한 cohort, 외부 데이터 revision까지 포함하고, 모델 pk만으로 동일 렌더라고 판단하지 않는다. signed token과 CSRF·사용자별 속성은 공유 결과 밖에 둔다. hash 비용과 적중률·캐시 RAM·동시 miss를 측정하고, bounded cache와 동일 키의 중복 계산 합치기를 사용한다. TTL은 정확성 증명이 아니다. 외부 변경이 렌더 결과를 바꾸는 시점에 invalidation 또는 revision이 필요하다.

현재 token 재사용 정책도 있으므로 “서명 매번 제거”를 전부 신규 이득으로 세지 않는다. handler/after_render가 구독·외부 효과를 가진다면 렌더 캐시 hit라는 이유로 생명주기까지 건너뛰면 안 된다. **추정:** 공유 피드에서는 효과가 크지만 사용자별 키가 모두 다르면 캐시 비용만 늘 수 있다. [현재 state·lifecycle 계약](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/CLAUDE.md).

### Coalescing과 배압

**제안:** 명령 처리와 화면 그리기를 분리한다. “현재 가격/진행률/온라인 수”처럼 최신 상태만 의미 있는 업데이트는 짧은 창 또는 이미 진행 중인 렌더 동안 dirty로 표시하고 최신 revision을 한 번 그린다. 결제·추가·삭제 등 업무 명령을 누락시키거나 순서를 바꾸지 않는다. 각 이벤트의 ref/완료 응답도 보존한다. stream 명령·JS side effect는 일반적으로 교환·합성이 가능하지 않으므로 무조건 마지막 것만 남기지 않는다.

diff 기준은 브라우저에 전달될 렌더 순서와 맞아야 한다. 아직 보내지 않은 diff를 버릴 때 그 diff를 기준으로 만든 다음 diff까지 그대로 보내면 화면이 깨질 수 있다. 마지막 확정된 baseline에서 다시 diff하거나 전체 reset으로 재동기화하는 상태 기계가 필요하다. queue는 개수뿐 아니라 bytes·최고 대기시간·revision lag로 제한하고 느린 연결을 격리한다. overflow 시 bounded disconnect와 jitter 재접속·snapshot 복구가 필요하다.

현재 Broadcast에는 이미 느린 연결 queue·hold·1013 종료가 있다. 제안은 이를 없애거나 중복 구현하는 것이 아니라 일반 렌더까지 적용 가능한 부하 정책의 범위를 정하는 것이다. **예상 효과는 갱신 폭주 때의 불필요한 계산 억제이지 단일 클릭의 기본 렌더 시간 단축이 아니다.** [현행 배압·hold](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-patch.md).

### Django 템플릿의 부분 의존 추적

현재 temporary assign 추적은 이름/속성 읽기를 관찰하고, 실행되지 않은 if 가지는 정적으로 살피며, stale만 읽는 부분의 이전 출력을 보존한다. 전체 일반 필드에 대한 컴파일된 변경 추적도 아니고 일반적인 렌더 성능 향상을 입증한 실험도 아니다. [설계·한계·기존 검증](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/temporary-assigns-change-tracking.md).

**제안하는 확장 범위:** 불변 DTO의 필드, 명시적 dependency 선언을 가진 순수 filter/tag, 고정 이름 include/extends, 안정적 key의 리스트부터 허용하고 나머지는 정상 렌더로 fallback한다. 변수 노드만 캐시하면 property·context 구성 비용은 이미 지불했을 수 있으므로 계산 단계까지 분리해야 한다.

의존성을 확신하기 어려운 구성은 다음과 같다.

- 동적 include/extends/함수 이름, 임의 custom tag와 context 직접 변경.
- property·callable/filter가 DB, 시간, 난수, 캐시, 요청 user/perms, locale, 외부 서비스 또는 전역 상태를 읽는 경우.
- in-place list/dict/model mutation, lazy QuerySet, 동일 pk의 데이터 갱신.
- 이번 실행에서 지나지 않은 if/empty 분기, forloop counter와 형제 위치 의존, 상속 block·slot·자식 lifecycle.
- 렌더 중 await하거나 tag가 부수 효과를 발생시키는 경우.

동적 read tracing만으로는 실행되지 않은 경로나 숨은 외부 읽기를 완전히 알 수 없다. 보수적 fallback·명시적 데이터 revision과 함께 독립 전체 렌더 결과를 비교하는 검증이 필요하다. HEEx도 외부 DB 변화를 자동 추적하지 않지만 Django의 임의 tag를 허용한 채 같은 보증을 준다고 주장하는 것은 더 어렵다. [HEEx 제한](https://phoenix-live-view.hexdocs.pm/1.2.12/assigns-eex.html), [wireview 공유 검사·fallback](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-fanout.md).

## 결론의 신뢰 범위

확실한 것은 wireview에서 공유 렌더·Broadcast·압축 설정·다중 프로세스가 이미 큰 차이를 보였다는 점이다. 외부 gateway는 유휴 연결 비용을 옮기는 검증된 아키텍처지만 wireview 전체 메모리 절감률은 아직 측정되지 않았다. LiveView 소유 화면은 개인화 계산 생략과 코어 활용에서 구조적 이점이 있으나 마이그레이션 비용과 Django 도메인 병목이 남는다. 현재 자료로 제품 간 동일 앱의 절대 동접 순위를 매길 수 없다. 구체적인 선택과 반증 가능한 벤치는 [options.md](./high-concurrency-options-codex-2026-10-13.md)에 정리했다.
