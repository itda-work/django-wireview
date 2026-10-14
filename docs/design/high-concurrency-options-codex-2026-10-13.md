# 고동접 아키텍처 선택지와 추천 순위

기준일 **2026-10-13**, wireview **1.5.1 / a4e957c4fc5333cf243f363c272a0894dae79ae2**. 저장소 변경·새 벤치 없이 작성한 선택지다. 외부 구조·버전·출처의 자세한 근거는 [findings.md](./high-concurrency-findings-codex-2026-10-13.md)에 있다. 아래 예상 효과는 **저장소 실측**이라고 명시한 경우를 제외하면 **설계상 추정이며 절감률을 보장하지 않는다**.

## 판단의 출발점

“동접”에는 서로 다른 네 가지 비용이 있다.

- **유휴 연결:** WS/TLS/압축/heartbeat와 앱 세션 메모리.
- **개인별 이벤트:** handler·ORM·context·template·diff·서명.
- **공통 알림:** 한 업데이트를 몇 번 렌더하고 몇 번 인코딩하는가.
- **전송:** subscriber 수, payload bytes, 네트워크 대역폭과 slow client queue.

대략 `CPU ≈ 개인 이벤트율 × 개인 이벤트 비용 + 공통 발행률 × (공유 생성 비용 + 구독자 수 × 전달 비용)`이다. 렌더를 한 번으로 줄여도 N명에게 전달할 bytes는 남는다. 전체 RAM은 gateway+Python worker+broker+kernel+queue를 합쳐야 한다. 이 식은 용량을 나누어 측정하기 위한 **모델**이며 현재 서비스 예측값이 아니다.

기존 기준선에는 shared_render, Broadcast, keyed diff, temporary assign 출력 보존, token 재사용, transport 분리가 포함된다. 외부 gateway의 경쟁 상대를 최적화 이전 notification 경로로만 잡으면 효과를 과대평가한다. [현재 계약](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/CLAUDE.md), [저장소 실측](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/PERFORMANCE.md).

## 선택지 비교

표의 CPU는 **업무 이벤트+렌더 CPU**, 팬아웃은 **공통 업데이트 생성·분배**, 메모리는 **총 시스템 연결당 증분**이다. “낮음/높음”은 상대적 개발·운영 비용이며 인월 견적이 아니다.

