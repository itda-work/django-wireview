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
| 작업 절차 (세션 시작, 이슈·wip, 완료 정의, 커밋, 언어 규약) | `wireview-dev` 스킬 (`.claude/skills/wireview-dev/SKILL.md`) |
| 설정 키와 기본값 | `wireview/settings.py`의 `DEFAULT` |
| 기능 로드맵과 미구현 목록 | `docs/FEATURE-GAP.md` (GAP 번호), 작업 추적은 GitHub Issues |
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
├── core/rendered.py       동적 마커 기반 diff 구조. LiveComponent 자리는 참조 dynamic {"c": id}
├── core/session.py        SessionView. Django 세션의 읽기 전용 뷰. 소켓에서는 connect 때 한 번 읽는다
├── core/live_session.py   페이지 경계 정본. live_session() 선언과 레지스트리, @session.view,
│                          인증 세대 지문(auth_fingerprint), 로그아웃 무효화 발행 (GAP-009)
├── core/state.py          data-state 서명·복원 (v2 봉투: 클래스·경계·인증 세대 결합, 만료, 토큰 재사용)
├── core/signing.py        서명 키 정본. get_signer(salt) 하나로 모든 서명 지점이 SIGNING_KEY와 fallback을 공유
├── core/transport.py      Outbound·Broker 인터페이스와 Channels 구현. 채널 레이어를 건드리는 유일한 곳
├── template_engine.py     템플릿 VariableNode에 diff 마커 자동 주입
├── consumer.py            WireviewConsumer (WebSocket, /__wireview__). send_render가 자식 LiveComponent의 joined/update/leaving과 렌더를 함께 처리
├── views.py               UploadView (청크 업로드 HTTP 엔드포인트). 무상태 — 서명 토큰만으로 판단하고
│                          토큰에서 계산한 경로에 쓴다. 어느 워커에 닿아도 된다 (#83)
├── urls.py                websocket_urlpatterns, urlpatterns
├── repository.py          ComponentRepository: 연결당 컴포넌트 인스턴스 관리. LiveComponent의 수명주기 배치(take_lifecycle)
├── live_component.py      LiveComponent (부모 연결을 공유하는 중첩 상태 컴포넌트)
├── function_component.py  @function_component (상태 없는 템플릿 함수)
├── slots.py               슬롯 시스템 ({% fill %}, {% render_slot %})
├── async_result.py        AsyncResult / AsyncState
├── auto_broadcast.py      Django signals → 컴포넌트 mutation() 알림
├── event_transpiler.py    {% on %} 수정자 파싱 (.prevent, .debounce.300 ...)
├── js.py                  JS() 명령 빌더
├── schemas.py, serializer.py  Pydantic 스키마, 모델 직렬화
├── settings.py            WIREVIEW 설정 기본값
├── checks.py              Django system checks (조용한 실패를 manage.py check로. wireview.W001~W011)
├── telemetry.py           옵트인 계측 시그널 (event_handled, component_rendered, diff_computed, broadcast_published)
├── testing.py             mount(), MountedComponent, ComponentTestCase.
│                          내비게이션 단언·follow_redirect·follow_push·스트림 검사
├── utils.py, log.py       db 헬퍼, 로깅
├── debug/sync_detector.py sync/async 전환 중첩 감지 (DEBUG_SYNC_TRANSITIONS)
├── features/              streams.py, presence.py (PresenceMixin), uploads.py (UploadRegistry·토큰 v2),
│                          hooks.py (앱의 static/<app_label>/hooks/*.js 수집. 페이지가 아니라 프로젝트 단위),
│                          upload_store.py (청크 경로 계산·append·취소 마커·sweep. 워커들이 공유하는 유일한 상태)
├── templatetags/wireview.py  템플릿 태그 전체 (아래 표)
├── management/commands/   wireview_stubs (.pyi 생성), wireview_lsp (IDE 메타데이터 JSON),
│                          wireview_agent_setup (앱 개발자용 스킬을 프로젝트 .claude/skills/ 에 설치),
│                          wireview_upload_gc (토큰 만료보다 오래된 청크 파일 정리)
├── templates/wireview_header.html  {% wireview_header %}가 렌더. wireview.min.js를 로드
└── static/wireview/       wireview.js (소스), rendered.mjs (diff 적용·HTML 복원 순수 함수),
                           streams.mjs (스트림 DOM 판단 순수 함수), reload.mjs (reload 쿨다운 판단),
                           live-session.mjs (경계 넘음 판단 순수 함수), ready.mjs (defer 스크립트가 다 돌았는가),
                           wireview-boost.js, types.d.ts
                           wireview.min.js는 빌드 산출물이며 gitignore

tests/
├── test_*.py              라이브러리 단위·통합 테스트. WebSocket 없이 mount() 사용
│                          test_e2e_harness.py 는 E2E 하네스 자체의 계약을 지킨다
│                          test_live_session_contract.py 는 회귀가 아니라 계약을 진술한다 —
│                          컴포넌트가 생기는 경로 8개 × 거절 사유 5종을 parametrize로 돌린다.
│                          경로를 새로 만들면 행을 추가한다
├── js/*.test.mjs          클라이언트 순수 모듈 테스트 (node --test)
└── testproj/              Django 테스트 프로젝트(설정·URLconf). 채널 레이어는 WIREVIEW_TEST_LAYER가 고르고
                           settings_nats.py·settings_redis.py가 이를 고정하는 진입점이다.
                           e2e_server.py 가 E2E용 라이브 ASGI 서버의 정본이다 —
                           브라우저가 필요한 모든 스위트가 이것을 쓴다 (복제하면 test_e2e_harness.py가 실패한다).
                           server_errors() 가 블록 동안 서버가 남긴 ERROR 를 돌려준다: 핸들러가 터지면
                           소켓이 죽고 페이지가 멈출 뿐이라 브라우저 쪽에서는 느린 것과 구별되지 않는다.
                           e2e_browser.py 가 브라우저 대기의 정본이다 (open_live·expect_text·expect_count).
                           page.wait_for_selector 를 다른 곳에 쓰면 test_e2e_harness.py 의 가드가 실패한다
                           bookmarks/ 는 예제가 아니라 wireview 스킬 검증의 기준선이고,
                           uploadprobe/ 는 워커 둘짜리 업로드 E2E(test_multiworker_uploads.py)의 픽스처,
                           livesession/ 은 경계 넘는 이동 E2E(test_live_session_e2e.py)의 픽스처다

examples/                  예제 앱 11개. 각 디렉터리 = 개념 하나 + tests.py 하나 + README 하나.
                           testproj 위에서 돌고 make test가 함께 실행한다(pytest tests examples).
                           E2E는 todo/tests.py, livecomp/tests.py, hooks/tests.py. 인덱스는 examples/README.md
                           hooks/ 는 wire-hook 의 유일한 사용자이자 클라이언트 훅 경로의 유일한 검증이다

docs/                      features/ 기능 레퍼런스, tutorials/ 15편, FEATURE-GAP.md, ARCHITECTURE.md,
                           ROADMAP.md, DEPLOYMENT.md, PERFORMANCE.md, design/ 설계 메모(README.md 인덱스), implementation/ 구현 노트
                           (implementation/wire-protocol.md 가 메시지 형태의 정본)
bench/                     성능 벤치마크 (make bench, make bench-compare BASE=<ref>). windows/ 는 Parallels 게스트 실측 레인. 설명은 bench/README.md
typings/                   channels 타입 스텁 (pyright용)
skills/wireview/           앱 개발자용 스킬의 정본. 휠에 wireview/agent_skills/ 로 실린다.
                           .claude/skills/wireview 는 이것을 가리키는 심링크(dogfooding)
AGENTS.md                  .claude/skills/ 를 안 읽는 에이전트(Codex 등)를 위한 포인터
.claude/settings.json      권한 허용 목록과 ruff format 훅
```

### 템플릿 태그 (`{% load wireview %}`)

| 태그 | 용도 |
|------|------|
| `wireview_header` | JS 로드와 boost 메타 |
| `component`, `component_block` + `fill` + `render_slot` | 컴포넌트 렌더링, 슬롯 |
| `live_component`, `live_component_block` + `fill`, `live_tag_header` | LiveComponent, 슬롯 전달 |
| `func`, `func_block` | Function Component |
| `tag_header` | 컴포넌트 루트 엘리먼트 속성 |
| `on` | 이벤트 바인딩. `{% on "click.prevent" "handler" arg=1 %}` |
| `cond`, `class` (태그), `str`, `concat` (필터) | 조건, 클래스, 문자열 헬퍼 |
| `upload_input`, `upload_drop_zone`, `upload_button`, `upload_preview` | 파일 업로드 |

### 클라이언트 DOM 속성

`wire-hook`, `wire-stream`, `wire-viewport-top/bottom`, `wire-disabled-with`, `wire-feedback-for`, `wire-no-feedback`, `wire-auto-recover`, `wire-flash`, `wire-upload-drop`, `wire-preview`. 로딩 클래스는 `wireview-click-loading` 계열. 상세는 `docs/features/`.

## 명령

| 할 일 | 명령 |
|------|------|
| 테스트 (e2e·slow 제외) | `make test` |
| 품질 일괄 (lint + typecheck) | `make quality` |
| JS 빌드 | `make build-js` — clone 직후와 `wireview/static/wireview/wireview.js` 수정 후 필수 |
| 클라이언트 테스트 | `make test-js` |

전체 표(E2E 레이어, 벤치마크, Windows 실측, 타입 스텁)와 선행 조건은 `wireview-dev` 스킬에.
정의는 `Makefile` (`make help`)이 정본이다.

## 절차

**작업 절차는 `wireview-dev` 스킬에 있다.** 세션 시작(진행 중인 작업 찾기), 이슈·GAP·`wip`
라벨 규약, **도중에 발견한 결함을 다루는 법**, 완료 정의 체크리스트, 커밋과 이슈 종료,
전체 명령 표, 벤치 측정 주의. 이 문서는 지도와 금지선만 담는다.

추적의 진실 소스는 **GitHub Issues**(`itda-work/django-wireview`)다. 새 대화는
`gh issue list --label wip`로 시작한다.

## 함정

아래 중 열두 개는 `manage.py check`가 잡는다 (`wireview.W001`~`W011`, `docs/features/checks.md`).

- **`wireview.min.js`가 없으면 페이지에서 JS가 로드되지 않는다.** clone 직후와 `wireview/static/wireview/wireview.js` 수정 후 `make build-js`.
- **testproj의 채널 레이어는 `WIREVIEW_TEST_LAYER`가 고른다.** 기본은 `memory`(브로커 불요), `make test-e2e`와 CI는 `nats`다. E2E는 `tests/e2e.sh`가 nats-server를 직접 띄우고 끝나면 정리하므로 미리 켜 둘 필요가 없다(이미 떠 있으면 그것을 쓴다). 바꾸려면 `make test-e2e LAYER=redis` 또는 `LAYER=memory`. channels-nats는 dev extras에 있으므로 `make install`이면 들어온다.
- **단위·통합 테스트도 일부는 채널 레이어를 쓴다.** `tests/test_uploads.py`의 `UploadView` 테스트가 세션 채널로 보낸다. 그래서 기본값이 `memory`다. 브로커가 없는 레이어를 기본으로 두면 그 두 테스트가 연결 타임아웃으로 2분씩 걸린다.
- **클라이언트가 호출할 수 있는 메서드.** `_`로 시작하지 않고 **사용자 코드에서 정의한** 메서드만 이벤트 핸들러로 노출되고 `validate_call`로 감싸진다. 프레임워크(`wireview.*`)와 Pydantic이 소유한 이름은 서브클래스에서 오버라이드해도 노출되지 않는다 — `mount`·`joined`·`update`·`send_to_parent`·`model_post_init`은 클라이언트가 부를 수 없다. 판정은 `ComponentRepository._is_user_defined_method`, 회귀 테스트는 tests/test_security.py. 내부 헬퍼는 반드시 `_` 접두사. 핸들러와 라이프사이클 메서드는 async.
- **컴포넌트 이름은 클래스명으로 전역 등록.** 다른 모듈에서 같은 클래스명을 쓰면 경고가 난다. 템플릿에서 `app:Name` 또는 FQN으로 구분한다.
- **상태 필드.** JSON 직렬화 가능해야 한다. `_temporary_assigns`는 기본값이 있는 필드만 초기화된다. `_exclude_fields` 기본값은 `{"user", "wire"}`.
- **pyright는 `tests/`를 검사하지 않고, `tsc`는 checkJs=false라 JS 본문을 검사하지 않는다.** 둘 다 통과해도 해당 영역은 검증된 것이 아니다.
- **gitignore 대상.** `*.pyi` (AUTO_GENERATE_STUBS가 DEBUG에서 생성), `.wireview/`, `tests/static/`, `*.min.js`.
- **컴포넌트 ID**는 페이지 안에서 고유해야 한다.
- **LiveComponent는 부모가 소유한다.** 클라이언트는 `wireview-live` 요소에 join을 보내지 않고, 자식의 `joined()`·`update()`·`leaving()`과 렌더는 `consumer.send_render`가 부모 렌더 뒤에 처리해 같은 `render` 메시지의 `children`으로 보낸다. 렌더를 보내는 새 경로를 만들 때 `send_render`를 우회하면 자식 초기화가 조용히 빠진다. 계약은 `docs/design/live-component-ownership.md`.
- **채널 레이어는 core/transport.py에서만 만진다.** `get_channel_layer`, `group_add`, `group_send`를 다른 모듈에 쓰면 tests/test_transport.py의 가드가 실패한다. fan-out은 `get_broker().publish`, 세션 메시지는 `WireviewMeta.send`.
- **프로세스를 늘리면 InMemory 레이어는 조용히 깨진다.** 브로드캐스트가 같은 프로세스의 연결에만 닿고 오류는 나지 않는다. 다중 프로세스에는 channels_redis나 channels-nats가 필수다. 성능은 둘이 대등하다(`docs/design/transport-abstraction.md` §5-3).
- **Windows에서 daphne는 연결 약 500개에서 죽는다.** daphne가 selector 루프를 강제하고 CPython의 Windows select()는 소켓 512개가 상한이다. Windows 배포는 uvicorn 단일 프로세스를 포트별로 N개 띄우고 Caddy로 분배한다(`docs/DEPLOYMENT.md`). `uvicorn --workers`도 Windows에서는 selector 루프다. 실측은 `bench/results/win11-parlab-*`, 재현은 `bench/windows/run.sh`.
- **USE_HMIN은 diff 마커를 지운다.** django-hmin이 HTML 주석을 제거하므로 부분 diff가 꺼지고 토큰 diff로 퇴화한다. 켤 때는 대역폭 손익을 실측한다.
- **`UPLOAD_TEMP_DIR`은 첫 청크가 올 때에야 읽힌다.** 잘못된 경로나 마운트되지 않은 볼륨은 기동 시 아무 신호도 없고, 업로드가 하나씩 `ImproperlyConfigured`로 실패한다. 빈 문자열은 미설정과 같게 다뤄 시스템 temp로 간다(`Path("")`가 cwd이기 때문이다). `manage.py check`의 `wireview.W008`이 미리 잡는다.
- **청크 업로드가 워커 사이에서 공유해야 하는 것은 둘뿐이다.** 청크 저장소(`UPLOAD_TEMP_DIR`, 한 호스트면 시스템 temp가 이미 공유)와 서명 키. 엔드포인트는 상태를 안 들고 있으므로 스티키 라우팅은 필요 없다. 키가 어긋나면 403, 디렉터리가 어긋나면 200을 받고도 완료되지 않는다. 상세는 `docs/features/chunked-uploads.md`.
- **서명은 `wireview/core/signing.py`의 `get_signer(salt)`로만 한다.** `TimestampSigner(salt=...)`를 직접 만들면 `SIGNING_KEY`와 fallback을 무시해 키 로테이션이 조용히 깨진다. 서명 지점은 둘 — 업로드 토큰과 data-state.
- **async 뷰와 `ATOMIC_REQUESTS`.** Django 핸들러는 async 뷰를 트랜잭션으로 감쌀 수 없어 뷰를 부르기 전에 500을 낸다. 업로드 엔드포인트는 `wireview/urls.py`에서 모든 alias에 `non_atomic_requests`로 등록해 피한다. 새 async 뷰를 추가하면 같은 처리가 필요하고, **뷰를 직접 호출하는 테스트는 이 실패를 못 잡는다**.
- **live_session은 `django.template.context_processors.request`에 의존한다.** 템플릿 태그가 경계를
  `context["request"]`에서 읽는다. 없으면 기능이 통째로 조용히 꺼진다 — 페이지는 200으로 그려지고
  경계만 없다. `wireview.W010`이 잡는다.
- **페이지 경계는 페이지가 선언한다.** `live_session`은 컴포넌트가 아니라 Django 뷰에 붙고
  (`@session.view`), 한 페이지·한 연결에 하나다. `authorize` 술어는 뷰(첫 바이트 전)와
  join(마운트 전) 두 곳에서 도는 **같은 함수**여야 한다 — 둘을 따로 두면 조용히 어긋난다.
  `_live_sessions`를 선언한 컴포넌트는 경계가 없는 페이지에서도 거절된다.
- **mount가 halt하거나 예외를 던지면 아무것도 렌더되지 않는다** (#58부터). 컴포넌트는 저장소에서도
  지워지므로 그 id로 오는 이벤트도 처리되지 않는다. 렌더를 보내는 새 경로를 만들 때
  `wire.mount_halted`를 건너뛰면 가드가 막으려던 HTML과 `data-state`가 그대로 나간다. 예외를
  "미완"으로 다루면 인가 조회가 실패하는 쪽이 거절보다 통과하기 쉬워진다.
- **인증 세대는 세션 키가 아니라 `login()`이 찍는 nonce(`_wireview_auth_gen`)다.** signed-cookie
  백엔드의 `session_key`는 서명 쿠키 문자열 전체라 세션에 뭘 쓰든 바뀐다. 그리고 그 백엔드는
  로그아웃을 서버에서 폐기하지 못한다 — 경계 뒤에 진짜 인가가 있으면 서버 저장형 백엔드를 쓴다.
- **`STATE_ACCEPT_LEGACY`와 live_session은 동시에 열 수 없다.** 옛 토큰에는 그 페이지에 경계가
  있었는지를 말해 줄 것이 없어서, 받아 주면 뷰에 붙인 정책이 통째로 빠진다(`wireview.W010`).
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
