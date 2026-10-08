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
| 릴리스 버전 | git 태그 `v<버전>`과 `pyproject.toml`의 version. 편집기 확장은 따로: 태그 `vscode-v<버전>`과 `editors/vscode/package.json`, 절차는 `docs/ROADMAP.md`의 "VS Code 확장 릴리스 절차" |
| 무엇이 공개 API인가, 폐기 절차 | `docs/COMPATIBILITY.md` |

## 저장소 지도

```
wireview/
├── __init__.py            공개 API의 전부. _EXPORTS 표로 지연 로딩한다. 하위 모듈은 모두 내부다
│                          (docs/COMPATIBILITY.md, tests/test_public_api.py가 문서의 import까지 지킨다. #98)
├── component.py           폐기 예정 re-export. import하면 WireviewDeprecationWarning, 2.0에서 제거
├── deprecation.py         WireviewDeprecationWarning, warn_deprecated(). 공개 API를 없애는 유일한 경로
├── py.typed               타입 검사기가 패키지의 주석을 읽게 한다. ci-build가 wheel에 있는지 본다
├── core/component.py      Component 베이스: 라이프사이클, 이벤트 디스패치, streams·uploads·async·flash·hooks 메서드.
│                          ComponentOptions가 `class Meta:`를 해석해 cls._meta에 둔다(키 단위 상속, #99)
├── core/handlers.py       클라이언트가 부를 수 있는 메서드의 판정 정본(is_client_callable). 디스패처·check·validate_call 감싸기가 함께 쓴다(#127)
├── core/meta.py           WireviewMeta (self.wire): push_to/replace_to, push_js, put_flash, push_title 등 클라이언트 명령
├── core/rendered.py       동적 마커 기반 diff 구조. LiveComponent 자리는 참조 dynamic {"c": id}.
│                          중첩 {% component %}의 라이브 출력은 <!--@wv(:id-->로 감싸고 파싱이 지운다(슬롯을 그리는 쪽이 다시 그릴 자리)
├── core/render_reads.py   초기화된 temporary assign만 읽은 동적 부분을 렌더 중에 찾는다. 그 부분은 이전 값 그대로(#111).
│                          다른 컴포넌트의 패스 안에서 그려지는 중첩 컴포넌트도 자기 것을 따로 찾는다(rendered.keep_stale).
│                          남는 부분이 그린 다른 컴포넌트·슬롯이 그 뒤 바뀌었으면 다시 그린다(자기 렌더가 부분별로 기록, template_engine._PartNode)
├── core/shared_render.py  Meta.shared_render(#176 2단계). 같은 브로드캐스트 메시지(message_id)를 처리하는 연결들이 같은 클래스·id·필드·언어·시간대의
│                          렌더를 한 번만 하고 Rendered를 함께 쓴다. data-state는 공유하지 않는다 — 렌더는 그 자리에 STATE_SLOT을 쓰고 연결마다 자기 토큰을 끼운다(Shared.with_state).
│                          범위 밖(LiveComponent·temporary_assigns·slots·live_sessions·모델·QuerySet 타입 필드·다른 컴포넌트를 그리거나 보는 사람을 읽는 템플릿)은 공유하지 않고 W019가 알린다.
│                          필드에 모델 인스턴스·QuerySet이 들어 있으면 키가 없다(같은 pk의 다른 속성). 토큰은 루프 밖에서 서명한다(렌더 트립, 받은 쪽들은 _Signer가 모아 db 트립 하나).
│                          VERIFY_SHARED_RENDER(DEBUG·wireview.testing)면 user·session·request 읽기가 오류이고, 받은 렌더를 다시 렌더해 비교한다
├── core/patches.py        Broadcast(#178). 스트림 삽입·삭제, push_event, JS를 발행하는 곳에서 한 번 렌더·직렬화하고 프레임을 id 자리에서 자른다([앞, 뒤]).
│                          프로세스(이벤트 루프)마다 transport.PatchHub의 채널 하나가 패치 그룹 wireview.patch.<토픽>(알림 그룹과 따로)에 들고, 받은 메시지를
│                          그 토픽을 듣는 세션마다 _take_patch로 넘긴다. 세션은 대상 클래스·토픽의 reachable 인스턴스마다 id만 끼워 연결별 큐(상한 QUEUE_LIMIT)에
│                          넣고 자기 태스크가 send_text로 쓴다 — 브라우저가 받는 바이트는 세션의 것과 같다.
│                          컴포넌트는 joined() 전에 패치를 잡아 두고 release_patches 메일에서 놓는다(reset 뒤에 써지게).
│                          joined() 밖의 stream() reset은 wire.patch_gate로 잡는다. 잡기는 토큰으로 센다: reset의 stream_op 메일이 자기 토큰(hold)을
│                          싣고 소켓에 쓰일 때 놓는다, join의 토큰은 release_patches 메일이 놓는다. 마지막 토큰이 놓일 때 쓴다. 메일이 채널로 나간 뒤
│                          처리 중인 메시지 없이 HOLD_SECONDS가 지나면 패치를 쓰지 않고 연결을 닫는다(1013, WireviewConsumer.dispatch → handling_message)
├── core/watched.py        Watched: 보는 사람을 읽으면 오류인 감시 객체. shared_render(VERIFY일 때)와 Broadcast 항목 렌더(언제나)가 함께 쓴다.
│                          Broadcast 항목의 this는 대상 클래스를 품어 {% on %}이 핸들러를 클래스에서 검사한다(stands_for)
├── core/template_reload.py 개발 서버에서 템플릿이 바뀌면(자동 리로더의 file_changed) 이 프로세스의 열린 연결마다 rejoin을 보낸다(#180).
│                          세션은 start()에서 자기 루프와 함께 등록하고(REJOIN_ON_TEMPLATE_CHANGE, 기본 DEBUG일 때만), 리로더 스레드는 call_soon_threadsafe로 넘긴다.
│                          세션은 rejoin을 바로 쓰지 않고 자기 채널로 메일(template_changed)을 보내 그 차례에 쓴다 — 처리 중인 핸들러·join의 답 뒤.
│                          페이지는 sync를 보내 synced가 올 때까지 기다렸다가 다시 join한다(static의 rejoins.mjs). 앞지르면 처리 중이던 이벤트가 되돌아갔다.
│                          refused는 여기서 풀지 않는다 — 페이지의 다시 join이 retry_join으로 푼다
├── core/connections.py   keep_connections(). 동기 렌더가 컴포넌트의 async 코드로 건너는 다리(async_to_sync)가 그 스레드의 DB 연결을 닫지 않게 붙잡는다(#190).
│                          wireview의 어떤 모듈도 import하지 않는다 — utils가 core를 import하는 도중에 meta가 이것을 읽는다
├── core/render_gate.py    RenderGate. 워커 스레드가 렌더하는 동안 그 컴포넌트의 start_async·assign_async 작업 단계를 렌더 뒤로 미룬다(#138).
│                          렌더가 async property를 오래 기다리는 동안 작업이 막혀 있으면 경고한다(교착 의심, #147)
├── core/session.py        SessionView. Django 세션의 읽기 전용 뷰. 소켓에서는 connect 때 한 번 읽는다
├── core/live_session.py   페이지 경계 정본. live_session() 선언과 레지스트리, @session.view,
│                          인증 세대 지문(auth_fingerprint), 로그아웃 무효화 발행 (GAP-009)
├── core/origin.py         WebSocket Origin 검사. 컨슈머가 accept 전에 ALLOWED_HOSTS와 대조한다(CSWSH, #96)
├── core/state.py          data-state 서명·복원 (v2 봉투: 클래스·경계·인증 세대 결합, 만료, 토큰 재사용)
├── core/signing.py        서명 키 정본. get_signer(salt) 하나로 모든 서명 지점이 SIGNING_KEY와 fallback을 공유
├── core/model_state.py    상태 안의 모델 인스턴스를 pk로 서명하고(어디에 있든) 필드 타입 표기를 따라 다시 불러온다 (#113)
├── core/transport.py      Outbound·Broker 인터페이스와 Channels 구현. 채널 레이어를 건드리는 유일한 곳
├── template_engine.py     템플릿 VariableNode에 diff 마커 자동 주입. 마커 변수는 값을 직접 resolve하고 평범한 int·str은
│                          Django의 VariableNode와 같은 출력을 지름길로 찍는다(render_value, #176). 바꾸면 tests/test_template_values.py가 Django 출력과 맞춘다
├── consumer.py            WireviewConsumer: Channels WebSocket 어댑터(/__wireview__). 소켓 수락·거절과 세션 시작·종료, JSON 전달만
├── session.py             WireviewSession: 세션 로직 전부(inbound command_*, 메일 component_*, 렌더). Outbound로만 내보낸다.
│                          channels를 import하지 않는다(tests/test_session_extraction.py). send_render가 자식 LiveComponent의
│                          joined/update(update_many)/leaving과 렌더를 함께 처리 (GAP-027, #60)
├── views.py               UploadView (청크 업로드 HTTP 엔드포인트). 무상태 — 서명 토큰만으로 판단하고
│                          토큰에서 계산한 경로에 쓴다. 어느 워커에 닿아도 된다 (#83)
├── urls.py                websocket_urlpatterns, urlpatterns
├── repository.py          ComponentRepository: 연결당 컴포넌트 인스턴스 관리. LiveComponent의 수명주기 배치(take_lifecycle).
│                          패스 안에서 그려진 {% component %}가 그리는 LiveComponent도 그린 쪽의 배치에 담는다(end_inline_pass).
│                          슬롯 주인이 자기 렌더로 fill의 {% component %}를 다시 그린 패스는 그 주인의 배치에 담는다
├── live_component.py      LiveComponent (부모 연결을 공유하는 중첩 상태 컴포넌트)
├── function_components.py  @function_component (상태 없는 템플릿 함수). 공개 이름과 겹치지 않게 복수형이다(#98).
│                          그 템플릿의 {% component %}는 페이지 저장소에, 함수를 그린 컴포넌트가 그린 것으로 그려진다(this는 넘기지 않는다).
│                          HTTP 렌더에서 저장소가 아직 없으면 그 컴포넌트가 함수를 그린 컨텍스트에 만든다(templatetags의 _page_repository)
├── slots.py               슬롯 시스템 ({% fill %}, {% render_slot %})
├── async_result.py        AsyncResult / AsyncState
├── auto_broadcast.py      Django signals → 컴포넌트 mutation() 알림. senders에 적은 모델만, 비우면 아무것도 연결하지 않는다.
│                          senders가 매핑이면 모델마다 적은 필드만 보낸다(#144). 받는 쪽 serializer.decode는 페이로드에 없는
│                          필드를 deferred로 둔다(#153) — 기본값으로 채우면 save가 행을 덮어쓴다.
│                          connect()가 ready()에서 한 번 연결한다(테스트는 다른 AutoBroadcast로 다시 부른다)
├── event_transpiler.py    {% on %} 수정자 파싱 (.prevent, .debounce.300 ...)
├── js.py                  JS() 명령 빌더
├── schemas.py, serializer.py  Pydantic 스키마, 모델 직렬화
├── settings.py            WIREVIEW 설정 기본값
├── checks.py              Django system checks (조용한 실패를 manage.py check로. wireview.W001~W019)
├── telemetry.py           옵트인 계측 시그널 (event_handled, component_rendered, diff_computed, broadcast_published 구간과
│                          connection_opened·connection_closed·join_rejected(닫힌 사유 집합)·publish_failed 이벤트, #124)
├── testing.py             mount(), MountedComponent, ComponentTestCase. mount한 컴포넌트는 같은 프로세스의 Broadcast를 받는다(core/patches.listen).
│                          render()는 실제 저장소의 HTTP 렌더 모드로 자식까지 그린다(자식 수명주기는 컨슈머 몫, #115).
│                          내비게이션 단언·follow_redirect·follow_push·스트림 검사
├── utils.py, log.py       db 헬퍼, 로깅
├── debug/sync_detector.py sync/async 전환 중첩 감지 (DEBUG_SYNC_TRANSITIONS)
├── debug/render_queries.py 렌더 부분별 SQL 귀속(DEBUG_RENDER_QUERIES, None = DEBUG, #182). 굵은 경계(렌더·async property·핸들러·mount·joined·작업·서명)는
│                          ContextVar 스코프이고, 템플릿 파일:줄과 sync property 이름은 쿼리 순간 스택에서 읽는다(노드 자신의 origin·token).
│                          래퍼는 execute_wrappers 맨 아래에 한 번 두고 남긴다. 요청을 모아 다른 태스크가 처리하는 길은 capture()·restored()로
│                          항목마다 요청한 쪽의 상태를 되살린다(_Signer). 태스크를 만드는 새 길은 시작에서 detach()하고 scope("task")를 연다.
│                          MountedComponent.queries()가 테스트 단언. 계약 표는 tests/test_render_queries.py
├── debug/render_queries_file.py 그 귀속을 편집기가 읽을 JSON 줄로 쓴다(DEBUG_RENDER_QUERIES_DIR, #188). 가장 바깥 일 하나가 한 줄이고, 안에서 끝난
│                          렌더마다 클래스 단위 스냅샷(0건 포함)이 든다. 템플릿 줄은 실행 중인 Template.source의 지문과 함께(filesystem·app_directories
│                          로더만). 프로세스마다 덧붙이기만 하는 세그먼트 파일이고, 끝까지 쓰지 못한 줄은 되돌리고 끈다. DEBUG가 아니거나
│                          WIREVIEW_RENDER_QUERIES_DIR=off(루트 conftest.py가 둔다)이거나 wireview.testing을 import했으면 쓰지 않는다.
│                          형식의 정본은 docs/features/render-queries.md "편집기로 보내기", 계약은 tests/test_render_queries_file.py
├── features/              streams.py, presence.py (PresenceMixin), uploads.py (UploadRegistry·토큰 v2),
│                          hooks.py (앱의 static/<app_label>/hooks/*.js 수집. 페이지가 아니라 프로젝트 단위. 템플릿 디렉터리는 TEMPLATES가 아니라 엔진의 로더에게 묻는다),
│                          upload_store.py (청크 경로 계산·append·취소 마커·sweep. 워커들이 공유하는 유일한 상태),
│                          toasts.py (toast()·atoast()와 그것을 받는 컴포넌트. {% wireview_toasts %}가 심는다, #116)
├── apps.py                ready()가 시그널 수신자·체크·toasts 컴포넌트를 연결하고 live·live_sessions 모듈을 autodiscover한다
├── templatetags/wireview.py  템플릿 태그 전체 (아래 표)
├── management/commands/   wireview_stubs (.pyi 생성), wireview_lsp (편집기 메타데이터 JSON. 형식은 자기 version 필드로 따로 매기고
│                          editors/vscode 가 읽는다: 컴포넌트·함수 컴포넌트·훅·템플릿 디렉터리·엔진의 태그와 필터. docs/features/editor-support.md),
│                          wireview_agent_setup (앱 개발자용 스킬을 프로젝트 .claude/skills/ 에 설치),
│                          wireview_upload_gc (토큰 만료보다 오래된 청크 파일 정리),
│                          wireview_check_templates (확장의 진단을 node로 돌리는 CI 관문. error면 1, --strict면 warning도, 못 돌면 2.
│                          wheel의 wireview/template_diagnostics/ 를, 체크아웃에서는 editors/vscode 를 부른다, #179)
├── project_template/       startproject --template 용 스타터(시작하기 튜토리얼의 프로젝트). *.py-tpl 과 html 뿐, 모듈이 아니다.
│                          tests/test_project_template.py 가 스크래치에 만들어 check·첫 화면을 보고, ci-build 가 wheel 에 있는지 본다(#131).
│                          tests/test_starter_e2e.py 는 그 프로젝트를 자기 runserver(daphne)와 uvicorn으로 띄워 브라우저로 입력해 본다(#151).
│                          uvicorn은 정적 파일을 서빙하지 않으므로 스타터 asgi.py가 DEBUG일 때 ASGIStaticFilesHandler로 감싼다
├── templates/wireview_header.html  {% wireview_header %}가 렌더. wireview.min.js를 로드
├── templates/wireview/toasts.html  {% wireview_toasts %}가 심는 토스트 수신 컴포넌트의 템플릿
└── static/wireview/       wireview.js (소스), rendered.mjs (diff 적용·HTML 복원 순수 함수),
                           streams.mjs (스트림 DOM 판단 순수 함수. pinContainerIds: morph 전에 렌더의 컨테이너에 화면 컨테이너의 id를 붙여 자리 짝짓기로 지워지지 않게 한다),
                           targets.mjs (TargetQueue: 요소가 아직 없는 stream_op·exec_js·push_event는 다음 프레임까지 기다린다. 순서는 컴포넌트별), reload.mjs (reload 쿨다운 판단),
                           live-session.mjs (경계 넘음 판단 순수 함수), ready.mjs (defer 스크립트가 다 돌았는가),
                           events.mjs (wire-on-* 바인딩의 수정자 해석 순수 함수. 렌더 적용 중의 포커스·change 이벤트는 사용자 것이 아니다: isRenderEcho),
                           values.mjs (morph가 사용자가 고친 입력값을 덮어써도 되는가. IME가 조합 중인 칸은 언제나 지킨다),
                           loading.mjs (로딩 표시를 어느 응답이 끝내는가. ref로 짝짓는다),
                           navigation.mjs (boost 이동이 끝났음을 누구에게 알리는가: 훅의 navigated()와 wireview:navigated.
                           push_to·replace_to·popstate가 patch인가: 같은 경로, history.state의 페이지 표식(wireviewPage), #169.
                           같은 URL인가(기록 항목을 만들지 않는다), 조각만 다른가(브라우저에 맡긴다), #170.
                           가져오기가 무엇으로 끝났나(fetchOutcome: 페이지·다른 출처 리다이렉트·중단·무응답 — boost 폼은 no-cors로 보내 가른다),
                           bfcache가 되살린 문서가 주소창에 다시 도착해야 하나(arrivesOnRestore).
                           wireview:before-navigate의 detail과 이동 종류(kind). 취소한 뒤로·앞으로 가기는 Navigation API가
                           알려 준 떠난 항목으로 traverseTo로 돌아가고, 그 사이의 popstate를 TraversalUndo가 가른다, #154),
                           reconnect.mjs (재연결 백오프를 헤더 메타에서 읽는다. WIREVIEW RECONNECT_*),
                           uploads.mjs (업로드 manager의 수명. 인스턴스가 끝나면 폐기하고, 렌더의 instances가 알린 인스턴스 번호와
                           같은 config만 받는다. 끝난 인스턴스의 upload_op는 세션이 먼저 버린다.
                           join·join 실패·렌더가 알린 인스턴스에서 페이지가 할 일도 여기 있다: joining·joinFailed·named, #142),
                           joins.mjs (같은 id로 다시 보낸 join의 응답을 기다리는 동안 어느 render·error·remove·reload·joined가
                           지금 join의 것인가. LiveComponent의 render는 루트의 join으로 가른다(#146). join의 ref로 짝짓는다. vsn 6 이상의 서버에만 싣는다. settledEvent가 join의 ref를
                           이벤트 정산(로딩·valueGuard)에서 뺀다. rejoinable은 rejoin(#180)이 다시 join할 컴포넌트를 고른다),
                           rejoins.mjs (rejoin이 sync의 답을 기다리는 동안, 그리고 다시 join하는 동안 보낼 메시지를 붙잡는다. 다시 join만 through로 지나간다.
                           소켓에 쓰는 곳은 _send 하나다 — 다른 길은 그 붙잡기를 지나친다(tests/js/rejoins.test.mjs가 본다), #180),
                           wireview-boost.js, types.d.ts
                           wireview.min.js는 빌드 산출물이며 gitignore

tests/
├── test_*.py              라이브러리 단위·통합 테스트. WebSocket 없이 mount() 사용
│                          test_async_safety.py 는 DJANGO_ALLOW_ASYNC_UNSAFE 가 진입점에 돌아오지 않는지 본다.
│                          허용·거절은 저장소 루트의 conftest.py 가 한다(#120)
│                          test_e2e_harness.py 는 E2E 하네스 자체의 계약을 지킨다. test_e2e_script.py 는 tests/e2e.sh 가 넘긴 경로만 돌리는지,
│                          redis 레이어의 서버를 쓰거나 띄우고 실패·중단에도 정리하는지 본다(#171. 실제 redis-server가 없으면 CI에서 실패)
│                          테스트 모듈을 tests.test_x 로 import하지 않는다 — pytest가 이미 test_x 로 읽어 두 번 실행되고
│                          컴포넌트가 두 번 등록된다. 공용 헬퍼는 testproj/ 에(outbound.py 의 RecordingOutbound). test_suite_imports.py 가 본다
│                          test_live_session_contract.py 는 회귀가 아니라 계약을 진술한다 —
│                          컴포넌트가 생기는 경로 11개 × 거절 사유 5종을 parametrize로 돌린다.
│                          경로를 새로 만들면 행을 추가한다
│                          test_feature_gap.py 는 docs/FEATURE-GAP.md 의 ✅ 행마다 근거 칸의 테스트가
│                          실제로 있는지, 개요의 숫자가 표를 센 값인지 본다(#110)
│                          test_doc_links.py 는 추적되는 모든 .md 의 상대 링크가 파일에, 앵커가 그 파일의 제목에 닿는지 본다.
│                          제목을 고치면 그 제목을 가리키던 링크가 이 테스트에서 실패한다
│                          test_deployment_examples.py 는 docs/DEPLOYMENT.md 의 수신자·readiness 코드 블록을
│                          꺼내 실제로 돌린다. 문서의 예시를 고치면 이 테스트가 본다
│                          test_doc_examples.py 는 사용자 문서(README·features·tutorials·skills·examples)의 코드가
│                          되풀이된 실수를 하지 않는지 본다: 블록은 파싱되고, JS() 체인은 실제 시그니처에 묶이고,
│                          함수 컴포넌트는 f-string·`+`·`.format()`·`%`로 마크업을 만들지 않고, mount() 시그니처는 실제와 같다
├── js/*.test.mjs          클라이언트 순수 모듈 테스트 (node --test)
│                          js/roundtrip.mjs 는 테스트가 아니라 test_diff_roundtrip.py 의 드라이버다 —
│                          서버 diff 를 실제 rendered.mjs 로 적용해 매 단계 HTML 이 렌더와 같은지 본다
└── testproj/              Django 테스트 프로젝트(설정·URLconf). 채널 레이어는 WIREVIEW_TEST_LAYER가 고르고
                           settings_nats.py·settings_redis.py가 이를 고정하는 진입점이다.
                           e2e_server.py 가 E2E용 라이브 ASGI 서버의 정본이다 —
                           브라우저가 필요한 모든 스위트가 이것을 쓴다 (복제하면 test_e2e_harness.py가 실패한다).
                           server_errors() 가 블록 동안 서버가 남긴 ERROR 를 돌려준다: 핸들러가 터지면
                           소켓이 죽고 페이지가 멈출 뿐이라 브라우저 쪽에서는 느린 것과 구별되지 않는다.
                           e2e_browser.py 가 브라우저 대기의 정본이다 (open_live·expect_text·expect_count).
                           INBOX_SHIM 은 서버가 보낸 메시지를 페이지에 넘기는 순서를 테스트가 정하게 한다.
                           warning_guard.py 는 ASGI 핸들러가 동기 이터레이터를 서빙했다는 경고가 기록되면 실행을 실패시키는 플러그인이다.
                           page.wait_for_selector 를 다른 곳에 쓰면 test_e2e_harness.py 의 가드가 실패한다
                           waiting.py 의 eventually() 가 async 테스트에서 백그라운드 작업의 결과를 기다리는 정본이다 —
                           고정 sleep 으로 기다리지 않는다. 타이머(debounce·throttle·만료)를 재는 테스트만 sleep 을 둔다(#143).
                           기다리는 일을 하는 태스크가 있으면 task= 로 넘긴다 — 그 태스크가 던지면 그 예외가 바로 올라온다(#148).
                           time_limit.py 는 테스트 하나(setup·teardown 포함)가 test_time_limit 초를 넘기면 모든 스레드의
                           스택을 찍고 실행을 끝내는 플러그인이다. 실패한 테스트의 teardown 멈춤까지 잡는다(#148).
                           pdb에 들어가면 멈춘다. 끝낸 뒤의 teardown은 돌지 않으므로 자식 프로세스를 띄우는 픽스처는 own()에 넘긴다.
                           server_process.py 는 별도 프로세스로 띄운 서버(스타터의 runserver, 업로드 E2E의 uvicorn 워커)의
                           준비 판정 정본이다. 포트에 연결되는가가 아니라 고유 토큰 요청이 그 서버의 접근 로그에 찍혔는가로 보고,
                           bind 실패 로그나 프로세스 종료는 로그와 함께 바로 실패시킨다(#152). testproj를 uvicorn 프로세스로
                           띄우는 것도 여기다(launch_uvicorn·start_uvicorn: 업로드 E2E의 워커, 재연결 E2E의 재시작·다른 워커).
                           row_guard.py 는 테스트가 끝난 뒤(롤백·flush 후) 커밋된 행이 늘어 있으면 그 테스트를 teardown 오류로
                           실패시키는 플러그인이다. async 테스트의 ORM 쓰기는 워커 스레드 연결에서 커밋되어 롤백되지 않는다 —
                           그런 테스트는 django_db(transaction=True)로 표시한다(#133).
                           bookmarks/ 는 예제가 아니라 wireview 스킬 검증의 기준선이고,
                           uploadprobe/ 는 워커 둘짜리 업로드 E2E(test_multiworker_uploads.py)의 픽스처,
                           livesession/ 은 경계 넘는 이동 E2E(test_live_session_e2e.py)의 픽스처다(staff-func/ 는 경계 안 패널을 첫 태그인 함수 컴포넌트의 템플릿이 그리는 페이지),
                           listprobe/ 는 항목 재배열 diff 를 옛 형태와 비교하는 E2E(test_comprehension_moves_e2e.py)의 픽스처,
                           cspprobe/ 는 인라인 허용 없는 CSP 아래 모든 바인딩 모양과 브라우저 업로드를 도는 E2E(test_csp_e2e.py)의 픽스처,
                           valueprobe/ 는 렌더가 입력 중인 값을 지우지 않는지, 렌더가 지운 포커스 칸의 blur·change가 나가지 않고 폼 피드백도 건드림으로 치지 않는지 보는 E2E(test_input_values_e2e.py)의 픽스처,
                           errorprobe/ 는 예외를 던지는 핸들러와 join을 보는 E2E(test_errors_e2e.py)의 픽스처(holder 는 join이 실패한 컴포넌트와, 역시 join이 실패하는 held-nest 안의 LiveComponent held-child 를 렌더마다 다시 그린다, late/ 는 같은 id로 다시 join되는 페이지,
                           그 안의 ErrorNest 는 LiveComponent 하나를 들고 ?visit=swap 은 그 id를 루트로 바꾼다. remove가 지운 포커스 칸의 blur도 여기서 본다.
                           slot/ 은 join이 실패하는 컴포넌트 둘 — 슬롯에 호스트의 LiveComponent를 받은 것과 자기 LiveComponent를 든 것 — 과 그 훅들이 떠나는 페이지.
                           그 LiveComponent는 업로드를 받는다(슬롯의 것은 실패 뒤에도 끝내야 한다).
                           핸들러가 들은 것은 HEARD 에 남아, 서버가 거절했는지를 테스트가 본다),
                           offlineprobe/ 는 연결이 끊긴 페이지의 바인딩·큐와 재연결 뒤의 훅·폼 복구를 보는 E2E(test_offline_e2e.py)의 픽스처,
                           hookprobe/ 는 훅의 소유(중첩 컴포넌트)·이동·떠날 때의 destroyed·pushEvent 응답 짝, 렌더가 새로 그린 LiveComponent·다시 그린 컴포넌트의 훅과 그 joined()의 push_event, 다시 그린 컴포넌트의 join과 viewport, 부모의 패치가 중첩 컴포넌트 안에 그린 훅·바인딩, 같은 렌더가 그린 훅에 가는 push_event, 다른 컴포넌트의 패치가 먼저 돌아도 새 LiveComponent(와 그것의 첫 작업이 그리는 bud)가 그려지는지를 보는 E2E(test_hooks_e2e.py)의 픽스처,
                           tempprobe/ 는 초기화된 temporary assign이 다음 렌더에 화면에 남는지 보는 E2E(test_temporary_assigns_e2e.py)의 픽스처(?nest=1 은 호스트의 패스, 다른 컴포넌트의 슬롯 안, 호스트 값을 그리는 fill을 받은 component_block으로 그려진 probe, ?rows=1 은 행을 중첩 컴포넌트가 그리는 목록, ?live=1 은 호스트의 패스가 그리는 블록에 목록과 함께 든 LiveComponent, ?notes=1 은 서명 상태 밖의 temporary assign이 바뀌는 중첩 컴포넌트를 목록과 함께 든 블록, ?joined=1 은 그 temporary assign을 joined()에서 불러오는 것, ?func=1 은 그 자리를 함수 컴포넌트의 템플릿이 그리고 그것의 joined()가 서명 필드를 바꾸는 것),
                           slotprobe/ 는 슬롯에 넣은 LiveComponent·일반 컴포넌트가 슬롯을 그리는 컴포넌트(component_block·live_component_block)의 자기 렌더, 슬롯을 숨겼다 보이기, 예외 뒤·같은 페이지 boost의 다시 join 뒤에도 남는지 보는 E2E(test_slot_live_components_e2e.py)의 픽스처(?other=1 은 같은 id의 frame을 fill 없이 두는 다른 페이지, ?otherhost=1 은 다른 호스트가 그 frame을 fill 없이 그리는 페이지),
                           stickyprobe/ 는 sticky 컴포넌트가 boost 이동을 건너 이어지는지(id 없는 것 포함), 그 훅과 페이지가
                           이동마다 한 번 navigated 알림을 받는지, late/ 의 sticky LateSticky가 아직 그리지 않은 id를 late-root/ 가
                           자기 루트로 그릴 때 그 루트가 join되는지, shelf-a/·shelf-b/ 의 sticky StickyShelf 안에 그린 ShelfLabel이
                           이동의 params를 듣는지(navigated의 carried) 보는 E2E(test_sticky_e2e.py)의 픽스처,
                           nestprobe/ 는 그린 쪽이 넘긴 prop으로 중첩 컴포넌트가 숨기거나 처음 보이거나 새 props를 넘기는 LiveComponent의
                           leaving()·joined()·update()가 그린 쪽의 render에 실리는지 보는 E2E(test_inline_pass_lifecycle_e2e.py)의 픽스처(?hidden=1 은 숨긴 채 시작,
                           visit-shown 링크는 같은 id의 둘을 새로 받는 boost 이동 — 그린 쪽의 첫 렌더는 중첩 컴포넌트의 join에 맡긴다),
                           deadprobe/ 는 JavaScript를 끈 브라우저가 첫 렌더를 읽고 폼으로 뷰에 가는지 보는 E2E(test_dead_view_e2e.py)의 픽스처,
                           reconnectprobe/ 는 재연결 뒤 상태(같은 서버·재시작한 프로세스·다른 워커)를 보는 E2E(test_reconnect_state_e2e.py)의 픽스처,
                           imeprobe/ 는 한글 조합 중에 오는 렌더(자기 debounce·브로드캐스트·서버가 바꾼 칸 값)를 CDP IME로 보는 E2E(test_ime_e2e.py)의 픽스처,
                           historyprobe/ 는 push_to·replace_to의 patch와 이동, boost 이동·새로고침 뒤 뒤로·앞으로 가기, follow_push()와 브라우저의 일치,
                           이동의 params를 누가 듣는가(HEARD, 모든 페이지의 sticky HistoryDock과 box 페이지에만 있는 HistoryTray. 첫 HTTP 렌더가 들은 것은 HTTP_HEARD, #177), 같은 URL로의 push,
                           조각 링크, 네트워크 오류로 실패한 가져오기(boost 폼 포함, post/ 는 받은 POST를 센다. ?away=1 은 다른 출처로 리다이렉트한다), 중지된(AbortError) 이동, method="put" 폼을 보는 E2E(test_history_e2e.py)와 이동마다 wireview:before-navigate·그 취소를 보는 E2E(test_before_navigate_e2e.py, #154)의 픽스처(members/ 는 ls-members 경계 안, bar-a/·bar-b/ 는 sticky가 아닌 HistoryBar를 같은 id로 그리는 두 페이지 — 뒤로 가기의 캐시 사본에서 떠나는 페이지의 것이 params를 듣지 않는지 본다. guarded/ 는 자기 필드와 비교해 일찍 돌아오는 params_changed를 가진 두 컴포넌트 — join이 첫 응답의 마운트 상태에서 다시 듣는지 본다, #177),
                           lossprobe/ 는 capacity를 넘는 브로드캐스트가 레이어마다 어떻게 버려지는지 보는 E2E(test_broadcast_loss_e2e.py)의 픽스처,
                           shareprobe/ 는 shared_render를 선언한 보드와 보는 사람을 부르는 인사를 브라우저 컨텍스트 여럿(사용자 하나씩)이 보는 E2E(test_shared_render_e2e.py)의 픽스처,
                           broadcastprobe/ 는 Broadcast로 항목·훅 이벤트·JS를 받는 피드를 브라우저 컨텍스트 둘이 보는 E2E(test_broadcast_e2e.py)의 픽스처(같은 이름의 스트림을 그리는 다른 클래스,
                           ?failing=1 은 join이 실패한 대상, 끊겼다 다시 붙은 페이지),
                           reloadprobe/ 는 개발 서버에서 템플릿을 저장하면(file_changed) 새로고침 없이 새 템플릿이 보이고, 깨진 템플릿으로 막힌 컴포넌트가 고친 뒤 상태 그대로 돌아오는지 보는 E2E(test_template_reload_e2e.py)의 픽스처(shadow.py 가 TEMPLATES DIRS 앞에 테스트의 디렉터리를 두고 그 사본을 고친다. slow_bump 은 저장하는 순간 처리 중인 핸들러, away/ 는 rejoin이 기다리는 동안의 boost 이동 대상),
                           jsprobe/ 는 JS() 명령 전부와 로딩 클래스를 브라우저에서 도는 E2E(test_js_commands_e2e.py)의 픽스처,
                           formprobe/ 는 Django 폼 검증·wire-feedback-for·debounce·throttle을 보는 E2E(test_forms_e2e.py)의 픽스처,
                           fileprobe/ 는 업로드의 모든 입구(입력·드롭 존·미리보기·external)와 숨겼다 다시 보인 LiveComponent의 업로드, 렌더가 새로 그린 일반 컴포넌트와 그 안의 LiveComponent(같은 업로드 이름)의 join·업로드, 재연결 뒤 숨겼다 다시 보인 그 LiveComponent가 새 상태로 시작하는지, late/ 에서 joined()의 작업이 끝나야 그리는 일반 컴포넌트 안의 LiveComponent가 재연결 뒤 제 상태로 돌아오는지를 보는 E2E(test_uploads_e2e.py)의 픽스처,
                           inheritprobe/ 는 다중 테이블 상속 모델들이다(3단, pk를 따로 둔 자식, 키 타입이 다른 부모). mutation()이 받은 자식 인스턴스가 부모 컬럼을 덮지 않고 제 부모 행에 붙어 있는지 본다(test_mutation_instance.py, 마이그레이션 없음),
                           streamprobe/ 는 스트림의 dom_id·limit·wire-viewport-*(재연결 뒤 포함)와 같은 이름의 스트림을 쓰는 두 컴포넌트(delete 포함), 스트림 연산이 지운 포커스 항목의 blur를 보는 E2E(test_streams_e2e.py)의 픽스처다(?away=1 은 떠났다 돌아오는 컴포넌트 없는 페이지, ?pair=1 은 둘째 probe, ?nest=1 은 probe 목록 앞의 LiveComponent. seed 버튼은 joined()에서 push_js와 스트림을 보내고 자기 wire-viewport-bottom을 가진 LiveComponent를 새로 그린다(?seeded=1 은 처음부터, ?fill=1 은 그 첫 페이지가 창을 채운다, reseed 링크는 boost 이동으로 probe를 같은 id로 다시 join한다, strip 은 부모와 그것을 한 패치로 바꾼다), reveal 은 목록들 앞에 빈 스트림 컨테이너를 드러낸다)

examples/                  예제 앱 11개. 각 디렉터리 = 개념 하나 + tests.py 하나 + README 하나.
                           testproj 위에서 돌고 make test가 함께 실행한다(pytest tests examples).
                           E2E는 todo/tests.py, livecomp/tests.py, hooks/tests.py, notifications/tests.py, search/tests.py. 인덱스는 examples/README.md
                           hooks/ 는 wire-hook 을 쓰는 유일한 예제다. 훅 경로의 E2E 검증은 그것과
                           testproj의 hookprobe/·stickyprobe/·offlineprobe/ 가 나눠 진다

docs/                      features/ 기능 레퍼런스, tutorials/ 15편, FEATURE-GAP.md, ARCHITECTURE.md,
                           ROADMAP.md, DEPLOYMENT.md, PERFORMANCE.md, design/ 설계 메모(README.md 인덱스), implementation/ 구현 노트
                           (implementation/wire-protocol.md 가 메시지 형태의 정본. 표의 이름은 tests/test_wire_protocol_doc.py 가 코드와 맞춘다)
                           implementation/docs-site-bundle.md 가 문서 사이트 묶음이 itda.work(website 저장소)에 약속하는 것의 정본이다 — 바꾸려면 website에 먼저 알린다(#166)
                           site.toml 이 문서 사이트(itda.work/wireview/)의 목차·주소·튜토리얼 학습 순서의 정본, redirects.toml 이 옮긴 주소.
                           scripts/docs_site/nav.py 가 읽고, tests/test_doc_site.py 가 분류를, tests/test_tutorials.py 가 튜토리얼 README·nav 줄을 이것과 맞춘다(#158).
                           site-urls.txt 가 공개 URL 목록이다 — 사이트 빌드가 이것과 비교해 사라진 URL을 실패시킨다(#159)
scripts/docs_site/         문서 사이트 빌드(make docs-site·make docs-site-bundle·make docs-serve, #159). nav.py 가 site.toml·redirects.toml 해석과 제목 앵커(slug)의
                           유일한 정본이고 표준 라이브러리만 쓴다 — 문서 가드 테스트도 이것을 import한다. render.py 는 Markdown 렌더와 링크
                           재작성(사이트 페이지는 사이트 경로로, 그 밖의 저장소 파일은 태그 고정 GitHub로), build.py 는 산출물·관문,
                           serve.py 는 폴링 재빌드 개발 서버, bundle.py 는 릴리스 자산 docs-site-v<버전>.tar.gz(결정론적, 항목 mtime은 커밋 시각, dist/ 밖 build/site-dist/, #160·#166).
                           문서가 보여 주는 저장소 이미지는 render.Linker 가 assets/<이름>.<해시>.<확장자>로 묶음에 싣는다(외부 출처는 Pretendard뿐).
                           llms.txt(안내 산문은 templates/llms.txt)와 스킬(skills/wireview/)의 Markdown 게시본도 build.py 가 만든다(#164).
                           templates/·assets/ 가 itda.work 레이아웃의 재현이다(원본과 커밋은 site.css 머리 주석).
                           렌더 의존성은 dependency-group docs(기본 그룹)에만 있다. 산출물은 `build/docs-site/`(gitignore)
editors/vscode/            VS Code 확장(#156). wheel·sdist에 싣지 않고 버전도 따로다 — 단 src/core/*.ts 와 scripts/diagnose.ts 는
                           hatch_build.py 가 wheel의 wireview/template_diagnostics/ 로 싣는다(wireview_check_templates, #179).
                           그래서 그 둘은 node 내장 모듈 밖을 import하지 않는다. src/core/ 는 vscode를 import하지 않는 순수 모듈
                           (node --test가 .ts를 그대로 돈다 — import는 .ts까지, enum 금지), src/*.ts 는 등록과 위치 변환뿐인 어댑터.
                           진단의 원칙: Django·wireview가 렌더할 때 낼 오류만, 확실하지 않으면 말하지 않는다.
                           tests/test_vscode_extension.py 가 이 저장소의 모든 템플릿에 진단 0건인지, 확장의 표(태그 스니펫·wire-* 속성·
                           메타데이터 버전·들여쓰기 규칙의 블록 태그)가 라이브러리와 같은지 본다(node만 필요). 확장 자체는 make ext-test·ext-test-host 등(make help).
                           어댑터의 수명(폴더 폐기·메타데이터 소스 전환·제한 모드)은 test/folders.test.ts 가 VS Code API를 test/stub/ 로 바꿔
                           실제 FolderProject와 자식 프로세스로 본다. 낡은 실행의 결과를 버리는 판단은 core/runner.ts 의 Generations 하나다.
                           실행마다 출력 파일이 따로다 — 멈춘 프로세스가 늦게 쓴 파일은 다음 실행이 지운다. 자기 실행의 것이 아닌 파일은 실행 timeout보다 오래된 것만.
                           신뢰하지 않은 워크스페이스에서는 프로세스를 띄우지도 메타데이터를 읽지도 않는다(folders.ts 의 run·load).
                           렌더 부분별 SQL의 inlay hint(#188): core/queries.ts 가 판단(세그먼트 이어 읽기·클래스별 최신 스냅샷·줄 확신 규칙),
                           queries.ts 가 FolderProject의 세대에 묶인 감시, queryFiles.ts 가 디스크. 서버가 쓴 실제 파일은 test/queries-driver.ts 로
                           tests/test_vscode_extension.py 가 읽어 본다
                           CI의 vscode-extension-host 잡(VS Code 다운로드)만 릴리스 게이트 밖이다
                           게시는 태그 vscode-v<버전>의 .github/workflows/vscode-release.yml이 Marketplace·Open VSX(itda.django-wireview)에 한다(#163).
                           아이콘은 images/icon.png, 원본은 images/icon.svg
scripts/act_ci.py          make ci-local. ci.yml을 act로 잡·매트릭스 칸 하나씩 돌린다. 러너 이미지는 .github/act/Dockerfile, act 옵션은 .actrc.
                           GitHub 러너와 다른 점(좀비를 거두지 않는 pid 1 → --init, 칸끼리 같은 데몬, colima의 소켓)은 그 머리 주석에.
                           tests/test_act_ci.py 가 잡·칸을 ci.yml에서 빠짐없이 읽는지, .actrc가 그 이미지를 쓰는지 본다
bench/                     성능 벤치마크 (make bench, make bench-compare BASE=<ref>). windows/ 는 Parallels 게스트 실측 레인. 설명은 bench/README.md
                           compare_fastapi/ 는 같은 화면을 wireview와 FastAPI(React·손 JS)로 만든 비교 벤치(make bench-fastapi, #174).
                           chart.py 가 RESULT 하나에서 docs/images/bench-fastapi-*.svg 와 PERFORMANCE.md 의 표를 만들고,
                           tests/test_bench_fastapi.py 가 README "숫자"·PERFORMANCE.md 의 숫자·차트가 그 결과와 같은지 본다
typings/                   channels 타입 스텁 (pyright용)
skills/wireview/           앱 개발자용 스킬의 정본. 휠에 wireview/agent_skills/ 로 실린다(hatch_build.py가 링크를 태그로 고정).
                           .claude/skills/wireview 는 이것을 가리키는 심링크(dogfooding)
AGENTS.md                  .claude/skills/ 를 안 읽는 에이전트(Codex 등)를 위한 포인터
hatch_build.py             빌드 훅. PyPI 페이지(README)·프로젝트 URL·휠에 싣는 스킬의 main 링크를 태그 v<버전>으로 바꾼다.
                           템플릿 진단(editors/vscode 의 core·diagnose.ts)을 package.json(type: module)과 함께 휠에 싣는다
.claude/settings.json      권한 허용 목록과 ruff format 훅
```

