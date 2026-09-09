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

CMD ["uvicorn", "myproject.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--loop", "uvloop"]
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

**Redis 클러스터:**

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                ("redis-node-1", 6379),
                ("redis-node-2", 6379),
                ("redis-node-3", 6379),
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
    reverse_proxy 127.0.0.1:8001 127.0.0.1:8002 127.0.0.1:8003 127.0.0.1:8004
}
```

Caddy는 바이너리 하나이고 WebSocket 업그레이드를 알아서 처리하며 TLS 종단도 맡을 수 있다. 채널
레이어는 channels-nats이고 `nats-server.exe`를 Windows 서비스로 띄운다(channels-nats README).
SQLite는 모든 프로세스가 공유하므로 WAL과 busy timeout을 켠다. Django 5.1+는 pragma를 직접 실행할 수
있다.

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

Django 4.2와 5.0에서는 같은 pragma를 `connection_created` 시그널 핸들러에서 실행한다.

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
    "USE_HTML_DIFF": True,            # 변경분만 보낸다
    "USE_HMIN": True,                 # HTML 압축 (django-hmin 필요)
    "DEBUG_SYNC_TRANSITIONS": False,  # 운영에서는 끈다
    # 서명 상태 (data-state)
    "STATE_MAX_AGE": 14 * 24 * 3600,  # data-state 유효 기간
    "STATE_REFRESH_AFTER": None,      # None이면 STATE_MAX_AGE // 2
    "STATE_ACCEPT_LEGACY": False,     # 아래 "업그레이드" 참고
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

## 업그레이드: 서명된 컴포넌트 상태

v2 상태 봉투(`#58`) 이후 `data-state` 값은 **발급된 컴포넌트 클래스, 페이지의 `live_session`, 발급
당시의 인증 세대와 함께** 서명되고 `WIREVIEW["STATE_MAX_AGE"]`(기본 14일)이 지나면 만료된다.
v1 봉투(`#76`)와 그 이전의 두 형식은 경계를 담고 있지 않으므로 서버가 거절한다. 예전 배포가 그린 탭이 다시 붙으면 `join`이 거절되고 `reload` 명령을 받아
새 배포에서 페이지를 다시 읽는다. 그것이 의도된 복구다 — 현재 인증 컨텍스트와 새 토큰으로 돌아온다.

롤링 배포에서 따라오는 것이 둘이다.

- **열려 있던 탭의 미저장 입력은 리로드와 함께 사라진다.** 그게 문제라면 롤아웃 구간 동안
  `WIREVIEW["STATE_ACCEPT_LEGACY"] = True`로 두고 끝나면 되돌린다. 켜 둔 동안 옛 상태를 받아 주고
  WARNING으로 기록한다. v1은 클래스는 검사하지만 경계는 모르고, 그 이전 형식은 클래스도 검사할 수
  없다. **어느 쪽도 `live_session`이 걸린 페이지에는 들어가지 못한다** — "경계 없음"으로 디코드되기
  때문이다. 즉 롤아웃 플래그가 넓히는 것은 디코드되는 범위이지 경계가 받아들이는 범위가 아니다.
- **모든 프로세스가 `SECRET_KEY`를 공유해야 한다.** 전에도 그랬지만, 이제 어긋나면 컴포넌트가 조용히
  사라지는 대신 리로드 루프로 드러난다. 클라이언트는 30초 안에 두 번 리로드하기를 거부하고 경고를
  남기므로, 설정 오류가 페이지를 돌리는 대신 브라우저 콘솔에 보인다.

`live_session`을 쓰는 프로젝트에는 하나 더 있다. **로그아웃이 기존 소켓을 닫는 것은 브로커를
지난다.** InMemory 채널 레이어에서는 같은 프로세스의 연결에만 닿으므로, 다중 워커에서는 다른 워커가
들고 있는 소켓이 닫히지 않는다. `manage.py check --deploy`의 `wireview.W006`이 같은 것을 가리킨다.

`STATE_REFRESH_AFTER`는 상태가 그대로여도 토큰을 다시 발급하기까지의 나이다. 반드시
`STATE_MAX_AGE`보다 작아야 한다. `STATE_MAX_AGE - STATE_REFRESH_AFTER`마다 한 번이라도 렌더되는
컴포넌트는 페이지가 열려 있는 동안 만료되지 않는다.

## WebSocket 프록시

### Nginx

```nginx
upstream django {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://django;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws/ {
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
    reverse_proxy localhost:8000
}
```

Caddy는 WebSocket 업그레이드를 알아서 처리한다.

### AWS ALB

- WebSocket 연결에 sticky session을 켠다
- idle timeout을 최소 3600초로 둔다
- 타깃 그룹에 WebSocket 헬스체크를 건다

## 모니터링

### 볼 지표

1. **WebSocket 연결** — 활성 연결 수, 연결 지속 시간, 재연결률
2. **렌더 성능** — `render_diff()` 지연(P50, P95, P99), HTML diff 크기 분포
3. **채널 레이어** — Redis 메모리, 큐 깊이, pub/sub 지연

### Prometheus

`django-prometheus`로 직접 지표를 추가한다.

```python
from prometheus_client import Counter, Histogram

ws_connections = Counter(
    'wireview_websocket_connections_total',
    'Total WebSocket connections'
)

render_duration = Histogram(
    'wireview_render_duration_seconds',
    'Time spent rendering components',
    buckets=[.005, .01, .025, .05, .1, .25, .5, 1]
)
```

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

### WebSocket

```python
# management/commands/check_websocket.py
from django.core.management.base import BaseCommand
import websocket

class Command(BaseCommand):
    def handle(self, *args, **options):
        ws = websocket.create_connection("ws://localhost:8000/ws/")
        ws.send('{"command": "ping"}')
        result = ws.recv()
        ws.close()
        self.stdout.write(f"WebSocket OK: {result}")
```

## 확장

### 수평 확장

1. 브로커 기반 채널 레이어를 쓴다 (다중 인스턴스의 필수 조건)
2. WebSocket 연결에 sticky session을 건다
3. 세션 저장소를 공유한다 (Redis/Memcached)

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

1. 워커 수를 늘린다: `--workers N` (N = CPU 코어 × 2 + 1)
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
            count = cache.get(key, 0)
            if count > 100:  # 분당 연결 100개
                await send({"type": "websocket.close", "code": 4029})
                return
            cache.set(key, count + 1, 60)
        return await self.inner(scope, receive, send)
```

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
