# Deployment Guide

Production deployment guide for django-wireview applications.

## ASGI Server Configuration

### Recommended: Uvicorn with uvloop

For best async performance, use Uvicorn with uvloop:

```bash
pip install uvicorn[standard] uvloop
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --loop uvloop
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

### Production: Redis (Recommended)

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

Only use for development/testing:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}
```

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
