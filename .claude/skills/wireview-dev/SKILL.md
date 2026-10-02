---
name: wireview-dev
description: django-wireview 저장소에서 라이브러리 자체를 고칠 때의 작업 절차. 세션을 시작하며 지금 뭘 이어받을지 찾을 때, 기능·수정에 착수할 때(이슈·GAP·wip 라벨), 완료 정의를 확인할 때, 커밋·이슈 종료·대화 마무리 때, 그리고 테스트·E2E·벤치마크·타입검사 명령이 필요할 때 사용한다. CLAUDE.md는 지도와 금지선만 담고, 절차는 전부 여기 있다. API 사용법이 아니라 저장소 운영 절차 전용 — 컴포넌트를 어떻게 쓰는지는 docs/features/ 와 docs/tutorials/ 가 정본이다.
---

# django-wireview 개발 절차

`CLAUDE.md`가 지도와 금지선이라면, 이 문서는 절차다. **정본을 여기에 복제하지 않는다.**
아래 표의 왼쪽만 기억하고, 실제 내용은 오른쪽에서 읽는다.

| 무엇 | 정본 |
|------|------|
| 저장소 구조, 함정(금지선), 템플릿 태그 | `CLAUDE.md` |
| 개발 명령의 실제 정의 | `Makefile` (`make help`) |
| 기능별 API | `docs/features/README.md` |
| 남은 갭과 GAP 번호 | `docs/FEATURE-GAP.md` |
| 벤치 사용법과 해석 | `bench/README.md` |
| 진행 중인 작업 | GitHub Issues (`itda-work/django-wireview`) |

---

## 1. 세션을 시작할 때

**추적의 진실 소스는 GitHub Issues다.** 대화는 끊기지만 이슈는 남는다.

```bash
gh issue list --label wip          # 진행 중인 작업. 마지막 코멘트가 이어받을 지점
git log --oneline -10              # 코드 쪽 현재 위치
git status --short
```

**잔여 이슈를 고를 때는 `docs/design/backlog-review-2026-09-10.md`를 먼저 읽는다.** 이슈 본문 여럿이
`v0.3.0`(live_session) 이전에 쓰였고, 그 릴리스가 컴포넌트가 생기는 방식을 바꿨다 — 어떤 이슈는 전제가
바뀌었고 어떤 이슈는 약속할 수 있는 범위가 좁아졌다. 그 문서는 이슈별로 **지금 읽으면 빠져 있는
사실**만 적는다. 읽고 나서 고칠 것이 있으면 그 문서가 아니라 **이슈를 고친다.**

전체 조망이 필요할 때만 `docs/FEATURE-GAP.md`(남은 갭)와 `docs/ROADMAP.md`(버전 계획)를 읽는다.
읽지 않고 시작해도 되는 경우가 대부분이다.

## 2. 착수할 때

**비자명한 작업은 착수 전에 이슈를 만든다.** 오타·한 줄 패치는 제외.

- **기능 단위는 GAP 번호.** `docs/FEATURE-GAP.md`의 GAP-nnn을 고르거나 새로 만든다.
  GAP은 *기능* 단위 id이고 이슈는 *작업* 단위다. 이슈 제목에 GAP 번호를 넣는다: `GAP-022: Telemetry 훅`
- **GAP은 Phoenix LiveView 대비 기능 갭에만 쓴다.** 툴링·문서·CI 작업에 GAP을 억지로 붙이지 않는다.
  그런 작업은 GAP 없이 이슈만 만든다.
- 새 GAP이면 `docs/FEATURE-GAP.md`에도 항목을 추가한다.
- **착수하면 `wip` 라벨을 붙이고, 중단하거나 끝내면 뗀다.** `wip`는 종류와 직교한 상태 표시다.

### 라벨

| 축 | 라벨 |
|----|------|
| 제품 | `bug`, `enhancement`, `documentation` |
| 인프라·툴링 | `ci`, `chore`, `testing`, `refactor`, `security`, `audit` |
| 영역 | `area: js-interop`, `area: components`, `area: navigation`, `area: forms`, `area: uploads`, `area: performance`, `area: devtools`, `area: transport` |
| 단계 | `P0-foundation`, `P1-core`, `P2-advanced`, `P3-polish` |
| 종류 | `type: feature-gap`, `epic` |
| 상태 | `wip` |

