# Benchmarks

wireview의 성능 주장을 직접 재기 위한 도구입니다. 결과는 `bench/results/`에 JSON으로 남고, 과거 커밋과 나란히 비교할 수 있습니다.

```bash
make bench                         # 현재 트리. 인프로세스 + WebSocket(daphne, 연결 500개)
make bench ARGS="--skip-ws"        # 인프로세스만 (10초 안쪽)
make bench-compare BASE=997ee59    # 과거 커밋을 worktree에 받아 같은 벤치를 돌리고 비교표 출력
make bench ARGS="--layer nats --processes 4 --connections 2000"   # channels-nats 위에서 daphne 4개. nats-server 필요 (벤치가 임시 포트로 직접 띄운다)
make bench ARGS="--layer redis --processes 4 --connections 2000"  # channels_redis 위에서 daphne 4개. redis-server 필요 (벤치가 임시 포트로 직접 띄운다)
make bench ARGS="--server uvicorn"  # daphne 대신 uvicorn. Windows에서 daphne는 select() 루프(프로세스당 소켓 512개)에 묶이므로 이쪽으로 잰다
make bench ARGS="--server uvicorn-nodeflate"  # permessage-deflate를 끈 uvicorn. 연결당 메모리의 대부분이 이 압축 컨텍스트다 (docs/design/transport-abstraction.md §5-2-1)
```

## 무엇을 재는가

| 구분 | 지표 | 방법 |
|------|------|------|
| payload_bytes | 이벤트별 render 페이로드 크기 | `WireviewMeta.render_diff`를 라이브 저장소로 호출. 컨슈머가 보내는 것과 같은 JSON |
| timing | 이벤트당 ms, 템플릿 렌더 ms | 핸들러 + render_diff 300회 평균 |
| memory | 항목 50개 컴포넌트 하나의 메모리 | tracemalloc, 50개 마운트 평균 |
| ws | 연결당 서버 RSS, join/s, 이벤트/s, 페이로드, 브로드캐스트 ms | daphne(또는 `--server uvicorn`)를 띄우고 실제 WebSocket 연결. 기본은 인메모리 레이어(프로세스 1개). `--layer nats`·`--layer redis`면 여러 서버 프로세스가 그 레이어를 공유하며 브로커는 벤치가 임시 포트로 띄운다. 서버 로그는 `bench/.data/logs/` |
| ws.broadcast_ms | 브로드캐스트 하나가 모든 연결에 닿는 시간 | 연결 하나가 `abroadcast`를 부르고, 구독한 모든 컴포넌트가 다시 렌더할 때까지의 벽시계 시간. 프로세스가 늘면 렌더가 병렬화된다 |

컴포넌트는 `bench/benchapp/live.py` 둘입니다. `BenchFlat`은 스칼라 7개, `BenchList`는 항목마다 `{% if %}`가 있는 루프와 최상위 `{% if %}`가 있습니다.

## 비교가 공정한 이유

`bench-compare`는 과거 커밋을 별도 worktree에 받아 그 커밋의 의존성으로 venv를 만들고, 벤치 코드만 현재 트리에서 복사해 넣습니다. 벤치는 공개 API(`mount`, `render_diff`)와 wire 프로토콜만 쓰므로 GAP-024 이전 코드에서도 그대로 돕니다. join 상태는 구형식 서명(v1 봉투 이전)을 쓰므로, `bench/settings.py`가 `WIREVIEW["STATE_ACCEPT_LEGACY"] = True`로 이를 받아들이게 합니다(운영 권장 설정이 아닙니다).

## 읽는 법

- `list.first_render`는 첫 렌더라 항상 전체입니다. 나머지 `list.*`가 부분 diff인지가 핵심입니다.
- `list.no_change`는 0이어야 합니다. 값이 있으면 상태 없이도 diff가 나가는 회귀입니다.
- 이벤트당 ms는 CPU 단일 코어 기준이고, 대부분 Django 템플릿 렌더입니다.
- `ws.*.per_connection_kb`의 대부분은 daphne와 Channels 스택입니다. 연결만 열고 join하지 않으면 약 39 KB입니다.

## Windows (Parallels 게스트)

Windows 수치는 macOS 호스트의 Parallels 랩 클론(`win11-parlab`, ARM Windows 11)에서 같은 벤치를 돌려 얻는다. 게스트 제어는 `windows-parallels-lab` 스킬(`~/.claude/skills/`)의 `pmlab.sh`이고, 저장소 쪽 드라이버는 `bench/windows/run.sh`다.

```bash
source ~/.claude/skills/windows-parallels-lab/scripts/pmlab.sh
pmlab_start && pmlab_wait_ready          # 랩 클론 기동 (마스터 VM은 건드리지 않는다)
bench/windows/run.sh stage               # HEAD 아카이브, channels-nats wheel, nats-server arm64, 게스트 스크립트를 공유 폴더로
bench/windows/run.sh provision           # C:\bench 에 uv, Python 3.12 (arm64 + x64), venv 둘, nats-server
bench/windows/run.sh run                 # 여섯 구성을 분리 실행하고 끝날 때까지 기다린다 (약 3분)
bench/windows/run.sh collect             # bench/results/win11-parlab-*.json 으로 복사
pmlab_stop
```

게스트에는 venv가 둘이다. `.venv`는 네이티브 ARM64 Python으로 uvicorn 스택만 있다. daphne는 autobahn과 cryptography의 ARM64 wheel이 없어 컴파일러 없이는 설치되지 않는다. `.venv-x64`는 x64 에뮬레이션 Python으로 daphne와 uvicorn이 모두 있다. 에뮬레이션은 CPU 비용을 2배쯤 부풀리므로 x64 수치는 상대 비교용이다. 여섯 구성은 `bench/windows/seq.ps1`에 있고, 결과 해석은 `docs/design/transport-abstraction.md` §5-2에 있다. 핵심은 하나다. daphne는 Windows에서 프로세스당 연결 약 500개에서 `select()` 한계로 죽고, 단일 프로세스 uvicorn은 죽지 않는다.

## 레이어 비교

`--layer` 는 `memory`(프로세스 1개 전용), `nats`(channels-nats), `redis`(channels_redis) 셋이다. 브로커는 벤치가 임시 포트에 직접 띄우고 끝나면 정리하므로 미리 켜 둘 필요가 없다. 이미 떠 있는 브로커를 쓰려면 `NATS_URL` 또는 `REDIS_URL` 을 준다.

레이어를 비교할 때는 **회차를 여러 번 돌려 중앙값을 쓴다**. 첫 회차는 콜드 캐시로 처리량이 25%쯤 낮게 나온다. 2026-09-08 비교 결과는 `docs/design/transport-abstraction.md` §5-3, 회차별 수치는 `bench/results/a993181-daphne-layer-comparison.spread.json` 에 있다.
