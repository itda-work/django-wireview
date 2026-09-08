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

## 5-2. Windows 실측 (2026-09-08)

배포 전제가 Windows이므로 같은 기계의 Parallels Desktop 게스트(`win11-parlab`, ARM Windows 11 build 26200, 6 vCPU, 12 GB)에서 같은 벤치를 돌렸다. 재현은 `bench/windows/run.sh`(macOS 호스트 + `windows-parallels-lab` 스킬), 결과는 `bench/results/win11-parlab-*.json`, 호스트 대조군은 같은 커밋에서 잰 `bench/results/a993181-*.json`이다. 연결 2,000개, 항목 5개 컴포넌트 기준. 게스트의 Python은 네이티브 ARM64 빌드와 x64 에뮬레이션 빌드 둘을 썼다. daphne는 ARM64 wheel이 없는 의존성(autobahn, cryptography) 때문에 x64 빌드로만 돌렸다.

| 서버 | 레이어·프로세스 | 플랫폼 | 이벤트당 CPU | 연결당 RSS | join/s | 이벤트/s | 브로드캐스트 |
|---|---|---|---:|---:|---:|---:|---:|
| daphne | InMemory 1 | macOS 호스트 | 0.53 ms | 46.0 KB | 1,123 | 3,023 | 814 ms |
| daphne | InMemory 1 | Windows x64 에뮬 | 1.72 ms | 51.7 KB† | 341† | 1,043† | 369 ms† |
| daphne | NATS 4 | macOS 호스트 | 0.54 ms | 54.9 KB | 2,245 | 12,387 | 141 ms |
| daphne | NATS 4 | Windows x64 에뮬 | 1.57 ms | 52.1 KB | 825 | 3,543 | 437 ms |
| uvicorn | InMemory 1 | macOS 호스트 | 0.57 ms | 211.5 KB | 1,188 | 3,263 | 728 ms |
| uvicorn | InMemory 1 | Windows ARM64 네이티브 | 0.82 ms | 161.5 KB | 677 | 2,043 | 1,093 ms |
| uvicorn | InMemory 1 | Windows x64 에뮬 | 1.81 ms | 147.4 KB | 407 | 1,129 | 1,873 ms |
| uvicorn | NATS 4 | macOS 호스트 | 0.54 ms | 215.8 KB | 2,324 | 12,993 | 179 ms |
| uvicorn | NATS 4 | Windows ARM64 네이티브 | 0.79 ms | 165.6 KB | 1,582 | 6,061 | 306 ms |

† 연결 400개. daphne는 Windows에서 연결 600개를 시도하면 500개에서 죽는다(아래).

읽는 법:

- **daphne는 Windows에서 프로세스당 소켓 약 500개가 상한이다.** daphne가 import 시점에 asyncio를 selector 루프로 강제하고(`daphne/__init__.py`), CPython의 Windows `select()`는 512개까지만 받는다. 실측: 연결 600개 시도 → 500개 join 뒤 서버 로그에 `ValueError: too many file descriptors in select()`, 이후 연결 거부(`bench/results/win11-parlab-x64-daphne-memory-1proc-600.failed.txt`). NATS 4프로세스 × 500개는 간신히 통과했지만 운영에서 쓸 여유가 아니다.
- **uvicorn은 단일 프로세스면 IOCP 루프라 이 한계가 없다.** 한 프로세스가 2,000개를 들었다. 단 `--workers N`은 Windows에서 selector 루프로 떨어지므로(uvicorn `loops/asyncio.py`), 프로세스 확장은 "포트별 단일 프로세스 N개 + 앞단 프록시"로 한다.
- **Windows의 처리량 저하는 CPU 효율 차이만큼이다.** ARM64 네이티브 uvicorn은 호스트 대비 이벤트당 CPU 1.44배, 이벤트/s 0.63배, join 0.57배다. WebSocket I/O 계층이 아니라 하이퍼바이저와 Windows를 합친 CPU 효율이 병목이다. 하이퍼바이저 몫과 Windows 몫은 이 구성으로는 분리되지 않는다(베어메탈 x64 Windows가 필요하다).
- **프로세스 확장은 Windows에서도 그대로 먹힌다.** uvicorn 1→4 프로세스에서 이벤트 3.0배, 브로드캐스트 3.6배 빨라졌다(호스트는 4.0배·4.1배).
- **x64 에뮬레이션은 CPU만 2.2배 느리다.** 연결당 메모리는 같다. 실제 x64 서버의 수치가 아니므로 참고용이다.
- **uvicorn의 연결당 메모리가 daphne의 3~4.5배인 것은 permessage-deflate 때문이다**(호스트 211 vs 46 KB, Windows 162 vs 52 KB). uvicorn은 이 압축 확장을 기본으로 협상하고 daphne는 제안하지 않는다. 끄고 재면 격차가 사라진다 — 아래 §5-2-1.