CI나 빌드 작업을 `bug`/`enhancement`에 억지로 넣지 않는다.
현재 라벨 목록은 `gh label list --limit 100`이 정본이다. **`--limit`을 빼면 30개에서 잘린다.**

## 3. 도중에 발견한 결함

**범위 밖이라도 미루지 않는다.** 작업 중에 다른 결함을 발견하면 그 자리에서 파악하고, 고치고,
테스트로 검증한다. "이건 이 이슈 범위가 아니다"는 보고할 이유이지 남겨 둘 이유가 아니다.

발견한 것을 남겨 두면 두 가지가 따라온다. 다음 사람은 그것이 이미 알려진 것인지 모르고 같은 시간을
다시 쓰고, 무엇보다 **그것을 가장 잘 아는 순간은 발견한 순간**이라 나중에 고치는 비용이 항상 더
크다. E2E 서버 스레드 teardown 경합이 그렇게 네 파일에 복제된 채로 살아 있었다.

검증은 실행으로 한다. "고쳤다"의 근거는 **결함을 되돌려 넣었을 때 실패하는 테스트**다.
경합처럼 확률적인 결함은 확률적인 재현에 기대지 말고 결정론적 성질로 바꿔 고정한다
(`tests/test_e2e_harness.py`가 그 예다 — 경합 자체가 아니라 "스레드가 남지 않는다"를 검사한다).

## 4. 완료 정의

기능 하나를 끝냈다고 말하려면 전부 있어야 한다.

- [ ] 구현
- [ ] `tests/test_<feature>.py` — 마커(`unit`/`integration`/`slow`/`e2e`) 필수, `--strict-markers`다
- [ ] `docs/features/<feature>.md`
- [ ] `docs/features/README.md` 인덱스 갱신
- [ ] 새 문서는 `docs/site.toml`에 분류 (사이트 페이지 또는 `[exclude]`)
- [ ] `docs/FEATURE-GAP.md` 상태 갱신 (GAP 작업인 경우)
- [ ] `CHANGELOG.md` Unreleased 한 줄
- [ ] 새 모듈·태그·속성이 생겼으면 `CLAUDE.md`의 저장소 지도 갱신
- [ ] 설정 키를 추가했으면 `wireview/settings.py`의 `DEFAULT`에 기본값
- [ ] **성능을 주장하는 변경이면 `make bench-compare`로 전후 수치를 문서에 남긴다**

## 5. 커밋과 종료

- **언어 규약: 사람이 읽는 산문은 한국어, 기계와 git log가 읽는 것은 영어.**
  - 한국어 — `docs/` 전부, `README.md`, `CLAUDE.md`, `AGENTS.md`, `skills/`, 예제 README.
    `tests/test_agent_docs.py`의 `test_the_documentation_is_written_in_korean`이 지킨다.
  - 영어 — 커밋 메시지(Conventional Commits), 코드 주석·docstring, 로그·예외 메시지,
    `CHANGELOG.md`(커밋 메시지와 나란히 읽힌다), `docs/legacy/`(보존된 과거 기록).
- **GAP 작업이면 커밋 제목에 GAP 번호를 적는다.** 예: `feat: Add on_mount hooks (GAP-021)`
- **커밋 메시지에는 `#N` 평참조만 쓴다.** `Closes #N`으로 자동 종결하지 않는다.
  완료 정의를 실제로 만족했는지 확인한 뒤 `gh issue close <N> --comment "..."`로 닫는다.
- **대화를 끝낼 때 `wip` 이슈에 코멘트를 남긴다.** 한 것 / 다음 단계 / 막힌 것 셋.
  별도 handoff 문서를 만들지 않는 이유가 이것이다.
- 이슈와 PR은 `gh` CLI로 다룬다.

## 6. 명령