| 선택지 | CPU / 팬아웃 / 메모리 / 코어 활용 | 효과의 근거 | 개발 비용·wireview 계약 충돌 | 운영 복잡도 | 우선 검증할 벤치 |
|---|---|---|---|---|---|
| **A. 현행 Django 구조 + 용도별 Broadcast/shared_render + 프로세스 확장** | CPU: 공유 부분 감소, 개인 렌더 그대로. 팬아웃: 이미 큰 감소. 메모리: 압축·stream 상태 제한으로 감소 가능. 코어: worker process | **저장소 실측:** 같은 stream의 1,000연결 NATS notification 276.3→Broadcast18.0ms. 1→4process 3,056→9,784events/s. 다른 부하에 배수 전용 불가 | 낮음. 기능을 새로 만드는 것이 아니라 적용 범위 선택. viewer-dependent 화면은 공유 금지; stream은 일반 state 변경 대체 아님 | 낮음–중간: 현행 ASGI+NATS/Redis, worker 수·DB pool 관리 | B1/B2/B3. 현행 최적 경로를 대조군으로 고정 |
| **B. 공유 피드만 Centrifugo/AnyCable/Pushpin에 분리** | CPU: publisher 1회 유지, 개인 이벤트 그대로. 팬아웃: gateway 전담. 메모리: 읽기 전용 화면에서 Python session까지 없앨 때 가장 유리. 코어: gateway+Python 독립 확장 | 외부 gateway 구조 선례. wireview Broadcast 자체가 이미 효율적이므로 추가 이득은 주로 연결·전송 계층이라는 **추정** | 중간. 별도 client adapter·인증/토픽 정책·history. wireview와 gateway socket 둘 다 열면 비용 중복. Broadcast id/reachable/hold 규칙 이식 필요 | 중간: gateway·broker·두 경로 관측, 일부 기능은 상용 | B1/B3/B5. 2socket과 1socket 구성을 모두 측정 |
| **C. Phoenix 연결·PubSub + Django WireviewSession/render RPC** | CPU: 개인 렌더 감소 없음, RPC 비용 추가. 팬아웃: 앞단으로 이동. 메모리: Python WS 제거, Python session은 남음. 코어: gateway BEAM + backend processes | AnyCable류 분리와 현재 Outbound/Broker가 근거. 전체 wireview 실측은 **없음** | 높음. 세션 pinning·RPC 순서·worker epoch·write 완료 ACK·auth revocation·patch registry 필요. signed state만으로 stateless 변환 불가 | 높음: BEAM+Python+broker+RPC, 분산 상태 machine | B1/B2/B4/B5. 앱과 gateway RSS 합계·RPC p99·재배포 복구 |
| **D. 특정 실시간 화면을 Phoenix LiveView로 전환; Django는 업무 API/ORM/admin** | CPU: HEEx 변경 추적으로 불변 계산 생략. 팬아웃: view별 렌더 남을 수 있음. 메모리: UI 상태가 BEAM으로 이동, 자동 감소 보장 없음. 코어: BEAM scheduler | 1.2.12의 구조적 이점. 동일 wireview 앱의 속도 배수는 **미측정** | 매우 높음. component/template/hooks와 UI 테스트 재작성. signed data-state/mount 계약은 대체. 인증 bridge·API 도메인 경계 설계 | 높음: 두 런타임·API 버전·revocation, 화면 경계가 명확하면 C보다 상태 중복 적음 | B2/B4/B5. 동일 기능·DB 호출 수·부분 변경 비율을 맞춘 화면 비교 |
| **E. Django HTTP 명령 + Mercure/GRIP SSE HTML 피드** | CPU: 읽기 위주 공유 업데이트에 유리; HTTP마다 개인 렌더하면 그대로. 팬아웃: hub. 메모리: Django 상주 UI session 제거 가능. 코어: HTTP worker+hub | Mercure/GRIP의 공개 구조. wireview와 동일 기능 결과는 **추정** | 중간–높음. 양방향 wire protocol·upload·renew 등을 그대로 지원하지 않음. snapshot/cursor와 별도 클라이언트 필요 | 중간: HTTP+hub, JWT 갱신·bounded history | B1/B3/B4. 최초 snapshot과 구독 사이 유실·토큰 만료 |
| **F. 순수 렌더를 process/subinterpreter pool로 분리** | CPU: 작업량은 비슷, 전송 비용 증가 가능. 팬아웃: 공유 후 남은 렌더만 대상. 메모리: interpreter cache 등 증가 가능. 코어: 병렬화 | CPython3.14 PEP734 API. wireview 통합 성능은 **미측정** | 높음. 불변 DTO, ORM 없는 렌더, extension 호환·worker 초기화 필요. session/DB ownership 유지 | 중간–높음: pool 크기·timeout·worker 실패·cache 예열 | B2/B6. 1/4/8core, pickle비용과 process pool 대조 |
| **G. 일반 상태 캐시·coalescing·부분 의존 추적 확장** | CPU: hit·생략률에 따라 감소. 팬아웃: 갱신 폭주 합치기. 메모리: 캐시 증가와 queue 감소의 교환. 코어: 본질적 변화 없음 | shared_render 실측은 근거가 있으나 메시지 간 캐시·일반 HEEx식 추적은 **신규 추정** | 중간–매우 높음. 숨은 외부 의존·auth cohort·template version·부작용·diff baseline 정확성. 현재 temporary 추적과 구별 | 낮음–중간: invalidation·적중률·검증 운영 | B2/B3/B5. 전체 렌더와 결과 동등성·0%hit 최악 조건 |
| **H. 3.14t / PyPy / Cython·mypyc / uvloop / JSON 변경** | CPU: 각 hot path에 국한. 팬아웃: 구조 변화 없음. 메모리: 개선·악화 모두 가능. 코어: 3.14t도 실제 thread 실행 필요 | 공식 지원과 국소 실험 근거만 있음. 단순 uvloop/orjson 적용은 template 병목 해결 아님 | 낮음(JSON 실험)–높음(engine/thread 안전성). 전역 marker·DB thread affinity·wheel·Windows 제약 | 낮음–중간, native build/플랫폼 매트릭스 시 증가 | B6. 최신 deps와 실제 GIL 상태, 앱 전체 회귀·RSS |