#### 5-2-1. 연결당 메모리의 원인: permessage-deflate (2026-09-09)

같은 머신(macOS 호스트, InMemory 레이어, 프로세스 1개, 항목 5개 컴포넌트)에서 연결 2,000개:

| 서버 | 연결당 RSS |
|---|---:|
| daphne | 45.9 KB |
| uvicorn (기본값) | 211.5 KB |
| uvicorn `--ws-per-message-deflate false` | 50.6 KB |

차이는 160.9 KB다. 연결 하나가 zlib 압축 컨텍스트 두 개(보내는 쪽 deflate, 받는 쪽 inflate)를
잡는 값과 일치한다 — 같은 인터프리터에서 `compressobj(wbits=-15)` + `decompressobj(wbits=-15)`
쌍 500개를 만들면 **쌍당 158.8 KB**다. daphne(autobahn)는 permessage-deflate를 제안하지 않으므로
이 비용이 없다. 즉 서버 구현의 효율 차이가 아니라 **기본값 차이**다.

압축을 끄면 대역폭은 어떻게 되는가. wireview가 보내는 것은 대부분 작은 diff라 압축률이 나쁘다.

| 페이로드 | 원본 | deflate 후 |
|---|---:|---:|
| 항목 5개 컴포넌트의 이벤트 diff | 100 B | 84 B (84%) |
| 항목 50개 첫 렌더(HTML 포함) | 2,349 B | 320 B (14%) |

그래서 판단은 워크로드가 가른다.

- **유휴 연결이 많고 diff가 작은 앱**(대시보드, 알림, 채팅 목록): 끈다. 연결당 160 KB를 돌려받고
  대역폭은 거의 그대로다. 연결 10,000개면 1.6 GB 차이다.
- **첫 렌더가 크거나 스트림으로 HTML을 많이 보내는 앱**: 켜 두는 편이 낫다. 다만 그 이득도
  `make bench ARGS="--server uvicorn"`과 `--server uvicorn-nodeflate`를 나란히 돌려 실측한 뒤 정한다.

재현: `make bench ARGS="--connections 2000 --server uvicorn"`, 같은 명령의 `--server uvicorn-nodeflate`.


배포 결론은 `docs/DEPLOYMENT.md`의 Windows 절에 적었다: daphne 대신 uvicorn, 포트별 단일 프로세스 N개, `--workers` 금지, Caddy가 분배, nats-server는 Windows 서비스, SQLite는 WAL.

설치 쪽 발견: Windows ARM64에는 `lru-dict`, `autobahn`, `cryptography`의 wheel이 없다. lru-dict는 wireview 의존에서 뺐고(순수 Python LRU), daphne(autobahn → cryptography)는 Rust 툴체인 없이는 ARM64에 설치되지 않는다. uvicorn 경로는 wheel만으로 설치된다.

## 5-3. Redis 대 NATS (2026-09-08)

"NATS를 왜 쓰나"는 Windows 이야기지만, macOS·Linux에서도 프로세스를 늘리면 채널 레이어가 필요하다. 그때 기본 선택지인 channels_redis와 나란히 쟀다. 같은 커밋, 같은 기계, daphne 4프로세스, 연결 2,000개, 항목 5개다. 레이어마다 3회 돌려 중앙값을 싣는다(`bench/results/a993181-daphne-layer-comparison.spread.json`에 전체 회차).

| 레이어 | 연결당 RSS | join/s | 이벤트/s | 브로드캐스트 |
|---|---:|---:|---:|---:|
| channels_redis 4.3.0 (Redis 8.10) | 62.7 KB | 2,079 | 12,058 | 225 ms |
| channels-nats 0.2.0 (nats-server 2.14.6) | 54.9 KB | 2,245 | 12,387 | 141 ms |

uvicorn 4프로세스로도 같은 경향이다.

| 레이어 | 연결당 RSS | join/s | 이벤트/s | 브로드캐스트 |
|---|---:|---:|---:|---:|
| channels_redis | 225.4 KB | 2,021 | 9,858 | 161 ms |
| channels-nats | 215.8 KB | 2,324 | 12,993 | 179 ms |