PR 전에 `make quality`와 `make test`를 통과시킨다. CI(`.github/workflows/ci.yml`)는 Python×Django 매트릭스
테스트, 의존성 최신 해 테스트(`make test-latest`), 하한 테스트(`make test-lowest`), NATS·Redis 레이어마다 한 번씩 도는 E2E, lint, typecheck, build 일곱 잡이다.
평소에는 수동 실행 전용이고, 태그 push 때 `.github/workflows/release.yml`이 이 전체를 불러 통과해야만 PyPI에 올린다(#122).
릴리스 절차는 `docs/ROADMAP.md`의 "릴리스 절차".

| 할 일 | 명령 | 선행 조건 |
|------|------|-----------|
| 의존성 설치 | `make install` 과 `npm ci` | `uv sync --dev`는 dev 도구를 설치하지 않는다. extras를 써야 한다 |
| JS 빌드 | `make build-js` | 개발 서버와 E2E 전에 필수. 산출물은 gitignore |
| 테스트 (e2e·slow 제외) | `make test` 또는 `make test ARGS="-k streams"` | collectstatic은 Makefile이 처리. `DJANGO_ALLOW_ASYNC_UNSAFE`를 켜면 스위트가 시작을 거절한다(#120). 테스트 하나가 60초를 넘기면 모든 스레드의 스택을 찍고 실행이 끝난다 — 디버거를 붙일 때는 `-o test_time_limit=0`(#148) |
| 동시 실행 확인 | `make test-concurrent` | 같은 사본에서 테스트 스위트 둘을 동시에 돌린다. 테스트 DB는 프로세스마다 따로라 둘 다 통과해야 한다(#125). `--ff`는 `addopts`가 아니라 Makefile 타깃에 있다 |
| 최신 의존성 테스트 | `make test-latest` | `uv.lock`을 무시하고 새로 설치하는 사람이 받는 최신 해로 돈다. lock이 옛 버전에 묶여 있으면 기본 레인은 새 설치의 결함을 못 본다(#127) |
| 하한 의존성 테스트 | `make test-lowest` | `pyproject.toml`이 허용하는 가장 오래된 런타임 의존성(django·channels·pydantic)으로 돈다. dev 도구는 그에 맞는 최신이다. 깨지면 하한을 올린다(#132). channels 하한은 nats-server 바이너리가 있어야 검증된다 |
| E2E | `make test-e2e` (NATS), `LAYER=redis`·`LAYER=memory`로 변경 | nats-server 바이너리와 JS 빌드. 서버 기동·정리는 `tests/e2e.sh`가 한다. 일부만 돌리려면 `WIREVIEW_TEST_LAYER=memory ./tests/e2e.sh tests/test_streams_e2e.py`나 `-k streams` — 경로를 넘기면 기본 경로(`tests examples`)는 빠진다. redis는 `REDIS_URL`에 떠 있는 서버를 쓴다 |
| 빌드한 wheel 스모크 | `make ci-build` 뒤 `make ci-smoke` | wheel을 lock 없이 새 의존성에 설치해 import·`check`. 릴리스 게이트의 마지막 단계 |
| 린트 | `make lint` (ruff + djlint) | |
| 타입 검사 | `make check` (pyright, `tests/` 제외) | |
| 클라이언트 테스트 | `make test-js` (`npm test`, node --test) | |
| 포맷 | `make format` | |
| 품질 일괄 | `make quality` | CI의 lint·typecheck 잡과 동일 범위 |
| 개발 서버 | `make run-daphne` | JS 빌드, Redis |
| 타입 스텁 확인 | `cd tests && uv run python manage.py wireview_stubs --check` | |
| 성능 실측 | `make bench`, 비교는 `make bench-compare BASE=<ref>`, 서버 선택은 `ARGS="--server uvicorn"` | WebSocket 구간은 daphne 또는 uvicorn을 직접 띄우며 Redis 불필요 |
| Windows 실측 | `bench/windows/run.sh` (stage → provision → run → collect) | macOS + Parallels 랩 클론 + `windows-parallels-lab` 스킬. 상세는 `bench/README.md` |

`uv.lock`에서 채널 레이어 패키지(`channels-nats`, `channels-redis`)를 올리면 그 레이어의 E2E 레인을 돌리고
`docs/COMPATIBILITY.md`의 채널 레이어 표를 같은 커밋에서 고친다. 표와 lock이 어긋나면 `tests/test_supported_versions.py`가 실패한다(#130).

### 테스트를 어디에 두는가

- 라이브러리 단위·통합 테스트: `tests/test_*.py`. WebSocket 없이 `mount()`를 쓴다.
- 예제 앱과 그 테스트: `examples/<app>/tests.py`. 예제는 `tests/testproj/`의 Django 프로젝트에
  얹혀 돌아가고 `make test`가 함께 실행한다. E2E는 `examples/todo/tests.py`, `examples/livecomp/tests.py`.
- 하네스 픽스처(예제가 아닌 것)는 `tests/testproj/`에 남는다: 설정·URLconf와 `tests/testproj/bookmarks/`.
- 클라이언트 순수 모듈: `tests/js/*.test.mjs` (node --test).
- async 테스트가 태스크·채널 레이어가 나중에 할 일을 기다릴 때는 고정 `asyncio.sleep` 대신
  `testproj.waiting.eventually(조건)`을 쓴다. 느린 러너에서만 실패하는 테스트가 여기서 나왔다(#143).
  그 일을 하는 태스크를 쥘 수 있으면 `task=`로 넘긴다 — 태스크가 던진 예외가 "시간 안에 참이 아니다" 대신 바로 올라온다(#148).
  고친 테스트는 그 경로에 지연을 넣은 파라미터로도 돌린다(`tests/test_presence.py`의 `broadcast_pace`).
  메시지를 "조용해질 때까지" 모으는 루프(`receive_nothing`, `wait_for(..., 0.2)`)도 같은 문제다 — 기대하는
  메시지는 끝까지 기다리고, 조용한 창은 "그 뒤에 더 없음"을 확인하는 데만 쓴다(`tests/test_joined.py`).
  타이머 자체(만료·리셋·충전)는 벽시계 대신 테스트가 움직이는 시계로 잰다(`tests/test_presence.py`의
  `presence_clock`, `tests/test_lifecycle_hooks.py`의 `TestRateLimitExample.clock`). 브라우저의 debounce·throttle만 sleep을 둔다.
- **async 테스트가 ORM에 쓰면 `@pytest.mark.django_db(transaction=True)`.** 그냥 `django_db`의 롤백은 테스트 스레드의
  연결에만 걸리고, async ORM 호출은 워커 스레드의 연결에서 곧바로 커밋된다. 남은 행은 다음 테스트의 UNIQUE 충돌이나
  개수 단언으로 순서에 따라 드러난다. `tests/testproj/row_guard.py`가 그런 테스트를 teardown 오류로 실패시킨다 — 실행의
  마지막 테스트와 테스트 하나만 돌린 실행도 본다(#133).

### 벤치마크를 잴 때 주의

**`tracemalloc`을 켠 채로 시간을 재지 않는다.** 할당 추적이 5배쯤 부풀린다.
시간과 메모리는 따로 잰다. 첫 호출은 템플릿 컴파일이 섞이므로 워밍업 뒤 중앙값을 쓴다.
레이어를 비교할 때는 회차를 여러 번 돌려 중앙값을 쓴다 — 첫 회차는 콜드 캐시로 25%쯤 낮다.

## 7. 새 코드를 쓸 때 걸리는 것들

금지선의 정본은 `CLAUDE.md`의 「함정」이다. 여기서는 착수 전에 자주 놓치는 것만 짚는다.

- **클라이언트가 호출할 수 있는 메서드.** `_`로 시작하지 않고 사용자 코드가 정의한 메서드는 이벤트
  핸들러로 노출되고 `validate_call`로 감싸진다. 둘 다 `wireview/core/handlers.py`의 한 규칙이다 — 감쌀
  대상을 따로 정하지 않는다(#127). 내부 헬퍼는 반드시 `_` 접두사.
  핸들러와 라이프사이클 메서드는 async.
- **채널 레이어는 `wireview/core/transport.py`에서만 만진다.** `get_channel_layer`, `group_add`,
  `group_send`를 다른 모듈에 쓰면 `tests/test_transport.py`의 가드가 실패한다.
  fan-out은 `get_broker().publish`, 세션 메시지는 `WireviewMeta.send`.
- **import.** 새 코드는 `from wireview import Component, LiveComponent, JS, mount`.
  하위 모듈은 전부 내부다. 사용자에게 보이는 이름은 `wireview/__init__.py`의 `_EXPORTS`와 `__all__`에
  함께 추가한다(`tests/test_public_api.py`, `docs/COMPATIBILITY.md`). `wireview.component`는 폐기 예정이다.
- **JS.** `wireview/static/wireview/wireview.js`는 ES2020, 2칸 들여쓰기, JSDoc. 포매터는 없다.
  DOM 없이 검증 가능한 로직은 `wireview/static/wireview/rendered.mjs`처럼 순수 모듈로 빼고 `tests/js/`에 node 테스트를 둔다.
- **pyright는 `tests/`를 검사하지 않고, `tsc`는 checkJs=false라 JS 본문을 검사하지 않는다.**
  둘 다 통과해도 그 영역이 검증된 것은 아니다.
