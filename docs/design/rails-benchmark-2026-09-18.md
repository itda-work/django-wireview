# Rails가 걷어낸 것들, 그리고 Django와 wireview의 자리

> 2026-09-18. **조사 메모다 — 결정이 아니다.** 제안(§6)은 이슈가 되기 전까지 아무것도 구속하지 않는다.
> 계기는 Mark Round의 「Returning to Rails in 2026」(2026-03-05)과 그 GeekNews 스레드다.
> 이 저장소의 규약대로, 성능을 말하는 문장에는 조건을 적었고 외삽은 외삽이라고 적었다.
> 웹 조사에서 1차 출처로 확인한 것과 추론을 본문에서 구분한다.

---

## 0. 요약

**Rails가 최근 3년간 한 일은 기능 추가가 아니라 "기본 경로의 총비용"을 줄인 것이다.** 빌드 도구,
Redis, PaaS, 네이티브 앱 팀 — 앱 하나를 끝까지 가져가는 데 필요하던 외부 의존을 하나씩 기본값 안으로
접어 넣었다. 계기가 된 글의 필자가 감탄한 것도 개별 기능이 아니라 "혼자서, 저녁 시간에, 운영까지 갔다"는
경험이다.

**Django는 같은 질문에 반대 방향으로 답했다.** 코어에는 인터페이스와 작은 조각만 두고 구현은 밖에 둔다.
`django.tasks`에는 워커가 없고, 실시간은 코어 밖(Channels)이며 공식 프로덕션 레이어는 Redis 하나뿐이고,
프런트엔드와 배포에는 입장이 없다. 이것은 태만이 아니라 선택이다(연 1회 릴리스로 가는 DEP 20, 2026년
설문 보고서의 제목 "Boring is so back"). 그래서 Rails 8의 간판 기능 상당수는 Django 코어에 **오지 않는다.**
서버 주도 UI를 코어에 넣자는 제안(new-features #26)에 대한 가장 지지받은 답은 "재원 없이는 불가능하고,
DRF처럼 서드파티가 사실상의 표준이 되는 길은 열려 있다"였다.

**wireview는 그 빈자리 중 가장 큰 곳(Hotwire + Solid Cable 축)에 서 있다.** 기능은 이미 Hotwire를
넘어 LiveView 동급이다(비교표 113행 중 107행). 그러므로 Rails에서 배울 것은 기능이 아니라 세 가지다 —
**기본 경로를 줄이는 것, HTTP와 공존하는 것, 접점(작업 큐·배포·네이티브)을 잇는 것.**

이번 조사에서 직접 확인한 것 여섯 가지가 그 주장의 근거다.

| 확인한 것 | 결과 |
|---|---|
| README·튜토리얼대로 새 프로젝트를 만들면 동작하는가 | **두 군데서 끊겨 있었다.** 채널 레이어를 안 적으면 소켓이 `AttributeError`로 죽고, `daphne` 없이 `runserver`를 띄우면 WebSocket이 열리지 않는다. 고쳤다(#87) |
| "DB가 곧 브로커"(Solid Cable)가 wireview에서 얼마나 비싼가 | 시제품을 만들어 같은 벤치로 쟀다. **NATS와 사실상 같다** — 이벤트 12,177/s 대 12,203/s, 2,000연결 브로드캐스트 254 ms 대 213 ms(폴링 100 ms), 20 ms 폴링이면 198 ms |
| 저장 20회 버스트에 한 연결이 받는 렌더 | **21회, 567 KB — 그중 20회는 바이트까지 똑같은 화면이다.** 합치기가 없다. Turbo가 refresh에 debounce를 건 바로 그 자리다 |
| 현행 LTS(5.2)와 최신(6.1)에서 도는가 | 둘 다 `make test` 범위 전부 통과. CI 매트릭스와 classifier에 없어서 넣었다 |
| 벤치가 도는가 | **0.3.0 이후 첫 join에서 죽어 있었다.** 고쳤다(#88) |
| 밖에서 어떻게 보이는가 | LICENSE 파일 없음, GitHub 설명·토픽 없음, PyPI 요약·저자는 reactor 그대로, README는 한국어뿐, 별 0 |

제안은 §6에 우선순위순으로 있다. 앞의 넷만 적으면 — **(1) 외부 표시를 고친다 (2) 첫 5분 경로를 테스트로
고정한다 (3) 렌더를 합친다 (4) 브로커 없는 다중 프로세스를 지원 조합으로 만든다.**

---

## 1. 계기가 된 글이 말하는 것

Mark Round는 DevOps 아키텍트이고 Rails 3~4 이후 13년 만에 돌아왔다. 1년간 저녁 시간에 밴드용 셋리스트
앱(setlist.rocks, 약 100개 밴드가 쓴다)을 만들어 운영까지 갔다. 그가 꼽은 것:

- **No build.** importmap으로 Node 없이 JS를 고정(`bin/importmap pin`)하고, Turbo가 링크·폼을 가로채
  SPA 같은 속도를 내고, Stimulus 컨트롤러를 필요한 곳에만 뿌린다. 그는 Webpack 설정을 "유리를 씹는" 일에 비유한다.
- **Solid 3종.** 캐시·작업 큐·WebSocket 어댑터가 전부 DB 위에서 돈다. Redis가 없다. 개발 중에는 큐가 Puma
  안에서 돌고(`SOLID_QUEUE_IN_PUMA=1`) 반복 작업은 `recurring.yml`에 "every day at 3am"이라고 적는다.
- **SQLite 프로덕션 기본값.** WAL, `synchronous=normal`, `busy_timeout` 같은 pragma를 예전에는 initializer에서
  손으로 걸었는데 이제 어댑터의 기본값이고 `database.yml`의 `pragmas:`로 조정한다.
- **Kamal.** "Heroku의 마법이 돌아왔는데 스택이 전부 보인다." Dockerfile은 Rails가 만들고, `kamal deploy`가
  빌드·푸시·헬스체크·무중단 전환·정리를 한다. kamal-proxy가 TLS까지 맡는다.
- 인증 생성기는 마음에 들었지만 결국 Devise를 골랐다 — 보안 민감 영역은 증명된 길을 간다는 이유다.

우려도 적었다. Ruby·Rails의 인기는 내려가고(Stack Overflow 2025), gem 생태계의 활동이 줄었다.

GeekNews 댓글의 논점은 다른 쪽이었다. **정적 타입이 없는 큰 코드베이스의 유지보수**와 런타임에 정의되는
메서드 때문에 **디버깅이 어렵다**는 비판, 마이크로서비스 피로, 업그레이드가 점진적이라는 안정성(Next.js와
대비), 그리고 "Rails는 데이터 모델·비즈니스 로직 문제에 맞고 동시성·인프라 문제에는 다른 언어가 필요하다"는
적합성 논의다.

**읽는 법.** 이 글의 주인공은 기능이 아니다. 백엔드 출신 한 사람이 프런트엔드 빌드, 인프라 부품, 배포
플랫폼을 **하나도 새로 배우지 않고** 끝까지 갔다는 경험이다. 아래 전부가 이 관점에서 출발한다.

---

## 2. Rails가 실제로 한 일 — 목록이 아니라 논지 하나

7.1(2023-10)부터 8.1(2025-10)까지를 **무엇을 걷어냈는가**로 다시 묶으면 이렇다.

| 걷어낸 것 | 수단 | 시기 |
|---|---|---|
| JS 빌드 도구 (Node·번들러) | importmap-rails, Propshaft(기본 자산 파이프라인), Turbo·Stimulus | 7.0 → 8.0 |
| Redis | **Solid Queue**(작업), **Solid Cache**(캐시), **Solid Cable**(pub/sub) | 8.0 |
| 별도 DB 서버 (소규모) | SQLite 프로덕션 pragma 기본값, 용도별 SQLite 파일 분리 | 7.1 → 8.0 |
| PaaS | Dockerfile 기본 생성(7.1), **Kamal 2** + kamal-proxy, Thruster | 8.0 |
| 컨테이너 레지스트리 | registry-free Kamal 배포 | 8.1 |
| CI 벤더 의존 | `bin/ci` + `config/ci.rb` (로컬에서 같은 검사) | 8.1 |
| 인증 gem | 인증 생성기 (`bin/rails generate authentication`) | 8.0 |
| 보안·운영의 "나중에 붙이는 것들" | `rate_limit`, `allow_browser`, brakeman·rubocop 기본 포함, PWA 자리, devcontainer | 7.2 |
| 네이티브 앱 팀 | **Hotwire Native** (웹뷰 하이브리드 + bridge components) | 2024-09 |
| "긴 작업은 재시작하면 처음부터" | **Active Job Continuations** (step·cursor로 재개) | 8.1 |
| 로그 파싱 | **Structured Event Reporting** (`Rails.event`) | 8.1 |

DHH의 표현으로는 "one-person framework"이고, 8.0 발표(2024-11-07)의 제목은 "No PaaS Required"였다. 핵심은
**각 항목이 프레임워크 밖의 전문가나 벤더 하나를 기본 경로에서 지운다**는 것이다.

2026-09-18 현재 최신은 8.1.3.1(2026-07-29)이고 **8.2는 나오지 않았다.** 다음 주 Rails World 2026(오스틴,
09-23~24)의 아젠다에 "Herb in Rails 8.2"(HTML을 아는 ERB)와 "A deep dive into Solid Cable"이 있다.

### 작동 원리에서 눈여겨볼 것

- **Solid Queue**는 `FOR UPDATE SKIP LOCKED`로 DB를 큐로 쓴다. 워커·디스패처·스케줄러가 한 supervisor 아래
  돌고, 동시성 제한(`limits_concurrency`)·반복 작업·일시정지와 Mission Control 대시보드가 같이 온다.
  8.0 발표는 HEY 하나가 하루 2,000만 건을 돌리고 Resque 계열 gem 여섯을 대체했다고 적는다.
- **Solid Cache**는 "메모리에 작게"가 아니라 **"디스크에 크게, 오래"**를 택했다. 캐시가 클수록 적중률이
  오르고 요즘 SSD는 충분히 빠르다는 판단이다. 같은 발표의 수치로 Basecamp는 10 TB를 60일 보존하고 P95 렌더
  시간이 절반이 됐다.
- **작업 등록은 커밋 뒤로 미뤄진다**(7.2 "Prevent jobs from being scheduled within transactions"). Django에서는
  같은 것이 아직 제안이다(new-features #161 `Task.enqueue_on_commit()`).
- **Solid Cable**은 브로드캐스트를 테이블에 INSERT하고 서버 프로세스마다 **0.1초 간격으로 폴링**한다.
  메시지는 하루 보존 후 자동 정리된다. 37signals의 Fizzy가 이 설정 그대로 프로덕션에서 쓴다(`config/cable.yml`).
- **SQLite**는 cache·queue·cable이 **각자의 파일**을 쓴다. 쓰기 잠금이 하나뿐인 DB에서 주 DB와 부하를
  섞지 않으려는 구성이다(§5.2의 시제품도 같은 이유로 파일을 나눴다).

### 그리고 그 한계 (같이 읽어야 한다)

- **Solid Cable은 공짜가 아니다.** Rails PR #58703(2026-09-08, AnyCable 저자)의 로컬 실측: 2,000명 ×
  20 msg/s에서 평균 지연이 **Solid Cable 465 ms 대 Redis 21 ms**이고, 750명은 80 msg/s에서 무너진다.
  37signals 규모의 협업 앱에는 충분하다는 것이 Fizzy로 입증됐을 뿐이다.
- **Hotwire 본체는 느리게 움직인다.** Turbo는 8개월, Stimulus는 3년간 릴리스가 없다. "유지보수 모드냐"는
  이슈(turbo #1456)에 DHH는 "open source gift"라고 답했다. 37signals 자신은 미출시 브랜치(`offline-cache`)를
  프로덕션에서 쓴다.
- **Turbo 8의 morphing은 날카로운 칼이다.** 브라우저가 들고 있는 상태(열린 `<details>`, 입력 중인 폼,
  서드파티 위젯이 바꾼 DOM)를 서버 HTML이 덮어쓴다. thoughtbot의 회고 제목이 "Turbo morphing woes"이고,
  Fizzy에는 편집 중에만 동적으로 `data-turbo-permanent`를 거는 `morph_guard_controller`가 있다.
- **Rails가 기본 제공하는 PWA는 자리뿐이다.** 생성되는 service worker는 전부 주석이다.

### 그리고 2026년의 Rails는 에이전트를 재고 있다

공식 블로그가 2026-08부터 "Agents on Rails"를 연재한다. 실제 기능 티켓을 모델에게 주고 끝까지 가는지를 재는
벤치마크이고 하네스(`lemans`)를 공개했다. 2단계(2026-09-09)의 최고 성적이 60회 중 21회 해결이고, 실패의
요지는 한 문장이다 — "티켓에 적혀 있지 않으면 모델은 하지 않는다." 숙련자가 암묵적으로 채우는 관례를
에이전트는 채우지 못한다. **관례가 곧 에이전트의 가드레일**이라는 것을 Rails는 수치로 말하기 시작했다.

---

## 3. 같은 축에서 본 Django (2026-09)

Django 6.1이 2026-08-05에 나왔다(현재 6.1.1). 다음은 6.2 LTS(2027-04)이고, 그 뒤로는 **연 1회 릴리스 +
CalVer(Django 2028) + 전 릴리스 3년 지원**이다(DEP 20). Rails가 기본값을 공격적으로 갈아 끼우는 동안
Django는 주기를 늦추고 지원을 늘렸다.

| Rails | Django 코어 | 서드파티 | 갭 |
|---|---|---|---|
| Solid Queue | `django.tasks`(6.0) — **인터페이스뿐.** 내장 백엔드는 Immediate·Dummy 둘. 워커·DB 백엔드·재시도·스케줄 없음. 6.1에서도 그대로 | `django-tasks-db`(사실상 표준), `steady-queue`(**Solid Queue의 Django 포팅**), `django-ox`, `dj-queue` … 9개월 만에 DB 백엔드 6종 이상 | 크다. "절반만 지은 집". DEP 14는 production-grade `DatabaseBackend`를 약속했으나 실리지 않았다 |
| Solid Cache | **원래 있다** (`DatabaseCache`) | — | 없다 |
| Solid Cable | **없다.** ASGI 핸들러가 `websocket` scope를 명시적으로 거부한다 | Channels 4.3.2(2025-11, 이후 10개월 무릴리스. 실질 변경은 4.2.0이 마지막). 공식 프로덕션 레이어는 `channels_redis` 하나. `channels_postgres`(별 84) | **가장 크다, 구조적이다** |
| Hotwire | 없다. 프런트엔드에 입장이 없다. 6.0의 template partials가 간접 지원 | HTMX 34%(2021년 5% → 2025년 24% → 2026년 34%), `django-htmx` | 크다, 철학적이다 |
| Propshaft + importmap | fingerprint는 있다. JS 모듈 그래프는 "experimental". importmap·프로덕션 서빙 없음 | whitenoise(코어 편입을 Steering Council이 진행 중), `django-vite` | 크다 |
| Kamal·Dockerfile | 없다. `startproject`는 파일 6개 | `django-simple-deploy`, cookiecutter-django, Kamal 직접 사용 글들 | 크다, 의견 부재 |
| 인증 생성기 | **Django가 앞선다.** 단 2FA·passkey는 없다(Trac #25612, 11년째) | `django-allauth`(MFA·passkey·OIDC IdP까지) | 역방향 |
| `rate_limit` | 없다 (문서가 "Django does not throttle"이라 명시) | `django-ratelimit`(3년 무릴리스) | 작고 선명하다. new-features #204에서 논의 중 |
| SQLite 프로덕션 기본값 | 옵션은 열었으나(5.1) **기본값은 `DEFERRED`, WAL 아님.** 문서는 여전히 말린다 | `dj-lite`, 블로그 가이드 | 코드 몇 줄, 의지의 문제 |
| Structured events | 없다 (`logging` + signals) | `django-structlog`, OTel | 개념의 부재 |
| Continuations | 없다. 논의도 찾지 못했다 | — | timeout·retry가 먼저다 |
| Admin | **Django 최대 자산** (설문 가치 2위) | unfold 등 | 역방향 |

**수요는 있다.** 2026년 설문에서 VPS·자가 호스팅 44%, 모놀리스 54%, Docker 프로덕션 52%, 서버 렌더 템플릿
72%다. Kamal과 Hotwire가 겨냥하는 바로 그 사용자층이다. 공급(공식 답)이 없을 뿐이다.

**그리고 코어는 오지 않는다.** new-features #26 "Officially Supported Server-Driven Frontend Framework"
(+23)에 대한 django-unicorn 저자 Adam Hill의 답이 요점을 정리한다 — Hotwire는 37signals가, LiveView는
Dashbit이 돈을 댄다. Django에는 그 재원이 없고 Fellow의 지원 부담을 감당할 수 없다. **대신 DRF는 공식
사이트에 언급 한 번 없이 사실상의 표준이 됐고, 그 길은 열려 있다.** WebSocket을 코어에 넣자는 제안은
존재하지도 않는다.

Django의 구조적 대답은 따로 있다. 서드파티를 `pip install django[...]`로 공식 추천하는 **package extras
DEP 초안**(2026-08-21)이다. 요건은 보안 정책, BSD 호환 라이선스, import 시 부작용 없음, Django 릴리스에
맞춘 deprecation이다. §6-P0이 이것과 닿는다.

---

## 4. wireview는 지금 어디에 있나

### 설계 축에서

| | Hotwire | LiveView | Livewire | **wireview** |
|---|---|---|---|---|
| 상태의 위치 | 서버에 없다 (DB·세션·URL) | 연결당 프로세스 | 요청마다 스냅샷 왕복 | 연결당 컴포넌트 인스턴스 + 서명된 `data-state` |
| 전송 | HTTP 우선, 소켓은 브로드캐스트용 | WebSocket 우선, longpoll 폴백 | HTTP | WebSocket 필수 (폴백 없음, GAP-012 결정) |
| diff | 페이지·프레임 HTML을 받아 클라이언트가 morph | static/dynamic 분리 | 컴포넌트 HTML morph | static/dynamic 분리 + idiomorph |
| JS 없을 때 | **완전 동작** | 첫 렌더만 | 첫 렌더만 | 첫 렌더만 (스트림 목록은 비어 있다 — GAP-034) |
| 끊겼을 때 | 브로드캐스트만 멈춘다 | 전부 멈춘다. 재연결 후 폼 복구 | — | 전부 멈춘다. 토큰으로 rejoin, 폼 복구 |
| 네이티브 | Hotwire Native 1.3 (활발) | LiveView Native — **archived** | NativePHP | 없다 |

### Rails·Hotwire보다 이미 나은 곳

- **타입.** GeekNews 댓글이 Rails에 던진 가장 큰 비판에 대한 답이 wireview에는 구조로 있다. 상태는 Pydantic
  필드이고 핸들러 인자는 `validate_call`로 검증되며 `.pyi` 스텁과 LSP 메타데이터가 생성된다.
- **인가 경계.** `turbo_stream_from`의 서명된 스트림 이름에는 **만료도, 사용자 결합도 없다** — 그 페이지를 한 번
  렌더받은 사람이 기한 없이 쓰는 bearer capability다(turbo-rails 소스로 확인). wireview의 `data-state`는
  클래스·경계·인증 세대에 묶이고 만료되며, 로그아웃이 기존 소켓을 회수한다.
- **조용한 실패를 신호로.** `manage.py check`의 `wireview.W001`~`W012`. Rails에 대응물이 없다.
- **에이전트 친화.** 휠에 실린 스킬과 `wireview_agent_setup`은 Django 생태계가 new-features #108("Laravel
  Boost 같은 것")에서 아직 기다리고 있는 바로 그 형태다. `tests/testproj/bookmarks/`(스킬 검증의 기준선)는
  Rails가 "Agents on Rails"로 하는 일의 작은 판이다. Django 본체의 AI 대응은 아직 방어(기여 문서의 AI 절)이고
  `llms.txt`도 없다.

### Rails와 같은 결론에 이미 도달한 곳

- **No build.** 번들 하나 + `defer` 훅 파일. 앱 개발자에게 Node가 필요 없다.
- **No Redis.** channels-nats — 바이너리 하나, 영속 계층 없음, Windows 네이티브.
- **No sticky.** 청크 업로드는 무상태 HTTP + 서명 토큰(#83). join은 토큰에서 상태를 복원하므로 HTTP 렌더와
  소켓이 다른 워커에 닿아도 된다.
- **커밋 뒤에 알린다.** `send_to`가 `transaction.on_commit`에 걸린다. Rails의 `broadcasts_refreshes`가
  `after_commit`인 것과 같은 이유다.
- **SQLite + 단일 서버**를 기본 배포 전제로 둔 것(`docs/design/transport-abstraction.md` §5-1).

### 경쟁 지형

Django의 LiveView식 라이브러리 중 활발한 것은 둘이다. **djust**(Rust VDOM, 2026-01 첫 릴리스 후 8개월간
82회 릴리스)는 SSE 폴백, HTTP-only 모드, PWA 모듈, lazy hydration, View Transitions, TurboNav 가이드,
LiveView Native ADR까지 **넓게** 간다. 포럼에는 "문서에 있는 템플릿 태그가 패키지에 없다"는 피드백이 있다.
wireview는 **깊게** 갔다 — 계약 테스트, 변이 검사, 여섯 라운드의 적대적 리뷰, 실측 기반 설계 메모.
문제는 그 깊이가 밖에서 **보이지 않는다**는 것이다(§5.6).

---

## 5. 이번 조사에서 직접 확인한 것

측정 조건(공통): Apple M5 Pro 18코어·64 GB, macOS 27.0, Python 3.12.14, Django 6.0, Channels 4.3.2,
daphne 4.2.1, SQLite 3.53.1, 커밋 `a87f435` + 아래 수정이 들어간 작업 트리.

### 5.1 첫 5분 경로가 두 군데서 끊겨 있었다 (#87)

"`rails new` 뒤에 바로 된다"에 해당하는 경로를 README와 튜토리얼 01대로 따라가 봤다.

1. **채널 레이어를 안 적으면 소켓이 죽는다.** Channels에는 기본 레이어가 없다. `CHANNEL_LAYERS`가 없으면
   `get_channel_layer()`가 `None`이고 컨슈머에 `channel_name`이 생기지 않는데, `connect()`가 accept **뒤에**
   그 속성을 읽었다. 결과는 `AttributeError: 'WireviewConsumer' object has no attribute 'channel_name'`과
   재연결 루프다. README는 "기본 InMemory 채널 레이어"라고 적고 설정을 요구하지 않았다.
2. **`daphne` 없이는 `runserver`가 WebSocket을 받지 못한다.** 튜토리얼 01은 `pip install django-wireview` 뒤
   `manage.py runserver`를 시킨다. 그 서버는 WSGI다. 스크래치 프로젝트로 재현했다 — 핸드셰이크에 일반 HTTP
   응답이 돌아오고, 페이지는 그려지고, 아무것도 반응하지 않고, **오류가 없다.**

고친 것: 컨슈머가 ASGI 진입점(`websocket_connect`)에서 accept 전에 `ImproperlyConfigured`로 거절하고,
`wireview.W012`가 같은 문장으로 미리 알린다. README·튜토리얼·스킬에 `daphne`와 `CHANNEL_LAYERS`를 넣었다.
가드를 되돌리면 원래의 `AttributeError`가 재현되며 테스트가 실패한다.

**왜 아무도 몰랐나.** 단위 테스트 세 곳이 레이어 없는 bare 컨슈머에 `channel_name`을 손으로 넣고
`connect()`를 직접 부른다. 테스트가 쓰는 입구와 사용자가 쓰는 입구가 달랐다. 이 저장소에는 "예제는
테스트다"라는 원칙이 있는데 **README는 테스트가 아니었다.**

### 5.2 "DB가 곧 브로커"는 wireview에서 얼마나 비싼가

Solid Cable의 설계를 그대로 옮긴 채널 레이어 시제품을 만들어(196줄, 부록 A) 이 저장소의 벤치로 쟀다.
발행은 SQLite 파일에 INSERT, 서버 프로세스마다 `id > 마지막으로 본 id`를 폴링한다. 그룹 멤버십은
channels-nats 0.2.0과 같이 프로세스 로컬이고, 같은 프로세스의 구독자에게는 파일을 거치지 않고 바로 넣는다.

daphne 4프로세스, 연결 2,000개, 항목 5개 컴포넌트, 레이어당 4회의 중앙값이다.

| 레이어 | 연결당 RSS | join/s | 이벤트/s | 브로드캐스트 (2,000연결 전부 재렌더) |
|---|---:|---:|---:|---:|
| channels-nats 0.2.0 + nats-server 2.14.6 | 57.0 KB | 2,139 | 12,203 | 213 ms |
| SQLite 폴링 시제품, 100 ms (Solid Cable 기본값) | 52.0 KB | 2,018 | 12,177 | 254 ms |
| SQLite 폴링 시제품, 20 ms | 51.9 KB | 2,063 | 12,172 | 198 ms |

- **이벤트 처리량은 같다.** 회차 변동 안이다. 이 저장소가 이미 적어 둔 대로 이벤트당 비용은 레이어가
  아니라 Django 템플릿 렌더가 지배한다.
- **브로드캐스트는 폴링 간격의 절반쯤 늦다**(+41 ms). 나머지 200 ms는 어느 레이어든 프로세스당 500개
  컴포넌트를 다시 렌더하는 시간이다. 20 ms로 폴링하면 차이가 사라진다.
- 발행 한 건의 비용은 INSERT 하나, 실측 38 µs다(WAL, `synchronous=NORMAL`).
- 빈 폴링은 프로세스당 초당 10~50회의 PK 범위 조회다.

**Rails 쪽 수치와 왜 다른가.** PR #58703에서 Solid Cable이 Redis보다 20배 느려진 조건은 2,000명 × 20 msg/s,
즉 초당 40,000건 전달이다. wireview에서 전달 한 건은 **Python 렌더 한 번**이고 4프로세스의 상한이 초당
12,000건 남짓이므로, 레이어가 병목이 되기 전에 렌더가 먼저 포화한다. "wireview의 fan-out은 구독 세션 수만큼
Python 렌더다"(`transport-abstraction.md` §2)의 다른 표현이다. **이 문단은 추론이다** — 지속 발행률을 올려
가며 잰 것이 아니라 브로드캐스트 한 건을 잰 것이다.

**만들다가 밟은 것.** 프로세스 넷이 동시에 기동해 `PRAGMA journal_mode=WAL`을 걸자 즉시
`database is locked`가 났다. 저널 모드 전환은 배타 잠금이 필요하고 **busy handler를 타지 않는다.** Rails가
SQLite 어댑터에서 다듬은 것이 이런 종류의 모서리이고, Django의 `init_command`에 같은 PRAGMA를 적는
이 저장소의 배포 문서도 다중 프로세스 동시 기동에서 같은 경합을 만날 수 있다(확인하지 않았다).

**시제품이 하지 않은 것**: 메시지 만료·용량 규약, 지속 발행률 측정, Windows 측정, 다중 호스트(SQLite 파일은
한 호스트 안에서만 공유된다 — 그 너머는 같은 설계를 PostgreSQL에 얹는 것이고 Solid Cable이 그렇게 한다).

### 5.3 저장 버스트와 렌더 횟수

todo 예제의 `XTodoList`에 join한 뒤 ORM으로 `Item`을 N번 만들고, 그 연결이 받은 `render`를 셌다(InMemory
레이어, 단일 프로세스).

| 저장 횟수 | 받은 render | 바이트 |
|---:|---:|---:|
| 1 | 1 | 4,415 |
| 20 | 21 | 566,882 |

21회의 내역이 요점이다. **목록 컴포넌트의 렌더 20회는 전부 28,320바이트로 똑같다** — 컨슈머가 첫 메시지를
처리할 때 이미 20행이 다 있었으므로 20번 모두 같은 최종 화면을 그려 보냈다. 19번은 순수한 낭비다. 나머지
하나는 같은 채널을 구독한 카운터(502바이트)다. 카운터도 서버에서는 20번 렌더했지만 19번은 diff가 비어
전송되지 않았다. **부분 diff는 대역폭을 구하고 CPU는 구하지 못한다.**

`_dispatch_notifications`는 메시지마다 구독 컴포넌트의 `mutation()`을 부르고 **곧바로** `send_render`를 한다.
합치기도, 원인 연결 식별도 없다. 그래서 일괄 작업 하나가 **연결된 모든 탭에** N번의 렌더를 일으킨다. 예제가
이 비용을 앱 코드로 피하고 있다는 것이 증거다 — `add()`는 `skip_render()`를 부르고 주석에 "mutation()이
렌더하게 둔다"고 적는다.

Turbo는 같은 자리에 장치 둘을 뒀다. refresh를 서버에서 0.5초·클라이언트에서 150 ms debounce하고, 요청 id를
실어 **자기가 일으킨 신호는 버린다.**

### 5.4 Django 5.2 LTS와 6.1

CI 매트릭스는 4.2·5.0·5.1·6.0이었다. **현행 LTS와 최신 릴리스가 빠져 있었고**, 테스트하는 넷 중 셋(4.2·5.0·5.1)은
upstream 지원이 끝났다. 로컬에서 5.2와 6.1로 `make test`와 같은 범위(e2e·slow 제외)를 돌렸고 둘 다
통과했다(1,375개·1,376개). 매트릭스와 classifier, README에 넣었다. EOL 버전을 뺄지는 메인테이너의 정책
결정이라 건드리지 않았다.

Channels 4.3.2의 PyPI classifier에는 6.1이 없다(main에는 6.1 테스트가 들어가 있고 릴리스가 없다).

### 5.5 벤치가 깨져 있었다 (#88)

`make bench`의 WebSocket 구간이 0.3.0 이후 첫 join에서 죽어 있었다. 벤치는 과거 커밋에서도 돌도록 join
상태를 일부러 구형식으로 서명했는데, live_session을 선언한 프로젝트는 `STATE_ACCEPT_LEGACY`와 무관하게
옛 토큰을 거절하고 testproj가 경계를 선언한다. CI는 벤치를 돌리지 않는다. 재는 트리 자신의 `sign_state`를
쓰게 고쳤고 `tests/test_bench_harness.py`가 지킨다.

**§5.1과 같은 모양이다.** 검증되지 않는 입구는 조용히 썩는다 — 이 저장소가 live_session에서 비싸게 배운
것의 다른 사례다.

### 5.6 밖에서 어떻게 보이는가

- 저장소 루트에 **LICENSE 파일이 없다.** `pyproject.toml`은 MIT를 선언하고 README는 없는 파일에 링크한다.
  GitHub API의 `license`가 `null`이다. django-reactor에서 파생됐으므로 원저작권 고지가 필요한데, 원
  저장소에도 LICENSE 파일은 없고 PyPI 메타데이터만 MIT라고 말한다(저자 Eddy Ernesto del Valle Pino).
  옮겨 올 원문이 없다는 뜻이다. **저작권자 표기는 메인테이너가 정할 일이라 만들지 않았다.**
- GitHub 설명·홈페이지·토픽이 비어 있다. 별 0.
- PyPI 요약은 "Brings LiveView from Phoenix framework into Django" — reactor의 문구 그대로이고 `authors`도
  reactor 원저자 한 명이다.
- README와 이슈가 한국어뿐이다. 제3자의 언급(블로그·뉴스레터·포럼·벤치마크)을 찾지 못했다.
  `tanrax/django-interactive-frameworks-benchmark`는 LiveView·Reactor·htmx·Unicorn을 비교하는데 wireview는 없다.

---

## 6. 제안

우선순위는 (채택을 막는 정도) × (비용의 역수)다. 각 항목에 Rails의 무엇에서 왔는지, 기존 결정과 어떻게
닿는지를 적는다.

### P0. 외부 표시 — 기능이 아니지만 지금은 어떤 기능도 발견되지 않는다

LICENSE, GitHub 설명·토픽, PyPI 요약·저자, 그리고 **영어 README 한 장**. Hotwire의 반면교사도 여기에 있다 —
Turbo는 기능이 브랜치에만 있고 릴리스가 없어서 "유지보수 모드냐"는 질문을 받는다. 작은 팀이 같은 인상을
피하는 가장 싼 방법은 지원 범위와 릴리스를 기계적으로 드러내는 것이다.

package extras DEP가 승인되면 "공식 추천 패키지"의 요건(보안 정책, import 부작용 없음, Django 릴리스에 맞춘
deprecation, `django/main` 대비 CI)이 생긴다. `SECURITY.md`와 `django-main` CI 행은 지금 넣어도 싸다.

한국어 문서 규약과 부딪히지 않는다. 규약은 "사람이 읽는 산문은 한국어"이고, 영어 README는 **발견을 위한
입구 한 장**이면 된다(`tests/test_agent_docs.py`의 `ENGLISH_BY_DESIGN`에 한 줄).

### P1. 첫 5분 경로를 테스트로 고정한다 — `rails new`의 교훈

#87은 증상이고 원인은 **README가 테스트가 아니었다**는 것이다.

- **빠른 시작을 실행하는 테스트.** 임시 디렉터리에 README의 설정 블록 그대로 프로젝트를 만들고, 실제 ASGI
  서버에 WebSocket으로 붙어 카운터를 한 번 올린다. `tests/testproj/e2e_server.py`가 이미 있으므로 새
  하네스가 필요 없다.
- **ASGI 한 줄.** README가 시키는 `asgi.py`는 열몇 줄 전부가 상용구이고, `django.setup()`을 import보다 먼저
  불러야 한다는 순서 함정이 들어 있다. `from wireview.asgi import application` 한 줄로 줄일 수 있다.
- **(결정 필요) `runserver`가 WSGI일 때 알리는 검사.** `runserver`를 실제로 실행 중이고 그 명령의 제공자가
  stock일 때만 경고하면 uvicorn 사용자에게 오탐이 없다. `sys.argv`를 보는 검사가 이 저장소의 취향에 맞는지는
  판단이 필요해 구현하지 않았다.
- DEP 15(`django new`, 템플릿 선택형 — 승인됐고 미구현)가 들어오면 wireview 프로젝트 템플릿이 끼울 자리가
  생긴다. 지금 만들 것은 아니다.

### P2. 렌더를 합친다 — Turbo의 debounce와 request-id의 교훈

§5.3의 수치가 근거다. 제안은 **`mutation()`·`notification()`은 메시지마다 부르되 렌더는 컴포넌트당 한 번으로
미루는 것**이다. 렌더는 상태의 순수 함수이므로 합쳐도 의미가 바뀌지 않는다.

- 수신 경로에서 컴포넌트를 dirty로 표시하고, 짧은 창(한 프레임, 10~20 ms) 뒤에 한 번 렌더한다.
  `force_render`는 누적에서 이기고 `skip_render`는 마지막 값이 아니라 "한 번이라도 렌더를 원했는가"로 합친다.
- 원인 연결 건너뛰기(Turbo의 request-id)는 **하지 않는 편이 낫다.** 같은 연결의 다른 컴포넌트가 그 모델을
  구독할 수 있어서 건너뛰는 단위가 연결이 아니라 "이 이벤트에서 이미 렌더한 컴포넌트"여야 하고, 합치기가
  들어가면 되돌아온 신호의 렌더는 빈 diff가 되어 CPU만 든다. 그 CPU가 문제인지는 `make bench`에 버스트
  시나리오를 넣어 먼저 잰다.
- GAP-035(LiveComponent 배치 업데이트, #74)와 같은 계열의 문제다. 같이 설계한다.
- **브로드캐스트 유실과의 관계.** `transport-abstraction.md` §5-4가 적은 대로 어느 레이어든 at-most-once이고
  유실의 결과는 낡은 화면이다. Turbo가 델타 대신 "다시 그려라"만 보내는 이유 중 하나가 이것이다 — 멱등한
  신호는 다음 한 번으로 복구된다. wireview의 mutation 경로는 이미 그 모양이다(재렌더가 서버 진실을 다시
  읽는다). 점검할 곳은 **streams**다. 끊긴 사이의 `stream_insert`는 사라지므로, rejoin 때 스트림을 서버
  진실로 재설정한다는 계약이 문서에 명시돼 있는지 확인한다.

### P3. 브로커 없는 다중 프로세스 — Solid Cable의 교훈

§5.2가 근거다. **단일 호스트에서는 DB 폴링 레이어가 NATS와 같은 성능을 낸다.** 이 저장소의 배포 전제
(SQLite + 단일 서버 + Windows)에서 남은 외부 프로세스는 nats-server 하나였고, 이것은 그것마저 지운다.

- channels-nats와 같은 이유로 **별도 패키지**여야 한다 — wireview가 아니라 Channels 수준의 부품이다.
  기존 것들은 약하다: `channels_postgres`(별 84, 최근 커밋은 dependabot뿐, NOTIFY 페이로드 8 KB 제한),
  `channels-sqlite` 0.1(릴리스 하나).
- **NATS를 대체하지 않는다.** 다중 호스트와 높은 발행률은 NATS의 자리다. 이것은 "`pip install`만으로 끝나는
  구성"이라는 새 맨 아래 칸이다.
- 가장 자주 만날 수요는 다중 워커가 아니라 **웹 프로세스 밖의 발행자**다. 연결 2,000개를 단일 프로세스가
  든다는 것은 이 저장소가 이미 쟀다. 그런데 InMemory에서는 `manage.py` 명령·cron·작업 워커가 저장한 모델이
  웹 프로세스에 닿지 않는다. 오류도 없다(`wireview.W006`은 `--deploy`에서만 뜬다). P4가 이것에 걸린다.
- 착수 전에 잴 것: 지속 발행률(초당 10·50·200건), Windows(`bench/windows/run.sh`), 주 DB와 파일을 나눴을 때와
  합쳤을 때, 4프로세스 동시 기동의 WAL 경합.
- 기본값으로 삼을지는 별개의 결정이다. Django 문화에서 설정을 몰래 채우는 것은 환영받지 않는다 — #87도
  그래서 자동 폴백이 아니라 거절 + 안내로 고쳤다.

### P4. `django.tasks` 접점 — Solid Queue와 `broadcast_*_later`의 교훈

`start_async`·`assign_async`는 **연결에 묶인** 작업이다. 탭을 닫으면 사라진다. 내구성이 필요한 작업의 표준
접점은 이제 `django.tasks`다(워커는 없지만 인터페이스는 코어다).

- `await self.track_task(result)` — 결과 id를 상태에 넣고 `task.<id>` 토픽을 구독한다. `django.tasks.signals`의
  `task_finished`(6.0에 있다 — `task_enqueued`·`task_started`와 함께)에서 그 토픽으로 발행하는 수신기를
  wireview가 등록한다. 재연결하면 토큰의 id로 `aget_result()`를 다시 읽는다.
- **백엔드에 의존하지 않는다.** Immediate면 그 자리에서 끝나고, DB 백엔드면 워커 프로세스가 발행한다 —
  그래서 P3(또는 NATS·Redis)가 전제다.
- 진행률은 코어에 API가 없다(new-features #141 metadata가 논의 중). 작업 안에서 부르는 작은 발행 헬퍼로
  시작하고 코어가 정하면 맞춘다. Continuations에 해당하는 것은 Django에 논의조차 없으므로 기다리지 않는다.
- 이것이 Hotwire보다 나은 지점이 된다. Rails에서는 잡이 스트림 이름과 partial을 직접 지정해 브로드캐스트한다.
  여기서는 컴포넌트가 작업을 **구독**한다.

### P5. Kamal 배포 레시피 — "No PaaS"의 교훈

Django에는 공식 배포 답이 없고, wireview는 ASGI + WebSocket이라 평범한 Django보다 배포가 한 단계 어렵다.
**검증된 레시피 하나**가 채택 장벽을 가장 크게 낮춘다. Kamal은 프레임워크 중립이고 Django 사례가 이미 있다.

- Dockerfile + `deploy.yml`(uvicorn, SQLite 볼륨, NATS 액세서리 또는 P3). kamal-proxy는 WebSocket을 그대로 지난다.
- **무중단 전환에서 열린 소켓이 어떻게 되는지**를 문서가 말해야 한다. 드레인 뒤 소켓이 닫히고, 클라이언트가
  재연결하고, 토큰으로 rejoin하고, 폼이 복구된다 — wireview는 이 경로를 이미 갖고 있고(서명된 상태, GAP-008),
  이것은 연결당 상태를 메모리에 두는 모델의 가장 큰 약점에 대한 답이다. 배포 두 번 사이에 탭이 살아남는
  것을 보여 주는 절차 하나면 된다.
- 같이 정리할 것: `docs/DEPLOYMENT.md`의 "WebSocket 연결에 sticky session을 건다"(ALB 절과 수평 확장 절)는
  같은 문서의 "스티키 라우팅은 필요 없다"와 어긋난다. 소켓은 그 자체로 한 워커에 붙어 있고 join은 토큰에서
  복원하므로 스티키가 필요한 경로를 찾지 못했다. **의도가 있는지 확인이 필요해 고치지 않았다.**

### P6. 이벤트 폭주 제어 — `rate_limit`의 교훈

클라이언트가 부를 수 있는 핸들러가 소켓 위에 열려 있는데 서버에는 빈도 제한이 없다(`consumer.py`·
`repository.py`에 해당 코드 없음. 배포 문서의 미들웨어 예시는 **연결 수**만 센다).

Django 코어의 rate limiting 논의(#204)가 막힌 이유는 저장소다 — 캐시를 rate limit 저장소로 쓰는 것은
안티패턴이라는 반론이 있다. **wireview는 그 문제가 없다.** 연결이 한 워커에 붙어 있으므로 연결당 토큰
버킷을 프로세스 메모리에 두면 되고 공유 저장소가 필요 없다. `WIREVIEW["EVENT_RATE"]` 하나와 핸들러 단위
오버라이드면 Rails의 컨트롤러 한 줄에 해당한다. 초과 시의 동작(버림 / 닫음)과 로그 등급이 결정할 것이다.

### P7. GAP-033·GAP-034에 대한 Hotwire의 입력

둘 다 열려 있고 "무엇을 약속할지부터"가 과제다. Hotwire가 먼저 밟은 곳이 있다.

- **보존의 어휘를 나눈다 (GAP-033).** Turbo는 `data-turbo-permanent` 하나에 "내비게이션을 건너 생존"과
  "morph에서 제외"를 같이 실었다가 2026-08에 스스로 쪼개고 있다(PR #1562 — "broader than needed").
  LiveView도 셋으로 나눈다: `phx-update="ignore"`, `JS.ignore_attributes`, sticky. `wire-sticky` 하나에 두
  의미를 싣지 않는다. idiomorph의 `beforeNodeRemoved`가 조상 단위로 한 번만 불린다는 한계는 boost의 morph에도
  그대로 해당한다.
- **편집 중에는 덮어쓰지 않는다.** Fizzy의 `morph_guard_controller`, thoughtbot의 `<details open>` 사례,
  LiveView 1.2.8의 포커스된 폼 요소 보호. 남의 브로드캐스트가 입력 중인 폼과 열린 `<details>`·`<dialog>`를
  되돌리지 않는 것을 기본 동작으로 두고 opt-in으로 푼다. `wire-hook`에는 "서브트리가 morph된 뒤"의 콜백이
  필요하다 — 요소가 제거되지 않으면 재초기화할 계기가 없다는 것이 turbo #1083의 요지다.
- **폼 폴백은 303/422로 (GAP-034).** #73의 후보 중 "`{% on "submit" %}` 폼이 `action`·`method`도 함께 낸다"가
  Hotwire의 검증된 길이다. 규약은 성공 303, 검증 실패 422. **JS가 꺼진 사용자보다 소켓이 붙기 전에 제출하는
  사용자가 훨씬 흔하다** — 이것이 이 선택지의 진짜 값이다. 스트림 초기 항목을 첫 HTTP 렌더에 넣는 것이
  "읽기 전용 보장" 쪽이다.

### P8. boost: hover prefetch와 View Transitions

Turbo 8, Livewire 4, djust가 모두 가졌고 boost에는 없다(grep 0건). 둘 다 작고 독립적이다.

- prefetch: `mouseenter` + 100 ms, 크기 1·TTL 10초 캐시, `X-Sec-Purpose: prefetch`, GET·동일 출처만.
  boost의 fetch는 HTTP 렌더만 받으므로 **join이 일어나지 않아 부수 효과가 없다.** prefetch 응답도 live_session
  경계 판정을 똑같이 지나야 한다.
- View Transitions: body morph를 `document.startViewTransition()`으로 감싸는 옵트인. `prefers-reduced-motion`이면 끈다.

### P9. Hotwire Native의 서버 계약 — 스파이크부터

**네이티브 위젯 렌더러는 죽었고(LiveView Native archived) 웹뷰 하이브리드만 살아남았다.** 서버 계약은 작고
프레임워크 중립이다 — Turbo.js가 실린 HTML, UA 판별, historical location 라우트 셋, path configuration JSON.
Laravel은 구현했고 **Django에는 구현한 곳이 없다.**

넘어야 할 것은 하나다. Hotwire Native는 `window.Turbo`가 없으면 어댑터 등록에서 예외를 던진다. 즉 Turbo Drive가
내비게이션을 소유해야 하고 boost와 정면으로 겹친다. 현실적인 형태는 **"Turbo Drive 공존 모드"**다 — boost를
끄고, `turbo:load`에서 컴포넌트를 join하고, `turbo:before-cache`에서 연결 표시를 걷어 낸다. 지금 클라이언트는
컴포넌트를 boost 이벤트에서만 다시 찾으므로(`joinAllComponents`는 공개 API가 아니다) 그 입구부터 필요하다.
djust는 "TurboNav는 내비게이션마다 소켓을 끊는다"고 경고한다. **되는지 모른다.** Hotwire Native 데모 셸로
하루짜리 스파이크가 첫 단계다.

### P10. 레시피 둘

- **Web Push × Presence.** Campfire는 "지금 그 방에 소켓으로 붙어 있지 않은 사람에게만" 푸시한다.
  `PresenceMixin`이 이미 있으므로 기능이 아니라 예제로 충분하고 #41(알림 예제 보강)의 자연스러운 다음이다.
- **LLM 토큰 스트리밍.** 2026년에 서버 주도 UI를 찾는 가장 흔한 이유인데 예제가 없다. `dom_action`의
  append가 이미 있어서 누적 텍스트를 매번 다시 보내지 않고 쓸 수 있다.

### 나중 — 아이디어로만

보일 때 마운트(Turbo Frames `loading="lazy"`, Livewire islands — 연결당 메모리를 아낀다), 상태 없는 구독 태그
(한 번 렌더해 N명에게 — 공지 배너처럼 사용자별 상태가 없는 영역의 렌더를 O(N)에서 O(1)로), 개발 중 파일
종류별 live reload(Hotwire Spark — 단 Spark 자신이 1년 반째 정체다).

---

## 7. 따라 하지 않을 것

- **"다시 그려라"만 보내는 모델로 물러서지 않는다.** Turbo 8의 refresh는 클라이언트마다 HTTP 왕복을 하나 더
  만들고 모두가 동시에 요청한다. wireview의 부분 diff가 대역폭에서 엄격히 낫다. 가져올 것은 "멱등한 신호는
  유실에 강하다"는 통찰뿐이다(P2).
- **WebSocket 폴백을 다시 열지 않는다.** GAP-012의 결정은 그대로다. 새 사실 하나만 각주로 남길 만하다 —
  djust가 같은 모양의 SSE 폴백(긴 GET 하나 + 짧은 POST들)을 출시했다. 다중 워커에서 세션 상태를 어떻게
  다루는지는 확인하지 못했다.
- **작업 큐를 만들지 않는다.** 이미 여섯이 넘고 `steady-queue`는 Solid Queue를 그대로 옮겼다. 접점만 만든다(P4).
- **배포 도구를 만들지 않는다.** Kamal은 그대로 쓸 수 있다. 빠진 것은 레시피다(P5).
- **네이티브 위젯 렌더러를 만들지 않는다.** LiveView Native의 결말이 경고다.
- **Turbo의 서명 스트림 이름을 본뜨지 않는다.** 만료도 사용자 결합도 없다. 상태 없는 구독을 만들더라도
  `get_signer`와 live_session 결합 수준을 유지한다.
- **오프라인 상호작용을 약속하지 않는다.** 37signals도 "방문한 페이지 캐시"까지만 갔고 그것도 1년째 미출시다.
  서버가 진실인 모델에서 약속할 수 있는 것은 읽기 전용 셸과 끊김의 정직한 표시까지다.

---

## 8. 이 메모의 한계

- §5.2는 **브로드캐스트 한 건**의 측정이다. 지속 발행률, Windows, 다중 호스트는 재지 않았다.
- §5.3은 todo 예제 하나다. `force_render`를 쓰는 예제라 바이트 수는 최악에 가깝다. **횟수**가 요점이다.
  발행자가 같은 프로세스의 스레드였으므로 20건이 한꺼번에 도착했다. 다른 프로세스가 브로커로 발행하면
  도착이 흩어져 중간 화면들이 섞이지만 횟수는 같다.
- Rails 쪽은 **본문에 인용한 항목만** 1차 출처(8.0·8.1 발표, 7.2 릴리스 노트, actionpack CHANGELOG, 릴리스
  태그)로 확인했다. Active Record Tenanting, Lexxy(Action Text 새 에디터), Action Push Native 같은 주변
  움직임은 조사에서 빠졌다.
- Rails 8.2의 확정 내용과 Rails World 2026(2026-09-23~24)의 발표는 이 메모에 없다. 아직 열리지 않았다.
- 기준선의 channels-nats는 이 저장소 venv에 설치된 0.2.0이다. PyPI 최신(0.7.1)으로는 재지 않았다.
- Django 설문 수치는 JetBrains 페이지를 요약 도구로 읽은 값이다. 두 번 읽어 일관됨은 확인했으나 원표를
  직접 보지는 않았다.
- Django에서 Hotwire Native를 쓴 공개 사례를 찾지 못했다 — 부재를 증명한 것은 아니다.
- "Channels가 유지보수 모드"라는 것은 공식 표현이 아니라 릴리스 이력에서 내린 판단이다.
- 경쟁 프로젝트 중 djust·Tetra·Django LiveView는 문서를 직접 읽었고, 나머지의 아키텍처 분류는 저장소 설명
  수준이다.

---

## 부록 A. 시제품 레이어와 재현

**프로덕션 코드가 아니다.** 질문 하나에 수치를 대기 위해 쓴 것이고 저장소에 넣지 않았다. 아래 세 파일을
임의의 디렉터리에 두고 그 경로를 `PYTHONPATH`에 넣는다.

```bash
P=/path/to/proto ROOT=$PWD
export PYTHONPATH=$P:$ROOT:$ROOT/tests
uv run python $P/drive.py nats nats 4
PROTO_POLL_INTERVAL=0.1  uv run python $P/drive.py sqlite-100ms sqlite 4
PROTO_POLL_INTERVAL=0.02 uv run python $P/drive.py sqlite-20ms  sqlite 4
```

`bench/ws.py`의 `run()`은 모르는 레이어 이름에는 브로커를 띄우지 않고, 서버 프로세스는
`DJANGO_SETTINGS_MODULE`을 환경에서 받으므로 저장소를 고치지 않고 레이어를 끼울 수 있다.

회차별 수치(연결당 KB / join/s / 이벤트/s / 브로드캐스트 ms):

```text
nats          57.4/1921/12332/210.5   57.1/2129/12017/215.7   56.9/2149/12362/225.0   56.9/2173/12073/206.0
sqlite-100ms  53.5/1876/11996/234.6   51.7/2162/12188/269.1   52.2/1818/12166/266.6   51.8/2160/12187/240.8
sqlite-20ms   51.6/2110/12210/190.7   51.9/2028/12230/220.4   51.9/2055/12106/197.4   51.9/2070/12133/198.1
```

`proto_settings.py`:

```python
import os

from bench.settings import *  # noqa: F401,F403

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "proto_layer.SqlitePollingChannelLayer",
        "CONFIG": {
            "path": os.environ["PROTO_LAYER_DB"],
            "polling_interval": float(os.environ.get("PROTO_POLL_INTERVAL", "0.1")),
        },
    }
}
```

`drive.py`:

```python
import json, os, statistics, sys, tempfile

label, layer, rounds = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.environ["DJANGO_SETTINGS_MODULE"] = "proto_settings" if layer == "sqlite" else "bench.settings"
os.environ.setdefault("PROTO_LAYER_DB", os.path.join(tempfile.mkdtemp(), "cable.sqlite3"))
import django

django.setup()
from bench import ws

keys = ("per_connection_kb", "joins_per_s", "events_per_s", "broadcast_ms")
runs = []
for i in range(rounds):
    os.environ.pop("NATS_URL", None)  # run() starts a fresh broker each time
    if layer == "sqlite":
        os.environ["PROTO_LAYER_DB"] = os.path.join(tempfile.mkdtemp(), "cable.sqlite3")
    res = ws.run(connections=2000, item_counts=(5,), processes=4, layer=layer, server="daphne")["items_5"]
    runs.append({k: round(res[k], 1) for k in keys})
    print(label, "round", i + 1, runs[-1], flush=True)
print("MEDIAN", label, json.dumps({k: statistics.median(r[k] for r in runs) for k in keys}))
```

`proto_layer.py`:

```python
"""PROTOTYPE, not production code: a Solid Cable-style channel layer.

One SQLite file is the broker. A publish is an INSERT; every server process polls
for rows newer than the last one it saw and hands them to its own local channels.
Group membership is process-local, like channels-nats >= 0.2.0.
"""

import asyncio
import random
import sqlite3
import string
import threading
import time

import msgpack
from channels.layers import BaseChannelLayer

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    origin  TEXT NOT NULL,
    target  TEXT NOT NULL,
    payload BLOB NOT NULL,
    created REAL NOT NULL
);
"""


def _token(n):
    return "".join(random.choice(string.ascii_letters) for _ in range(n))


class SqlitePollingChannelLayer(BaseChannelLayer):
    extensions = ["groups"]

    def __init__(self, path, polling_interval=0.1, retention=60.0, expiry=60, capacity=1000, **kwargs):
        super().__init__(expiry=expiry, capacity=capacity, **kwargs)
        self.path = path
        self.polling_interval = float(polling_interval)
        self.retention = float(retention)
        self.client_prefix = _token(8)
        self.queues = {}
        self.groups = {}
        self._local = threading.local()
        self._poller = None
        self._loop = None
        self._last_id = 0
        conn = self._conn()
        for _ in range(200):
            try:
                conn.executescript(SCHEMA)
                break
            except sqlite3.OperationalError:
                time.sleep(0.025)

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
            conn.execute("PRAGMA busy_timeout=5000")
            # Switching the journal mode takes an exclusive lock and does NOT go through
            # the busy handler: several server processes starting at once get
            # "database is locked" straight away. WAL is a property of the file, so
            # only the first one really has to win; the rest retry until they see it.
            for _ in range(200):
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError:
                    time.sleep(0.025)
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _insert(self, origin, target, message):
        self._conn().execute(
            "INSERT INTO messages (origin, target, payload, created) VALUES (?, ?, ?, ?)",
            (origin, target, msgpack.packb(message, use_bin_type=True), time.time()),
        )

    def _ensure_poller(self):
        loop = asyncio.get_running_loop()
        if self._poller is None or self._poller.done() or self._loop is not loop:
            self._loop = loop
            self._last_id = self._conn().execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
            self._poller = loop.create_task(self._poll())

    async def _poll(self):
        next_trim = time.time() + 10
        while True:
            try:
                rows = self._conn().execute(
                    "SELECT id, origin, target, payload FROM messages WHERE id > ? ORDER BY id", (self._last_id,)
                ).fetchall()
                for row_id, origin, target, payload in rows:
                    self._last_id = row_id
                    if origin == self.client_prefix:
                        continue  # already delivered locally by the sender's fast path
                    self._dispatch(target, msgpack.unpackb(payload, raw=False))
                if time.time() >= next_trim:
                    next_trim = time.time() + 10
                    self._conn().execute("DELETE FROM messages WHERE created < ?", (time.time() - self.retention,))
            except sqlite3.OperationalError:
                pass  # busy: try again next tick
            await asyncio.sleep(self.polling_interval)

    def _dispatch(self, target, message):
        kind, _, name = target.partition(":")
        if kind == "g":
            for channel in list(self.groups.get(name, ())):
                self._put(channel, message)
        elif self.client_prefix in name:
            self._put(name, message)

    def _put(self, channel, message):
        queue = self.queues.get(channel)
        if queue is None:
            queue = self.queues[channel] = asyncio.Queue(maxsize=self.get_capacity(channel))
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass  # the spec lets a group send drop on a full channel

    async def new_channel(self, prefix="specific."):
        self._ensure_poller()
        return f"{prefix}.sqlite{self.client_prefix}!{_token(12)}"

    async def send(self, channel, message):
        self.require_valid_channel_name(channel)
        if self.client_prefix in channel and self._loop is asyncio.get_running_loop():
            self._put(channel, message)  # our own channel: no round trip through the file
        else:
            self._insert("", "c:" + channel, message)

    async def receive(self, channel):
        self.require_valid_channel_name(channel)
        self._ensure_poller()
        queue = self.queues.get(channel)
        if queue is None:
            queue = self.queues[channel] = asyncio.Queue(maxsize=self.get_capacity(channel))
        try:
            return await queue.get()
        finally:
            if queue.empty():
                self.queues.pop(channel, None)

    async def group_add(self, group, channel):
        self.require_valid_group_name(group)
        self._ensure_poller()
        self.groups.setdefault(group, set()).add(channel)

    async def group_discard(self, group, channel):
        members = self.groups.get(group)
        if members is not None:
            members.discard(channel)
            if not members:
                self.groups.pop(group, None)

    async def group_send(self, group, message):
        self.require_valid_group_name(group)
        on_our_loop = self._loop is asyncio.get_running_loop()
        if on_our_loop:
            for channel in list(self.groups.get(group, ())):
                self._put(channel, message)
        # Tagged with our prefix only when the local members were already served,
        # so our own poller skips the row.
        self._insert(self.client_prefix if on_our_loop else "", "g:" + group, message)

    async def close(self):
        if self._poller is not None:
            self._poller.cancel()
```

## 부록 B. 출처

Rails·Hotwire

```text
https://www.markround.com/blog/2026/03/05/returning-to-rails-in-2026/
https://news.hada.io/topic?id=27470
https://github.com/rails/solid_cable
https://github.com/rails/rails/pull/58703                      Action Cable fastlane — Solid Cable 대 Redis 실측
https://github.com/basecamp/fizzy/blob/main/config/cable.yml
https://github.com/basecamp/fizzy/blob/main/app/javascript/controllers/morph_guard_controller.js
https://github.com/basecamp/once-campfire                       Web Push × presence
https://dev.37signals.com/a-happier-happy-path-in-turbo-with-morphing/
https://github.com/hotwired/turbo/pull/1019                    page refresh + morphing
https://github.com/hotwired/turbo/pull/1561  /pull/1562         refresh 합치기, 보존 정책 분리
https://github.com/hotwired/turbo/issues/1083  /issues/1456     morph와 서드파티 위젯, "유지보수 모드냐"
https://github.com/hotwired/turbo-rails/blob/main/app/channels/turbo/streams/stream_name.rb
https://thoughtbot.com/blog/turbo-morphing-woes
https://radan.dev/articles/how-to-avoid-problem-with-turbo-morphing
https://native.hotwired.dev/overview/how-it-works
https://github.com/hotwired/hotwire-native-ios/blob/main/Source/Turbo/WebView/turbo.js
https://turbo-laravel.com/docs/hotwire-native/
https://github.com/liveview-native/live_view_native             archived
https://rubyonrails.org/world/2026/agenda
https://rubyonrails.org/2024/11/7/rails-8-no-paas-required       8.0 — Solid 3종의 37signals 실측
https://rubyonrails.org/2025/10/22/rails-8-1                     8.1 — continuations, structured events, bin/ci
https://guides.rubyonrails.org/7_2_release_notes.html            7.2 — allow_browser, PWA, devcontainer, brakeman
https://rubyonrails.org/2026/9/9/agents-on-rails-stage-2         에이전트 벤치마크
```

Django

```text
https://docs.djangoproject.com/en/6.1/releases/6.1/
https://www.djangoproject.com/weblog/2026/aug/10/annual-release-cycle/          DEP 20
https://docs.djangoproject.com/en/6.1/topics/tasks/
https://github.com/django/deps/blob/main/final/0014-background-workers.rst
https://theorangeone.net/posts/django-dot-tasks-exists/
https://www.loopwerk.io/articles/2026/django-tasks-review/
https://hernandis.me/blog/introducing-steady-queue/
https://github.com/django/new-features/issues/26   /114  /204  /108  /10
https://github.com/django/deps/pull/118                                          package extras
https://channels.readthedocs.io/en/latest/topics/channel_layers.html
https://github.com/danidee10/channels_postgres
https://lp.jetbrains.com/django-developer-survey-2026/
https://blog.jetbrains.com/pycharm/2026/08/the-state-of-django-2026-boring-is-so-back/
https://wsvincent.com/modern-django-deployments-in-2026/
https://www.coryzue.com/writing/kamal-django/
https://github.com/djust-org/djust
https://djangopackages.org/grids/g/live-views/
```

이 저장소

```text
docs/design/transport-abstraction.md      §2 fan-out은 Python 렌더, §5-1 SQLite·Windows 전제, §5-4 유실이 남기는 것
docs/design/longpolling-fallback.md       §5 폴백을 만들지 않는다
docs/design/backlog-review-2026-09-10.md  GAP-033·034가 0.3.0 이후 다시 물어야 할 것
#87  #88                                  이번 조사에서 고친 것
```