외부 근거: [AnyCable architecture](https://docs.anycable.io/architecture), [Centrifugo proxy](https://centrifugal.dev/docs/server/proxy), [Pushpin GRIP](https://pushpin.org/docs/protocols/grip/), [Mercure history](https://mercure.rocks/docs/concepts/reconnection-and-history), [LiveView 1.2.12 추적](https://phoenix-live-view.hexdocs.pm/1.2.12/assigns-eex.html), [PEP734](https://peps.python.org/pep-0734/), [CPython free-threading](https://docs.python.org/3/howto/free-threading-python.html). 런타임별 지원 버전과 외부 벤치 조건은 [findings.md §5](./high-concurrency-findings-codex-2026-10-13.md#5-python-런타임-2026-10-현재) 참조.

## 내 추천 순위

### 1위 — A를 유지하며 실제 트래픽을 세 종류로 나눈다

가장 강한 근거는 이미 있는 저장소 실측이다. 공통 stream은 Broadcast, 동일 상태의 제한된 렌더는 shared_render, 개인화 화면은 일반 renderer로 둔다. 운영 압축 설정과 worker/process 대조군을 고정한다. 이것은 큰 엔진 재작성에 착수하자는 제안이 아니라 **비교할 수 있는 현행 기준선을 채택하자는 선택**이다.

이 순위의 전제는 기존 Django API·handler·테스트·Windows 설치 경험의 가치가 크다는 것이다. 현행 배포가 이미 이 기준선을 충분히 적용했다면 반복 최적화를 계속할 이유는 없다. 그때는 다음 두 후보 중 병목에 맞는 쪽으로 바로 넘어간다. [실측 조건](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/PERFORMANCE.md).

### 2위 — 유휴·공통 피드가 많으면 B, 장기적인 개인화 CPU 병목이면 D

기본 추천은 **좁은 공유 피드의 B**다. 새 gateway를 전체 wireview 대체품으로 만들지 않고, 읽기 전용 화면에서 Python 상주 연결을 제거할 수 있는지를 먼저 본다. 표준 publish/history 기능이 중요하면 Centrifugo, Rails 호환 RPC 모델을 참고하려면 AnyCable, 기존 HTTP backend를 유지하는 proxy 패턴이면 GRIP가 후보다. Elixir 운영 기반이 이미 있으면 Phoenix도 같은 자리에 들어갈 수 있지만 구현해야 할 gateway 계약이 늘어난다. 제품 간 절대 성능 우열을 주장하는 순위는 아니다.

반면 **대부분 사용자가 지속해서 서로 다른 상태를 수정하고 template CPU가 지배**한다면 D를 B보다 앞에 둔다. LiveView가 UI를 소유해야 HEEx 계산 생략을 얻는다. Phoenix를 연결 앞단에만 놓는 C는 이 문제를 해결하지 못한다. 경계가 분명한 대시보드 한 화면을 골라 같은 기능의 D 벤치를 만들고, Django 도메인 호출·권한 모델을 유지한 상태에서 판단하는 편이 낫다.

이 분기는 **아키텍처에서 도출한 추정**이다. 공개 2M 소켓이나 Centrifugo PRO 유휴 수치로 결정한 것이 아니다. [LiveView 엔진](https://hex.pm/packages/phoenix_live_view/1.2.12/files/lib/phoenix_live_view/engine.ex), [gateway 구조](https://centrifugal.dev/docs/server/proxy).

### 3위 — 기존 화면을 반드시 보존해야 하고 연결 비용이 지배할 때만 C

C는 요구 계약을 거의 그대로 유지하면서 연결 서버를 바꾸려는 선택이다. 그러나 session pinning·인증 세대·patch hold·reachable 정보가 두 런타임 사이에 나뉘므로 단순 proxy보다 훨씬 큰 시스템이 된다. 연결당 Python session이 남는 형태와 완전 무상태 RPC를 혼동하면 기대 메모리 절감을 얻지 못한다.

**채택 조건 제안:** 전체 RSS 중 제거 가능한 WS 계층의 비중이 크고, 수신자별 렌더가 이미 충분히 줄어 있으며, gateway 추가 후 동일 SLO의 비용/연결이 의미 있게 낮아질 때. 이를 만족하지 못하면 C 대신 A 유지 또는 D의 화면별 전환이 낫다. 정확한 절감률은 아직 측정되지 않았다. [현행 transport 경계](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/transport-abstraction.md).

### 4위 — G/F/H는 병목이 확인된 범위에서만 실험한다

갱신 폭주에서 최신 상태만 필요한 경우 G의 coalescing은 런타임 교체보다 직접적일 수 있다. 반대로 일반 Django template를 자동으로 HEEx처럼 만드는 시도는 의미론·호환성을 바꾸는 엔진 프로젝트다. 명시적 순수 DTO·dependency 계약을 받아들일 수 없다면 보수적으로 정상 렌더한다.

F는 순수 렌더 경계를 먼저 확립한 뒤 process pool을 기준으로 서브인터프리터를 비교한다. 3.14t는 최신 Django/asgiref와 전역 marker 격리를 검증한 후 후보다. Cython/mypyc는 profile이 가리키는 순수 parser 함수, orjson/msgspec은 직렬화가 실제 지배적인 경우에만 우선순위를 올린다. **근거 없는 범용 속도 배수를 기대하는 순위가 아니다.** [기존 CPU profile](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-fanout.md), [현재 marker](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/wireview/template_engine.py).

## Phoenix 두 후보의 계약 선택표

| 현재 계약 | C: Phoenix transport + Django UI | D: Phoenix LiveView UI + Django domain |
|---|---|---|
| signed data-state | Django가 기존 envelope 검증·갱신. gateway는 연결 identity/세대와 결합해 라우팅. token만으로 runtime 복원 불가 | UI 상태는 LiveView로 대체. Django 도메인 API token·인가와 별개 설계 |
| live_session·auth generation | 같은 boundary 유지. 구독 registry에 세대/epoch 동기화. revocation이 render queue 뒤에 묻히지 않도록 제어 경로 필요 | Django 발급 token/introspection + Phoenix HTTP/socket mount 검증. 양쪽 live_session 이름만 맞추는 것은 불충분 |
| handler의 ORM·transaction | Django에서 그대로 수행. 연결 affinity·DB cleanup 유지 | 업무 API로 옮겨 한 transaction 안에서 처리. UI에 ORM 객체를 보내지 않음 |
| mount() 단위 테스트 | 기존 테스트 유지. transport/RPC/write-ACK는 별도 통합 시험 | UI 테스트는 LiveViewTest 등으로 재작성. Django domain 테스트는 유지 |
| Broadcast 1회 렌더·id 대체 | 앞단에 동일 class/topic/reachable/vsn/instance 검사 및 prefix+id+suffix 경로 구현 | 기본 LiveView 업데이트로 재설계. 공유 fragment와 LV subtree 소유권 중복 금지 |
| join reset과 patch 순서 | gateway 실제 write 완료 후 hold 해제. timeout은 무조건 flush가 아님 | LiveView mount/stream 복구 흐름으로 대체. Django commit/event 순서 별도 보장 |
| 장애 복구 | worker 소실 시 rejoin 우선; state migration은 별도 고비용 기능 | view 소실 시 DB/업무 snapshot에서 remount; UI 임시 상태 보존 범위 명시 |

이 표는 **통합 설계 제안**이다. 현재 제공되는 완성 adapter의 기능 목록이 아니다. 근거: [wire protocol](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/implementation/wire-protocol.md), [Broadcast 순서 계약](https://github.com/itda-work/django-wireview/blob/a4e957c4fc5333cf243f363c272a0894dae79ae2/docs/design/broadcast-patch.md), [LiveView 보안](https://phoenix-live-view.hexdocs.pm/1.2.12/security-model.html).

## 선택을 바꿀 수 있는 벤치 설계

다음은 실행 결과가 아닌 **검증 계획**이다. 한 번의 최대 연결 수보다 동일 기능·동일 지연 목표에서 비용/사용자와 실패 복구를 비교한다.

| ID | 부하 | 대조군과 측정값 | 후보를 탈락시킬 결과 |
|---|---|---|---|
| **B1 유휴 용량** | 2k→10k→50k 연결 단계, 충분히 안정화. 미join / join5 / 큰 상태, idle99%·90% 분리 | A와 B/C/E. 총 RSS·PSS 가능 시 측정, kernel socket memory, broker, DB연결, CPU heartbeat, handshake/s. 동일 TLS/압축/ping | gateway만 작고 총 RAM은 그대로 또는 증가; 이중 socket 비용이 이득 상쇄 |
| **B2 개인화 이벤트** | 1k/10k 사용자에서 개별 field 변경, 큰 list 중1개 수정, 모든 필드 변경. ORM 없음/실제ORM 분리 | A/C/D/F. 이벤트 CPU, context/template/parse/diff/RPC별 시간, DB쿼리, p50/p95/p99, 완료 events/s. 1/4/8core 고정 | C의 RPC만 증가; D가 필요한 API 왕복 때문에 p99 악화; pool전송이 렌더보다 비쌈 |
| **B3 공통 팬아웃** | 1k/10k/50k subscriber, 같은 stream fragment·동일 bytes, 1/10/100발행/s. viewer cohort 수1/10/N | notification, shared_render, Broadcast, gateway, LiveView별 갱신 비교. publisher CPU·render횟수·수신 node CPU·네트워크·전달 p99 | 최적 Broadcast 대비 이점 없음; 개인화 때 잘못된 공유 발생; bytes 병목이 그대로인데 CPU 효과로 과장 |
| **B4 복구·배포** | gateway/worker/broker 각각 재시작, 연결 단절·message 중복/지연/역전, rolling deploy, token 만료·로그아웃·renew | 복구시간, 재join 폭주, 누락/중복 업무 처리, snapshot 후 patch 순서, 오래된 instance/세대 차단 | 완료 여부 불명 명령이 중복 DB쓰기; 권한 철회 후 데이터 전송; snapshot이 patch를 덮음 |
| **B5 배압·정확성** | 1% 느린 client, 10초간 burst, 캐시0/50/90%hit, 동적tag·locale·tenant·CSRF 차이, 숨은 외부 데이터 변화 | queue bytes/age/lag 상한, 정상client p99, close1013/resync, 참조 전체렌더와 DOM·state 동등성, ref완료 | RSS 무제한 상승, 정상client 동반 정지, stalecache/다른사용자정보, diff baseline 불일치 |
| **B6 런타임** | 같은 순수 template·실제wireview renderer. GIL/t빌드1/4/8thread, process/interpreter pool, uvloop off/on, encoder별 | 실제 GIL활성, 초기화/warmup, 장기CPU·RSS·race, wheel지원, Windows 설치, bytes 계약 | 라이브러리 import 후 GIL재활성, 전역marker 충돌, 8thread에서 역성장, 배포 요구 불충족 |

공통 통제 조건은 다음과 같다.

1. 동일 서버·OS·CPU 제한·메모리 제한, 부하 발생기는 별도 장비. backend+gateway+broker의 자원을 전부 합산한다. Linux 운영 목표와 현재 macOS 결과를 분리한다.
2. 소켓 구현·TLS·압축·payload·ping·RTT·토픽 분포를 고정한다. 기능을 줄인 echo 서버와 stateful UI를 같은 제품 성능이라고 비교하지 않는다.
3. 각 조합의 패키지 lock·commit·명령·raw 결과를 보존하고, 예열 뒤 여러 회 반복해 분포를 보고한다. cache hit와 cold start를 분리한다.
4. 메시지 publish ACK, gateway write, 브라우저 DOM 적용 지연을 서로 구분한다. backlog가 계속 증가하는 처리량은 지속 가능한 처리량으로 세지 않는다.
5. 계약 검증은 성능보다 먼저 통과해야 한다. 올바른 사용자·instance·revision에 전달됐는지를 확인하고 그다음 동일 SLO에서 비용을 비교한다.

SLO나 목표 동접 수가 지시서에 없으므로 임의로 “p99 100ms이면 합격” 같은 제품 기준은 만들지 않았다. 먼저 실제 화면의 허용 지연·active ratio·상태 크기를 넣어 이 표를 평가해야 한다.

## 현 시점의 결정

지금 근거로 전체 프로젝트를 Phoenix로 바꾸거나 nogil로 전환할 이유는 충분하지 않다. **A를 기준선으로 고정하고, 유휴/공유 피드에는 B, 개인화 CPU가 지배하는 독립 화면에는 D를 비교하는 것이 추천 방향**이다. C는 기존 UI 계약 보존이 필수인 경우의 높은 비용을 감수하는 선택이다. 순수 렌더 병렬화·부분 추적은 별도 실험으로 두고, 이미 효율적인 Broadcast보다 어느 비용을 더 줄이는지 증명할 때 채택한다.