- **이벤트 처리량은 사실상 같다.** 회차 변동(±5%) 안이다. 예상대로 이벤트당 비용은 레이어가 아니라 Django 템플릿 렌더가 지배한다.
- **브로드캐스트는 NATS가 37% 빠르다.** fan-out을 서버가 하는 구조는 같지만 왕복이 짧다.
- **연결당 메모리는 NATS가 8 KB 적다.** 0.2.0의 프로세스당 구독 하나가 여기서 값을 한다.
- 첫 회차는 두 레이어 모두 처리량이 25%쯤 낮게 나왔다(콜드 캐시). 벤치를 한 번 버리고 재는 이유다.

**결론.** 성능만 보면 둘 중 무엇을 골라도 된다. NATS가 브로드캐스트와 메모리에서 조금 앞서지만 배포를 뒤집을 크기는 아니다. 선택 기준은 운영이다. Redis가 이미 있으면 channels_redis를 쓴다(성숙도와 레퍼런스 구현). Redis가 없고 같은 제품이 Windows에도 나간다면 NATS로 통일하는 편이 낫다. `CHANNEL_LAYERS` 한 블록으로 개발 Windows와 운영 Linux가 같아지고, nats-server는 어느 OS에서든 바이너리 하나에 영속 저장이 없어 운영 부담이 작다. 대신 channels-nats는 0.2.0 알파이고 배포 사례가 우리뿐이다.

## 5-4. 유실이 wireview에 남기는 것 (2026-09-08)

5-3은 성능만 비교했다. 성능이 대등해도 두 레이어의 의미론이 같지는 않다. 레이어별 규약의 정본은 각 라이브러리에 있고([channels-nats README의 "Channels 규약과 다른 점"](https://github.com/itda-work/channels-nats#channels-규약과-다른-점), `channels_redis`의 `expiry`·`capacity`), 여기에는 그 차이가 **wireview에서 무엇으로 보이는지**만 적는다.

어느 레이어든 전달 보장은 at-most-once다. 그래서 브로드캐스트는 유실될 수 있고, **유실의 결과는 오류가 아니라 낡은 화면이다.** 브로드캐스트를 놓친 컴포넌트는 `notification()`이 불리지 않아 다시 렌더하지 않고, 다음 이벤트가 올 때까지 그 화면만 낡은 채로 남는다. 예외도 로그도 사용자에게는 없다.

설계에 반영할 것 둘.

- **정확성이 중요한 화면은 브로드캐스트에만 기대지 않는다.** 잔액, 재고, 마감 시각처럼 틀리면 곤란한 값은 사용자 액션 시 재조회하거나 주기적으로 갱신한다. 브로드캐스트는 "빨리 보여주기"이지 "정확히 보장하기"가 아니다.
- **레이어를 바꾸면 과부하 신호가 사라진다.** `channels_redis`는 채널 큐가 넘치면 보내는 쪽에 `ChannelFull`을 던지지만 channels-nats는 pub/sub이라 받는 쪽이 조용히 버린다. wireview는 `ChannelFull`을 잡는 곳이 없어 동작이 바뀌지는 않으나, 느린 클라이언트로 가는 브로드캐스트의 유실이 예외가 아니라 로그로만 드러난다는 뜻이다.

NATS의 영속성(JetStream)은 이 레이어가 쓰지 않는다. 그 판단과 Jepsen 보고서의 적용 범위는 [channels-nats README](https://github.com/itda-work/channels-nats#core-nats만-쓴다--jetstream을-쓰지-않는-이유)에 있다.

## 6. 착수 기준

프로세스당 동시 연결이 수천을 넘고 유휴 연결이 많은 워크로드(대시보드, 알림)가 실제로 생길 때. 지금 실측으로는 Python 워커 하나가 2,000 유휴 연결을 188 MB로 들고 초당 수천 이벤트를 처리하므로, 워커 여덟 개면 만 단위 연결까지 프런트 없이 간다. 그 전에는 4절의 준비만 유지한다.

kraken(Elixir)을 쓸지 Go로 새로 만들지는 기술이 아니라 조직의 결정이다. Elixir는 클러스터링과 presence가 내장이고, Go는 단일 바이너리 배포와 성숙한 참조 구현(AnyCable-Go, Centrifugo)이 있으며 노드 간 pub/sub에 Redis나 NATS가 필요하다.