### 템플릿 태그 (`{% load wireview %}`)

| 태그 | 용도 |
|------|------|
| `wireview_header` | JS 로드와 boost 메타 |
| `wireview_toasts` | `toast()`·`atoast()`를 받아 플래시로 띄우는 보이지 않는 컴포넌트. 레이아웃에 한 번 |
| `component`, `component_block` + `fill` + `render_slot` | 컴포넌트 렌더링, 슬롯 |
| `live_component`, `live_component_block` + `fill`, `live_tag_header` | LiveComponent, 슬롯 전달 |
| `func`, `func_block` | Function Component |
| `tag_header` | 컴포넌트 루트 엘리먼트 속성 |
| `on` | 이벤트 바인딩. `{% on "click.prevent" "handler" arg=1 %}` |
| `cond`, `class` (태그), `str`, `concat` (필터) | 조건, 클래스, 문자열 헬퍼 |
| `upload_input`, `upload_drop_zone`, `upload_button`, `upload_preview` | 파일 업로드 |

### 클라이언트 DOM 속성

`wire-on-<이벤트>[.<수정자>…]`(`{% on %}`의 출력, 값은 JSON), `wire-hook`, `wire-update="ignore"`, `wire-boost`(폼), `wire-stream`, `wire-viewport-top/bottom`, `wire-disabled-with`, `wire-join-failed`(서버가 그린다, join이 실패한 컴포넌트), `wire-feedback-for`, `wire-no-feedback`, `wire-auto-recover`, `wire-flash`, `wire-upload`, `wire-upload-select`, `wire-upload-drop`, `wire-preview`. 로딩 클래스는 `wireview-click-loading` 계열. 상세는 `docs/features/`.

