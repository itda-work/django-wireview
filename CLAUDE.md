# django-wireview AI Guide

> 에이전트를 위한 최소 지도. API 상세와 예시는 여기에 복제하지 않고 `docs/`로 링크한다.

## 정체성

Phoenix LiveView 스타일의 Django 실시간 컴포넌트 라이브러리. Pydantic v2 기반 `Component`가 서버에서 렌더링되고, Django Channels WebSocket으로 HTML diff를 보내 idiomorph로 DOM을 갱신한다. django-reactor의 후속 프로젝트.

## 정본 (여기에 복제하지 않는다)

| 무엇 | 정본 |
|------|------|
| Python·Django 지원 범위, 의존성, 패키지 버전 | `pyproject.toml`, `.github/workflows/ci.yml` 매트릭스 |
| 코드 스타일 (ruff 120자, double quotes, djlint 2칸) | `pyproject.toml`의 `[tool.ruff]`, `[tool.djlint]`, `[tool.pyright]` |
| 개발 명령 | `Makefile` (`make help`) |
| 설정 키와 기본값 | `wireview/settings.py`의 `DEFAULT` |
| 기능 로드맵과 미구현 목록 | `docs/FEATURE-GAP.md` |
| 기능별 API 상세 | `docs/features/README.md` (인덱스) |
| 학습 순서 | `docs/tutorials/README.md` |
| 릴리스 버전 | git 태그 `v*`와 `pyproject.toml`의 version |

## 저장소 지도

```
wireview/
├── __init__.py            공개 API lazy export (from wireview import Component, LiveComponent, JS, mount ...)
├── component.py           하위 호환 re-export. 새 코드는 wireview에서 import
├── core/component.py      Component 베이스: 라이프사이클, 이벤트 디스패치, streams·uploads·async·flash·hooks 메서드
├── core/meta.py           WireviewMeta (self.wire): push_to/replace_to, push_js, put_flash, push_title 등 클라이언트 명령
├── core/rendered.py       동적 마커 기반 diff 구조
├── core/state.py          data-state 서명·복원 (압축 형식, 구형식 호환)
├── core/transport.py      Outbound·Broker 인터페이스와 Channels 구현. 채널 레이어를 건드리는 유일한 곳
├── template_engine.py     템플릿 VariableNode에 diff 마커 자동 주입
├── consumer.py            WireviewConsumer (WebSocket, /__wireview__)
├── views.py               UploadView (청크 업로드 HTTP 엔드포인트)
├── urls.py                websocket_urlpatterns, urlpatterns
├── repository.py          ComponentRepository: 연결당 컴포넌트 인스턴스 관리
├── live_component.py      LiveComponent (부모 연결을 공유하는 중첩 상태 컴포넌트)
├── function_component.py  @function_component (상태 없는 템플릿 함수)
├── slots.py               슬롯 시스템 ({% fill %}, {% render_slot %})
├── async_result.py        AsyncResult / AsyncState
├── auto_broadcast.py      Django signals → 컴포넌트 mutation() 알림
├── event_transpiler.py    {% on %} 수정자 파싱 (.prevent, .debounce.300 ...)
├── js.py                  JS() 명령 빌더
├── schemas.py, serializer.py  Pydantic 스키마, 모델 직렬화
├── settings.py            WIREVIEW 설정 기본값
├── testing.py             mount(), MountedComponent, ComponentTestCase
├── utils.py, log.py       db 헬퍼, 로깅
├── debug/sync_detector.py sync/async 전환 중첩 감지 (DEBUG_SYNC_TRANSITIONS)
├── features/              streams.py, presence.py (PresenceMixin), uploads.py (UploadRegistry)
├── templatetags/wireview.py  템플릿 태그 전체 (아래 표)
├── management/commands/   wireview_stubs (.pyi 생성), wireview_lsp (IDE 메타데이터 JSON)
├── templates/wireview_header.html  {% wireview_header %}가 렌더. wireview.min.js를 로드
└── static/wireview/       wireview.js (소스), rendered.mjs (diff 적용·HTML 복원 순수 함수), wireview-boost.js, types.d.ts
                           wireview.min.js는 빌드 산출물이며 gitignore

tests/
├── test_*.py              라이브러리 단위·통합 테스트. WebSocket 없이 mount() 사용
├── js/*.test.mjs          클라이언트 순수 모듈 테스트 (node --test)
└── testproj/              Django 테스트 프로젝트. settings.py의 채널 레이어는 WIREVIEW_TEST_LAYER가 고르고
                           settings_nats.py·settings_redis.py가 이를 고정하는 진입점이다. 앱: todo, chat, dashboard, livecomp, notifications,
                           poll, quiz, rating, search, slots. E2E는 todo/tests.py, livecomp/tests.py

docs/                      features/ 기능 레퍼런스, tutorials/ 15편, FEATURE-GAP.md, ARCHITECTURE.md,
                           ROADMAP.md, DEPLOYMENT.md, PERFORMANCE.md, design/ 설계 메모, implementation/ 구현 노트
                           (implementation/wire-protocol.md 가 메시지 형태의 정본)
bench/                     성능 벤치마크 (make bench, make bench-compare BASE=<ref>). windows/ 는 Parallels 게스트 실측 레인. 설명은 bench/README.md
typings/                   channels 타입 스텁 (pyright용)
.claude/settings.json      권한 허용 목록과 ruff format 훅
```

