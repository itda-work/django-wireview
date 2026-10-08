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
uv run python -m bench.render_queries --paired --times 5   # 렌더 부분별 SQL 계측(#182)을 켜고 끈 렌더 비용. 한 프로세스에서 끔·켬을 라운드마다 번갈아 잰다. --compare <기능 전 트리>는 base·끔·켬을 프로세스로 번갈아. 결과 해석은 docs/design/render-part-queries.md §4-4
```

## 무엇을 재는가

| 구분 | 지표 | 방법 |
|------|------|------|
| payload_bytes | 이벤트별 render 페이로드 크기 | `WireviewMeta.render_diff`를 라이브 저장소로 호출. 컨슈머가 보내는 것과 같은 JSON |
| timing | 이벤트당 ms, 템플릿 렌더 ms | 핸들러 + render_diff 300회 평균 |
| memory | 항목 50개 컴포넌트 하나의 메모리 | tracemalloc, 50개 마운트 평균 |
| async | `start_async`·`assign_async` 작업 하나의 ms(결과 렌더 포함) | `BenchAsync`의 작업은 10단계를 돌며 단계마다 상태를 바꾼다. `*_idle_ms`는 다른 렌더가 없을 때(게이트가 바로 열리는 경로), `start_async_busy_ms`는 컴포넌트가 루프 한 바퀴 간격으로 계속 렌더하는 동안이다. 이때 작업의 단계는 진행 중인 렌더를 기다린다(#138). `busy_renders_per_op`는 작업 하나가 끝날 때까지 돈 렌더 수로, ms가 아니라 횟수다 |
| ws | 연결당 서버 RSS, join/s, 이벤트/s, 페이로드, 브로드캐스트 ms | daphne(또는 `--server uvicorn`)를 띄우고 실제 WebSocket 연결. 기본은 인메모리 레이어(프로세스 1개). `--layer nats`·`--layer redis`면 여러 서버 프로세스가 그 레이어를 공유하며 브로커는 벤치가 임시 포트로 띄운다. 서버 로그는 `bench/.data/logs/` |
| ws.broadcast_ms | 브로드캐스트 하나가 모든 연결에 닿는 시간 | 연결 하나가 `abroadcast`를 부르고, 구독한 모든 컴포넌트가 다시 렌더할 때까지의 벽시계 시간. 프로세스가 늘면 렌더가 병렬화된다 |

컴포넌트는 `bench/benchapp/live.py`에 있습니다. `BenchFlat`은 스칼라 7개, `BenchList`는 항목마다 `{% if %}`가 있는 루프와 최상위 `{% if %}`가 있습니다. `BenchBoard`는 LiveComponent `BenchCard` 셋을 둔 부모이고, `BenchAsync`는 `start_async`·`assign_async`로 작업을 돌립니다.

## 비교가 공정한 이유

`bench-compare`는 과거 커밋을 별도 worktree에 받아 그 커밋의 의존성으로 venv를 만들고, 벤치 코드만 현재 트리에서 복사해 넣습니다. 벤치는 주로 `mount`, `render_diff`와 wire 프로토콜을 씁니다. 내부 메서드에 닿는 곳(`list.template_render_ms`의 컨텍스트 읽기, LiveComponent 시나리오의 컨슈머 렌더 경로)은 그 메서드가 없는 옛 트리에서 예전 이름으로 되돌아가므로 GAP-024 이전 코드에서도 그대로 돕니다. 반대로 라이브러리에서 내부 이름을 지우면 벤치가 깨질 수 있어, `tests/test_bench_harness.py`가 작은 크기로 `payload.run()`을 한 번 돌립니다(#145에서 `_get_context_async`를 지운 뒤 `make bench`가 죽었습니다). join 상태는 **재는 트리 자신의 `sign_state`로** 서명합니다(`bench/ws.py`의 `join_state`) — 커밋마다 그 커밋이 `data-state`에 넣었을 토큰을 받고, 서명 봉투 이전의 트리는 그 시절의 `Signer().sign(json)`을 받습니다. 예전에는 무조건 구형식으로 서명하고 `STATE_ACCEPT_LEGACY`에 기댔는데, 0.3.0부터 live_session을 선언한 프로젝트는 그 플래그와 무관하게 옛 토큰을 거절하고 testproj가 경계를 선언하므로 WebSocket 구간이 첫 join에서 죽어 있었습니다. `tests/test_bench_harness.py`가 이것을 지킵니다.

## 읽는 법

- `list.first_render`는 첫 렌더라 항상 전체입니다. 나머지 `list.*`가 부분 diff인지가 핵심입니다.
- `list.no_change`는 0이어야 합니다. 값이 있으면 상태 없이도 diff가 나가는 회귀입니다.
- `list.insert_front`·`remove_first`·`move_last_to_first`·`reverse` 등 목록 편집(항목 50개, `list500.*`은 500개)은 항목의 위치가 밀리는 변경입니다. 위치 기반 diff는 편집 지점 뒤의 항목을 전부 다시 보냅니다(GAP-030, `docs/design/keyed-comprehension.md`). 바이트는 서명 상태를 포함한 diff 객체 JSON이고, 500개 목록에서는 그 대부분이 서명 상태입니다. `list500.*_ms`는 그 편집의 렌더당 시간이고, `rotate_duplicates`는 내용으로 짝짓는 diff의 최악 경우(모든 항목이 같음)입니다.
- 이벤트당 ms는 CPU 단일 코어 기준이고, 대부분 Django 템플릿 렌더입니다.
- `ws.*.per_connection_kb`의 대부분은 daphne와 Channels 스택입니다. 연결만 열고 join하지 않으면 약 39 KB입니다.

## Windows (Parallels 게스트)

Windows 수치는 macOS 호스트의 Parallels 랩 클론(`win11-parlab`, ARM Windows 11)에서 같은 벤치를 돌려 얻는다. 저장소 쪽 드라이버는 `bench/windows/run.sh`다. 게스트 제어(`pmlab_start`·`pmlab_push`·`pmlab_runps` 등)는 **저장소 밖에 있다** — 유지보수자 머신의 `windows-parallels-lab` 에이전트 스킬이 주는 `pmlab.sh`이고, 이 저장소에도 휠에도 들어 있지 않다. `run.sh`는 그 파일을 `PMLAB_SH` 환경 변수(기본값은 유지보수자의 설치 경로 `~/.claude/skills/windows-parallels-lab/scripts/pmlab.sh`)에서 읽는다. 그 스킬이 없으면 `run.sh`가 쓰는 것을 `prlctl`로 직접 만들어 `PMLAB_SH`로 가리킨다: 함수 `pmlab_state`(VM이 떠 있으면 `running`을 출력), `pmlab_push <로컬 파일> <게스트 쪽 이름>`(공유 폴더에 넣기), `pmlab_runps <스크립트>`(게스트에서 PowerShell 실행, `PMLAB_EXEC_TIMEOUT` 초까지 기다림), 그리고 변수 `PMLAB_SHARE_DIR`(호스트 쪽 공유 폴더 경로). `run.sh`의 주석이 말하는 `pmlab_start`·`pmlab_stop`·`pmlab_snapshot`과 아래의 `pmlab_wait_ready`는 손으로 하는 VM 기동·정지·스냅샷이라 `prlctl start`·`stop`·`snapshot`으로 대신해도 된다. 아래 순서는 그 스킬이 있는 머신의 것이다.

```bash
source "${PMLAB_SH:-$HOME/.claude/skills/windows-parallels-lab/scripts/pmlab.sh}"   # 저장소 밖
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

