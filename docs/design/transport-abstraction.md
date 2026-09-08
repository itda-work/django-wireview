# 연결 계층 추상화와 Go/Elixir 프런트 검토

> 2026-09-08 작성. 실측에 근거해 "언제, 어떻게 연결 계층을 바꿀 것인가"를 정리한다.

---

## 1. 실측

testproj를 daphne로 띄우고 실제 WebSocket 연결을 열어 쟀다 (macOS, Python 3.12, Channels 4.3, InMemory 채널 레이어).

| 시나리오 | 연결당 RSS |
|---------|-----------|
| 연결만 열고 join 안 함 | 39.2 KB |
| join, 항목 5개 컴포넌트 | 46.7 KB |
| join, 항목 50개 컴포넌트 | 66.3 KB |
| 2,000 연결 유지 | 44.5 KB, 유휴 188 MB |

| 처리량 | 프로세스당 |
|--------|-----------|
| join | 1,200~1,900/s |
| 이벤트 왕복 | 3,000~5,700/s |

이벤트 하나의 CPU 비용은 0.2~0.6 ms이고 그중 60% 이상이 Django 템플릿 렌더다 (`docs/features/html-diff.md`). 연결당 메모리는 세 층으로 나뉜다. 7절의 Go 프런트 실험으로 실제 비율을 쟀다.

| 층 | 항목 5개 컴포넌트, 연결당 | 근거 |
|----|---------------------------|------|
| daphne/twisted 소켓 처리 | 약 17 KB | daphne 46 KB에서 Go 프런트 뒤의 Python 호스트 29 KB를 뺀 값 |
| Channels 컨슈머, asyncio 큐, 인메모리 채널 레이어, 세션 | 약 22 KB | Python 호스트 29 KB에서 컴포넌트 상태를 뺀 값 |
| wireview 컴포넌트 상태와 렌더 스냅샷 | 약 7 KB (항목 50개면 약 27 KB) | tracemalloc |

처음 추정과 달리 daphne 자체는 연결당 메모리의 3분의 1 남짓이고, 나머지는 Python 세션 기계 자체다. 연결 계층을 바꿔도 이 나머지는 그대로다.

## 2. 연결 계층이 가져갈 수 있는 것과 없는 것

Go나 Elixir 프런트(AnyCable-Go, Centrifugo, 조직의 kraken)가 맡을 수 있는 것:

- WebSocket 종단, TLS, 하트비트, 재연결 폭주, 백프레셔
- 소켓 처리 비용 (연결당 약 17 KB, Python 프로세스 밖으로)
- 동일 페이로드의 fan-out: `stream_op`, `exec_js`, DOM 액션, presence

맡을 수 없는 것:

- 이벤트당 렌더 (Django 템플릿, Python)
- 모델 변경 브로드캐스트 뒤의 **연결별 재렌더**. 세션마다 상태가 달라 페이로드도 다르다. wireview의 fan-out은 "구독 세션 수만큼 Python 렌더"다.

따라서 연결 계층 교체는 유휴 연결 밀도와 운영 안정성의 문제이지, 이벤트 처리량의 문제가 아니다.

## 3. 선택지

**A. 상태 유지형 Python 워커 + 프런트 프록시.** 프런트가 연결마다 Python 워커에 스트림을 하나 붙인다. Python은 지금처럼 `ComponentRepository`와 렌더 스냅샷을 메모리에 들고, 프로토콜 핸들러는 거의 그대로다. 워커 재시작 시 상태를 잃는 것은 현재와 같다.

**B. 무상태 Django.** kraken과 AnyCable의 모델. 프런트가 세션 상태를 bytes로 들고 이벤트마다 넘긴다. Django는 서명 상태에서 컴포넌트를 복원하고, `Rendered.from_dict()`로 이전 스냅샷을 되살려 부분 diff를 만든 뒤, 새 상태와 스냅샷을 돌려준다. 수평 확장이 자유롭고 워커 재시작에도 세션이 살지만, 이벤트당 CPU가 늘고 세션 핸들러를 컨슈머에서 분리해야 한다.

프로토콜은 새로 설계하지 않는다. `docs/implementation/wire-protocol.md`의 네 종류 트래픽이 정본이고, kraken의 `Authenticate`/`Authorize`/`HandleEvent`와 `broadcast`/`direct_send`/`group_send` 명령이 (2)(3)(4)에 대응한다.

## 4. 지금까지 한 것