### 템플릿 태그 (`{% load wireview %}`)

| 태그 | 용도 |
|------|------|
| `wireview_header` | JS 로드와 boost 메타 |
| `component`, `component_block` + `fill` + `render_slot` | 컴포넌트 렌더링, 슬롯 |
| `live_component`, `live_tag_header` | LiveComponent |
| `func`, `func_block` | Function Component |
| `tag_header` | 컴포넌트 루트 엘리먼트 속성 |
| `on` | 이벤트 바인딩. `{% on "click.prevent" "handler" arg=1 %}` |
| `cond`, `class` (태그), `str`, `concat` (필터) | 조건, 클래스, 문자열 헬퍼 |
| `upload_input`, `upload_drop_zone`, `upload_button`, `upload_preview` | 파일 업로드 |

### 클라이언트 DOM 속성

`wire-hook`, `wire-stream`, `wire-viewport-top/bottom`, `wire-disabled-with`, `wire-feedback-for`, `wire-no-feedback`, `wire-auto-recover`, `wire-flash`, `wire-upload-drop`, `wire-preview`. 로딩 클래스는 `wireview-click-loading` 계열. 상세는 `docs/features/`.

## 명령

| 할 일 | 명령 | 선행 조건 |
|------|------|-----------|
| 의존성 설치 | `make install` 과 `npm ci` | `uv sync --dev`는 dev 도구를 설치하지 않는다. extras를 써야 한다 |
| JS 빌드 | `make build-js` | 개발 서버와 E2E 전에 필수. 산출물은 gitignore |
| 테스트 (e2e·slow 제외) | `make test` 또는 `make test ARGS="-k streams"` | collectstatic과 DJANGO_ALLOW_ASYNC_UNSAFE는 Makefile이 처리 |
| E2E | `make test-e2e` (NATS), `make test-e2e LAYER=redis` | nats-server 127.0.0.1:4222와 channels-nats, JS 빌드, `make playwright-install` |
| 린트 | `make lint` (ruff + djlint) | |
| 타입 검사 | `make check` (pyright, tests/ 제외) | |
| 클라이언트 테스트 | `make test-js` (`npm test`, node --test) | |
| 포맷 | `make format` | |
| 품질 일괄 | `make quality` | CI의 lint·typecheck 잡과 동일 범위 |
| 개발 서버 | `make run-daphne` | JS 빌드, Redis |
| 타입 스텁 확인 | `cd tests && uv run python manage.py wireview_stubs --check` | |
| 성능 실측 | `make bench`, 과거 커밋과 비교는 `make bench-compare BASE=997ee59`, 서버 선택은 `ARGS="--server uvicorn"` | WebSocket 구간은 daphne 또는 uvicorn을 직접 띄우며 Redis 불필요 |
| Windows 실측 | `bench/windows/run.sh` (stage → provision → run → collect) | macOS + Parallels 랩 클론 + `windows-parallels-lab` 스킬. 상세는 `bench/README.md` |

CI(`ci.yml`)는 Python×Django 매트릭스 테스트, Redis를 띄운 E2E, lint, typecheck, build 다섯 잡이다. PR 전에 `make quality`와 `make test`를 통과시킨다.

## 작업 규약

- **기능 단위는 GAP 번호.** `docs/FEATURE-GAP.md`의 GAP-nnn 항목을 고르거나 새로 만들고, 커밋 제목에 적는다. 예: `feat: Add on_mount hooks (GAP-021)`
- **완료 정의.** 구현 + `tests/test_<feature>.py` + `docs/features/<feature>.md` + `docs/features/README.md` 인덱스 갱신 + FEATURE-GAP.md 상태 갱신 + `CHANGELOG.md` Unreleased 한 줄. 새 모듈·태그·속성이 생기면 이 문서의 지도도 갱신한다. 성능을 주장하는 변경은 `make bench-compare`로 전후 수치를 문서에 남긴다.
- **커밋.** 영어, Conventional Commits. 이슈와 PR은 `gh` CLI로 다룬다.
- **언어.** 코드 주석과 docstring은 영어. 문서는 한국어 기본.
- **테스트 마커.** `unit` / `integration` / `slow` / `e2e` 중 하나 이상을 붙인다 (`--strict-markers`). 라이브러리 테스트는 `tests/test_*.py`, 앱·E2E 테스트는 `tests/testproj/<app>/tests.py`.
- **설정 키 추가** 시 `wireview/settings.py`의 `DEFAULT`에 기본값을 넣는다.
- **JS.** `wireview.js`는 ES2020, 2칸 들여쓰기, JSDoc. 포매터는 없다. DOM 없이 검증 가능한 로직은 `rendered.mjs`처럼 순수 모듈로 빼고 `tests/js/`에 node 테스트를 둔다.
- **import.** 새 코드는 `from wireview import Component, LiveComponent, JS, mount`. `wireview.component` 경로는 하위 호환용.

