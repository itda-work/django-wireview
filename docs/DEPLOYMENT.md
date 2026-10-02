# 배포 가이드

django-wireview 앱을 운영에 올릴 때의 설정이다.

## ASGI 서버

### 권장: uvicorn + uvloop

```bash
pip install uvicorn[standard] uvloop
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --loop uvloop \
    --ws-per-message-deflate false
```

**permessage-deflate를 끄는 이유.** uvicorn은 WebSocket 압축을 기본으로 협상하고, 그러면 연결마다
zlib의 deflate·inflate 컨텍스트를 들고 있게 된다 — 실측 연결당 159KB다. uvicorn이 연결당 daphne보다
4배 무거워 보이는 이유가 통째로 이것이다(2,000연결에서 211KB 대 46KB, 압축을 끄면 51KB). wireview가
보내는 것은 잘 안 줄어드는 작은 diff라서(전형적인 이벤트 페이로드가 16% 남짓 줄어든다) 그 메모리로
사는 대역폭이 거의 없다. 첫 렌더나 스트림이 큰 HTML을 밀어내는 경우에만 켜 두고, 켜기 전에 양쪽을
재 본다.

```bash
make bench ARGS="--connections 2000 --server uvicorn"
make bench ARGS="--connections 2000 --server uvicorn-nodeflate"
```

**Docker 예:**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# 앞단 웹 서버가 서빙할 /static/을 STATIC_ROOT에 모은다(아래 「정적 파일」). STATIC_ROOT = BASE_DIR / "staticfiles"이면
# /app/staticfiles다. 이 파일은 이 이미지 안에 있다 — 앞단이 거기에 닿는 길은 「정적 파일」의 컨테이너 배포를 본다.
# collectstatic도 설정을 import하므로, 설정이 빌드 때 없는 환경 변수(비밀 키 등)를 요구하면 이 줄에만 임시 값을 준다.
RUN python manage.py collectstatic --noinput

CMD ["uvicorn", "myproject.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--loop", "uvloop", "--ws-per-message-deflate", "false"]
```

### 대안: Daphne

Django Channels의 레퍼런스 ASGI 서버다.

```bash
pip install daphne
daphne -b 0.0.0.0 -p 8000 myproject.asgi:application
```

### 대안: Hypercorn

HTTP/2를 지원한다.

```bash
pip install hypercorn
hypercorn myproject.asgi:application --bind 0.0.0.0:8000 --workers 4
```

## 채널 레이어

이 프로젝트가 겨냥하는 것은 NATS다. E2E 스위트가 그 위에서 돌고 아래 배포 레시피도 그것을 전제한다.
channels_redis도 완전히 지원하며, Redis가 이미 인프라에 있다면 그쪽이 맞다. 나란히 재 보면 성능은
대등하므로(`docs/design/transport-abstraction.md` §5-3) 선택은 운영 문제다.

### 운영: NATS

`channels-nats`는 채널 레이어를 NATS 서버 위에서 돌린다. Go 바이너리 하나이고 Linux·macOS·Windows
네이티브 빌드가 있으며 운영할 영속성 계층이 없다. 컨슈머와 wireview 코드는 그대로이고 설정만 다르다.

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_nats.NatsChannelLayer",
        "CONFIG": {"servers": [os.environ["NATS_URL"]]},
    }
}
```

같은 NATS 서버를 가리키는 서버 프로세스 여럿이 레이어 하나를 공유한다. 단일 서버 SQLite 배포에
필요한 것은 이게 전부이고, SQLite + Windows 전제에서 빠져 있던 조각이 이것이었다. 토큰 인증,
Windows 서비스 등록, channels_redis와의 차이는 channels-nats README에 있다.

### 운영: Redis

```python
# settings.py
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("redis", 6379)],
            "capacity": 1500,
            "expiry": 10,
        },
    },
}
```

**Redis 여러 대(샤딩):**

`hosts`에 여럿을 적으면 channels_redis가 채널·그룹 이름의 해시로 **서로 독립된 Redis 서버들**에 나눠 싣는다.
Redis Cluster 모드가 아니다 — Cluster의 노드 목록을 적으면 안 된다. 모든 프로세스가 같은 목록을 **같은 순서로**
가져야 같은 그룹이 같은 서버로 간다. 한 서버가 죽으면 그 서버에 해시된 그룹의 브로드캐스트가 사라지므로,
샤딩은 처리량을 늘릴 뿐 가용성을 높이지 않는다.

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                ("redis-shard-1", 6379),
                ("redis-shard-2", 6379),
                ("redis-shard-3", 6379),
            ],
        },
    },
}
```

장애 조치가 필요하면 각 항목을 Sentinel이 지키는 마스터로 적는다.

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                {"sentinels": [("sentinel-1", 26379), ("sentinel-2", 26379)], "master_name": "wireview"},
            ],
        },
    },
}
```

### 개발: In-Memory

