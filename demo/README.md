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

| 서비스 | 이미지 | 하는 일 |
|------|------|------|
| `app` | `demo/Dockerfile`의 `app` | uvicorn 워커 2개(`WEB_CONCURRENCY`). 시작할 때마다 `migrate`와 `demo_reset` 뒤 서빙한다 |
| `web` | `demo/Dockerfile`의 `web` | Caddy. `/static/`을 이미지에 복사해 둔 파일로 서빙하고 나머지(WebSocket 포함)를 app으로 넘긴다. 루트가 아닌 사용자(UID 10002)로 컨테이너 포트 8080에서 듣고, 호스트의 `127.0.0.1:8080`에만 열린다 |
| `redis` | `redis:7.4-alpine` | 채널 레이어. 128MB, `volatile-lru`. 영속하지 않는다 |
| `limits` | `redis:7.4-alpine` | 속도 제한 카운터만. 32MB, `noeviction`. 영속하지 않는다 |
| `janitor` | `app`과 같은 이미지 | 대시보드 펄스(20초), 오래된 게스트·만료 세션 정리와 WAL 체크포인트·파일 크기 예산(10분), 매일 초기화(`DEMO_RESET_AT`)와 그 뒤 `VACUUM`. 라운드가 성공할 때마다 `/data/janitor.ok`를 갱신하고 헬스체크가 그것을 본다 |
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
  (HTTP 503, 소켓 1013). 키는 모두 만료가 있고(한도 창 10초~1시간, 열린 소켓 수는 하루), 키 하나가 약 100바이트라 32MB면
  방문자 창 20만 개쯤이다. 찬 상태는 창이 만료되면 풀린다.
