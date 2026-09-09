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
테스트, Redis를 띄운 E2E, lint, typecheck, build 다섯 잡이다.

| 할 일 | 명령 | 선행 조건 |
|------|------|-----------|
| 의존성 설치 | `make install` 과 `npm ci` | `uv sync --dev`는 dev 도구를 설치하지 않는다. extras를 써야 한다 |
| JS 빌드 | `make build-js` | 개발 서버와 E2E 전에 필수. 산출물은 gitignore |
| 테스트 (e2e·slow 제외) | `make test` 또는 `make test ARGS="-k streams"` | collectstatic과 `DJANGO_ALLOW_ASYNC_UNSAFE`는 Makefile이 처리 |
| E2E | `make test-e2e` (NATS), `LAYER=redis`·`LAYER=memory`로 변경 | nats-server 바이너리와 JS 빌드. 서버 기동·정리는 `tests/e2e.sh`가 한다 |
| 린트 | `make lint` (ruff + djlint) | |
| 타입 검사 | `make check` (pyright, `tests/` 제외) | |
| 클라이언트 테스트 | `make test-js` (`npm test`, node --test) | |
| 포맷 | `make format` | |
| 품질 일괄 | `make quality` | CI의 lint·typecheck 잡과 동일 범위 |
| 개발 서버 | `make run-daphne` | JS 빌드, Redis |
| 타입 스텁 확인 | `cd tests && uv run python manage.py wireview_stubs --check` | |
| 성능 실측 | `make bench`, 비교는 `make bench-compare BASE=<ref>`, 서버 선택은 `ARGS="--server uvicorn"` | WebSocket 구간은 daphne 또는 uvicorn을 직접 띄우며 Redis 불필요 |
| Windows 실측 | `bench/windows/run.sh` (stage → provision → run → collect) | macOS + Parallels 랩 클론 + `windows-parallels-lab` 스킬. 상세는 `bench/README.md` |

### 테스트를 어디에 두는가

- 라이브러리 단위·통합 테스트: `tests/test_*.py`. WebSocket 없이 `mount()`를 쓴다.
- 예제 앱과 그 테스트: `examples/<app>/tests.py`. 예제는 `tests/testproj/`의 Django 프로젝트에
  얹혀 돌아가고 `make test`가 함께 실행한다. E2E는 `examples/todo/tests.py`, `examples/livecomp/tests.py`.
- 하네스 픽스처(예제가 아닌 것)는 `tests/testproj/`에 남는다: 설정·URLconf와 `tests/testproj/bookmarks/`.
- 클라이언트 순수 모듈: `tests/js/*.test.mjs` (node --test).

### 벤치마크를 잴 때 주의

**`tracemalloc`을 켠 채로 시간을 재지 않는다.** 할당 추적이 5배쯤 부풀린다.
시간과 메모리는 따로 잰다. 첫 호출은 템플릿 컴파일이 섞이므로 워밍업 뒤 중앙값을 쓴다.
레이어를 비교할 때는 회차를 여러 번 돌려 중앙값을 쓴다 — 첫 회차는 콜드 캐시로 25%쯤 낮다.

## 7. 새 코드를 쓸 때 걸리는 것들

금지선의 정본은 `CLAUDE.md`의 「함정」이다. 여기서는 착수 전에 자주 놓치는 것만 짚는다.

- **클라이언트가 호출할 수 있는 메서드.** `_`로 시작하지 않는 소문자 이름의 메서드는 이벤트
  핸들러로 노출되고 `validate_call`로 감싸진다. 내부 헬퍼는 반드시 `_` 접두사.
  핸들러와 라이프사이클 메서드는 async.
- **채널 레이어는 `wireview/core/transport.py`에서만 만진다.** `get_channel_layer`, `group_add`,
  `group_send`를 다른 모듈에 쓰면 `tests/test_transport.py`의 가드가 실패한다.
  fan-out은 `get_broker().publish`, 세션 메시지는 `WireviewMeta.send`.
- **import.** 새 코드는 `from wireview import Component, LiveComponent, JS, mount`.
  `wireview.component` 경로는 하위 호환용이다.
- **JS.** `wireview/static/wireview/wireview.js`는 ES2020, 2칸 들여쓰기, JSDoc. 포매터는 없다.
  DOM 없이 검증 가능한 로직은 `wireview/static/wireview/rendered.mjs`처럼 순수 모듈로 빼고 `tests/js/`에 node 테스트를 둔다.
- **pyright는 `tests/`를 검사하지 않고, `tsc`는 checkJs=false라 JS 본문을 검사하지 않는다.**
  둘 다 통과해도 그 영역이 검증된 것은 아니다.
