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

이벤트 하나의 CPU 비용은 0.2~0.6 ms이고 그중 60% 이상이 Django 템플릿 렌더다 (`docs/features/html-diff.md`). 연결당 메모리의 약 85%는 Python WebSocket 스택(daphne, Channels 컨슈머)이고 wireview 세션 상태는 작은 컴포넌트에서 7 KB 남짓이다.

## 2. 연결 계층이 가져갈 수 있는 것과 없는 것

Go나 Elixir 프런트(AnyCable-Go, Centrifugo, 조직의 kraken)가 맡을 수 있는 것:

- WebSocket 종단, TLS, 하트비트, 재연결 폭주, 백프레셔
- 유휴 연결 보유 (연결당 메모리의 85%)
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

## 5-1. 결정 (2026-09-08): NATS 레이어를 별도 라이브러리로

SQLite와 Windows(WSL2·Docker 없음)를 기본 배포 전제로 두기로 했다. 이 전제에서 빠진 부품은 Redis 없이 프로세스를 잇는 채널 레이어뿐이고, 그것은 wireview가 아니라 Channels 수준의 부품이다. 그래서 [channels-nats](https://github.com/itda-work/channels-nats)를 별도 저장소로 만들었다. NATS 서버는 그대로 쓰고 Python 레이어만 구현하며, subject(`<prefix>.ch.<channel>`, `<prefix>.grp.<group>`)가 외부 계약이다. 2단계에서 Go 프런트를 붙이더라도 그 subject로 합류하므로 wireview와 사용자 코드는 바뀌지 않는다. 7절의 goproxy 실험 코드는 `feat/go-front` 브랜치에 참고용으로 남기고 병합하지 않는다.

wireview를 그 위에서 돌린 실측이다 (`make bench ARGS="--layer nats --processes 4 --connections 2000"`, 같은 기계). 브로드캐스트는 연결 하나가 `abroadcast`를 부르고 2,000개 연결의 컴포넌트가 모두 다시 렌더할 때까지의 시간이다.

| 구성 | 컴포넌트 | 연결당 RSS | join/s | 이벤트/s | 브로드캐스트 |
|------|---------|-----------:|-------:|---------:|------------:|
| daphne 1개, InMemory | 항목 5개 | 46.2 KB | 1,086 | 3,013 | 862 ms |
| daphne 4개, channels-nats | 항목 5개 | 61.0 KB | 1,979 | 10,485 | 221 ms |
| daphne 1개, InMemory | 항목 50개 | 69.8 KB | 691 | 1,452 | 1,769 ms |
| daphne 4개, channels-nats | 항목 50개 | 85.1 KB | 1,340 | 4,124 | 393 ms |

프로세스를 넷으로 늘리자 이벤트 처리량이 3배, 브로드캐스트가 4배 빨라졌다. 연결당 메모리는 channels-nats 0.1.0에서 15 KB 늘었는데 채널마다 NATS 구독을 두던 비용이었고, 0.2.0에서 프로세스당 구독 하나로 바꾸자 항목 5개 기준 55.3 KB, 브로드캐스트 143 ms로 내려왔다(`bench/results/663b3f7-nats-4proc.json`은 0.1.0 값). 남은 9 KB는 로컬 mailbox와 그룹 멤버십이다. 브라우저 E2E도 `--ds=testproj.settings_nats`로 같은 레이어 위에서 통과했다.

## 6. 착수 기준

프로세스당 동시 연결이 수천을 넘고 유휴 연결이 많은 워크로드(대시보드, 알림)가 실제로 생길 때. 지금 실측으로는 Python 워커 하나가 2,000 유휴 연결을 188 MB로 들고 초당 수천 이벤트를 처리하므로, 워커 여덟 개면 만 단위 연결까지 프런트 없이 간다. 그 전에는 4절의 준비만 유지한다.

kraken(Elixir)을 쓸지 Go로 새로 만들지는 기술이 아니라 조직의 결정이다. Elixir는 클러스터링과 presence가 내장이고, Go는 단일 바이너리 배포와 성숙한 참조 구현(AnyCable-Go, Centrifugo)이 있으며 노드 간 pub/sub에 Redis나 NATS가 필요하다.
