# Deployment Guide

Production deployment guide for django-wireview applications.

## ASGI Server Configuration

### Recommended: Uvicorn with uvloop

For best async performance, use Uvicorn with uvloop:

```bash
pip install uvicorn[standard] uvloop
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --loop uvloop \
    --ws-per-message-deflate false
```

**Why turn off permessage-deflate.** uvicorn negotiates WebSocket compression by default and
each connection then holds a zlib deflate and inflate context: 159 KB of RSS per connection,
measured. That is the whole reason uvicorn looks 4× heavier than daphne per connection
(211 KB vs 46 KB at 2,000 connections; 51 KB with compression off). wireview sends small diffs
that barely compress — a typical event payload only shrinks by 16% — so the memory buys almost
no bandwidth. Keep compression on only if your first renders or streams push large HTML, and
measure both ways first:

```bash
make bench ARGS="--connections 2000 --server uvicorn"
make bench ARGS="--connections 2000 --server uvicorn-nodeflate"
```

**Docker example:**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "myproject.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--loop", "uvloop"]
```

### Alternative: Daphne

Daphne is the reference ASGI server from Django Channels:

```bash
pip install daphne
daphne -b 0.0.0.0 -p 8000 myproject.asgi:application
```

### Alternative: Hypercorn

Hypercorn supports HTTP/2 and has good performance:

```bash
pip install hypercorn
hypercorn myproject.asgi:application --bind 0.0.0.0:8000 --workers 4
```

## Channel Layer Configuration

This project targets NATS: it is what the E2E suite runs on and what the deployment
recipes below assume. channels_redis is fully supported and is the right choice when
Redis is already part of the infrastructure. Measured side by side they perform the
same (`docs/design/transport-abstraction.md` §5-3), so the choice is operational.

### Production: NATS (what this project targets)

`channels-nats` runs the channel layer on a NATS server: one Go binary with native builds
for Linux, macOS and Windows, no persistence to operate. Consumers and wireview code are
unchanged; only the settings differ.

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_nats.NatsChannelLayer",
        "CONFIG": {"servers": [os.environ["NATS_URL"]]},
    }
}
```

Several server processes pointed at the same NATS server share one layer. That is all a
single-server SQLite deployment needs, and it is the only piece the SQLite plus Windows
premise was missing. See the channels-nats README for token auth, the Windows service
setup, and how the layer differs from channels_redis.

### Production: Redis

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

**Redis Cluster:**

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

### Development: In-Memory

Development and single-process testing only. With more than one process it does not fail,
it just delivers broadcasts to the connections in the sending process and drops the rest:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}
```

### Windows single server: uvicorn × N behind Caddy, NATS, SQLite

Measured on 2026-09-08 in a Windows 11 guest (`docs/design/transport-abstraction.md` §5-2,
`bench/results/win11-parlab-*.json`). Two Windows facts decide the layout:

- **Do not run daphne on Windows.** daphne pins asyncio to the selector loop, and CPython's
  Windows `select()` takes at most 512 sockets. A daphne process dies with
  `ValueError: too many file descriptors in select()` at about 500 connections.
- **Do not use `uvicorn --workers` on Windows.** Multi-worker mode falls back to the same
  selector loop. A single-process uvicorn runs on IOCP and held 2,000 connections in the
  benchmark. Run one uvicorn per port and let Caddy spread the connections.

Also drop `"daphne"` from `INSTALLED_APPS` on Windows: it only backs `runserver`, and importing
it switches the asyncio policy for the whole process.

```powershell
# One single-process uvicorn per core, each on its own port (no --workers).
# Register each as a service with NSSM or a scheduled task in production.
1..4 | ForEach-Object {
    Start-Process uvicorn -ArgumentList "myproject.asgi:application --host 127.0.0.1 --port $(8000 + $_)"
}
```

```caddyfile
:80 {
    reverse_proxy 127.0.0.1:8001 127.0.0.1:8002 127.0.0.1:8003 127.0.0.1:8004
}
```

Caddy is a single binary, handles WebSocket upgrades on its own, and can also terminate TLS.
The channel layer is channels-nats with `nats-server.exe` as a Windows service (see the
channels-nats README). SQLite is shared by every process, so turn on WAL and a busy timeout.
Django 5.1+ can run the pragmas itself:

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

On Django 4.2 and 5.0 run the same pragmas from a `connection_created` signal handler.

Sizing: uvicorn costs about 160 KB of RSS per connection on Windows, nearly all of it
permessage-deflate — pass `--ws-per-message-deflate false` and it drops to roughly what daphne
needs (see [ASGI server configuration](#recommended-uvicorn-with-uvloop)). Event throughput
scales with the number of uvicorn processes: 1 → 4 processes gave 3× the events per second and
a 3.6× faster broadcast in the benchmark.

## Django Settings for Production

```python
# settings.py

