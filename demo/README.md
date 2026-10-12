# 공개 데모 (wireview-demo.itda.work)

저장소의 예제 14개를 Django 프로젝트 하나로 묶어 Docker Compose로 띄우는 배포 단위다(#214).
`examples/`의 앱을 그대로 싣고, 공개 사이트에 필요한 것(게스트 계정, 입력 제한, 매일 초기화, 운영 설정)은
전부 이 디렉터리에서 더한다. 예제 코드는 고치지 않는다. 테스트 프로젝트(`tests/testproj/`)와는 따로다 —
테스트 픽스처를 운영에 싣지 않는다.

## 구성

```
인터넷 ─ Cloudflare(TLS) ─ cloudflared ─▶ web(Caddy :80) ─▶ app(uvicorn ×2 :8000) ─▶ redis
                                           └ /static/ 직접 서빙          └ SQLite(/data 볼륨) ◀─ janitor
```

| 서비스 | 이미지 | 하는 일 |
|------|------|------|
| `app` | `demo/Dockerfile`의 `app` | uvicorn 워커 2개(`WEB_CONCURRENCY`). 시작할 때마다 `migrate`와 `demo_reset` 뒤 서빙한다 |
| `web` | `demo/Dockerfile`의 `web` | Caddy. `/static/`을 이미지에 복사해 둔 파일로 서빙하고 나머지(WebSocket 포함)를 app으로 넘긴다. 호스트의 `127.0.0.1:8080`에만 열린다 |
| `redis` | `redis:7.4-alpine` | 채널 레이어(DB 0)와 속도 제한 카운터(DB 1). 영속하지 않는다 |
| `janitor` | `app`과 같은 이미지 | 대시보드 펄스(20초), 오래된 게스트·만료 세션 정리(10분), 매일 초기화(`DEMO_RESET_AT`) |
| `cloudflared` | `cloudflare/cloudflared` | 터널. `tunnel` 프로필이라 기본으로는 뜨지 않는다 |

고른 이유:

- **uvicorn.** [배포 가이드](../docs/DEPLOYMENT.md#asgi-서버)가 권하는 서버이고, SIGTERM에 `leaving()`을 끝까지 돌린다
  (daphne·Granian은 아니다 — 채팅의 presence 정리가 거기 있다). permessage-deflate는 끈다(메모리와 BREACH).
- **워커 2개.** 데모의 부하는 작다. 하나로도 되지만 둘이어야 채널 레이어가 프로세스를 건너는 경로가 매일 쓰인다.
  늘리려면 `.env`의 `WEB_CONCURRENCY`.
- **Redis.** 워커가 둘 이상이면 InMemory 레이어는 브로드캐스트를 조용히 잃는다(`wireview.W006`). 같은 Redis가 속도 제한도
  센다 — 워커마다 따로 세면 한도가 워커 수만큼 늘어난다.
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
| 입력 길이 | 폼·쿼리의 필드가 500자를 넘으면 400. WebSocket 이벤트가 싣는 문자열은 500자로 자른다 | `limits.py` |
| 속도 | 이벤트는 소켓마다 버킷(30개, 초당 5개 충전)을 넘으면 소켓을 4029로 닫는다(페이지는 백오프 뒤 다시 붙는다). 주소마다 분당 POST 60개, 새 소켓 300개 | `limits.py` |
| 프레임 크기 | 페이지가 보내는 프레임이 64KB를 넘으면 1009로 닫는다. uvicorn의 상한은 1MB, Caddy의 요청 본문 상한도 1MB | `limits.py`, `Dockerfile`, `Caddyfile` |
| 업로드 | 업로드하는 예제가 없으므로 업로드 엔드포인트(`wireview.urls`)를 싣지 않는다 | `urls.py` |
| 운영 설정 | `DEBUG=False`, 비밀 키는 환경 변수, HTTPS 리다이렉트·보안 쿠키·HSTS(이 호스트만), `manage.py check --deploy` 통과 | `settings.py`, `tests/test_demo_site.py` |

주소마다의 한도는 Cloudflare가 준 `CF-Connecting-IP`를 Caddy가 `X-Real-IP`로 넘긴 값으로 센다. 행사장 Wi-Fi나 통신사 NAT
뒤의 사람들은 한 주소이므로 한도는 스크립트를 막는 정도로 넉넉하게 두었다. 모든 한도는 `.env`로 바꾼다(`.env.example`).

침묵한 검사는 둘이다: `security.W005`(HSTS `includeSubDomains`), `security.W021`(preload). itda.work의 다른 이름에 HTTPS를
강제하는 것은 이 데모가 정할 일이 아니다.

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
docker compose ps        # app·web·redis가 healthy, janitor가 Up
```

서비스가 `restart: unless-stopped`이므로 colima가 다시 뜨면 데모도 다시 뜬다. colima 자체가 로그인 때
뜨게 하려면 `brew services start colima`.

### 업데이트

```bash
cd ~/srv/django-wireview && git pull
cd demo && docker compose up -d --build
```

app이 새로 시작하므로 데이터는 시드로 돌아간다. 열려 있던 페이지는 재연결해 다시 join한다(서명 키가 같으면 새로고침 없이).
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

```bash
docker buildx build --platform linux/amd64 -f demo/Dockerfile --target app -t wireview-demo-app:amd64 --load ..
docker buildx build --platform linux/amd64 -f demo/Dockerfile --target web -t wireview-demo-web:amd64 --load ..
```

## Cloudflare Tunnel 연결

포트를 열지 않는다. cloudflared가 바깥으로 연결을 맺고 Cloudflare가 그 연결로 요청을 들여보낸다. 아래는 절차이고,
실행은 사용자가 확인한 뒤에 한다.

1. Cloudflare 대시보드 → Zero Trust → Networks → Tunnels → **Create a tunnel** → `Cloudflared`.
   이름은 `wireview-demo`.
2. 설치 화면의 명령에서 **토큰만** 복사해(`eyJ…`) `.env`의 `CLOUDFLARE_TUNNEL_TOKEN`에 넣는다. cloudflared는 이미지로
   돌리므로 그 화면의 설치 명령은 실행하지 않는다.
3. **Public Hostname** 추가: subdomain `wireview-demo`, domain `itda.work`, service `HTTP` → `web:80`.
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

Cloudflare 쪽에 둘 만한 것(선택): 이 호스트에 Rate Limiting 규칙(예: 분당 요청 600), Bot Fight Mode. 앱의 한도가 이미
있으므로 필수는 아니다.

## 로컬에서 확인

```bash
cd demo
cp .env.example .env   # DJANGO_SECRET_KEY를 채우고,
                       # DJANGO_CSRF_TRUSTED_ORIGINS=https://wireview-demo.itda.work,http://127.0.0.1:8080
docker compose up -d --build
cd .. && uv run python demo/smoke.py http://127.0.0.1:8080 --out /tmp/demo-smoke
cd demo && docker compose down
```

스모크는 평문 http 주소에는 Cloudflare처럼 `X-Forwarded-Proto: https`를 실어 보내, 운영 설정(HTTPS 리다이렉트, 보안
쿠키) 그대로 돈다. 그러면 Django가 폼의 Origin(`http://127.0.0.1:8080`)을 https 요청의 것과 다르게 보므로 그 출처를
`DJANGO_CSRF_TRUSTED_ORIGINS`에 더한다. 운영의 `.env`에는 넣지 않는다.

스모크가 보는 것: 목록의 14개, 예제마다 첫 페이지, WebSocket으로 라이브가 되는 페이지, 익명 채팅을 브라우저 둘이,
게스트 채팅(chat_lv2)을 게스트 둘이, 알림의 게스트 로그인, 이벤트 폭주(4029)와 큰 프레임(1009). 스크린샷은 `--out`에.

## 장애 때 볼 것

| 증상 | 볼 것 |
|------|------|
| 페이지가 안 뜬다(Cloudflare 502/1033) | `docker compose ps` — web이 healthy인가, cloudflared가 Up인가. `docker compose logs cloudflared` |
| 페이지는 뜨는데 아무것도 반응하지 않는다 | 브라우저 콘솔에서 `wireview.min.js`가 404인가(web 이미지를 다시 빌드), `/__wireview__` 소켓이 열리는가. Cloudflare의 WebSockets 설정 |
| 소켓이 바로 닫힌다 | `docker compose logs app \| grep demo.limits` — 한도에 걸렸는가(4029·1009, 새 소켓 403). Origin이 `ALLOWED_HOSTS`에 있는가 |
| app이 unhealthy | `/ready/`는 채널 레이어 왕복이다. `docker compose logs redis`, `docker compose exec redis redis-cli ping` |
| 다른 창의 채팅이 안 온다 | redis가 살아 있는가. 워커를 늘렸는데 InMemory로 바꾸지는 않았는가(`manage.py check --deploy`의 W006) |
| `database is locked` | janitor와 app이 같은 파일을 쓴다. WAL과 20초 timeout으로 버티는 부하인데 넘으면 janitor를 잠시 멈추고(`docker compose stop janitor`) 원인을 본다 |
| 디스크가 찬다 | `docker system df`. 로그는 돌아가지만 옛 이미지가 쌓인다 — `docker image prune` |
| 로그인했는데 로그인 화면으로 돌아간다 | 게스트가 12시간이 지나 지워졌거나 매일 초기화가 지나갔다. 다시 누르면 새 게스트다 |

## 파일

| 파일 | 내용 |
|------|------|
| `settings.py`, `urls.py`, `asgi.py`, `manage.py` | 프로젝트. 설정은 모두 환경 변수(`.env.example`) |
| `catalog.py`, `views.py`, `templates/demo/` | 목록 페이지, slots 예제의 페이지(그 예제에는 페이지가 없다), 헬스체크(`/health/`, `/ready/`) |
| `guests.py`, `apps.py` | 게스트 로그인 |
| `limits.py` | HTTP 미들웨어와 WebSocket 한도 |
| `seed.py`, `management/commands/` | 시드·초기화(`demo_reset`), janitor(`demo_janitor`) |
| `Dockerfile`, `Dockerfile.dockerignore`, `requirements.txt` | 이미지. 의존성 버전은 저장소의 `uv.lock`을 따른다 |
| `compose.yaml`, `Caddyfile`, `.env.example` | 런타임 |
| `smoke.py` | 띄운 데모의 브라우저 스모크 |

목록의 썸네일은 `static/demo/media/<예제>.webp`가 있으면 나온다(#213의 미디어가 들어갈 자리). 설정과 한도의 계약은
`tests/test_demo_site.py`가 `make test`에서 본다.