- **SQLite(WAL).** 쓰기가 채팅 한 줄·투표 한 번 단위이고 호스트가 하나다. [배포 가이드의 단일 서버 구성](../docs/DEPLOYMENT.md#windows-단일-서버-uvicorn--n--caddy-nats-sqlite)처럼
  WAL·busy timeout·`IMMEDIATE`를 켠다. Postgres는 서비스 하나와 백업 절차 하나를 더할 뿐 이 부하에서 얻는 것이 없다.
  데이터는 매일 시드로 돌아가므로 잃어도 되는 데이터다.
- **Caddy가 정적 파일.** 운영(`DEBUG=False`)의 uvicorn은 `/static/`을 서빙하지 않는다. web 이미지는 app 이미지의 `collectstatic`
  결과를 복사해 만든다 — 명명 볼륨으로 나누면 다음 배포에도 첫 번들이 남는다([정적 파일](../docs/DEPLOYMENT.md#정적-파일)).

## 공개 데모의 안전장치

| 무엇 | 어떻게 | 어디 |
|------|------|------|
| 로그인 예제(chat_lv2~4, notifications) | 로그인 화면의 버튼이 "Sign in as guest" 하나다. 누르면 그 브라우저만의 게스트(`guest-swift-otter-042` 꼴)로 로그인한다. 사생활 창은 다른 게스트다 | `guests.py`, `apps.py`, `urls.py` |
| 게스트 수명 | 만든 지 `DEMO_GUEST_MAX_AGE_HOURS`(12시간)가 지나면 janitor가 지운다. 같은 주소에서 시간당 60개까지 | `guests.py`, `demo_janitor` |
| 데이터 초기화 | app 컨테이너가 시작할 때마다, 그리고 매일 `DEMO_RESET_AT`(04:00, `DEMO_TIME_ZONE`)에 예제 테이블을 비우고 게스트·세션을 지운 뒤 시드를 쓴다 | `seed.py`, `demo_reset`, `demo_janitor` |
| 입력 길이 | 폼·쿼리의 필드가 500자를 넘으면 400(평문, 필드 이름을 되돌려 보내지 않는다). WebSocket이 싣는 문자열은 join의 서명 상태를 빼고 모두 500자로 자른다 — 이벤트 인자, 훅 페이로드, `params_changed`·`navigated`의 params와 `uri`의 쿼리 값 | `limits.py` |
| WebSocket 예산 | 소켓마다 버킷: 모든 프레임(120, 초당 20), 서버가 읽지 않는 프레임(JSON 아님·모르는 명령, 5개 — 소비자에 넘기지 않는다), 이벤트(30, 초당 5), 처음 보는 상태의 join(60, 초당 5 — 페이지의 첫 join들), 클릭 없이 렌더하는 것(같은 상태의 다시 join, `params_changed`, `navigated`: 20, 초당 2). 방문자마다 10초 창을 Redis에서 모든 소켓을 합쳐 센다: 프레임 600, 이벤트 100, join·렌더 300 — 재연결해도 창은 이어진다. 넘으면 4029로 닫는다(페이지는 백오프 뒤 다시 붙는다) | `limits.py` |
| 소켓 수 | 방문자마다 분당 새 소켓 300, 동시에 열린 소켓 40, 데모 전체 2000. 넘으면 핸드셰이크를 403으로 거절 | `limits.py` |
| 프레임 크기 | 페이지가 보내는 프레임이 UTF-8로 64KB를 넘으면 1009로 닫는다. uvicorn의 상한은 1MB, Caddy의 요청 본문 상한도 1MB | `limits.py`, `Dockerfile`, `Caddyfile` |
| HTTP 예산 | 방문자마다 분당 요청 300(정적 파일은 Caddy가 답하므로 빼고), 그중 POST 60. 넘으면 429(평문). compose의 헬스체크(`X-Real-IP` 없이 안에서 묻는 `/health/`·`/ready/`)는 세지 않는다 | `limits.py` |
| 저장 행 | 새 세션(쿠키 없는 GET이 만드는 것 — chat 방·quiz·rating)은 방문자마다 시간당 60개, 전체 20,000행까지. 게스트는 방문자마다 시간당 60, 전체 5,000. 넘으면 429·503. 로그인의 세션 키 교체는 세지 않는다 | `sessions.py`, `guests.py` |
| 디스크 | janitor가 10분마다 WAL을 체크포인트하고, 데이터베이스 파일이 `DEMO_DB_MAX_MB`(256MB)를 넘으면 그 자리에서 초기화하고 `VACUUM`한다. 매일 초기화 뒤에도 `VACUUM` | `demo_janitor` |
| 업로드 | 업로드하는 예제가 없으므로 업로드 엔드포인트(`wireview.urls`)를 싣지 않는다 | `urls.py` |
| 운영 설정 | `DEBUG=False`, 비밀 키는 환경 변수, HTTPS 리다이렉트·보안 쿠키·HSTS(이 호스트만), `manage.py check --deploy` 통과 | `settings.py`, `tests/test_demo_site.py` |
| 오류 응답 | 한도의 답은 모두 `text/plain`이고 방문자가 보낸 것을 되풀이하지 않는다. `/ready/`는 밖에 `{"status": "unready"}`만 답하고 이유는 로그(`demo.ready`)에 남긴다. 4xx마다 `django.request` 줄을 남기지 않는다(한도가 거절한 것은 `demo.limits` INFO로 한 줄) | `limits.py`, `views.py`, `settings.py` |

주소마다의 한도는 Cloudflare가 준 `CF-Connecting-IP`를 Caddy가 `X-Real-IP`로 넘긴 값으로 센다. IPv6 주소는 /64(가입자 하나가
받는 블록)로 센다. 행사장 Wi-Fi나 통신사 NAT 뒤의 사람들은 한 주소이므로 한도는 스크립트를 막는 정도로 넉넉하게 두었다.
모든 한도는 `.env`로 바꾼다(`.env.example`, 기본값은 `settings.py`의 `DEMO_LIMITS`).

Caddy는 사설 주소에서 온 `CF-Connecting-IP`를 믿는다(cloudflared가 compose 네트워크 안에 있기 때문). 그래서 호스트의
`127.0.0.1:8080`에 닿을 수 있는 사람은 그 헤더로 다른 방문자를 흉내 낼 수 있다 — `demo/abuse.py`가 그것으로 방문자를 나눈다.
그 포트는 호스트 밖으로 열지 않는다. 공개 연결 전에 실제 터널 경로에서 `CF-Connecting-IP`·`X-Forwarded-Proto`가 기대한 값인지,
다른 포트포워딩이나 프록시가 없는지 확인한다.

침묵한 검사는 둘이다: `security.W005`(HSTS `includeSubDomains`), `security.W021`(preload). itda.work의 다른 이름에 HTTPS를
강제하는 것은 이 데모가 정할 일이 아니다.

## 정할 것 (기본값으로 두었다)

아래는 운영자가 정할 일이라 바꾸지 않고 기본값으로 둔다. 모두 설정 하나로 바뀐다.

| 무엇 | 지금 | 바꾸는 곳 |
|------|------|------|
| notifications 예제의 수신자 목록 | 예제 그대로 모든 사용자(지금 있는 게스트들)가 보이고, 그들에게 알림을 보낼 수 있다 | 예제 쪽 변경이 필요하다 |
| 익명 채팅(chat 1단계)의 이름 | 누구나 아무 이름(`?username=`, 500자 이하)으로 쓴다. 사칭·욕설을 거르는 모더레이션은 없다. 매일 초기화가 유일한 정리 | 예제 쪽 변경이 필요하다 |
| 한도 값 | 위 표의 값 | `.env`의 `DEMO_*` |
| 초기화 | 매일 04:00(Asia/Seoul)과 app이 시작할 때마다(배포 = 초기화) | `DEMO_RESET_AT`, `DEMO_TIME_ZONE`, `Dockerfile`의 `CMD` |
| HSTS | 30일, 이 호스트만 | `DJANGO_HSTS_SECONDS` |

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

app이 새로 시작하므로 데이터는 시드로 돌아간다. 한도 카운터(`limits`)는 그대로 남는다. 열려 있던 페이지는 재연결해 다시 join한다(서명 키가 같으면 새로고침 없이).
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
되돌리려면 대시보드에서 public hostname을 지운다.

Cloudflare 쪽에 둘 만한 것(선택): 이 호스트에 Rate Limiting 규칙(예: 분당 요청 600), Bot Fight Mode. 앱의 한도(HTTP 예산,
세션·게스트 행, 소켓 수)가 Cloudflare 없이도 서므로 필수는 아니다. 다만 앱의 한도는 요청이 앱에 닿은 뒤에 세므로, 많은 주소에서
오는 분산된 요청은 Cloudflare 쪽에서만 막힌다.

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
60개, 재연결로 이벤트 창 새로 시작하기, 한글 프레임의 바이트 상한, 읽을 수 없는 프레임 폭주, 동시 소켓 41개, 쿠키 없는 GET 80개의 세션,
분당 요청 예산. 검사마다 다른 `CF-Connecting-IP`를 쓰므로 로컬 스택에서만 돈다.

## 장애 때 볼 것

| 증상 | 볼 것 |
|------|------|
| 페이지가 안 뜬다(Cloudflare 502/1033) | `docker compose ps` — web이 healthy인가, cloudflared가 Up인가. `docker compose logs cloudflared` |
| 페이지는 뜨는데 아무것도 반응하지 않는다 | 브라우저 콘솔에서 `wireview.min.js`가 404인가(web 이미지를 다시 빌드), `/__wireview__` 소켓이 열리는가. Cloudflare의 WebSockets 설정 |
| 소켓이 바로 닫힌다 | `docker compose logs app \| grep demo.limits` — 한도에 걸렸는가(4029·1009, 새 소켓 403, 1013은 카운터 Redis에 닿지 못함). Origin이 `ALLOWED_HOSTS`에 있는가 |
| 모든 요청이 503 "The demo is busy" | 한도 카운터(`limits`)에 닿지 못하거나 그것이 찼다. `docker compose ps limits`, `docker compose exec limits redis-cli info memory` |
| app이 unhealthy | `/ready/`는 채널 레이어 왕복과 카운터 쓰기다. 이유는 `docker compose logs app \| grep demo.ready`. `docker compose exec redis redis-cli ping` |
| janitor가 unhealthy | 정리가 2분 넘게 성공하지 않았다. Docker는 unhealthy만으로 재시작하지 않는다. `docker compose logs janitor` — 실패마다 한 줄, 15번 연속이면 ERROR "no cleanup is happening". 고친 뒤 `docker compose restart janitor` |
| 다른 창의 채팅이 안 온다 | redis가 살아 있는가. 워커를 늘렸는데 InMemory로 바꾸지는 않았는가(`manage.py check --deploy`의 W006) |
| `database is locked` | janitor와 app이 같은 파일을 쓴다. WAL과 20초 timeout으로 버티는 부하인데 넘으면 janitor를 잠시 멈추고(`docker compose stop janitor`) 원인을 본다 |
| 디스크가 찬다 | `docker system df`. 로그는 돌아가지만 옛 이미지가 쌓인다 — `docker image prune`. 데이터베이스는 `DEMO_DB_MAX_MB`를 넘으면 janitor가 초기화한다(로그 "past DEMO_DB_MAX_MB") |
| 로그인했는데 로그인 화면으로 돌아간다 | 게스트가 12시간이 지나 지워졌거나 매일 초기화가 지나갔다. 다시 누르면 새 게스트다 |

## 파일

| 파일 | 내용 |
|------|------|
| `settings.py`, `urls.py`, `asgi.py`, `manage.py` | 프로젝트. 설정은 모두 환경 변수(`.env.example`) |
| `catalog.py`, `views.py`, `templates/demo/` | 목록 페이지, 헬스체크(`/health/`, `/ready/`) |
| `guests.py`, `apps.py` | 게스트 로그인 |
| `limits.py`, `sessions.py` | HTTP 미들웨어와 WebSocket 한도, 새 세션의 예산 |
| `seed.py`, `management/commands/` | 시드·초기화(`demo_reset`), janitor(`demo_janitor`) |
| `Dockerfile`, `Dockerfile.dockerignore`, `requirements.txt` | 이미지. 의존성 버전은 저장소의 `uv.lock`을 따른다 |
| `compose.yaml`, `Caddyfile`, `.env.example` | 런타임 |
| `smoke.py`, `abuse.py` | 띄운 데모의 브라우저 스모크, 남용 탐침 |

목록의 썸네일은 #213의 `examples/<예제>/media/<예제>.webp`다 — `STATICFILES_DIRS`가 `demo/media/`로 모아 Caddy가
서빙한다(이미지에는 webp만 싣는다). 설정과 한도의 계약은
`tests/test_demo_site.py`가 `make test`에서 본다.
