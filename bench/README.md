# Benchmarks

wireview의 성능 주장을 직접 재기 위한 도구입니다. 결과는 `bench/results/`에 JSON으로 남고, 과거 커밋과 나란히 비교할 수 있습니다.

```bash
make bench                         # 현재 트리. 인프로세스 + WebSocket(daphne, 연결 500개)
make bench ARGS="--skip-ws"        # 인프로세스만 (10초 안쪽)
make bench-compare BASE=997ee59    # 과거 커밋을 worktree에 받아 같은 벤치를 돌리고 비교표 출력
make bench ARGS="--layer nats --processes 4 --connections 2000"   # channels-nats 위에서 daphne 4개. nats-server와 uv pip install -e ../channels-nats 필요
make bench ARGS="--server uvicorn"  # daphne 대신 uvicorn. Windows에서 daphne는 select() 루프(프로세스당 소켓 512개)에 묶이므로 이쪽으로 잰다
```

## 무엇을 재는가

| 구분 | 지표 | 방법 |
|------|------|------|
| payload_bytes | 이벤트별 render 페이로드 크기 | `WireviewMeta.render_diff`를 라이브 저장소로 호출. 컨슈머가 보내는 것과 같은 JSON |
| timing | 이벤트당 ms, 템플릿 렌더 ms | 핸들러 + render_diff 300회 평균 |
| memory | 항목 50개 컴포넌트 하나의 메모리 | tracemalloc, 50개 마운트 평균 |
| ws | 연결당 서버 RSS, join/s, 이벤트/s, 페이로드, 브로드캐스트 ms | daphne(또는 `--server uvicorn`)를 띄우고 실제 WebSocket 연결. 기본은 인메모리 레이어(프로세스 1개). `--layer nats`면 여러 서버 프로세스가 channels-nats를 공유. 서버 로그는 `bench/.data/logs/` |
| ws.broadcast_ms | 브로드캐스트 하나가 모든 연결에 닿는 시간 | 연결 하나가 `abroadcast`를 부르고, 구독한 모든 컴포넌트가 다시 렌더할 때까지의 벽시계 시간. 프로세스가 늘면 렌더가 병렬화된다 |

컴포넌트는 `bench/benchapp/live.py` 둘입니다. `BenchFlat`은 스칼라 7개, `BenchList`는 항목마다 `{% if %}`가 있는 루프와 최상위 `{% if %}`가 있습니다.

## 비교가 공정한 이유

`bench-compare`는 과거 커밋을 별도 worktree에 받아 그 커밋의 의존성으로 venv를 만들고, 벤치 코드만 현재 트리에서 복사해 넣습니다. 벤치는 공개 API(`mount`, `render_diff`)와 wire 프로토콜만 쓰므로 GAP-024 이전 코드에서도 그대로 돕니다. join 상태는 구형식 서명을 쓰는데, 새 서버도 이를 받아들입니다.

## 읽는 법

- `list.first_render`는 첫 렌더라 항상 전체입니다. 나머지 `list.*`가 부분 diff인지가 핵심입니다.
- `list.no_change`는 0이어야 합니다. 값이 있으면 상태 없이도 diff가 나가는 회귀입니다.
- 이벤트당 ms는 CPU 단일 코어 기준이고, 대부분 Django 템플릿 렌더입니다.
- `ws.*.per_connection_kb`의 대부분은 daphne와 Channels 스택입니다. 연결만 열고 join하지 않으면 약 39 KB입니다.