- `wireview/core/transport.py`: `Outbound`(세션→브라우저, 구독)와 `Broker`(fan-out, 세션 메일) 인터페이스, Channels 구현, `get_broker()`/`set_broker()`.
- Channels에 직접 닿던 지점 일곱 곳(컨슈머 4, `WireviewMeta` 2, `utils`·`views`·`broadcast`)이 모두 두 인터페이스를 거친다. `tests/test_transport.py`가 새 직접 호출을 막는다.
- `Rendered.to_dict()`/`from_dict()`: 렌더 스냅샷을 프로세스 밖에 둘 수 있다 (B의 전제).
- `docs/implementation/wire-protocol.md`: 명령과 메시지 형태의 정본.

## 5. 남은 것

| 단계 | 내용 | 필요한 때 |
|------|------|-----------|
| 세션 분리 | `WireviewConsumer`의 `command_*`/`component_*` 핸들러를 `Outbound`만 받는 `WireviewSession`으로 옮기고 컨슈머는 어댑터로 남긴다 | A, B 공통 |
| 세션 상태 export/import | 컴포넌트 상태 + `Rendered` 스냅샷 + 구독 집합 + 쿼리스트링을 한 덩어리로 (wire-protocol §6) | B |
| 업로드 레지스트리 | 프로세스 전역 `views._registries`를 세션 상태나 외부 저장소로 | B |
| 프런트 어댑터 | kraken(gRPC) 또는 Go 구현의 `Outbound`/`Broker` | 착수 시 |

## 6. 착수 기준

프로세스당 동시 연결이 수천을 넘고 유휴 연결이 많은 워크로드(대시보드, 알림)가 실제로 생길 때. 지금 실측으로는 Python 워커 하나가 2,000 유휴 연결을 188 MB로 들고 초당 수천 이벤트를 처리하므로, 워커 여덟 개면 만 단위 연결까지 프런트 없이 간다. 그 전에는 4절의 준비만 유지한다.

kraken(Elixir)을 쓸지 Go로 새로 만들지는 기술이 아니라 조직의 결정이다. Elixir는 클러스터링과 presence가 내장이고, Go는 단일 바이너리 배포와 성숙한 참조 구현(AnyCable-Go, Centrifugo)이 있으며 노드 간 pub/sub에 Redis나 NATS가 필요하다.

## 7. Go 프런트 실험 (#56, 2026-09-08)

선택지 A를 가장 단순한 형태로 만들어 쟀다. `goproxy/`(Go, `coder/websocket`, 연결당 goroutine)가 WebSocket을 종단해 Unix 소켓 하나로 프레임을 넘기고, `wireview/contrib/gohost.py`가 연결마다 기존 `WireviewConsumer`를 ASGI 앱으로 돌린다. 컨슈머, 채널 레이어, 인증 미들웨어는 변경 0. 같은 기계, 같은 벤치(`make bench`, 연결 2,000개).

| 프런트 | 컴포넌트 | 연결당 RSS 합계 | Python | Go | join/s | 이벤트/s |
|--------|---------|---------------:|-------:|---:|-------:|--------:|
| daphne | 항목 5개 | 45.8 KB | 45.8 | – | 1,246 | 3,514 |
| goproxy | 항목 5개 | 65.0 KB | 29.2 | 35.8 | 1,633 | 4,446 |
| daphne | 항목 50개 | 69.6 KB | 69.6 | – | 778 | 1,582 |
| goproxy | 항목 50개 | 90.8 KB | 53.4 | 37.4 | 933 | 1,665 |

`GOGC=25`로 줄여도 Go 쪽은 32~34 KB로 거의 같다. net/http와 websocket 라이브러리의 연결당 버퍼와 goroutine 스택이 구조적 비용이다.

**판정: 이슈의 기준(연결당 메모리 합계 40% 감소)에 미달. 채택하지 않는다.** 결과는 이렇게 읽는다.

- Python 쪽 연결당 메모리는 36% 줄었지만(45.8 → 29.2 KB), Go 쪽이 그보다 더 쓴다. 합계는 30~40% 늘었다.
- 처리량은 늘었다. join 20~30%, 이벤트 5~27%. daphne/twisted의 프레임 처리가 Go로 빠지고 Python은 JSON 줄만 읽기 때문이다.
- Go 프런트가 진짜로 덜어 주는 것은 소켓 처리 17 KB뿐이다. 나머지 29 KB는 Channels 컨슈머와 세션 기계라서, 연결 밀도를 올리려면 프런트가 아니라 **Python 세션 자체**를 가볍게 하거나(세션 분리, GAP-027) 무상태(선택지 B)로 가야 한다.
- Go 쪽을 5~10 KB로 내리려면 goroutine 없는 epoll 기반 서버(gobwas/ws 계열)가 필요하다. 그래도 합계는 daphne 대비 15~25% 감소에 그친다.

코드는 실험 그대로 남긴다. `make bench`는 Go 툴체인이 있으면 두 프런트를 나란히 재므로 이후 변경(세션 분리 등)의 효과를 같은 표로 확인할 수 있다.