DEBUG = False
ALLOWED_HOSTS = ["yourdomain.com"]

# Wireview settings
WIREVIEW = {
    "USE_HTML_DIFF": True,       # Enable efficient diff updates
    "USE_HMIN": True,            # Enable HTML minification (install django-hmin)
    "DEBUG_SYNC_TRANSITIONS": False,  # Disable in production
    # Signed component state (data-state)
    "STATE_MAX_AGE": 14 * 24 * 3600,  # How long a data-state stays valid
    "STATE_REFRESH_AFTER": None,      # None = STATE_MAX_AGE // 2
    "STATE_ACCEPT_LEGACY": False,     # See "Upgrading" below
}

# Security
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True

# Database connection pooling (recommended)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "mydb",
        "CONN_MAX_AGE": 60,  # Connection pooling
        "CONN_HEALTH_CHECKS": True,
    }
}
```

## Upgrading: the signed component state

Since the v1 state envelope (`#76`) the value in `data-state` is signed together with the
component class it was issued for, and it expires after `WIREVIEW["STATE_MAX_AGE"]`
(14 days by default). The two formats used before the envelope carry no class, so the server
rejects them: a browser tab that was rendered by an older deploy reconnects, has its `join`
refused, and gets a `reload` command that loads the page again from the new deploy. That is
the intended recovery — the page comes back with the current auth context and a fresh token.

Two things follow for a rolling deploy:

- **Unsaved input in an open tab is lost when that tab reloads.** If that matters, set
  `WIREVIEW["STATE_ACCEPT_LEGACY"] = True` for the length of the rollout window and turn it
  back off afterwards. While it is on, pre-envelope states are accepted and logged at WARNING,
  and the class binding cannot be checked for them.
- **All processes must share `SECRET_KEY`.** They already had to; the difference is that a
  mismatch now shows up as reload loops rather than dropped components. The client refuses to
  reload twice within 30 seconds and logs a warning instead, so a misconfiguration is visible
  in the browser console rather than spinning the page.

`STATE_REFRESH_AFTER` is how old a token may get before a render re-issues it even though the
state has not changed. It must stay below `STATE_MAX_AGE`: a component that renders at least
once every `STATE_MAX_AGE - STATE_REFRESH_AFTER` never expires while its page is open.

## WebSocket Proxy Configuration

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

Caddy automatically handles WebSocket upgrades.

### AWS ALB

For AWS Application Load Balancer:
- Enable sticky sessions for WebSocket connections
- Set idle timeout to at least 3600 seconds
- Use target groups with WebSocket health checks

## Monitoring Recommendations

### Key Metrics

1. **WebSocket connections**
   - Active connection count
   - Connection duration
   - Reconnection rate

2. **Render performance**
   - render_diff() latency (P50, P95, P99)
   - HTML diff size distribution

3. **Channel layer**
   - Redis memory usage
   - Message queue depth
   - Publish/subscribe latency

### Prometheus Metrics

Add custom metrics with `django-prometheus`:

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

### Logging

Configure logging to capture wireview events:

```python
LOGGING = {
    "version": 1,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "wireview": {
            "handlers": ["console"],
            "level": "WARNING",  # INFO for debugging
        },
        "wireview.sync_detector": {
            "handlers": ["console"],
            "level": "WARNING",
        },
    },
}
```

## Health Checks

### HTTP Health Check

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

### WebSocket Health Check

Test WebSocket connectivity:

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

## Scaling Considerations

### Horizontal Scaling

1. Use Redis channel layer (required for multi-instance)
2. Ensure sticky sessions for WebSocket connections
3. Use shared session storage (Redis/Memcached)

### Vertical Scaling

1. Increase worker count: `--workers N` (N = 2 * CPU cores + 1)
2. Use uvloop for better async performance
3. Enable connection pooling for database

### Rate Limiting

Protect against abuse:

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
            if count > 100:  # 100 connections per minute
                await send({"type": "websocket.close", "code": 4029})
                return
            cache.set(key, count + 1, 60)
        return await self.inner(scope, receive, send)
```

## Troubleshooting

### Common Issues

**WebSocket connections dropping:**
- Check proxy timeout settings
- Verify Redis connection stability
- Check server resource limits

**High memory usage:**
- Monitor component instance count
- Check for memory leaks in event handlers
- Review stream usage patterns

**Slow renders:**
- Enable `DEBUG_SYNC_TRANSITIONS` temporarily
- Profile with Django Debug Toolbar
- Check for N+1 queries

See [Performance Guide](PERFORMANCE.md) for optimization tips.