## FastAPI와 비교

`bench/compare_fastapi/`는 같은 작은 앱을 wireview와 FastAPI로 한 번씩 만들어 같은 조건에서 잰다(#174). 결과는 `bench/results/<커밋>-fastapi.json`에, 차트는 `docs/images/bench-fastapi-*.svg`에 남고, 해석은 [성능 가이드](../docs/PERFORMANCE.md#fastapi와-비교)에 있다.

```bash
make bench-fastapi                     # 클라이언트 빌드 → 세 구현을 5회차씩 → bench/results/<커밋>[-dirty]-fastapi.json
make bench-fastapi ARGS="--rounds 1 --clicks 30 --loads 4 --connections 200"   # 빨리 한 번 (몇 분)
make bench-fastapi-charts              # chart.py 의 RESULT 가 가리키는 결과에서 SVG 차트를 다시 만든다
```

node와 Playwright의 chromium이 필요하다. FastAPI는 런타임 의존성이 아니다 — `uv run --with fastapi==<버전>`으로 그 실행에만 얹는다(버전은 Makefile의 `FASTAPI_VERSION`). React·Vite는 `bench/compare_fastapi/client/package.json`과 그 lock에 있다.

### 무엇을 만들었나

한 화면에 공유 카운터, 알림 값, 항목 50개의 피드가 있다. 버튼 셋이 각각 값 하나를 바꾸고(`#increment`), 피드 맨 앞에 항목을 넣고(맨 뒤 항목이 빠져 늘 50개다, `#insert`), 모든 사용자에게 알림을 브로드캐스트한다(`#announce`). 세 구현이 같은 DOM id를 그린다.

| 구현 | 서버 | 클라이언트 | 사람이 쓴 파일 |
|------|------|------------|----------------|
| `wireview` | Django ASGI 핸들러, Channels InMemory 레이어, `Board` 컴포넌트 | `wireview.min.js` (`make build-js`) | `wv/board/live.py`, 템플릿 둘 |
| `fastapi-react` | FastAPI의 WebSocket 엔드포인트 하나(공식 문서의 방식, 브로드캐스트는 연결 목록을 차례로 돈다) | React 19, Vite 프로덕션 빌드 | `fastapi_app/main.py`, `client/react/` |
| `fastapi-vanilla` | 같은 엔드포인트 | 프레임워크 없는 손 JS, Vite 빌드 | `fastapi_app/main.py`, `client/vanilla/` |

클라이언트는 둘을 싣는다. FastAPI 생태계의 전형은 React지만, 첫 화면의 JS 크기는 클라이언트 선택이 거의 다 정하므로 하한으로 프레임워크 없는 클라이언트를 함께 잰다. FastAPI 쪽은 첫 데이터와 조작을 모두 WebSocket 하나로 주고받는다. 흔한 REST(`fetch`) 방식이면 요청마다 HTTP 헤더 수백 바이트와 연결 왕복이 붙으므로, 이 선택은 FastAPI에 유리하다. 데이터는 두 쪽 모두 같은 프로세스 메모리의 `store.py`를 읽고 쓴다 — ORM 둘이 아니라 스택 둘을 비교한다.

### 공정성 규칙

- 같은 기계, 같은 Python, 같은 서버: uvicorn 1프로세스, `--ws websockets`, `--log-level warning`, permessage-deflate 기본값(켜짐). wireview는 DEBUG off에 InMemory 레이어다. 정적 파일은 두 쪽 모두 앱이 직접 서빙한다(Django `ASGIStaticFilesHandler`, FastAPI `StaticFiles`).
- 회차마다 서버를 새로 띄우고, 회차마다 구현의 순서를 뒤집어 어느 쪽도 늘 더 따뜻한 기계에서 돌지 않게 한다. 기본은 5회차, 시나리오마다 워밍업 20번 뒤 200번 클릭, 첫 화면은 워밍업 3번 뒤 20번 로드다. 회차마다 중앙값·p95를 내고, 회차 중앙값들의 중앙값과 범위를 적는다.
- 측정 환경(CPU, OS, Python·패키지·브라우저·node·React·Vite 버전, 시작과 끝의 load average)은 결과 JSON의 `environment`에 있다.
- `tracemalloc`은 켜지 않는다.

### 무엇을 어떻게 재나

| 지표 | 방법 |
|------|------|
| 클릭에서 화면까지 | 페이지 안에서 잰다. 클릭 시각부터 `MutationObserver`가 바뀐 텍스트를 본 시각(`dom_ms`)과, 그 변화를 그린 프레임이 페인트된 뒤의 첫 작업 시각(`paint_ms`). 클릭은 프레임 안 임의의 위치에 떨어지도록 0~16.7ms 기다렸다 누른다(시드 고정, 세 구현이 같은 순서) |
| 브로드캐스트 | 다른 브라우저 컨텍스트(다른 사용자)의 페이지가 바뀔 때까지. 두 페이지가 공유하는 시계(`performance.timeOrigin + now()`)로 잰다 |
| 전송 바이트 | Chromium DevTools가 알려 주는 WebSocket 페이로드 바이트, 보낸 것과 받은 것. 두 쪽 모두 프레임 헤더 제외, deflate 전 |
| 서버 처리 시간 | `timing.py`의 `Timed`가 두 앱을 똑같이 감싼다. 클라이언트 메시지가 ASGI `receive`에서 나온 때부터 같은 연결의 다음 `send`까지 |
| 첫 화면 | 매번 새 브라우저 컨텍스트(캐시 없음)로, 다른 사이트의 페이지에서 링크를 따라오듯 들어온다. 내려받은 HTML·JS(원본과 gzip -6), 그동안의 WebSocket 바이트, First Contentful Paint, 조작 가능해진 시각(`data-is-live="true"`) |
| 팬아웃 | 연결 1,000개(브라우저가 아닌 WebSocket 클라이언트)가 붙은 서버에 브로드캐스트 하나를 보내 모든 연결이 갱신을 받을 때까지 |
| 코드 줄 수 | `loc.py`. 기능을 위해 사람이 쓴 파일만, 빈 줄·주석·docstring 제외. 두 쪽이 공유하는 저장소와 계측 코드, 생성 파일은 세지 않는다. 프로젝트 골격(wireview의 settings·urls·asgi, FastAPI 쪽의 vite 설정·package.json)은 따로 센다 |

첫 화면을 `about:blank`에서 열지 않는 이유: Django의 `SecurityMiddleware`가 기본으로 보내는 `Cross-Origin-Opener-Policy` 헤더가 있으면 Chromium이 렌더러 프로세스를 바꾸고, 그 값(이 기계에서 약 35ms)을 wireview 쪽만 냈다. 다른 사이트에서 들어오면 헤더와 상관없이 세 구현이 모두 프로세스를 바꾼다. 실제 방문자의 조건이 이쪽이다.

`dom_ms`와 `paint_ms`를 둘 다 싣는 이유: React와 손 JS는 메시지가 오자마자 DOM을 바꾸고 브라우저가 다음 프레임에 그린다. wireview 클라이언트는 패치를 그 다음 프레임(`requestAnimationFrame`)까지 모았다가 그 자리에서 쓴다. 그래서 `dom_ms`는 wireview에서만 프레임 하나만큼 늦게 나오지만, 사용자가 보는 것은 `paint_ms`다.

### 결과를 문서에 싣기

README "숫자"와 성능 가이드의 차트·표·숫자는 모두 `bench/compare_fastapi/chart.py`의 `RESULT`가 가리키는 결과 하나에서 나온다. 새로 쟀으면 `RESULT`를 새 파일로 바꾸고 `make bench-fastapi-charts`로 차트를 다시 만든 뒤, `python -m bench.compare_fastapi.chart --table`의 표를 성능 가이드에 붙이고 README와 성능 가이드의 문장을 새 숫자로 고친다(`--facts`가 README가 인용하는 값을 보여 준다). `tests/test_bench_fastapi.py`가 차트·표·문장의 숫자가 그 결과와 같은지, `RESULT`가 가장 새 측정인지, 결과의 코드 줄 수가 지금 구현과 같은지 본다. 구현을 고치면 다시 잰다.