## 명령

| 할 일 | 명령 |
|------|------|
| 테스트 (e2e·slow 제외) | `make test` |
| 지원 버전 격자 (Python × Django) | `make test-matrix` |
| 최신 의존성으로 (uv.lock 무시) | `make test-latest` |
| 하한 의존성으로 (pyproject의 최저) | `make test-lowest` |
| 품질 일괄 (lint + typecheck) | `make quality` |
| JS 빌드 | `make build-js` — clone 직후와 `wireview/static/wireview/wireview.js` 수정 후 필수 |
| 클라이언트 테스트 | `make test-js` |
| 문서 사이트 빌드 (관문 포함) | `make docs-site` — 산출물 `build/docs-site/`, `python -m http.server -d build/docs-site`로 `/wireview/`가 열린다 |
| 문서 사이트 릴리스 묶음 | `make docs-site-bundle` — `build/site-dist/docs-site-v<버전>.tar.gz`(결정론적). dist/ 에 두지 않는다(PyPI가 통째로 받는다) |
| 문서 사이트 개발 서버 | `make docs-serve` — 바뀌면 다시 빌드하고 브라우저를 새로고침한다(`ARGS="--port N"`) |

전체 표(E2E 레이어, 벤치마크, Windows 실측, 타입 스텁)와 선행 조건은 `wireview-dev` 스킬에.
정의는 `Makefile` (`make help`)이 정본이다.