## 함정

- **`wireview.min.js`가 없으면 페이지에서 JS가 로드되지 않는다.** clone 직후와 `wireview.js` 수정 후 `make build-js`.
- **testproj의 채널 레이어는 `WIREVIEW_TEST_LAYER`가 고른다.** 기본은 `memory`(브로커 불요), `make test-e2e`는 `nats`, CI는 `redis`다. `make test-e2e LAYER=redis`로 바꿀 수 있다. NATS로 돌리려면 `nats-server`와 `uv pip install -e ../channels-nats`(PyPI 미공개)가 필요하다.
- **단위·통합 테스트도 일부는 채널 레이어를 쓴다.** `tests/test_uploads.py`의 `UploadView` 테스트가 세션 채널로 보낸다. 그래서 기본값이 `memory`다. 브로커가 없는 레이어를 기본으로 두면 그 두 테스트가 연결 타임아웃으로 2분씩 걸린다.
- **클라이언트가 호출할 수 있는 메서드.** `_`로 시작하지 않는 소문자 이름의 메서드는 이벤트 핸들러로 노출되고 `validate_call`로 감싸진다. 내부 헬퍼는 반드시 `_` 접두사. 핸들러와 라이프사이클 메서드는 async.
- **컴포넌트 이름은 클래스명으로 전역 등록.** 다른 모듈에서 같은 클래스명을 쓰면 경고가 난다. 템플릿에서 `app:Name` 또는 FQN으로 구분한다.
- **상태 필드.** JSON 직렬화 가능해야 한다. `_temporary_assigns`는 기본값이 있는 필드만 초기화된다. `_exclude_fields` 기본값은 `{"user", "wire"}`.
- **pyright는 `tests/`를 검사하지 않고, `tsc`는 checkJs=false라 JS 본문을 검사하지 않는다.** 둘 다 통과해도 해당 영역은 검증된 것이 아니다.
- **gitignore 대상.** `*.pyi` (AUTO_GENERATE_STUBS가 DEBUG에서 생성), `.wireview/`, `tests/static/`, `*.min.js`.
- **컴포넌트 ID**는 페이지 안에서 고유해야 한다.
- **채널 레이어는 core/transport.py에서만 만진다.** `get_channel_layer`, `group_add`, `group_send`를 다른 모듈에 쓰면 tests/test_transport.py의 가드가 실패한다. fan-out은 `get_broker().publish`, 세션 메시지는 `WireviewMeta.send`.
- **프로세스를 늘리면 InMemory 레이어는 조용히 깨진다.** 브로드캐스트가 같은 프로세스의 연결에만 닿고 오류는 나지 않는다. 다중 프로세스에는 channels_redis나 channels-nats가 필수다. 성능은 둘이 대등하다(`docs/design/transport-abstraction.md` §5-3).
- **Windows에서 daphne는 연결 약 500개에서 죽는다.** daphne가 selector 루프를 강제하고 CPython의 Windows select()는 소켓 512개가 상한이다. Windows 배포는 uvicorn 단일 프로세스를 포트별로 N개 띄우고 Caddy로 분배한다(`docs/DEPLOYMENT.md`). `uvicorn --workers`도 Windows에서는 selector 루프다. 실측은 `bench/results/win11-parlab-*`, 재현은 `bench/windows/run.sh`.
- **USE_HMIN은 diff 마커를 지운다.** django-hmin이 HTML 주석을 제거하므로 부분 diff가 꺼지고 토큰 diff로 퇴화한다. 켤 때는 대역폭 손익을 실측한다.
- **data-state는 dynamic 파트다.** `{% tag_header %}`의 서명 상태는 라이브 렌더에서 마커로 감싸진다. static에 넣으면 fingerprint가 매번 바뀌어 부분 diff가 죽는다. 회귀 테스트는 tests/test_diff_stability.py.

## 문서 인덱스

- [README.md](./README.md) 사용 가이드와 빠른 시작
- [docs/features/README.md](./docs/features/README.md) 기능 레퍼런스 인덱스
- [docs/tutorials/README.md](./docs/tutorials/README.md) 튜토리얼 15편
- [docs/FEATURE-GAP.md](./docs/FEATURE-GAP.md) Phoenix LiveView 대비 갭과 GAP 번호
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) 아키텍처
- [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) 배포
- [docs/PERFORMANCE.md](./docs/PERFORMANCE.md) 성능
- [CHANGELOG.md](./CHANGELOG.md) 변경 이력