개발과 단일 프로세스 테스트 전용이다. 프로세스가 둘 이상이면 **실패하지 않고** 보내는 프로세스의
연결에만 브로드캐스트를 전달하고 나머지는 버린다.

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}
```

### Windows 단일 서버: uvicorn × N + Caddy, NATS, SQLite

2026-09-08에 Windows 11 게스트에서 실측했다(`docs/design/transport-abstraction.md` §5-2,
`bench/results/win11-parlab-*.json`). 구성을 결정하는 Windows 사실이 둘이다.

- **Windows에서 daphne를 쓰지 않는다.** daphne는 asyncio를 selector 루프로 고정하는데 CPython의
  Windows `select()`는 소켓 512개가 상한이다. daphne 프로세스는 연결 500개 근처에서
  `ValueError: too many file descriptors in select()`로 죽는다.
- **Windows에서 `uvicorn --workers`를 쓰지 않는다.** 다중 워커 모드도 같은 selector 루프로 떨어진다.
  단일 프로세스 uvicorn은 IOCP 위에서 돌아 벤치마크에서 2,000연결을 버텼다. 포트마다 uvicorn을
  하나씩 띄우고 Caddy가 연결을 분배하게 한다.

Windows에서는 `INSTALLED_APPS`에서 `"daphne"`도 뺀다. `runserver`를 받쳐 줄 뿐인데 import되는 것만으로
프로세스 전체의 asyncio 정책이 바뀐다.

```powershell
# 코어마다 단일 프로세스 uvicorn 하나, 각자 자기 포트에 (--workers 없이).
# 운영에서는 NSSM이나 작업 스케줄러로 서비스 등록한다.
1..4 | ForEach-Object {
    Start-Process uvicorn -ArgumentList "myproject.asgi:application --host 127.0.0.1 --port $(8000 + $_)"
}
```

```caddyfile
:80 {
    handle_path /static/* {
        root * C:/srv/myproject/staticfiles  # STATIC_ROOT. 배포마다 collectstatic
        file_server
    }
    reverse_proxy 127.0.0.1:8001 127.0.0.1:8002 127.0.0.1:8003 127.0.0.1:8004
}
```

Caddy는 바이너리 하나이고 WebSocket 업그레이드를 알아서 처리하며 TLS 종단도 맡을 수 있다. `/static/`도 Caddy가
서빙한다 — 운영(`DEBUG = False`)의 uvicorn은 정적 파일을 서빙하지 않으므로, 그 블록을 빼면 `wireview.min.js`가 404가
되고 어떤 컴포넌트도 살아나지 않는다([정적 파일](#정적-파일)). 채널
레이어는 channels-nats이고 `nats-server.exe`를 Windows 서비스로 띄운다(channels-nats README).
SQLite는 모든 프로세스가 공유하므로 WAL과 busy timeout을 켠다. pragma는 `init_command`로 직접 실행한다.

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}
```

용량 산정: Windows에서 uvicorn은 연결당 약 160KB의 RSS를 쓰는데 거의 전부가 permessage-deflate다.
`--ws-per-message-deflate false`를 주면 daphne 수준으로 떨어진다([ASGI 서버](#권장-uvicorn--uvloop)).
이벤트 처리량은 uvicorn 프로세스 수에 비례한다 — 벤치마크에서 1개에서 4개로 늘렸을 때 초당 이벤트가
3배, 브로드캐스트가 3.6배 빨라졌다.

## 운영용 Django 설정

```python
# settings.py

DEBUG = False
ALLOWED_HOSTS = ["yourdomain.com"]

# wireview 설정
WIREVIEW = {
    "DEBUG_SYNC_TRANSITIONS": False,  # 운영에서는 끈다
    # 서명 상태 (data-state)
    "STATE_MAX_AGE": 14 * 24 * 3600,  # data-state 유효 기간
    "STATE_REFRESH_AFTER": None,      # None이면 STATE_MAX_AGE // 2
    # 서명 키. 미설정이면 SECRET_KEY를 쓴다
    "SIGNING_KEY": None,
    "SIGNING_KEY_FALLBACKS": None,
}

# 보안
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True

# 데이터베이스 커넥션 재사용 (권장)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "mydb",
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}
```

## 정적 파일

ASGI 서버는 정적 파일을 서빙하지 않는다. `{% wireview_header %}`가 부르는 `wireview.min.js`도 정적 파일이라,
빠뜨리면 페이지는 200으로 그려지는데 어떤 컴포넌트도 살아나지 않는다 — 오류도, 검사 경고도 없다.

- **개발.** `runserver`는 스스로 `/static/`을 서빙하지만 uvicorn은 `asgi.py`의 `application`만 서빙한다.
  스타터 템플릿과 [시작하기 튜토리얼](tutorials/01-getting-started.md#asgipy-수정)의 `asgi.py`는 `DEBUG`일 때 HTTP 앱을
  `django.contrib.staticfiles.handlers.ASGIStaticFilesHandler`로 감싸 둘 다 된다.
- **운영(`DEBUG = False`).** 그 래퍼는 꺼진다. `STATIC_ROOT`를 정하고 배포마다 `python manage.py collectstatic`을
  돌린 뒤, 앞단의 웹 서버가 `/static/`을 서빙한다.

```nginx
location /static/ {
    alias /srv/myproject/staticfiles/;  # STATIC_ROOT
}
```

```caddyfile
yourdomain.com {
    handle_path /static/* {
        root * /srv/myproject/staticfiles
        file_server
    }
    reverse_proxy localhost:8000
}
```

**컨테이너 배포.** [Docker 예](#권장-uvicorn--uvloop)의 `collectstatic`은 파일을 **앱 이미지 안**에 모은다. 앞단이
호스트나 다른 컨테이너에 있으면 위의 `alias`가 가리키는 디렉터리는 비어 있고, 운영의 uvicorn은 정적 파일을 서빙하지
않으므로 `wireview.min.js`가 404다. 앞단 이미지를 앱 이미지에서 복사해 만든다.

```dockerfile
# 위 Docker 예로 빌드한 앱 이미지. 릴리스마다 둘을 같은 태그로 함께 빌드한다
FROM myproject:1.0 AS app
FROM nginx:1.27
COPY --from=app /app/staticfiles /srv/myproject/staticfiles
# 위의 location /static/ 과 앱으로 가는 proxy_pass
COPY nginx.conf /etc/nginx/conf.d/default.conf
```

명명 볼륨으로 앱 컨테이너의 `/app/staticfiles`를 앞단과 공유하는 방법은 피한다. Docker는 비어 있는 명명 볼륨만
이미지의 내용으로 채우므로, 다음 릴리스의 이미지로 바꿔도 볼륨에는 첫 배포의 번들이 남는다(아래의 `?v=`가 그것을
가리지 못한다). 볼륨을 쓴다면 컨테이너가 시작할 때마다 `collectstatic`을 그 볼륨에 다시 돌린다.

`{% wireview_header %}`는 번들 주소에 패키지 버전을 `?v=`로 붙인다. 업그레이드 뒤 `collectstatic`을 빠뜨리면
새 버전 번호로 옛 번들을 받게 되므로, 릴리스마다 `collectstatic`을 배포 절차에 둔다.

## WebSocket의 Origin

WebSocket 핸드셰이크에는 브라우저의 쿠키가 실린다. 막지 않으면 로그인한 사용자가 방문한 **다른 사이트의 페이지**가
이 사이트로 소켓을 열어 그 사용자로 행동할 수 있다(교차 사이트 WebSocket 하이재킹, CSWSH). `SameSite=Lax` 세션
쿠키가 일부를 막지만, 같은 사이트의 다른 서브도메인이나 `SameSite=None` 설정에서는 막지 못한다.

그래서 wireview의 컨슈머는 **소켓을 받기 전에** `Origin` 헤더의 호스트를 `ALLOWED_HOSTS`와 대조한다(#96).
Django가 `Host` 헤더에 쓰는 규칙과 같고, `DEBUG`이면서 `ALLOWED_HOSTS`가 비어 있으면 localhost를 허용한다.
`asgi.py`에서 `AllowedHostsOriginValidator`로 감쌌는지와 상관없이 켜져 있다.

- **`Origin`이 없는 연결은 통과한다.** 브라우저는 WebSocket 핸드셰이크에 항상 `Origin`을 보내므로, 없다는 것은
  브라우저가 아니라는 뜻이다(서버 간 호출, 헬스체크). CSWSH에는 남의 쿠키를 실어 줄 브라우저가 필요하다.
- **페이지와 소켓의 호스트가 다르면**(예: 페이지는 `www.example.com`, 소켓은 `ws.example.com`) 페이지의
  호스트도 `ALLOWED_HOSTS`에 있어야 한다.
- 끄려면 `WIREVIEW["CHECK_ORIGIN"] = False`. 앞단 프록시가 같은 검사를 확실히 할 때만 끈다.

## 업그레이드: 서명된 컴포넌트 상태

v2 상태 봉투(`#58`) 이후 `data-state` 값은 **발급된 컴포넌트 클래스, 페이지의 `live_session`, 발급
당시의 인증 세대와 함께** 서명되고 `WIREVIEW["STATE_MAX_AGE"]`(기본 14일)이 지나면 만료된다.
v1 봉투(`#76`)와 그 이전의 두 형식은 경계를 담고 있지 않으므로 서버가 거절한다. 예전 배포가 그린 탭이 다시 붙으면 `join`이 거절되고 `reload` 명령을 받아
새 배포에서 페이지를 다시 읽는다. 그것이 의도된 복구다 — 현재 인증 컨텍스트와 새 토큰으로 돌아온다.

롤링 배포에서 따라오는 것이 둘이다.

- **열려 있던 탭의 미저장 입력은 리로드와 함께 사라진다.** 옛 토큰을 받아 주던 롤아웃 창
  (`STATE_ACCEPT_LEGACY`)은 1.0 전에 없어졌다(#99). v2 봉투 이전 토큰은 0.3.0 이전 배포가 그린 탭에만
  있고, 그 탭은 한 번 새로 읽힌다. 이 거절은 서명 불일치(`invalid`)로 기록된다.
- **모든 프로세스가 `SECRET_KEY`를 공유해야 한다.** 전에도 그랬지만, 이제 어긋나면 컴포넌트가 조용히
  사라지는 대신 리로드 루프로 드러난다. 클라이언트는 30초 안에 두 번 리로드하기를 거부하고 경고를
  남기므로, 설정 오류가 페이지를 돌리는 대신 브라우저 콘솔에 보인다.

`live_session`을 쓰는 프로젝트에는 둘 더 있다.

**로그아웃이 기존 소켓을 닫는 것은 브로커를 지난다.** InMemory 채널 레이어에서는 같은 프로세스의
연결에만 닿으므로, 다중 워커에서는 다른 워커가 들고 있는 소켓이 닫히지 않는다.
`manage.py check --deploy`의 `wireview.W006`이 같은 것을 가리킨다. 브로커가 있어도 그룹 발행 자체는
최선 노력이다(채널 레이어 규격이 용량 초과 시 폐기와 멤버십 만료를 허용한다). 놓친 소켓은 스스로
다시 연결할 때까지 살아 있고 그동안 이벤트를 계속 처리한다 — join 재확인이 회수하는 것은 "다음
연결"이지 "언젠가 그 연결"이 아니다.

**공개로 돌던 페이지에 경계를 붙일 때는 순서가 있다.** 서버는 토큰이 말하는 경계만 보고 발급 시점은
모르므로, 그 전에 발급된 경계 없는 토큰은 새 정책을 지나지 않는다. 보호할 컴포넌트에
`Meta.live_sessions`를 먼저 선언하고, 즉시 끊어야 하면 `SIGNING_KEY`를 fallback 없이 교체한다. 상세는
[live_session](./features/live-session.md).

**세션 백엔드가 로그아웃의 의미를 정한다.** `signed_cookies` 백엔드는 서버에 아무 기록을 남기지
않으므로, 로그인 시점의 쿠키를 보관했다가 로그아웃 뒤 다시 제출하면 같은 사용자로 인증된다(Django
자신의 한계다). 경계 뒤에 진짜 인가가 걸린 페이지가 있다면 `db`·`cache`·`cached_db` 중 하나를 쓴다.
상세는 [live_session](./features/live-session.md).

`STATE_REFRESH_AFTER`는 상태가 그대로여도 토큰을 다시 발급하기까지의 나이다. 반드시
`STATE_MAX_AGE`보다 작아야 한다. `STATE_MAX_AGE - STATE_REFRESH_AFTER`마다 한 번이라도 렌더되는
컴포넌트는 페이지가 열려 있는 동안 만료되지 않는다.

## WebSocket 프록시

**WebSocket 은 선택이 아니라 요구사항이다.** django-wireview 에는 HTTP 폴백이 없다 —
프록시가 업그레이드를 통과시키지 못하면 페이지는 첫 HTML 만 그려지고 그 뒤로 아무것도 하지 않는다.
폴백을 만들지 않기로 한 근거는 `docs/design/longpolling-fallback.md` §5 에 있다.

아래 설정은 "권장"이 아니라 **최소 조건**이다. WebSocket 업그레이드와 함께 `/static/`도 앞단이 서빙한다.
앱으로 넘기면 운영에서는 `wireview.min.js`가 404이고, 페이지는 그려지지만 어떤 컴포넌트도 살아나지 않는다([정적 파일](#정적-파일)).

### Nginx

```nginx
upstream django {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name yourdomain.com;

    location /static/ {
        alias /srv/myproject/staticfiles/;  # STATIC_ROOT. 배포마다 collectstatic
    }

    location / {
        proxy_pass http://django;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /__wireview__ {
        proxy_pass http://django;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

### Caddy

```caddyfile
yourdomain.com {
    handle_path /static/* {
        root * /srv/myproject/staticfiles  # STATIC_ROOT
        file_server
    }
    reverse_proxy localhost:8000
}
```

Caddy는 WebSocket 업그레이드를 알아서 처리한다.

### AWS ALB

- idle timeout을 최소 3600초로 둔다
- 타깃 그룹 헬스체크는 [HTTP 헬스체크](#http) 경로로 건다. ALB 헬스체크는 HTTP/HTTPS만 지원해
  WebSocket으로는 걸 수 없다
- sticky session(타깃 그룹 stickiness)은 켜지 않아도 된다 — [수평 확장](#수평-확장) 참고

## 모니터링

### 볼 지표

1. **연결** — 열린 소켓 수, 연결 수명, 닫힘 코드 분포. 롤링 배포 중에는 재연결 속도
2. **join 거절** — 사유별 비율. `invalid`가 오르면 서명 키가 인스턴스마다 다르다
3. **렌더 성능** — 렌더·이벤트 지연(P50, P95, P99), diff 크기 분포
4. **채널 레이어** — 거부된 메시지(`publish_failed`), 레이어 자신의 유실 로그, 브로커 메모리·지연

1~3과 4의 앞부분은 [telemetry 시그널](./features/telemetry.md)로 나온다. wireview는 지표를 쌓지 않고 시그널만
보낸다. 켜려면 `WIREVIEW = {"TELEMETRY": True}`.

### telemetry 시그널을 지표로

수신자를 모듈 하나에 모으고 `AppConfig.ready()`에서 import한다. 수신자는 렌더·이벤트 경로 안에서 동기로 돌므로
카운터를 올리는 것 이상은 하지 않는다.

**Prometheus** (`prometheus_client`):

```python
# myapp/metrics.py -- import it from MyAppConfig.ready()
from django.dispatch import receiver
from prometheus_client import Counter, Gauge, Histogram

from wireview import telemetry

CONNECTIONS = Gauge("wireview_connections", "Open wireview sockets", multiprocess_mode="livesum")
CONNECTION_SECONDS = Histogram(
    "wireview_connection_seconds", "How long a socket stayed open", buckets=[1, 10, 60, 300, 1800, 3600, 14400]
)
CLOSES = Counter("wireview_connection_closes_total", "Closed sockets by close code", ["code"])
REJECTED = Counter("wireview_join_rejected_total", "Refused sockets and joins", ["reason"])
PUBLISH_FAILED = Counter("wireview_publish_failed_total", "Messages the channel layer refused", ["kind", "dropped"])
EVENT_SECONDS = Histogram("wireview_event_seconds", "Event handler time", ["component"])
RENDER_SECONDS = Histogram("wireview_render_seconds", "Template render time", ["component"])
EVENT_ERRORS = Counter("wireview_event_errors_total", "Handlers that raised", ["component"])


@receiver(telemetry.connection_opened)
def connection_opened(sender, **kwargs):
    CONNECTIONS.inc()


@receiver(telemetry.connection_closed)
def connection_closed(sender, code, duration_ms, **kwargs):
    CLOSES.labels(code=str(code)).inc()
    # None: telemetry came on after this socket opened, so it was never counted in
    if duration_ms is not None:
        CONNECTIONS.dec()
        CONNECTION_SECONDS.observe(duration_ms / 1000)


@receiver(telemetry.join_rejected)
def join_rejected(sender, reason, **kwargs):
    # A closed set (telemetry.JOIN_REJECTED_REASONS): safe as a label
    REJECTED.labels(reason=reason).inc()


@receiver(telemetry.publish_failed)
def publish_failed(sender, kind, dropped, **kwargs):
    PUBLISH_FAILED.labels(kind=kind, dropped=str(dropped).lower()).inc()


@receiver(telemetry.event_handled)
def event_handled(sender, component_name, duration_ms, error, **kwargs):
    EVENT_SECONDS.labels(component=component_name).observe(duration_ms / 1000)
    if error is not None:
        EVENT_ERRORS.labels(component=component_name).inc()


@receiver(telemetry.component_rendered)
def component_rendered(sender, component_name, duration_ms, **kwargs):
    RENDER_SECONDS.labels(component=component_name).observe(duration_ms / 1000)
```

`uvicorn --workers N`처럼 프로세스가 여럿이면 `prometheus_client`의 multiprocess 모드(`PROMETHEUS_MULTIPROC_DIR`)로
돌린다. `multiprocess_mode="livesum"`이 살아 있는 프로세스들의 연결 수를 더한다. `/metrics` 노출은
`django-prometheus`나 `prometheus_client.make_asgi_app()`으로 한다.

컴포넌트 이름 라벨은 컴포넌트 클래스 수만큼만 는다. `component_id`는 라벨로 쓰지 않는다 — 연결마다 새로 생긴다.

**OpenTelemetry** (`opentelemetry-api`, 내보내기는 SDK와 익스포터 설정의 몫):

```python
# myapp/otel_metrics.py -- import it from MyAppConfig.ready()
from django.dispatch import receiver
from opentelemetry import metrics

from wireview import telemetry

meter = metrics.get_meter("wireview")
connections = meter.create_up_down_counter("wireview.connections", description="Open wireview sockets")
rejected = meter.create_counter("wireview.join.rejected", description="Refused sockets and joins")
publish_failed = meter.create_counter("wireview.publish.failed", description="Messages the channel layer refused")
render = meter.create_histogram("wireview.render.duration", unit="ms", description="Template render time")


@receiver(telemetry.connection_opened)
def opened(sender, **kwargs):
    connections.add(1)


@receiver(telemetry.connection_closed)
def closed(sender, duration_ms, **kwargs):
    if duration_ms is not None:
        connections.add(-1)


@receiver(telemetry.join_rejected)
def refused(sender, reason, **kwargs):
    rejected.add(1, {"reason": reason})


@receiver(telemetry.publish_failed)
def refused_by_layer(sender, kind, dropped, **kwargs):
    publish_failed.add(1, {"kind": kind, "dropped": dropped})


@receiver(telemetry.component_rendered)
def rendered(sender, component_name, duration_ms, **kwargs):
    render.record(duration_ms, {"component": component_name})
```

두 예시는 `tests/test_deployment_examples.py`가 가짜 `prometheus_client`·`opentelemetry`를 끼워 실제로 돌린다.
수신자의 인자 이름이 시그널과 어긋나면 그 테스트가 실패한다.

**브로드캐스트 유실은 레이어의 로그에서 센다.** 레이어의 `group_send`는 가득 찬 멤버를 예외 없이 버린다 —
channels_redis는 `channels_redis.core` 로거에 INFO(`... channels over capacity in group ...`)로, channels-nats는
받는 쪽 프로세스의 `channels_nats` 로거에 WARNING(`mailbox for ... is full`)으로 남긴다. `publish_failed`가 잡는
것은 채널 하나로 보내다 거부된 것(`ChannelFull`)과 브로커 오류뿐이다. 로그 수집기에서 이 두 문장을 세면
브로드캐스트 유실률이 된다.

### 로깅

```python
LOGGING = {
    "version": 1,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "wireview": {
            "handlers": ["console"],
            "level": "WARNING",  # 디버깅할 때는 INFO
        },
        "wireview.sync_detector": {
            "handlers": ["console"],
            "level": "WARNING",
        },
    },
}
```

## 헬스체크

### HTTP

```python
# urls.py
from django.http import JsonResponse

def health_check(request):
    return JsonResponse({"status": "ok"})

urlpatterns = [
    path("health/", health_check),
    # ...
]
```

### Readiness: 채널 레이어 왕복

HTTP 헬스체크는 프로세스가 살아 있다는 것만 말한다. wireview의 브로드캐스트·세션 메일·로그아웃 무효화는 모두
채널 레이어를 지나므로, 브로커가 죽은 인스턴스는 페이지를 그리고 소켓도 받지만 서로의 소식을 전하지 못한다.
readiness는 레이어에 메시지를 한 번 돌려 본다. 그룹으로 보내 자기 채널에서 받으므로 wireview의 팬아웃과
같은 길(`group_add` → `group_send` → `receive`)이다.

```python
# myapp/health.py
import asyncio
import uuid

from channels.layers import get_channel_layer
from django.http import JsonResponse

ROUND_TRIP_TIMEOUT = 2  # seconds


async def channel_layer_ready(request):
    layer = get_channel_layer()
    if layer is None:
        return JsonResponse({"status": "unready", "reason": "no channel layer"}, status=503)
    group = f"readiness.{uuid.uuid4().hex}"
    try:
        async with asyncio.timeout(ROUND_TRIP_TIMEOUT):
            channel = await layer.new_channel()
            await layer.group_add(group, channel)
            try:
                await layer.group_send(group, {"type": "readiness.ping"})
                await layer.receive(channel)
            finally:
                await layer.group_discard(group, channel)
    except Exception as error:
        return JsonResponse({"status": "unready", "reason": repr(error)}, status=503)
    return JsonResponse({"status": "ready"})
```

```python
# urls.py
from django.urls import path

from myapp.health import channel_layer_ready

urlpatterns = [
    path("health/", health_check),  # liveness: the process answers
    path("ready/", channel_layer_ready),  # readiness: the broker answers too
]
```

- **async 뷰다.** `ATOMIC_REQUESTS`를 켠 프로젝트는 `django.db.transaction.non_atomic_requests`로 감싼다 —
  Django가 async 뷰를 트랜잭션으로 감쌀 수 없어 뷰를 부르기 전에 500을 낸다.
- **in-memory 레이어는 늘 통과한다.** 같은 프로세스 안의 큐라 확인할 브로커가 없다. 운영 레이어에서만 뜻이 있다.
- **liveness에 걸지 않는다.** 브로커 장애는 인스턴스를 재시작해도 낫지 않는다. liveness가 이것을 보면 브로커가
  잠깐 흔들릴 때 모든 인스턴스가 함께 재시작되고, 재시작마다 모든 페이지가 다시 join한다(아래 롤링 배포 절).
- 요청마다 그룹 하나를 만들고 지운다. 주기는 5~10초면 충분하다.

이 예시는 `tests/test_deployment_examples.py`가 in-memory 레이어와 실패하는 레이어로 돌린다.

### WebSocket 핸드셰이크

핸드셰이크만 해 보고 닫는다. ASGI 서버, 라우팅, `CHANNEL_LAYERS` 설정이 갖춰졌는지 본다 — 레이어 설정이
없으면 컨슈머가 accept 전에 거절한다. **브로커가 응답하는지는 보지 못한다.** 레이어는 소켓을 받을 때 브로커에
묻지 않으므로(channels_redis의 `new_channel`은 이름만 만든다) 그것은 위의 readiness가 본다. 메시지를 보낼
필요는 없고, wireview 프로토콜에는 `ping` 같은 명령도 없다.

```python
# management/commands/check_websocket.py
import websocket  # pip install websocket-client
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    def handle(self, *args, **options):
        ws = websocket.create_connection("ws://localhost:8000/__wireview__", timeout=5)
        ws.close()
        self.stdout.write("WebSocket OK")
```

wireview는 `Origin` 헤더가 있으면 그 호스트가 `ALLOWED_HOSTS`에 있어야 연결을 받는다([WebSocket의 Origin](#websocket의-origin)).
websocket-client는 접속 주소로 `Origin`을 채운다.

## 확장

### 수평 확장

1. 브로커 기반 채널 레이어를 쓴다 (다중 인스턴스의 필수 조건)
2. 세션 저장소를 공유한다 (DB, Redis/Memcached. signed-cookie 백엔드는 저장소가 필요 없다)
3. 서명 키(`SECRET_KEY` 또는 `WIREVIEW["SIGNING_KEY"]`)를 모든 인스턴스에 같게 둔다

**sticky session은 필요 없다.** 로드밸런서가 요청마다 아무 인스턴스에나 보내도 된다.

- WebSocket은 101 업그레이드 뒤 TCP 연결 하나가 끝날 때까지 한 인스턴스에 머문다. sticky 설정은 여러
  HTTP 요청에 걸치는 전송(long-polling 등)을 위한 것인데, wireview는 WebSocket만 쓴다
  ([longpolling-fallback.md](./design/longpolling-fallback.md)).
- 페이지를 그린 인스턴스와 join을 받는 인스턴스가 달라도 된다. join은 페이지에 실린 서명된 `data-state`만으로
  컴포넌트를 복원한다. 재연결도 같은 상태로 다시 join하므로 어디에 붙어도 된다.
- 청크 업로드도 무상태다(아래 절).

인스턴스 사이에서 같아야 하는 것은 위의 세 가지와 업로드 디렉터리뿐이다. sticky를 켜도 동작은 하지만
롤링 배포 뒤 새 인스턴스로 부하가 고르게 퍼지지 않고, 켜야만 동작하는 것처럼 읽힌다.

### 롤링 배포와 재연결

인스턴스 하나를 내리면 그 인스턴스가 들고 있던 소켓이 **한꺼번에** 닫힌다. 페이지는 다른 인스턴스로 다시
연결하고, 연결마다 페이지의 모든 컴포넌트가 서명된 상태로 다시 join한다(상태는 페이지에 있으므로 어느
인스턴스든 받는다). 그래서 롤링 배포의 부하는 요청 수가 아니라 **join 수**다.

#### 용량은 join/s로 잰다

평상시 처리량(이벤트/s)이 넉넉해도 배포 순간에는 join이 몰린다. 필요한 join 속도는 대략

```
내리는 인스턴스의 연결 수 × 페이지당 루트 컴포넌트 수 ÷ 재연결이 퍼지는 시간(초)
```

이고, 이것을 **남은 인스턴스들의 join/s 합**이 받아야 한다. 재연결이 퍼지는 시간은 클라이언트 백오프의
첫 대기 구간, 곧 `RECONNECT_MIN_DELAY_MS`부터 `RECONNECT_MIN_DELAY_MS + RECONNECT_JITTER_MS`까지다(기본 1~5초,
폭 4초).

프로세스 하나의 join/s 실측(`make bench`, 연결 2,000개, 항목 5개 컴포넌트,
[transport-abstraction.md](./design/transport-abstraction.md) §5-1~5-3):

| 구성 | join/s |
|------|-------:|
| daphne 1개, InMemory, macOS | 1,086~1,123 |
| daphne 4개, channels-nats, macOS | 1,979~2,245 (프로세스 합) |
| uvicorn 1개, InMemory, Windows ARM64 | 677 |
| daphne 1개, InMemory, Windows x64 에뮬 | 341 |

컴포넌트가 무거우면 더 낮다(항목 50개에서 daphne 1개 691). 이슈 #124의 외부 실측은 daphne 437~613 joins/s였다.
**자기 컴포넌트로 `make bench`를 돌려 잰 값을 쓴다.**

예: 인스턴스 넷, 각 5,000연결, 페이지당 루트 컴포넌트 둘, 프로세스당 600 join/s. 하나를 내리면 10,000 join이
생긴다. 기본 폭 4초면 초당 2,500이 남은 셋(합 1,800/s)에 떨어져 넘친다. 넘치면 join이 밀리고 사용자는
끊김 표시(`.wireview-disconnected`)를 그만큼 오래 본다 — 오류는 아니지만, 밀린 사이 다음 배포 단계가 오면
쌓인다. `RECONNECT_JITTER_MS`를 10,000으로 늘리면 초당 1,000으로 떨어져 들어간다.

```python
WIREVIEW = {
    # 첫 재시도: 1~11초 사이에서 페이지마다 한 번 뽑는다
    "RECONNECT_MIN_DELAY_MS": 1000,
    "RECONNECT_JITTER_MS": 10000,
    # 이후 재시도는 1.5배씩, 30초까지
    "RECONNECT_GROW_FACTOR": 1.5,
    "RECONNECT_MAX_DELAY_MS": 30000,
}
```

키의 뜻은 [설정](./features/settings.md#페이지와-연결)에 있다. 폭을 늘리면 **평소의** 끊김(네트워크가 잠깐 끊긴
노트북)도 그만큼 늦게 복구된다는 것이 대가다. 새 값은 새로 그려진 페이지부터 적용된다 — 이미 열린 페이지는
자기가 로드될 때의 값으로 재연결한다.

첫 대기의 끝(`RECONNECT_MIN_DELAY_MS + RECONNECT_JITTER_MS`)은 `RECONNECT_MAX_DELAY_MS` 이하로 둔다. 어떤 대기도
상한을 넘지 못하므로, 넘는 쪽을 뽑은 페이지는 상한에서 다시 한꺼번에 붙는다. 지터만 늘리고 상한을 그대로 두면
그렇게 된다. 이 조합과 클라이언트가 쓸 수 없는 값(음수, 숫자가 아닌 값), 0으로 도는 대기는 `manage.py check`가
`wireview.W016`으로 알린다. 환경 변수에서 읽는다면 `int()`로 바꿔 넣는다 — 문자열도 경고 대상이다.

#### SIGTERM 드레인 절차

wireview가 소켓을 조금씩 닫아 주지는 않는다. ASGI 서버가 종료 신호에 소켓을 한꺼번에 닫고, 흩는 것은 위의
클라이언트 백오프다. 한 인스턴스를 내리는 순서:

1. **새 연결을 먼저 끊는다.** 로드밸런서에서 인스턴스를 빼고(ALB 대상 등록 해제, Kubernetes는 파드가 종료 중이
   되면 엔드포인트에서 빠진다) 그것이 퍼질 시간을 준다 — Kubernetes면 `preStop`에 `sleep 10`. 이미 열린
   WebSocket은 이 단계에서 끊기지 않는다.
2. **SIGTERM.** uvicorn은 새 연결을 받지 않고, 열린 모든 WebSocket을 닫힘 코드 **1012**(service restart)로
   닫은 뒤, 앱 태스크가 끝나기를 `--timeout-graceful-shutdown`초까지 기다리고 남은 것을 취소한다. 소켓이 닫히면
   wireview가 연결마다 컴포넌트의 `leaving()`을 부르고 구독을 정리한다(`connection_closed`의 `code`가 1012).
   `leaving()`에서 presence를 지우거나 DB에 쓰는 앱이면 이 시간이 그만큼 필요하다.

   ```bash
   uvicorn myproject.asgi:application --workers 4 --timeout-graceful-shutdown 20 ...
   ```

   Kubernetes의 `terminationGracePeriodSeconds`는 `preStop` 대기와 이 값의 합보다 길게 둔다.
3. **다음 인스턴스로 넘어가기 전에 가라앉는 것을 본다.** 남은 인스턴스의 `connection_opened` 속도가 평소로
   돌아오고 `wireview_connections` 합이 배포 전 수준이 될 때까지 기다린다. 쿨다운 없이 연달아 내리면 방금
   재연결한 페이지들이 또 끊긴다. `maxSurge`로 새 인스턴스를 먼저 띄우면 남은 용량이 줄지 않는다.

**daphne는 이 절차에서 `leaving()`을 부르지 않는다.** daphne는 종료할 때 앱 코루틴을 취소할 뿐 WebSocket에
`disconnect`를 보내지 않으므로(`daphne/server.py`의 `kill_all_applications`), 컴포넌트의 `leaving()`과
`connection_closed`가 돌지 않는다. 그 정리에 기대는 앱은 uvicorn을 쓴다.

**배포가 서명 키를 바꾸면** 재연결한 모든 페이지가 `invalid`로 거절돼 전체 새로고침을 한다 — join이 아니라
HTTP 페이지 렌더가 몰린다. 키를 바꿀 때는 옛 키를 `SIGNING_KEY_FALLBACKS`(또는 `SECRET_KEY_FALLBACKS`)에 두고
배포한다([업그레이드](#업그레이드-서명된-컴포넌트-상태)). 배포 중 `wireview_join_rejected_total{reason="invalid"}`가
오르면 이것이다.

### 청크 업로드와 다중 프로세스

청크는 WebSocket이 아니라 HTTP로 오므로 로드밸런서가 아무 워커에나 보낸다. #83 이후로는 그래도
된다. 엔드포인트가 업로드 상태를 하나도 들고 있지 않고 서명된 토큰만으로 판단해, 토큰에서 계산한
경로에 쓰기 때문이다. 스티키 라우팅도, 업로드 전용 워커도 필요 없다.

모든 워커에서 같아야 하는 값이 둘이다.

| 값 | 왜 |
|---|---|
| `WIREVIEW["UPLOAD_TEMP_DIR"]` | 청크 저장소. 청크를 받는 워커와 완성된 파일을 읽는 워커가 같은 디렉터리를 봐야 한다. 미설정이면 시스템 temp인데, 한 호스트 안에서는 그것이 이미 공유다 |
| `WIREVIEW["SIGNING_KEY"]` (또는 `SECRET_KEY`) | 토큰이 청크의 유일한 권한 증거다. 키가 다른 워커는 403을 준다 |

| 배포 | 되나 |
|---|---|
| 한 호스트에 워커 N개 (포트마다 uvicorn + Caddy — 위 Windows 구성 그대로) | **된다.** 추가 인프라 0 |
| 여러 호스트 | `UPLOAD_TEMP_DIR`을 공유 볼륨(NFS/EFS)으로 두거나, external 업로드(`external=`, presigned S3/GCS — `docs/features/external-uploads.md`)를 쓴다. external은 워커를 아예 지나지 않는다 |
| 공유 볼륨 없는 여러 호스트 | 아직 안 된다. 오브젝트 스토리지 백엔드가 필요한데, Django Storage API에 append가 없어 청크가 다른 쓰기 모델이 된다 |

업로드 중에 워커가 죽으면 그 청크 파일이 남고, 무상태 엔드포인트는 이미 떠난 컴포넌트의 청크를
토큰이 만료될 때까지 받아 준다. 둘 다 `UPLOAD_TOKEN_MAX_AGE`로 묶이므로 정리 기준은 나이 하나다.
쓰기 경로가 워커당 10분에 한 번 기회적으로 청소하고, cron으로 돌리고 싶으면 다음이 있다.

```bash
python manage.py wireview_upload_gc
```

상세는 `docs/features/chunked-uploads.md`.

### 수직 확장

1. 워커 수를 늘린다: `--workers N` (N = CPU 코어 수). 비동기 워커 하나가 연결 수천 개를 들고 있으므로 WSGI의
   `코어 × 2 + 1` 공식은 맞지 않는다. 1개에서 4개로 늘렸을 때의 실측은 [Windows 단일 서버](#windows-단일-서버-uvicorn--n--caddy-nats-sqlite)에 있다
2. uvloop을 쓴다
3. 데이터베이스 커넥션을 재사용한다

### 속도 제한

```python
# middleware.py
from django.core.cache import cache

class WebSocketRateLimitMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            ip = scope["client"][0]
            key = f"ws_rate_{ip}"
            # add()는 키가 없을 때만 만들므로 창은 첫 연결에서 60초로 고정되고 연결마다 늘어나지 않는다
            await cache.aadd(key, 0, 60)
            try:
                count = await cache.aincr(key)
            except ValueError:  # add와 incr 사이에 창이 끝났다
                await cache.aset(key, 1, 60)
                count = 1
            if count > 100:  # 분당 연결 100개. 101번째부터 거절한다
                await send({"type": "websocket.close", "code": 4029})
                return
        return await self.inner(scope, receive, send)
```

`incr`이 원자적인 캐시(Redis, Memcached)여야 동시 연결이 같은 값을 읽고 한도를 넘기지 않는다. 프로세스가 여럿이면
`LocMemCache`는 프로세스마다 따로 센다.

이것은 **연결** 수만 센다. 열린 연결 안에서 오는 이벤트에는 wireview가 빈도·크기 상한을 두지 않는다 —
`.throttle`·`.debounce`는 브라우저에서만 돈다. 이벤트 속도 제한은 `handle_event` 훅으로 건다
([라이프사이클 훅](features/lifecycle-hooks.md#속도-제한)). 프레임 크기 상한은 uvicorn의 `--ws-max-size`(기본 16MB)다.

## 문제 해결

**WebSocket 연결이 끊긴다**

- 프록시 타임아웃 설정을 본다
- 브로커 연결이 안정적인지 확인한다
- 서버 자원 한도를 확인한다

**메모리를 많이 쓴다**

- 컴포넌트 인스턴스 수를 관찰한다
- 이벤트 핸들러의 누수를 찾는다
- 스트림 사용 패턴을 다시 본다

**렌더가 느리다**

- `DEBUG_SYNC_TRANSITIONS`를 잠시 켠다
- Django Debug Toolbar로 프로파일링한다
- N+1 질의를 찾는다

최적화는 [성능 가이드](PERFORMANCE.md)에 있다.