## 절차

**작업 절차는 `wireview-dev` 스킬에 있다.** 세션 시작(진행 중인 작업 찾기), 이슈·GAP·`wip`
라벨 규약, **도중에 발견한 결함을 다루는 법**, 완료 정의 체크리스트, 커밋과 이슈 종료,
전체 명령 표, 벤치 측정 주의. 이 문서는 지도와 금지선만 담는다.

추적의 진실 소스는 **GitHub Issues**(`itda-work/django-wireview`)다. 새 대화는
`gh issue list --label wip`로 시작한다.

## 함정

아래 중 여럿은 `manage.py check`가 잡는다 (`wireview.W001`~`W019`, `docs/features/checks.md`).

- **채널 레이어가 없으면 어떤 연결도 살아남지 못한다.** Channels에는 기본 레이어가 없다 — `CHANNEL_LAYERS`에 `default`가 없으면 `get_channel_layer()`가 `None`이고 컨슈머에 `channel_name`도 생기지 않는다. 컨슈머는 accept 전에 `ImproperlyConfigured`로 거절하고 `wireview.W012`가 같은 문장(`wireview/core/transport.py`의 `NO_CHANNEL_LAYER`)으로 미리 알린다(#87). 가드는 `connect()`가 아니라 `websocket_connect()`에 있다 — 단위 테스트는 레이어 없는 bare 컨슈머로 `connect()`를 직접 부르고, **그래서 그 테스트들은 이 실패를 한 번도 보지 못했다.**

- **`wireview.min.js`가 없으면 페이지에서 JS가 로드되지 않는다.** clone 직후와 `wireview/static/wireview/wireview.js` 수정 후 `make build-js`.
- **testproj의 채널 레이어는 `WIREVIEW_TEST_LAYER`가 고른다.** 기본은 `memory`(브로커 불요), `make test-e2e`는 `nats`, CI의 E2E는 `nats`와 `redis`를 한 번씩 돈다(지원 레이어 표는 `docs/COMPATIBILITY.md`). E2E는 `tests/e2e.sh`가 nats-server·redis-server를 직접 띄우고 끝나면(실패·중단 포함) 정리하므로 미리 켜 둘 필요가 없다(이미 떠 있으면 그것을 쓰고 끄지 않는다, #171). 바꾸려면 `make test-e2e LAYER=redis` 또는 `LAYER=memory`. channels-nats는 dev extras에 있으므로 `make install`이면 들어온다. `tests/test_nats_layer.py`는 nats-server가 없으면 로컬에서는 건너뛰지만 `CI`가 설정된 곳에서는 실패한다 — channels 하한의 유일한 근거라, 건너뛴 채 초록이면 하한이 검증되지 않는다(#132). `redis`는 `REDIS_URL`(기본 `redis://127.0.0.1:6379`, `/0` 같은 DB 번호는 붙여도 된다)에 서버가 없으면 띄운다 — 기본 URL이면 6379가 아니라 비어 있는 포트에(다른 실행이 그것을 자기 서버로 알고 쓰다 잃지 않게), `REDIS_URL`을 적었으면 그 주소에, 이 기계일 때만. 바이너리는 `REDIS_SERVER`, PATH의 `redis-server`, Homebrew 위치 순으로 찾는다.
- **testproj의 HTTP는 Django ASGI 핸들러(`get_asgi_application()`)다. `WsgiToAsgi`로 되돌리지 않는다.** 그 래퍼는 응답을
  `async_to_sync`로 보내고, uvicorn은 keep-alive 연결의 다음 요청을 그 호출 안에서 시작한다 — 다음 요청이 이미 끝난
  executor를 물려받아 `CurrentThreadExecutor already quit`로 죽는다. 전체 E2E에서만 가끔 보였다(#129). tests/test_e2e_harness.py가
  파이프라이닝으로 결정론적으로 지킨다. 테스트 DB는 프로세스마다 다른 파일이다(#125, `make test-concurrent`).
  정적 파일은 `ASGIStaticFilesHandler`가 서빙한다 — WhiteNoise 미들웨어는 동기 전용이라 ASGI 핸들러 아래에서 파일마다
  동기 이터레이터 경고를 냈다. 그 경고가 하나라도 기록되면 실행이 실패한다(`tests/testproj/warning_guard.py`).
- **단위·통합 테스트도 일부는 채널 레이어를 쓴다.** `tests/test_uploads.py`의 `UploadView` 테스트가 세션 채널로 보낸다. 그래서 기본값이 `memory`다. 브로커가 없는 레이어를 기본으로 두면 그 두 테스트가 연결 타임아웃으로 2분씩 걸린다.
- **클라이언트가 호출할 수 있는 메서드.** `_`로 시작하지 않고 **사용자 코드에서 정의한** 메서드만 이벤트 핸들러로 노출되고 `validate_call`로 감싸진다. 프레임워크(`wireview.*`)와 Pydantic이 소유한 이름은 서브클래스에서 오버라이드해도 노출되지 않는다 — `joined`·`handle_async`·`update`·`update_many`·`send_to_parent`·`model_post_init`은 클라이언트가 부를 수 없고 `validate_call`로 감싸지지도 않는다. 판정은 `wireview/core/handlers.py`의 `is_client_callable` 하나이고 디스패처·check·`_validate_handlers`가 함께 쓴다 — 감쌀 대상을 따로 고르던 시절 프레임워크 메서드의 `t.Self` 주석이 pydantic 2.13에서 import를 죽였다(#127). 회귀 테스트는 tests/test_security.py, tests/test_handler_validation.py. 내부 헬퍼는 반드시 `_` 접두사. 클래스 본문에 정의된 **클래스**(`class Meta:` 포함)는 호출 가능해도 노출되지 않는다(#99). 핸들러와 라이프사이클 메서드는 async.
- **컴포넌트 이름은 클래스명으로 전역 등록.** 다른 모듈에서 같은 클래스명을 쓰면 경고가 난다. 템플릿에서 `app:Name` 또는 FQN으로 구분한다.
- **상태 필드.** JSON 직렬화 가능해야 한다. 모델 인스턴스는 예외다: 단일 필드·목록·dict 값·`AsyncResult`의 결과 어디에 있든 pk로 서명되고, join 때 필드의 타입 표기를 따라 다시 읽힌다(`wireview/core/model_state.py`). 그래서 타입 표기가 곧 복원 규칙이다 — `list` 같은 맨 타입으로 적으면 pk 목록으로 돌아온다. `Meta.temporary_assigns`는 기본값이 있는 필드만 초기화되고, 서명 상태에 실리지 않으며, 초기화는 변경으로 치지 않는다(`wireview/core/render_reads.py`, #111). `Meta.exclude_fields`는 `user`·`wire`·`session`에 **더해진다**(뺄 수 없다).
- **테스트는 `DJANGO_ALLOW_ASYNC_UNSAFE` 없이 돈다(#120).** 그 플래그는 이벤트 루프 위의 동기 ORM을 막는 Django의 검사를 끄고, 켜 둔 동안 bookmarks·notifications 예제가 실서버에서 join하지 못하는데도 스위트는 초록이었다. 루트 `conftest.py`가 플래그를 보면 시작을 거절하고, `tests/test_async_safety.py`가 진입점(Makefile·e2e.sh·CI·bench)에 다시 들어오는지 본다. 국소 허용은 **E2E 항목의 Playwright 스레드 하나**뿐이다 — sync API가 그 스레드에 루프를 돌려 pytest-django 픽스처가 막히기 때문이고, 라이브 서버 스레드는 검사를 그대로 받는다. async 테스트에서 쿼리를 세려면 `CaptureQueriesContext` 대신 `testproj.queries.capture_queries()`를 쓴다(연결이 스레드마다 따로다). 라이브 렌더는 property를 워커 스레드에서 읽지만, 핸들러가 읽는 property는 루프 위에서 돈다. 워커 스레드가 렌더하는 동안 루프는 계속 돌므로, 루프에서 컴포넌트를 바꾸는 백그라운드 코드는 `wire._render_gate.run()`으로 돌린다 — 아니면 `data-state`와 본문이 다른 상태를 말하는 프레임이 나간다(#138, `wireview/core/render_gate.py`).
- **사용자 문서를 추가·이동하면 `docs/site.toml`에 분류한다.** 사이트에 싣는 페이지이거나 `[exclude]` 패턴이어야 하고, 어느 쪽도 아니면 tests/test_doc_site.py가 실패한다. 공개 주소가 바뀌면 `docs/redirects.toml`에 옛 주소를 남긴다.
- **공개 URL은 없애지 않는다.** 페이지를 빼거나 slug를 바꾸면 `docs/redirects.toml`로 옛 주소를 옮긴다 — `make docs-site`가 `docs/site-urls.txt`에 있는데 사이트에도 redirects에도 없는 URL로 실패한다(사라진 URL 관문). 새 URL은 `python -m scripts.docs_site build --update-urls`로 목록에 더한다. llms.txt와 게시 스킬 파일도 공개 URL이다 — 그 redirect는 파일에서 같은 확장자의 파일로만 가고, 옛 경로에 새 주소를 적은 짧은 파일이 남는다.
- **pyright는 `tests/`를 검사하지 않고, `tsc`는 checkJs=false라 JS 본문을 검사하지 않는다.** 둘 다 통과해도 해당 영역은 검증된 것이 아니다.
- **gitignore 대상.** `*.pyi` (AUTO_GENERATE_STUBS가 DEBUG에서 생성), `.wireview/`, `tests/static/`, `*.min.js`, `build/docs-site/`(문서 사이트 산출물), `build/site-dist/`(그 묶음), `build/act/`(`make ci-local`의 로그).
- **컴포넌트 ID**는 페이지 안에서 고유해야 한다.
- **LiveComponent는 부모가 소유한다.** 클라이언트는 `wireview-live` 요소에 join을 보내지 않고, 자식의 `joined()`·`update()`·`leaving()`과 렌더는 `WireviewSession.send_render`가 부모 렌더 뒤에 처리해 같은 `render` 메시지의 `children`으로 보낸다. 렌더를 보내는 새 경로를 만들 때 `send_render`를 우회하면 자식 초기화가 조용히 빠진다. 계약은 `docs/design/live-component-ownership.md`.
- **Channels가 핸들러 앞에서 타는 `aclose_old_connections()` 트립을 생략하지 않는다.** 렌더 트립이 앞뒤로 닫아도, 렌더 뒤의 코드
  (`after_render` 훅, `joined()`)가 연 연결을 다음 메시지의 핸들러가 그대로 쓰게 된다(#176 B6). `tests/test_dispatch_connections.py`가
  보인다. 컨슈머 테스트의 `WebsocketCommunicator`는 대화 중 `close_old_connections`를 no-op으로 바꿔 두므로 이 차이를 못 본다.
- **채널 레이어는 core/transport.py에서만 만진다.** `get_channel_layer`, `group_add`, `group_send`를 다른 모듈에 쓰면 tests/test_transport.py의 가드가 실패한다. fan-out은 `get_broker().publish`, 세션 메시지는 `WireviewMeta.send`.
- **프로세스를 늘리면 InMemory 레이어는 조용히 깨진다.** 브로드캐스트가 같은 프로세스의 연결에만 닿고 오류는 나지 않는다. 다중 프로세스에는 channels_redis나 channels-nats가 필수다. 성능은 둘이 대등하다(`docs/design/transport-abstraction.md` §5-3).
- **Windows에서 daphne는 연결 약 500개에서 죽는다.** daphne가 selector 루프를 강제하고 CPython의 Windows select()는 소켓 512개가 상한이다. Windows 배포는 uvicorn 단일 프로세스를 포트별로 N개 띄우고 Caddy로 분배한다(`docs/DEPLOYMENT.md`). `uvicorn --workers`도 Windows에서는 selector 루프다. 실측은 `bench/results/win11-parlab-*`, 재현은 `bench/windows/run.sh`.
- **설정은 쓰는 시점에 읽는다(#100).** `wireview.settings.X`는 모듈 `__getattr__`가 `settings.WIREVIEW`에서 찾고,
  테스트는 `override_settings`나 `tests/testproj/wireview_setting.py`의 `set_wireview`로 바꾼다. 모듈에 대입하면(monkeypatch 포함)
  `AttributeError`다 — 대입된 값이 이후의 override를 가렸기 때문이다. 새 설정 키는 `DEFAULT`에, 없앤 키는 `REMOVED`에 둔다(W014가 읽는다).
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
  `Meta.live_sessions`를 선언한 컴포넌트는 경계가 없는 페이지에서도 거절된다.
- **컴포넌트 코드를 부르는 새 경로는 예외를 `_crashed`로 받는다.** 핸들러·수신자·콜백의 예외가 세션 밖으로 나가면
  소켓이 닫히고 페이지 전체가 다시 join한다. 세션의 `_crashed(component, ref)`가 그 컴포넌트만 버리고 클라이언트에
  `error`를 보내 이벤트 전 상태로 다시 join하게 한다. join 단계의 예외는 `_join_failed`(재시도 없음 — 재시도하면 루프다).
  클라이언트가 보내지 않는 메시지는 `receive_json`이 로그 후 버린다. 계약은 `docs/features/errors.md`, 테스트는 tests/test_errors.py(#94).
- **join이 실패한 컴포넌트는 서버가 막는다.** 연결이 그 id를 기억하고(`repo.join_failed`), 부모의 패스가 그 id로
  다시 만든 인스턴스와 그것이 소유한 LiveComponent는 `repo.refused`가 참이다 — 이벤트는 빈 render로 답하고, 훅·업로드·
  브로드캐스트·`params_changed`·`wire.defer`·`update_live_component`는 버리며, 따로 렌더하지 않는다(그 렌더가 소유
  LiveComponent의 `joined()`를 돌린다). 그 id로 join이 오면 다시 시도한다(`retry_join`). 컴포넌트 코드를 부르는 새
  경로는 인스턴스를 `repo.get`이 아니라 `repo.reachable`(목록은 `reachable_components`)로 찾는다. 페이지가 소유를
  추정해 막던 때는 슬롯의 LiveComponent까지 막고 boost 뒤 다시 join하지 않았다.
- **mount가 halt하거나 예외를 던지면 아무것도 렌더되지 않는다** (#58부터). 컴포넌트는 저장소에서도
  지워지므로 그 id로 오는 이벤트도 처리되지 않는다. 렌더를 보내는 새 경로를 만들 때
  `wire.mount_halted`를 건너뛰면 가드가 막으려던 HTML과 `data-state`가 그대로 나간다. 예외를
  "미완"으로 다루면 인가 조회가 실패하는 쪽이 거절보다 통과하기 쉬워진다.
- **인증 세대는 세션 키가 아니라 `login()`이 찍는 nonce(`_wireview_auth_gen`)다.** signed-cookie
  백엔드의 `session_key`는 서명 쿠키 문자열 전체라 세션에 뭘 쓰든 바뀐다. 그리고 그 백엔드는
  로그아웃을 서버에서 폐기하지 못한다 — 경계 뒤에 진짜 인가가 있으면 서버 저장형 백엔드를 쓴다.
- **마크업에 스크립트를 넣지 않는다.** 이벤트는 `wire-on-*` 속성과 `<html>`의 위임 리스너로 간다(#90). 태그가
  `on*="…"`이나 인라인 `<script>`를 렌더하면 `'unsafe-inline'` 없는 CSP에서 조용히 죽는다 — 페이지는 그려지고 클릭만
  아무 일도 하지 않는다. tests/test_csp_e2e.py가 브라우저의 위반 보고로 지킨다. 헤더의 `<style>`·`<script>`는 요청의
  CSP nonce를 단다.
- **diff 형태를 새로 더하면 `PROTOCOL_VERSION`을 올린다.** 서버는 클라이언트가 소켓 URL의 `?vsn=`으로
  말한 버전 이하의 형태만 보낸다(`repo.vsn`, 없으면 0). 올리지 않고 새 형태를 보내면 옛 번들로 열린 페이지가
  그것을 모르는 값으로 넣어 `[object Object]`를 그린다. `wireview/core/rendered.py`와 `wireview/static/wireview/rendered.mjs`의 두 상수가
  같은지는 tests/test_comprehension_moves.py가 본다. 규칙은 `docs/implementation/wire-protocol.md` §7.
- **`Meta.shared_render`는 "선언이 틀리면 남의 화면이 간다"를 들인다(#176).** 같은 브로드캐스트를 처리하는 연결들이 렌더를
  함께 쓴다. 공유하는 것은 `Rendered`뿐이고 `data-state`는 연결마다 자기 토큰이다 — 서명이 경계와 인증 세대를 묶는다.
  렌더를 내보내는 새 경로가 선언한 클래스를 렌더하면 `shared_render.render`를 거쳐야 한다(자리 `STATE_SLOT`이 남지 않게).
  공유는 `shared_render.handling(message_id)` 안에서만 일어나고 지금은 `_dispatch_notifications`의 `send_render`만 그 안에 있다.
- **컴포넌트가 joined()를 도는 새 경로는 Broadcast 패치를 잡았다 놓는다(#178).** `joined()` 전에 `_hold_patches`, 그것이 쌓은
  작업을 보낸 뒤 `_let_patches_through`. 빠뜨리면 `joined()`의 스트림 reset(세션 메일, 늦게 써진다)이 그 사이에 쓴 패치를
  지운다. 패치를 쓰는 곳은 `wireview_patch` 하나이고, 쓰기 직전에 `repo.reachable`을 다시 묻는다. `Broadcast` 항목 템플릿의
  `this`·`user`·`request`·`perms`·`csrf_token`은 언제나 감시 객체다(`wireview/core/watched.py`) — 그 렌더가 모든 구독자의 것이다.
- **동기 렌더에서 컴포넌트의 async 코드로 건너는 새 다리는 `keep_connections()` 안에서 `async_to_sync`를 부른다(#190).** 그 스레드는
  요청의 것이고, 훅이 기다리는 Channels `database_sync_to_async`가 거기로 돌아와 `close_old_connections()`로 요청의 연결을 닫는다 —
  트랜잭션 안이면 뷰의 쓰기가 예외 없이 롤백된다. 메모리 SQLite는 `close()`를 무시하므로 테스트는 파일 DB(testproj의 기본)에서 본다.
  HTTP 렌더에서 스트림 연산은 아무것도 하지 않는다 — 다리마다 `wireview/core/meta.py`의 `HTTP_RENDER`(ContextVar)를 자기 저장소의 `not is_live`로 세운다. `channel_name`이 없다는 것으로 가르지 않는다(채널 없이 패치 게이트를 쓰는 세션 테스트가 있다). 회귀 테스트는 tests/test_http_render_connections.py.
- **data-state는 dynamic 파트다.** `{% tag_header %}`의 서명 상태는 라이브 렌더에서 마커로 감싸진다. static에 넣으면 fingerprint가 매번 바뀌어 부분 diff가 죽는다. 회귀 테스트는 tests/test_diff_stability.py.

## 문서 인덱스

- [README.md](./README.md) 사용 가이드와 빠른 시작
- [docs/features/README.md](./docs/features/README.md) 기능 레퍼런스 인덱스
- [docs/tutorials/README.md](./docs/tutorials/README.md) 튜토리얼 15편
- [docs/FEATURE-GAP.md](./docs/FEATURE-GAP.md) Phoenix LiveView 대비 갭과 GAP 번호
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) 아키텍처
- [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) 배포
- [docs/COMPATIBILITY.md](./docs/COMPATIBILITY.md) 공개 API, 폐기 절차, 지원 범위
- [docs/UPGRADING.md](./docs/UPGRADING.md) 0.x, 1.0 릴리스 후보, 1.0에서 1.1로, 1.1에서 1.2로, 1.2에서 1.3으로, 1.3에서 1.4로. 쓰던 버전별로 읽을 절과 보안 조치
- [docs/PERFORMANCE.md](./docs/PERFORMANCE.md) 성능
- [CHANGELOG.md](./CHANGELOG.md) 변경 이력
