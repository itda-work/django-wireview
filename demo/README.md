# 공개 데모 (wireview-demo.itda.work)

저장소의 예제 14개를 Django 프로젝트 하나로 묶어 Docker Compose로 띄우는 배포 단위다(#214).
`examples/`의 앱을 그대로 싣고, 공개 사이트에 필요한 것(게스트 계정, 입력 제한, 매일 초기화, 운영 설정)은
전부 이 디렉터리에서 더한다. 예제 코드는 고치지 않는다. 테스트 프로젝트(`tests/testproj/`)와는 따로다 —
테스트 픽스처를 운영에 싣지 않는다.

## 구성

```
인터넷 ─ Cloudflare(TLS) ─ cloudflared ─▶ web(Caddy :8080) ─▶ app(uvicorn ×2 :8000) ─▶ redis(채널 레이어)
                                           └ /static/ 직접 서빙            ├─▶ limits(한도 카운터)
                                                                          └ SQLite(/data 볼륨) ◀─ janitor
```

앞문은 셋 중 하나다 — 위의 Cloudflare Tunnel, [직접 공개 모드](#직접-공개-모드)(호스트의 Caddy가 80·443을 받아
web의 `:8081`로), 또는 [NAS 리버스 프록시 뒤](#nas-리버스-프록시-뒤)(NAS가 TLS를 끝내고 호스트의 Caddy가 LAN의 8091에서 받아
같은 `:8081`로). web은 두 포트를 함께 듣고 포트마다 자기 앞문의 헤더만 믿으므로, 터널과 다른 하나가 동시에 돌 수 있다.

| 서비스 | 이미지 | 하는 일 |
|------|------|------|
| `app` | `demo/Dockerfile`의 `app` | uvicorn 워커 2개(`WEB_CONCURRENCY`). 시작할 때마다 `migrate`와 `demo_reset` 뒤 서빙한다 |
| `web` | `demo/Dockerfile`의 `web` | Caddy. `/static/`을 이미지에 복사해 둔 파일로 서빙하고 나머지(WebSocket 포함)를 app으로 넘긴다. 루트가 아닌 사용자(UID 10002)로 컨테이너 포트 8080(터널)·8081(직접 모드)에서 듣고, 호스트의 `127.0.0.1`에만 열린다(8081은 `compose.direct.yaml`이 연다) |
| `redis` | `redis:7.4-alpine` | 채널 레이어. 128MB, `volatile-lru`. 영속하지 않는다 |
| `limits` | `redis:7.4-alpine` | 속도 제한 카운터만. 32MB, `noeviction`. 영속하지 않는다 |
| `janitor` | `app`과 같은 이미지 | 대시보드 펄스와 카운터 Redis 감시(20초), 오래된 게스트·만료 세션 정리와 보관 예산 다듬기·WAL 체크포인트·디스크 안전장치(10분), 매일 초기화(`DEMO_RESET_AT`)와 그 뒤 `VACUUM`. 라운드가 성공할 때마다 `/data/janitor.ok`를 갱신하고, 카운터 Redis가 80%를 넘으면 `/data/limits.full`을 둔다. 헬스체크가 둘을 본다 |
| `cloudflared` | `cloudflare/cloudflared` | 터널. `tunnel` 프로필이라 기본으로는 뜨지 않는다 |

고른 이유:

- **uvicorn.** [배포 가이드](../docs/DEPLOYMENT.md#asgi-서버)가 권하는 서버이고, SIGTERM에 `leaving()`을 끝까지 돌린다
  (daphne·Granian은 아니다 — 채팅의 presence 정리가 거기 있다). permessage-deflate는 끈다(메모리와 BREACH).
- **워커 2개.** 데모의 부하는 작다. 하나로도 되지만 둘이어야 채널 레이어가 프로세스를 건너는 경로가 매일 쓰인다.
  늘리려면 `.env`의 `WEB_CONCURRENCY`.
- **Redis 둘.** 워커가 둘 이상이면 InMemory 레이어는 브로드캐스트를 조용히 잃는다(`wireview.W006`). 속도 제한도 Redis에서
  센다 — 워커마다 따로 세면 한도가 워커 수만큼 늘고, 재연결하면 처음부터 센다. 카운터는 채널 레이어와 다른 Redis(`limits`)에 둔다:
  한 인스턴스의 `maxmemory` 정책은 DB마다 다르게 둘 수 없고, 채널 레이어가 메모리를 채울 때 `volatile-lru`가 카운터를 지우면
  그 한도가 풀린다. `limits`는 아무것도 지우지 않고(`noeviction`), 차면 쓰기를 거절한다 — 그때 한도는 통과시키지 않고 거절한다
  (HTTP 503, 소켓 1013). 어떤 키도 자기 창보다 오래 살지 않는다(한도 창 10초~1시간). 열린 소켓은 수가 아니라 임대로 센다 —
  소켓마다 30초에 한 번 갱신하고, 마지막 갱신에서 90초가 지나면 자리가 풀린다. 그래서 워커가 SIGKILL·OOM으로 죽어 반납을
  못 해도 90초 뒤 자리가 돌아오고, 소켓이 모두 닫힌 방문자의 키는 남지 않는다. 키 하나가 약 100바이트라 32MB면 키 25만 개쯤 —
  지난 한 시간의 방문자가 남긴 것이다. janitor가 메모리의 80%를 넘으면 ERROR를 남기고 unhealthy가 된다. 찬 상태는 창이 만료되면 풀린다.
- **SQLite(WAL).** 쓰기가 채팅 한 줄·투표 한 번 단위이고 호스트가 하나다. [배포 가이드의 단일 서버 구성](../docs/DEPLOYMENT.md#windows-단일-서버-uvicorn--n--caddy-nats-sqlite)처럼
  WAL·busy timeout·`IMMEDIATE`를 켠다. Postgres는 서비스 하나와 백업 절차 하나를 더할 뿐 이 부하에서 얻는 것이 없다.
  데이터는 매일 시드로 돌아가므로 잃어도 되는 데이터다.
- **Caddy가 정적 파일.** 운영(`DEBUG=False`)의 uvicorn은 `/static/`을 서빙하지 않는다. web 이미지는 app 이미지의 `collectstatic`
  결과를 복사해 만든다 — 명명 볼륨으로 나누면 다음 배포에도 첫 번들이 남는다([정적 파일](../docs/DEPLOYMENT.md#정적-파일)).
  `{% static %}`은 내용 해시가 붙은 이름(`ManifestStaticFilesStorage`)을 내고, Caddy는 그 파일에 `max-age=31536000, immutable`을
  단다 — Cloudflare가 엣지에 두므로 한국 방문자가 먼 엣지로 들어와도 정적 파일은 터널을 왕복하지 않는다. 해시 없는 원본과
  매니페스트는 `no-cache`, 페이지(HTML)는 세션·CSRF를 실으므로 `private, no-cache`다.

## 공개 데모의 안전장치

| 무엇 | 어떻게 | 어디 |
|------|------|------|
| 로그인 예제(chat_lv2~4, notifications) | 로그인 화면의 버튼이 "Sign in as guest" 하나다. 누르면 그 브라우저만의 게스트(`guest-swift-otter-042` 꼴)로 로그인한다. 사생활 창은 다른 게스트다 | `guests.py`, `apps.py`, `urls.py` |
| 게스트 수명 | 만든 지 `DEMO_GUEST_MAX_AGE_HOURS`(12시간)가 지나면 janitor가 지운다. 같은 주소에서 시간당 60개까지 | `guests.py`, `demo_janitor` |
| 데이터 초기화 | app 컨테이너가 시작할 때마다, 그리고 매일 `DEMO_RESET_AT`(04:00, `DEMO_TIME_ZONE`)에 예제 테이블을 비우고 게스트·세션을 지운 뒤 시드를 쓴다 | `seed.py`, `demo_reset`, `demo_janitor` |
| 입력 길이 | 폼·쿼리의 필드가 500자를 넘으면 400(평문, 필드 이름을 되돌려 보내지 않는다). WebSocket이 싣는 문자열은 join의 서명 상태를 빼고 모두 500자로 자른다 — 이벤트 인자, 훅 페이로드, `params_changed`·`navigated`의 params와 `uri`의 쿼리 값 | `limits.py` |
| WebSocket 예산 | 소켓마다 버킷: 모든 프레임(120, 초당 20), 서버가 읽지 않는 프레임(JSON 아님·모르는 명령, 5개 — 소비자에 넘기지 않는다), 이벤트(30, 초당 5), 컴포넌트마다 첫 join(60, 초당 5 — 페이지의 첫 join들. 컴포넌트는 서명을 검증한 상태의 클래스와 id로 가른다), 클릭 없이 렌더하는 것(같은 컴포넌트의 다시 join — 새로 서명한 상태여도, 검증되지 않는 상태의 join, 이벤트가 낳지 않은 `params_changed`, `navigated`: 20, 초당 2). 이벤트 바로 뒤의 `params_changed`는 그 이벤트가 바꾼 주소이므로 이벤트의 값에 든다 — 검색창(`.debounce.300`)에 쉬지 않고 쳐도 끊기지 않는다. 방문자마다 10초 창을 Redis에서 모든 소켓을 합쳐 센다: 프레임 600, 이벤트 100, join·렌더 300 — 재연결해도 창은 이어진다. 넘으면 4029로 닫는다(페이지는 백오프 뒤 다시 붙는다) | `limits.py` |
| 소켓 수 | 방문자마다 분당 새 소켓 300, 동시에 열린 소켓 40, 데모 전체 2000. 열린 소켓은 90초짜리 임대(30초마다 갱신)로 세므로 죽은 워커의 소켓은 90초 뒤 빠진다. 넘으면 핸드셰이크를 403으로 거절 | `limits.py` |
| 프레임 크기 | 페이지가 보내는 프레임이 UTF-8로 64KB를 넘으면 1009로 닫는다. uvicorn의 상한은 1MB, Caddy의 요청 본문 상한도 1MB | `limits.py`, `Dockerfile`, `Caddyfile` |
| HTTP 예산 | 방문자마다 분당 요청 300(정적 파일은 Caddy가 답하므로 빼고), 그중 POST 60. 넘으면 429(평문). compose의 헬스체크(`X-Real-IP` 없이 안에서 묻는 `/health/`·`/ready/`)는 세지 않는다 | `limits.py` |
| 저장 행 | 새 세션(쿠키 없는 GET이 만드는 것 — chat 방·quiz·rating)은 방문자마다 시간당 60개, 전체 20,000행까지. 게스트는 방문자마다 시간당 60, 전체 5,000. 넘으면 429·503. 로그인의 세션 키 교체는 세지 않는다 | `sessions.py`, `guests.py` |
| 보관 예산 | 방문자가 쓰는 테이블은 예산 안에서만 남는다: 채팅 방마다(네 단계 모두) 최근 메시지 `DEMO_KEEP_PER_ROOM`(200)개, 퀴즈마다 제출 200개, 사람마다 알림 50개, todo 100개 — 새 행이 저장될 때 더 오래된 것을 지운다. 그 테이블들과 방·멤버십·평점은 테이블마다 `DEMO_ROWS_MAX`(20,000)행까지이고, 넘는 새 행은 거절한다(HTTP는 503 평문, 소켓은 그 컴포넌트가 이벤트 전 상태로 다시 join해 입력이 남는다). janitor가 10분마다 한 번 더 다듬고, 지운 행이 남긴 빈 페이지가 64MB를 넘으면 `VACUUM`으로 디스크에 돌려준다 | `retention.py`, `apps.py`, `demo_janitor` |
| 디스크 | janitor가 10분마다 WAL을 체크포인트한다. 데이터베이스 파일이 `DEMO_DB_MAX_MB`(1GB)를 넘으면 그 자리에서 초기화하고 `VACUUM`한다 — **마지막 수단**이다. 보관 예산이 꽉 차도 200MB 남짓이라 여기까지 오면 예산 밖의 무언가가 자란 것이고, ERROR로 남긴다. 매일 초기화 뒤에도 `VACUUM` | `demo_janitor` |
| 업로드 | 업로드하는 예제가 없으므로 업로드 엔드포인트(`wireview.urls`)를 싣지 않는다 | `urls.py` |
| 운영 설정 | `DEBUG=False`, 비밀 키는 환경 변수, HTTPS 리다이렉트·보안 쿠키·HSTS(이 호스트만), `manage.py check --deploy` 통과 | `settings.py`, `tests/test_demo_site.py` |
| 오류 응답 | 한도의 답은 모두 `text/plain`이고 방문자가 보낸 것을 되풀이하지 않는다. `/ready/`는 밖에 `{"status": "unready"}`만 답하고 이유는 로그(`demo.ready`)에 남긴다. 4xx마다 `django.request` 줄을 남기지 않는다(한도가 거절한 것은 `demo.limits` INFO로 한 줄) | `limits.py`, `views.py`, `settings.py` |

주소마다의 한도는 Cloudflare가 준 `CF-Connecting-IP`(직접 모드에서는 호스트 Caddy가 본 TCP 상대 주소)를 Caddy가 `X-Real-IP`로 넘긴 값으로 센다. IPv6 주소는 /64(가입자 하나가
받는 블록)로 센다. 행사장 Wi-Fi나 통신사 NAT 뒤의 사람들은 한 주소이므로 한도는 스크립트를 막는 정도로 넉넉하게 두었다.
모든 한도는 `.env`로 바꾼다(`.env.example`, 기본값은 `settings.py`의 `DEMO_LIMITS`).

web의 Caddy는 사설 주소에서 온 헤더를 믿는다 — `:8080`은 `CF-Connecting-IP`(cloudflared가 compose 네트워크 안에 있기 때문),
`:8081`은 `X-Real-IP`(호스트의 Caddy가 colima 포워더를 거쳐 오기 때문). 포트마다 하나만 읽으므로 터널로 들어온 방문자의
`X-Real-IP`나 호스트 Caddy로 들어온 방문자의 `CF-Connecting-IP`는 아무도 가리키지 않는다. 그래서 호스트의
`127.0.0.1:8080`·`:8081`에 닿을 수 있는 사람은 그 헤더로 다른 방문자를 흉내 낼 수 있다 — `demo/abuse.py`가 그것으로 방문자를 나눈다.
두 포트는 호스트 밖으로 열지 않는다. 공개 연결 전에 실제 터널 경로에서 `CF-Connecting-IP`·`X-Forwarded-Proto`가 기대한 값인지,
다른 포트포워딩이나 프록시가 없는지 확인한다.

침묵한 검사는 둘이다: `security.W005`(HSTS `includeSubDomains`), `security.W021`(preload). itda.work의 다른 이름에 HTTPS를
강제하는 것은 이 데모가 정할 일이 아니다.

## 정할 것 (기본값으로 두었다)

아래는 운영자가 정할 일이라 바꾸지 않고 기본값으로 둔다. 모두 설정 하나로 바뀐다.

| 무엇 | 지금 | 바꾸는 곳 |
|------|------|------|
| notifications 예제의 수신자 목록 | 예제 그대로 모든 사용자(지금 있는 게스트들)가 보이고, 그들에게 알림을 보낼 수 있다. 게스트 이름은 무작위(`guest-swift-otter-042`)이고 시드는 계정을 만들지 않으므로 보이는 것은 게스트뿐이다 — 이대로 두기로 했다(#214) | 예제 쪽 변경이 필요하다 |
| 익명 채팅(chat 1단계)의 이름 | 누구나 아무 이름(`?username=`, 500자 이하)으로 쓴다. 사칭·욕설을 거르는 모더레이션은 없다. 매일 초기화가 유일한 정리 | 예제 쪽 변경이 필요하다 |
| 한도 값 | 위 표의 값 | `.env`의 `DEMO_*` |
| 초기화 | 매일 04:00(Asia/Seoul)과 app이 시작할 때마다(배포 = 초기화) | `DEMO_RESET_AT`, `DEMO_TIME_ZONE`, `Dockerfile`의 `CMD` |
| HSTS | 30일, 이 호스트만 | `DJANGO_HSTS_SECONDS` |

화면에 보이는 사람 이름은 모두 지어낸 것이다: 목록 카드의 화면(#213 녹화)에 나오는 지은·민수·하늘, 대시보드 활동의 이름
(`seed.py`의 `ACTIVITY_PEOPLE`, 게스트 이름과 같은 꼴), 게스트 이름. 목록 페이지 아래에도 한 줄로 적었다. 검색 예제의 책과
저자는 실제 책이다.

## 라이브러리 내부에 기대는 곳

`limits.py`의 `join_identity`는 join의 서명 상태를 검증해 컴포넌트(클래스와 id)를 읽으려고 wireview의 내부
`wireview.core.state.unsign_envelope`와 `Component._resolve`를 부른다. 공개 API에 그런 호출이 없기 때문이다. 라이브러리가 이
둘을 바꾸면 `tests/test_demo_site.py`의 `test_a_join_names_its_component_by_class_and_id`가 실패한다 — 그때 이곳을 고친다.

## 2021 머신에 올리기

대상은 `allieus-macbook-2021`(arm64 macOS, colima)이다. 이미지는 그 머신에서 빌드한다.

```bash
# 1. 도커 런타임 (이미 떠 있으면 건너뛴다). 자동 시작은 colima 스킬 참고
colima start --cpu 4 --memory 6

# 2. 코드
git clone https://github.com/itda-work/django-wireview.git ~/srv/django-wireview
cd ~/srv/django-wireview/demo

# 3. 설정: 비밀 키를 채운다
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'   # DJANGO_SECRET_KEY 에 넣는다

# 4. 빌드와 기동 (터널 없이)
docker compose up -d --build
docker compose ps        # app·web·redis·limits·janitor가 healthy
```

서비스가 `restart: unless-stopped`이므로 colima가 다시 뜨면 데모도 다시 뜬다. colima 자체가 로그인 때
뜨게 하려면 `brew services start colima`.

### 업데이트

```bash
cd ~/srv/django-wireview && git pull
cd demo && docker compose up -d --build
```

정적 파일은 바뀐 것만 새 해시 이름이 되므로 엣지 캐시를 비울 필요가 없다. app이 새로 시작하므로 데이터는 시드로 돌아간다. 한도 카운터(`limits`)는 그대로 남는다. 열려 있던 페이지는 재연결해 다시 join한다(서명 키가 같으면 새로고침 없이).
`DJANGO_SECRET_KEY`를 바꾸면 열려 있던 페이지가 모두 새로고침된다.

### 로그

```bash
docker compose logs -f app web        # 앱과 접근 로그(Caddy)
docker compose logs --since 1h janitor
```

로그는 json-file 드라이버가 컨테이너마다 10MB × 5개로 돌린다. 앱은 WARNING 이상과 `demo.*` INFO(소켓을 닫은 이유,
janitor의 정리)를 남긴다.

### 백업과 초기화

데이터는 매일 지워지므로 백업할 것이 거의 없다. 지우기 전에 남기고 싶으면:

```bash
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/data/demo.sqlite3'); s.backup(sqlite3.connect('/data/backup.sqlite3'))"
docker compose cp app:/data/backup.sqlite3 ./backup-$(date +%F).sqlite3
```

지금 바로 시드로 돌리려면 `docker compose exec app python demo/manage.py demo_reset`. 데이터베이스 파일까지 새로
만들려면 `docker compose down -v`(볼륨 삭제) 뒤 `up -d`.

### amd64

저장소 루트(`~/srv/django-wireview`)에서 — `-f`의 경로와 마지막 인자(빌드 컨텍스트 `.`)가 모두 루트 기준이다:

```bash
cd ~/srv/django-wireview
docker buildx build --platform linux/amd64 -f demo/Dockerfile --target app -t wireview-demo-app:amd64 --load .
docker buildx build --platform linux/amd64 -f demo/Dockerfile --target web -t wireview-demo-web:amd64 --load .
```

## Cloudflare Tunnel 연결

포트를 열지 않는다. cloudflared가 바깥으로 연결을 맺고 Cloudflare가 그 연결로 요청을 들여보낸다. 아래는 절차이고,
실행은 사용자가 확인한 뒤에 한다.

1. Cloudflare 대시보드 → Zero Trust → Networks → Tunnels → **Create a tunnel** → `Cloudflared`.
   이름은 `wireview-demo`.
2. 설치 화면의 명령에서 **토큰만** 복사해(`eyJ…`) `.env`의 `CLOUDFLARE_TUNNEL_TOKEN`에 넣는다. cloudflared는 이미지로
   돌리므로 그 화면의 설치 명령은 실행하지 않는다.
3. **Public Hostname** 추가: subdomain `wireview-demo`, domain `itda.work`, service `HTTP` → `web:8080`.
   cloudflared 컨테이너가 compose 네트워크 안에 있으므로 서비스 이름으로 닿는다.
   WebSocket은 Cloudflare에서 기본으로 켜져 있다(Network → WebSockets). 끄지 않는다 — wireview에는 HTTP 폴백이 없다.
4. 기동:

   ```bash
   docker compose --profile tunnel up -d
   docker compose logs cloudflared     # "Registered tunnel connection" 네 줄
   ```

5. 확인: `uv run python demo/smoke.py https://wireview-demo.itda.work --out /tmp/demo-smoke`(저장소 체크아웃에서).

DNS는 3번이 `wireview-demo.itda.work`의 CNAME을 만든다. 끊으려면 `docker compose stop cloudflared`,
되돌리려면 대시보드에서 public hostname을 지운다. 직접 공개 모드로 옮길 때는 이렇게 지우지 않는다("터널에서 옮기기").

Cloudflare 쪽에 둘 만한 것(선택): 이 호스트에 Rate Limiting 규칙(예: 분당 요청 600), Bot Fight Mode. 앱의 한도(HTTP 예산,
세션·게스트 행, 소켓 수)가 Cloudflare 없이도 서므로 필수는 아니다. 다만 앱의 한도는 요청이 앱에 닿은 뒤에 세므로, 많은 주소에서
오는 분산된 요청은 Cloudflare 쪽에서만 막힌다.

## 직접 공개 모드

Cloudflare를 거치지 않고 집 회선(2021 머신)이 80·443을 직접 받는다. 주 방문자가 한국인데 터널 경로에서는 한국 방문자가
Cloudflare의 먼 엣지(시애틀)로 들어가 왕복이 0.8~1초를 넘었다. 직접이면 국내 왕복은 수십 ms다.

```
인터넷 ─ 공유기(TCP 80·443, UDP 443) ─▶ macOS 호스트의 Caddy(TLS·Let's Encrypt·HTTP/3, :80 :443)
                                          └▶ 127.0.0.1:8081 ─(colima 포워더)─▶ web(Caddy :8081) ─▶ app …
```

### 왜 TLS를 맡는 Caddy가 컨테이너가 아니라 호스트에 있나

colima는 publish한 포트를 lima의 포트 포워더(사용자 공간 프록시)로 컨테이너에 넘기고, 그 과정에서 **방문자의 주소가 지워진다.**
#214에서 이 머신(colima 0.10.3, vz, grpc 포워더)으로 잰 값:

| 들어온 곳 | 컨테이너가 본 주소 |
|------|------|
| `127.0.0.1:18899` → 컨테이너(포워더) | `172.17.0.1` |
| LAN `192.168.10.37:18899` → 컨테이너(포워더) | `172.17.0.1` |
| LAN `192.168.10.37:80`·`:443` → 컨테이너(포워더) | `172.17.0.1` — 포워더는 1024 아래 포트도 잡는다(`limactl`이 `*:80`·`*:443` LISTEN) |
| LAN `192.168.10.37:443` → **호스트의 Caddy** → web | `192.168.10.37` |

컨테이너 Caddy가 80·443을 받으면 인터넷 전체가 방문자 하나가 된다 — 방문자당 소켓 40개, 분당 요청 300이 데모 전체의 상한이
된다. 호스트의 Caddy만 TCP 상대 주소를 본다. VM이 LAN 주소를 따로 갖는 colima bridged 모드(VM을 새로 만들어야 한다)나 pf `rdr`로
VM 주소에 넘기는 길도 주소를 지키지만, 둘 다 root 설정이 들고 이 머신에서 검증하지 못했다.

루트가 아니다: macOS(10.14 이후)는 일반 사용자에게 1024 아래 포트를 허용한다(이 머신에서 `0.0.0.0:80`·`:443` 바인드 확인).
Homebrew의 서비스는 로그인한 사용자의 LaunchAgent로 돈다. 컨테이너의 Caddy는 그대로 UID 10002, 포트 8080·8081이다.

### 방문자 주소

호스트의 Caddy(`Caddyfile.direct`)는 아무도 믿지 않는다(`trusted_proxies` 없음): 방문자는 TCP 상대 주소다. `X-Real-IP`를 그 주소로
덮어쓰고, `CF-Connecting-IP`·`Forwarded`·`True-Client-IP`는 지우고, `X-Forwarded-For`·`X-Forwarded-Proto`는 Caddy가 새로 쓴다. web의 `:8081`은 `X-Real-IP`만
읽는다. 방문자가 보낸 `CF-Connecting-IP`·`X-Real-IP`·`X-Forwarded-For`·`True-Client-IP`는 한도를 바꾸지 못한다:

- `tests/test_demo_site.py`가 두 Caddyfile의 규칙과 compose의 포트(둘 다 `127.0.0.1`)를, caddy가 있으면 caddy가 읽은 설정(`caddy adapt`)까지 본다.
- `demo/smoke.py`는 https 주소에서 헤더마다 다른 방문자를 적은 소켓 41개를 연다 — 41번째가 403이어야 한다(한 방문자).
  헤더를 믿는 `http://127.0.0.1:8080`에 같은 검사를 돌리면 41개가 다 들어간다(검사가 실패를 볼 수 있다는 대조).

노출되는 것: 집의 공인 IP(DNS가 그대로 알려 준다), 호스트 Caddy의 80·443. Cloudflare의 DDoS 보호·Bot Fight·Rate Limiting은 없다 —
막는 것은 앱의 한도뿐이고, 많은 주소에서 오는 분산된 요청이나 회선을 채우는 트래픽은 막지 못한다. 공유기에는 80·443(UDP 443) 말고
아무것도 넘기지 않는다.

### 2021 머신에서 켜기

```bash
# 1. 스택: web에 8081을 연다(터널이 돌고 있어도 된다 — 8080은 그대로)
cd ~/srv/django-wireview/demo
docker compose -f compose.yaml -f compose.direct.yaml up -d --build
lsof -nP -iTCP:8081 -sTCP:LISTEN      # 127.0.0.1:8081 이어야 한다 (*:8081 이면 멈춘다)

# 2. 호스트의 Caddy: Homebrew 서비스의 설정이 저장소의 파일을 가져온다
brew install caddy
echo "import $HOME/srv/django-wireview/demo/Caddyfile.direct" > /opt/homebrew/etc/Caddyfile
caddy validate --config /opt/homebrew/etc/Caddyfile
brew services start caddy             # 로그인 때 뜬다. 로그 /opt/homebrew/var/log/caddy.log
```

인증서와 ACME 계정은 `/opt/homebrew/var/lib/caddy`에 남아 재시작해도 다시 발급하지 않는다(Let's Encrypt 발급 한도). 이름은
`Caddyfile.direct`의 기본값(`wireview-demo.itda.work`)이다. macOS 방화벽이 켜져 있으면 caddy의 수신을 허용한다
(`/usr/libexec/ApplicationFirewall/socketfilterfw --add /opt/homebrew/opt/caddy/bin/caddy --unblockapp`, sudo).

공유기: 2021 머신(`192.168.10.197`, DHCP 예약으로 고정)으로 TCP 80, TCP 443, UDP 443(HTTP/3)을 넘긴다. 80은 Let's Encrypt의
HTTP-01 검증과 https 리다이렉트에 쓰인다.

### 터널에서 옮기기

인증서는 이름이 이 머신을 가리킨 뒤에야 받을 수 있다(HTTP-01). 그래서 순서는 "직접 모드 기동 → DNS 레코드 교체 → 인증서 확인 →
캐시 대기 → 터널 정리"다. **DNS 레코드 교체와 터널의 라우팅 정리는 따로 한다.** 라우팅(호스트 → `web:8080`)은 옛 DNS 응답을
캐시한 방문자를 받을 수 있는 유일한 길이므로 캐시가 다 지난 뒤에 지운다.

예상 중단 구간 — 보장하지 않는다:

- 새 응답(A)을 받은 방문자: DNS를 바꾼 뒤 첫 인증서가 발급될 때까지 인증서 오류나 연결 실패. 보통 수십 초지만 Let's Encrypt가
  늦거나 80이 밖에서 닿지 않으면 그만큼 길어진다.
- 옛 응답(Cloudflare 주소)을 캐시한 방문자: 옛 TTL(프록시 레코드는 5분) 동안 Cloudflare 엣지로 간다. 레코드가 더는 프록시가 아닐 때
  엣지가 그 이름을 여전히 터널로 보내는지는 확인하지 않았다 — 그 5분도 실패할 수 있다고 본다. 터널을 살려 두는 것은 보내 줄 때
  받기 위해서다. web은 두 앞문을 함께 받으므로 어느 쪽으로 와도 한도가 맞다.

방문자가 적은 시간(예: 매일 초기화 직후)에 한다.

1. 위의 "2021 머신에서 켜기"를 한다. caddy는 이름이 아직 터널을 가리키므로 발급에 실패하고 재시도한다(로그의 `challenge failed`는 정상).
2. 밖(휴대폰 테더링에 붙인 노트북 등)에서 `nc -vz 121.159.177.248 80`과 `443`이 열리는지 본다 — 공유기 포트포워딩 확인.
   같은 LAN 안에서는 공유기의 NAT 루프백 여부에 따라 결과가 다르므로 밖에서 본다.
3. **DNS 레코드만** 바꾼다: Cloudflare 대시보드 → itda.work → DNS → Records의 `wireview-demo` CNAME(`<터널 UUID>.cfargotunnel.com`,
   주황 구름)을 편집해 **A** `121.159.177.248`, Proxy status **DNS only**(회색 구름), TTL 1분으로 바꾼다. 편집 화면이 유형을 바꾸게
   하지 않으면 **이 화면에서** CNAME만 지우고 곧바로 A를 만든다. AAAA 레코드는 두지 않는다(이 회선의 IPv6는 열지 않았다).
   터널이 어떻게 만들어졌는지에 따라 손대지 않을 곳이 다르다:
   - **(a) 대시보드의 Public Hostname으로 만든 터널**(위 "Cloudflare Tunnel 연결"): Zero Trust → Tunnels → Public Hostname에서
     **지우지 않는다.** 거기서 지우면 DNS 레코드와 함께 터널의 호스트 규칙(`wireview-demo.itda.work` → `http://web:8080`)도 사라져,
     cloudflared가 돌고 있어도 옛 주소로 온 요청이 catch-all(`http_status:404`)에 떨어진다.
   - **(b) CLI로 만든 터널**(2021 머신의 지금 터널: `cloudflared tunnel create` + `cloudflared tunnel route dns`, 그 머신의 compose
     override가 cloudflared를 `--url http://web:8080`으로 띄운다): 호스트 규칙이 없고 모든 호스트를 web으로 보낸다. CNAME을 A로
     바꾸기만 하면 된다. `route dns`가 만든 것은 그 CNAME 하나다.
4. `brew services restart caddy` — 백오프를 기다리지 않고 바로 발급한다. 로그에 `certificate obtained successfully`.
5. 직접 HTTPS를 확인한다. 밖에서 `uv run python demo/smoke.py https://wireview-demo.itda.work --out /tmp/demo-smoke`.
   응답 헤더에 `cf-ray`가 없어야 직접이다(`dig +short wireview-demo.itda.work`가 `121.159.177.248`인 리졸버에서 본다).
   Caddy의 접근 로그(`/opt/homebrew/var/log/caddy.log`)의 `remote_ip`가 방문자들의 공인 주소여야 한다 — 모두 같은 사설 주소면 멈추고
   아래 "되돌리기"를 한다.
6. 캐시를 기다린다: 3번부터 옛 TTL(5분)에 여유를 더해 10분. 그동안 `docker compose logs -f cloudflared`에 요청이 지나가는지 보면
   옛 경로가 아직 쓰이는지 알 수 있다.
7. 그 뒤에 터널을 정리한다.
   - (a): `docker compose stop cloudflared`, 그다음 Public Hostname을 지운다(DNS는 이미 A이므로 지울 CNAME이 없다 — 대시보드가 A를 지우겠다고
     하면 멈춘다).
   - (b): `docker compose stop cloudflared`. 터널 자체(`cloudflared tunnel delete`)는 되돌릴 일이 없다고 확신할 때 지운다.

   이후의 `up`에는 `--profile tunnel`을 빼고 `-f compose.direct.yaml`을 붙인다.

되돌리기도 같은 원칙이다 — 새 길을 먼저 열고, 레코드를 바꾸고, 캐시가 지난 뒤에 옛 길을 닫는다:

1. 터널을 띄운다: `docker compose --profile tunnel up -d`, 로그의 "Registered tunnel connection". (a)에서 Public Hostname을 이미
   지웠다면 다시 만들어야 하는데, 대시보드는 같은 이름의 레코드(A)가 있으면 만들기를 거절할 수 있다. 그때는 A를 지우고 곧바로
   Public Hostname을 만든다 — 그것이 CNAME을 만들므로 2번이 함께 일어난다.
2. DNS → Records에서 A를 CNAME `<터널 UUID>.cfargotunnel.com`, **Proxied**(주황 구름)로 바꾼다. (b)는
   `cloudflared tunnel route dns --overwrite-dns <터널 이름> wireview-demo.itda.work`도 같다.
3. A의 TTL(1분)에 여유를 더해 5분 동안 호스트 Caddy를 그대로 둔다 — 옛 A를 캐시한 방문자가 아직 직접 들어온다.
   `cf-ray`가 붙는지 확인한 뒤 `brew services stop caddy`. web의 8081은 남아 있어도 해가 없다(루프백).

### 동적 IP

가정용 회선의 공인 IP는 통신사가 바꿀 수 있다. 바뀌면 A 레코드가 남의 주소를 가리킨다. `compose.direct.yaml`의 `ddns`
(프로필 `ddns`)가 5분마다 `https://1.1.1.1/cdn-cgi/trace`로 이 회선의 주소를 묻고, 다르면 레코드의 주소만 바꾼다(`demo/ddns.py`).
레코드를 새로 만들지 않고, 프록시를 켜거나 끄지 않으며, CNAME(터널의 것)은 건드리지 않는다 — "터널에서 옮기기" 3번에서 손으로 바꾼 A 레코드가 정확히
하나일 때만 움직인다.

토큰은 `.env`가 아니라 `ddns.env`에 둔다 — `.env`는 app과 janitor에도 가므로, DNS를 고칠 수 있는 토큰이 웹에 닿는
프로세스의 환경에 있지 않게 ddns 서비스만 `ddns.env`를 읽는다(gitignore).

```bash
cp ddns.env.example ddns.env   # CLOUDFLARE_ZONE_ID(itda.work 개요 화면 오른쪽), CLOUDFLARE_DNS_TOKEN
#   토큰: My Profile → API Tokens → Create Token → "Edit zone DNS" 템플릿,
#   Zone Resources는 Include → Specific zone → itda.work 하나만
docker compose -f compose.yaml -f compose.direct.yaml --profile ddns run --rm ddns python demo/ddns.py --once   # 토큰 시험
docker compose -f compose.yaml -f compose.direct.yaml --profile ddns up -d
docker compose -f compose.yaml -f compose.direct.yaml logs ddns    # 바뀔 때만 한 줄
```

IP가 바뀌면 그 순간부터 갱신(최대 5분)과 TTL(1분)만큼 끊긴다. 인증서는 이름에 묶이므로 다시 발급하지 않는다.

### 로컬에서 직접 모드 확인

Let's Encrypt는 공개 DNS가 필요하므로 로컬에서는 Caddy 자신의 CA(`DEMO_TLS=internal`)로 같은 경로를 돈다. `.env`는
`DEMO_HOST=localhost`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost,http://127.0.0.1:8080`.

```bash
cd demo && docker compose -f compose.yaml -f compose.direct.yaml up -d --build --wait && cd ..
DEMO_HOST=localhost DEMO_TLS=internal caddy run --config demo/Caddyfile.direct   # 다른 창에서, 끝나면 Ctrl-C
uv run python demo/smoke.py https://localhost --insecure --out /tmp/demo-smoke-direct
cd demo && docker compose -f compose.yaml -f compose.direct.yaml down
```

`--insecure`는 아무도 보증하지 않는 인증서를 받는다. 스모크는 wss로 라이브가 되는지와 위조 헤더 검사(41번째 소켓 403)까지 본다.

## NAS 리버스 프록시 뒤

공유기의 80·443이 이미 Synology NAS(`192.168.10.102`, DSM)로 가서 다른 서비스가 쓰고 있을 때의 앞문이다. NAS의 리버스
프록시(nginx)가 `wireview-demo.itda.work`의 TLS(DSM의 Let's Encrypt 인증서)를 끝내고, 2021 머신의 Caddy에 LAN의 평문 HTTP로
넘긴다. 직접 공개 모드처럼 Cloudflare를 거치지 않는다. HTTP/3는 없다(DSM이 하지 않는다).

```
인터넷 ─ 공유기(TCP 80·443) ─▶ NAS 192.168.10.102(DSM 리버스 프록시: TLS, X-Real-IP $remote_addr)
                                └▶ 192.168.10.197:8091 ─ 2021 머신의 Caddy(Caddyfile.proxy)
                                     └▶ 127.0.0.1:8081 ─(colima 포워더)─▶ web(Caddy :8081) ─▶ app …
```

호스트의 Caddy를 두는 이유는 직접 공개 모드와 같다 — colima의 포워더를 지나면 방문자가 모두 VM의 게이트웨이 하나가 된다.
스택은 `compose.direct.yaml` 그대로다(web의 8081을 호스트 루프백에).

### 방문자 주소 (NAS 뒤)

방문자의 주소를 아는 것은 NAS뿐이다. DSM이 자기 TCP 상대 주소(`$remote_addr`)를 `X-Real-IP`로 적고, 호스트의 Caddy
(`Caddyfile.proxy`)는 그 헤더를 **NAS 주소 하나에서 온 것만** 믿는다(`trusted_proxies static <NAS>`, `client_ip_headers X-Real-IP`).

- NAS가 아닌 주소에서 8091로 온 요청은 무엇을 읽기 전에 **403**이다(`remote_ip` — TCP 상대 주소로 가른다). LAN의 다른 기기가
  직접 붙어 `X-Real-IP`를 적어 남이 되거나 한도를 피하지 못한다.
- web으로는 `X-Real-IP`를 그 방문자로 덮어써 넘기고 `CF-Connecting-IP`·`Forwarded`·`True-Client-IP`는 지운다. web의 `:8081`은
  `X-Real-IP`만 읽는다.
- `X-Forwarded-Proto`는 NAS가 보낸 그대로 지나간다(`https` — DSM의 사용자 정의 헤더). Django의 `SECURE_PROXY_SSL_HEADER`가
  그것을 보고 요청을 https로 다룬다. NAS 밖의 요청은 어차피 403이다.
- NAS 주소에는 기본값이 없다. Homebrew 서비스는 셸의 환경 변수를 넘기지 않으므로 주소는 `import`의 인자로 준다. 인자 없이
  가져오면 IP 자리에 `{args[0]}`이 남아 Caddy가 뜨지 않는다.

믿는 범위는 NAS 전체다: NAS 위에서 도는 다른 프로그램(컨테이너 포함)도 8091에 `X-Real-IP`를 적을 수 있다. 같은 이유로 NAS의
주소는 DHCP 예약이나 고정 주소여야 한다 — 바뀌면 모든 요청이 403이고, 다른 기기가 그 주소를 받으면 그 기기를 믿는다.

`tests/test_demo_site.py`가 규칙(믿는 주소 하나, 그 밖 403, 헤더 덮어쓰기·삭제)과 caddy가 읽은 설정을 보고, caddy가 있으면
실제 프로세스로 NAS 주소에서 온 `X-Real-IP`·`X-Forwarded-Proto`가 지나가고 위조 헤더가 지워지는지, 다른 주소는 403인지 본다.
#214에서는 로컬에 DSM 흉내(TLS를 끝내고 `X-Real-IP {remote_host}`·`X-Forwarded-Proto https`를 적는 Caddy)를 앞에 두고
`demo/smoke.py https://localhost --insecure`가 모두 통과했다 — 위조 헤더를 실은 소켓 41개 중 41번째 403(한 방문자). LAN 주소에서
8091로 직접 보낸 요청은 위조 `X-Real-IP`와 함께 403이었다.

### DSM 설정

인증서(Let's Encrypt의 HTTP-01은 이름이 이 회선을 가리켜야 받을 수 있다 — 아래 "옮기기"의 순서를 따른다):

1. 제어판 → 보안 → 인증서 → 추가 → 새 인증서 추가 → Let's Encrypt에서 인증서 받기.
   도메인 이름 `wireview-demo.itda.work`, 이메일, 주체 대체 이름은 비운다. 80이 NAS로 넘어가 있어야 한다(이미 그렇다).
2. 같은 화면의 설정 → 서비스 목록에서 리버스 프록시 항목 `wireview-demo.itda.work`(아래에서 만든 규칙)의 인증서를 1번의 것으로
   고른다. 규칙을 먼저 만들어야 목록에 나온다.

리버스 프록시: 제어판 → 로그인 포털 → 고급 → 리버스 프록시 → 만들기.

| 탭 | 항목 | 값 |
|------|------|------|
| 일반 | 리버스 프록시 이름 | `wireview-demo` |
| 일반 | 소스: 프로토콜 / 호스트 이름 / 포트 | `HTTPS` / `wireview-demo.itda.work` / `443` |
| 일반 | 소스: HSTS 활성화 | 끈다 — Django가 이미 보낸다(`max-age` 30일) |
| 일반 | 대상: 프로토콜 / 호스트 이름 / 포트 | `HTTP` / `192.168.10.197` / `8091` |
| 사용자 정의 헤더 | 만들기 → WebSocket | `Upgrade` `$http_upgrade`, `Connection` `$connection_upgrade`가 들어간다 |
| 사용자 정의 헤더 | 만들기(직접) | `X-Real-IP` `$remote_addr` |
| 사용자 정의 헤더 | 만들기(직접) | `X-Forwarded-Proto` `https` |
| 고급 설정 | 프록시 HTTP 버전 | `HTTP 1.1` (WebSocket에 필요하다) |
| 고급 설정 | 프록시 연결 시간 제한 | `60` (그대로) |
| 고급 설정 | 프록시 전송 시간 제한 / 프록시 읽기 시간 제한 | `3600` / `3600` |

DSM의 기본 템플릿이 `X-Real-IP`·`X-Forwarded-Proto`를 이미 적는 버전이면 같은 값이 두 줄이 된다. 해가 없다 — Caddy는 첫
값을 읽고 web에는 하나로 덮어써 넘기며, Django는 쉼표로 이은 `X-Forwarded-Proto`의 첫 값을 본다.

시간 제한: DSM의 프록시 읽기·전송 시간 제한(기본 60초)은 그동안 오간 것이 없는 WebSocket을 끊는다. 앱의 uvicorn은 20초마다
WebSocket ping을 보내고 브라우저가 pong으로 답하므로 기본값으로도 끊기지 않지만, 그 ping에 기대지 않게 3600초로 늘린다.
끊겨도 페이지는 다시 붙고 상태를 되살린다. 고급 설정 탭이 없는 DSM이면 기본값으로 둔다(ping이 지킨다).

### 2021 머신에서 켜기 (NAS 뒤)

```bash
# 1. 스택: 직접 공개 모드와 같다
cd ~/srv/django-wireview/demo
docker compose -f compose.yaml -f compose.direct.yaml up -d --build
lsof -nP -iTCP:8081 -sTCP:LISTEN      # 127.0.0.1:8081 이어야 한다

# 2. 호스트의 Caddy: 인자가 NAS의 주소다
brew install caddy
echo "import $HOME/srv/django-wireview/demo/Caddyfile.proxy 192.168.10.102" > /opt/homebrew/etc/Caddyfile
caddy validate --config /opt/homebrew/etc/Caddyfile
brew services start caddy             # 로그인 때 뜬다. 로그 /opt/homebrew/var/log/caddy.log
lsof -nP -iTCP:8091 -sTCP:LISTEN      # caddy, *:8091
```

macOS 방화벽이 켜져 있으면 caddy의 수신을 허용한다(직접 공개 모드와 같은 명령). 포트는 `Caddyfile.proxy`의 기본값 8091을
그대로 쓴다 — Homebrew의 서비스에는 `DEMO_PROXY_PORT`가 닿지 않는다. 바꾸려면 그 기본값과 DSM의 대상 포트를 함께 고친다.

공유기는 손대지 않는다: 80·443은 NAS로 간다. 2021 머신(`192.168.10.197`)과 NAS(`192.168.10.102`)는 DHCP 예약으로 고정한다.

LAN 안에서 먼저 확인한다(DNS를 바꾸기 전):

```bash
# 2021 머신에서: NAS가 아니므로 403이어야 한다 (LAN 주소로 붙는다 — 127.0.0.1은 NAS가 아니다)
python3 -c "import urllib.request as u; u.urlopen('http://192.168.10.197:8091/')"   # HTTPError 403
```

NAS에서 8091로 닿는지는 DSM의 규칙을 만든 뒤 아래 5번에서 본다.

### 터널에서 옮기기 (NAS 뒤)

원칙은 [직접 공개 모드의 것](#터널에서-옮기기)과 같다 — 새 길을 열고, **DNS 레코드만** 바꾸고, 인증서를 확인하고, 캐시가 지난
뒤에 터널을 정리한다. 다른 것은 인증서를 받는 곳(NAS)뿐이다.

1. 위 "2021 머신에서 켜기 (NAS 뒤)"와 DSM의 리버스 프록시 규칙을 만든다(인증서는 아직 없다 — DSM의 기본 인증서로 응답한다).
2. DNS 레코드를 바꾼다: 직접 공개 모드 3번 그대로 — `wireview-demo` CNAME을 **A** `121.159.177.248`, **DNS only**(회색 구름),
   TTL 1분으로. (a) 대시보드의 Public Hostname은 **지우지 않는다**. AAAA는 두지 않는다.
3. 곧바로 DSM에서 Let's Encrypt 인증서를 받고(위 "DSM 설정"의 1번) 리버스 프록시 항목에 할당한다(2번). 2번부터 여기까지가
   새 응답(A)을 받은 방문자의 인증서 오류 구간이다 — 손으로 하는 만큼 걸린다(보통 1~2분).
4. 밖(휴대폰 테더링 등)에서 `uv run python demo/smoke.py https://wireview-demo.itda.work --out /tmp/demo-smoke`. 응답에 `cf-ray`가
   없어야 한다. 2021 머신의 Caddy 로그(`/opt/homebrew/var/log/caddy.log`)에서 `remote_ip`는 모두 `192.168.10.102`(NAS)이고
   `client_ip`가 방문자들의 공인 주소여야 한다. `client_ip`도 모두 `192.168.10.102`이면 DSM이 `X-Real-IP`를 보내지 않는 것이다 —
   모든 방문자가 한 사람으로 한도에 걸린다. 사용자 정의 헤더를 고치거나 되돌린다.
5. 직접 공개 모드 6·7번처럼 10분 기다린 뒤 `docker compose stop cloudflared`, (a)면 그다음 Public Hostname을 지운다.

되돌리기: 터널을 띄우고(`--profile tunnel up -d`) A를 터널의 CNAME(Proxied)으로 바꾼 뒤(직접 공개 모드의 되돌리기 1·2번), 5분 뒤
DSM의 리버스 프록시 규칙을 지우고 `brew services stop caddy`. DSM의 인증서는 남겨도 해가 없다(만료되면 DSM이 알린다).

동적 IP: `ddns`(직접 공개 모드의 "동적 IP")가 그대로 쓰인다 — A 레코드는 같은 회선 주소다.

## 로컬에서 확인

```bash
cd demo
cp .env.example .env   # DJANGO_SECRET_KEY를 채우고,
                       # DJANGO_CSRF_TRUSTED_ORIGINS=https://wireview-demo.itda.work,http://127.0.0.1:8080
docker compose up -d --build
cd .. && uv run python demo/smoke.py http://127.0.0.1:8080 --out /tmp/demo-smoke
uv run python demo/abuse.py http://127.0.0.1:8080
cd demo && docker compose down
```

스모크는 평문 http 주소에는 Cloudflare처럼 `X-Forwarded-Proto: https`를 실어 보내, 운영 설정(HTTPS 리다이렉트, 보안
쿠키) 그대로 돈다. 그러면 Django가 폼의 Origin(`http://127.0.0.1:8080`)을 https 요청의 것과 다르게 보므로 그 출처를
`DJANGO_CSRF_TRUSTED_ORIGINS`에 더한다. 운영의 `.env`에는 넣지 않는다.

스모크가 보는 것: 목록의 14개, 예제마다 첫 페이지, WebSocket으로 라이브가 되는 페이지, #213 장면 중 데모에서 그대로 도는
것(search·livecomp·slots·hooks — 자기 시드나 alice·bob 로그인이 필요한 장면은 뺀다)을 `paced=False`로, 익명 채팅을 브라우저 둘이,
게스트 채팅(chat_lv2)을 게스트 둘이, 알림의 게스트 로그인, 이벤트 폭주(4029)와 큰 프레임(1009). 스크린샷은 `--out`에.
평문 http 주소에는 실행마다 다른 `CF-Connecting-IP`를 실어, 연달아 돌려도 앞 실행이 쓴 방문자 한도에 걸리지 않는다.

`abuse.py`는 #214 검수의 탐침을 그대로 보내 모두 거절되는지 본다: 필드 이름의 반사(XSS), `params_changed` 100개, 같은 상태의 join
60개, 같은 컴포넌트를 새로 서명한 상태 60개의 join, 재연결로 이벤트 창 새로 시작하기, 한글 프레임의 바이트 상한, 읽을 수 없는
프레임 폭주, 동시 소켓 41개, 쿠키 없는 GET 80개의 세션, 분당 요청 예산. 정상 사용이 끊기지 않는지도 본다: 350ms마다 검색 입력
(`--typing-seconds`, 기본 60초 — 검수 조건대로 5분은 `--typing-seconds 300`). `--docker`를 주면 compose로 컨테이너 안을 보는
검사도 돈다: 감독 프로세스를 둔 채 워커만 SIGKILL한 뒤 90초 안에 같은 방문자가 다시 붙는지, 소켓을 닫은 방문자의 키가 Redis에
남지 않는지. 검사마다 다른 `CF-Connecting-IP`를 쓰므로 로컬 스택에서만 돈다.

## 장애 때 볼 것

| 증상 | 볼 것 |
|------|------|
| 페이지가 안 뜬다(Cloudflare 502/1033) | `docker compose ps` — web이 healthy인가, cloudflared가 Up인가. `docker compose logs cloudflared` |
| 직접 모드: 접속이 안 되거나 인증서 오류 | `brew services list`의 caddy, `/opt/homebrew/var/log/caddy.log`(발급 실패면 80이 밖에서 닿는가 — 공유기, macOS 방화벽). `lsof -nP -iTCP:443 -sTCP:LISTEN`이 caddy인가. A 레코드가 지금 회선 주소인가(`ddns` 로그) |
| NAS 뒤: DSM이 502 또는 시간 초과 | 2021 머신의 `brew services list`의 caddy, `lsof -nP -iTCP:8091 -sTCP:LISTEN`, macOS 방화벽. caddy 로그에 요청이 찍히는데 `403`이면 NAS 주소가 `Caddyfile`의 `import` 인자와 다르다(`remote_ip`를 본다) |
| NAS 뒤: 모두가 같은 방문자로 한도에 걸린다 | caddy 로그의 `client_ip`가 `192.168.10.102`뿐이면 DSM이 `X-Real-IP $remote_addr`를 보내지 않는다(사용자 정의 헤더) |
| NAS 뒤: 로그인·폼이 CSRF 403 | DSM이 `X-Forwarded-Proto https`를 보내지 않아 Django가 요청을 http로 본다(사용자 정의 헤더) |
| 직접 모드: 모두가 같은 방문자로 한도에 걸린다 | caddy 로그의 `remote_ip`가 사설 주소뿐이면 앞에 다른 프록시가 있거나 공유기가 NAT 루프백으로 넘긴 것이다. web의 8081이 `127.0.0.1`에만 열렸는가 |
| 페이지는 뜨는데 아무것도 반응하지 않는다 | 브라우저 콘솔에서 `wireview.min.js`가 404인가(web 이미지를 다시 빌드), `/__wireview__` 소켓이 열리는가. Cloudflare의 WebSockets 설정 |
| 소켓이 바로 닫힌다 | `docker compose logs app \| grep demo.limits` — 한도에 걸렸는가(4029·1009, 새 소켓 403, 1013은 카운터 Redis에 닿지 못함). Origin이 `ALLOWED_HOSTS`에 있는가 |
| 모든 요청이 503 "The demo cannot check its limits" | 한도 카운터(`limits`)에 닿지 못한다. `docker compose ps limits`, `docker compose logs limits` |
| 모든 요청이 503 "more visitors than it can keep track of" | 카운터 Redis가 찼다(janitor가 먼저 unhealthy, 로그 "of its memory"). `docker compose exec limits redis-cli info memory`, `dbsize`. 창이 만료되면 몇 분 안에 풀린다. 되풀이되면 compose의 `--maxmemory`를 올리거나 한도를 줄인다 |
| app이 unhealthy | `/ready/`는 채널 레이어 왕복과 카운터 쓰기다. 이유는 `docker compose logs app \| grep demo.ready`. `docker compose exec redis redis-cli ping` |
| janitor가 unhealthy | 정리가 2분 넘게 성공하지 않았거나 카운터 Redis가 80%를 넘었다(`/data/limits.full`, 로그 "of its memory"). Docker는 unhealthy만으로 재시작하지 않는다. `docker compose logs janitor` — 실패마다 한 줄, 15번 연속이면 ERROR "no cleanup is happening". 고친 뒤 `docker compose restart janitor`. 카운터 쪽은 80% 아래로 내려가면 다음 라운드에 저절로 healthy |
| 다른 창의 채팅이 안 온다 | redis가 살아 있는가. 워커를 늘렸는데 InMemory로 바꾸지는 않았는가(`manage.py check --deploy`의 W006) |
| `database is locked` | janitor와 app이 같은 파일을 쓴다. WAL과 20초 timeout으로 버티는 부하인데 넘으면 janitor를 잠시 멈추고(`docker compose stop janitor`) 원인을 본다 |
| 디스크가 찬다 | `docker system df`. 로그는 돌아가지만 옛 이미지가 쌓인다 — `docker image prune`. 데이터베이스는 보관 예산으로 200MB 남짓에서 멈추고, 그래도 `DEMO_DB_MAX_MB`(1GB)를 넘으면 janitor가 마지막 수단으로 초기화한다(ERROR "past DEMO_DB_MAX_MB" — 무엇이 자랐는지 찾는다) |
| 채팅의 옛 메시지가 사라졌다 | 방마다 최근 `DEMO_KEEP_PER_ROOM`(200)개만 남긴다. 정상이다 |
| 메시지·방을 만들 수 없다(503, 또는 보낸 메시지가 사라지고 입력칸에 남는다) | 그 테이블이 `DEMO_ROWS_MAX`에 닿았다(로그 `demo.retention` "holds DEMO_ROWS_MAX rows"). 매일 초기화로 풀린다 |
| 로그인했는데 로그인 화면으로 돌아간다 | 게스트가 12시간이 지나 지워졌거나 매일 초기화가 지나갔다. 다시 누르면 새 게스트다 |

## 파일

| 파일 | 내용 |
|------|------|
| `settings.py`, `urls.py`, `asgi.py`, `manage.py` | 프로젝트. 설정은 모두 환경 변수(`.env.example`) |
| `catalog.py`, `views.py`, `templates/demo/` | 목록 페이지, 헬스체크(`/health/`, `/ready/`) |
| `guests.py`, `apps.py` | 게스트 로그인 |
| `limits.py`, `sessions.py` | HTTP 미들웨어와 WebSocket 한도, 소켓 임대, 새 세션의 예산 |
| `retention.py` | 방문자가 쓰는 테이블의 보관 예산 |
| `seed.py`, `management/commands/` | 시드·초기화(`demo_reset`), janitor(`demo_janitor`) |
| `Dockerfile`, `Dockerfile.dockerignore`, `requirements.txt` | 이미지. 의존성 버전은 저장소의 `uv.lock`을 따른다 |
| `compose.yaml`, `Caddyfile`, `.env.example` | 런타임 |
| `compose.direct.yaml`, `Caddyfile.direct`, `ddns.py`, `ddns.env.example` | 직접 공개 모드: web의 8081, 호스트의 Caddy, A 레코드 갱신 |
| `Caddyfile.proxy` | NAS 리버스 프록시 뒤: NAS 하나만 믿고 LAN의 8091에서 받는 호스트의 Caddy. 스택은 `compose.direct.yaml` 그대로 |
| `smoke.py`, `abuse.py` | 띄운 데모의 브라우저 스모크(https면 위조 헤더 검사 포함), 남용 탐침 |

목록의 썸네일은 #213의 `examples/<예제>/media/<예제>.webp`다 — `STATICFILES_DIRS`가 `demo/media/`로 모아 Caddy가
서빙한다(이미지에는 webp만 싣는다). 설정과 한도의 계약은
`tests/test_demo_site.py`가 `make test`에서 본다.
