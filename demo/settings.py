"""
Settings of the public demo (wireview-demo.itda.work): the fourteen examples in one project.

Everything a deployment differs in comes from the environment (demo/.env.example lists it).
The demo runs behind Cloudflare Tunnel and a Caddy container (demo/compose.yaml): TLS ends at
Cloudflare, Caddy serves /static/ and passes the rest to uvicorn, so Django sees plain HTTP and
learns the scheme from X-Forwarded-Proto.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from wireview.schemas import AutoBroadcast

#: The repository root in a checkout, /app in the image: where `examples` and `demo` import from
BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ImproperlyConfigured(f"Set the environment variable {name} (demo/.env.example lists them).")
    return value


def _env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in _env(name, default).split(",") if item.strip()]


SECRET_KEY = _env("DJANGO_SECRET_KEY")
DEBUG = _env("DJANGO_DEBUG", "0") == "1"

DEMO_HOST = _env("DEMO_HOST", "wireview-demo.itda.work")
# The public name, and the names compose's own probes use (the healthchecks, Caddy's upstream).
# The WebSocket's Origin is checked against this list too (docs/DEPLOYMENT.md, "WebSocket의 Origin").
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", f"{DEMO_HOST},localhost,127.0.0.1,app,web")
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS", f"https://{DEMO_HOST}")

INSTALLED_APPS = [
    "demo",
    "examples.todo",
    "examples.chat",
    "examples.chat_lv2",
    "examples.chat_lv3",
    "examples.chat_lv4",
    "examples.dashboard",
    "examples.poll",
    "examples.rating",
    "examples.search",
    "examples.quiz",
    "examples.notifications",
    "examples.slots",
    "examples.livecomp",
    "examples.hooks",
    "wireview",
    "channels",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "demo.limits.RequestLimitMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "demo.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                # live_session reads the page's boundary from the request (wireview.W010)
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

ASGI_APPLICATION = "demo.asgi.application"

REDIS_URL = _env("REDIS_URL", "redis://127.0.0.1:6379")

# Redis, never the in-memory layer: uvicorn runs several workers, and an in-memory layer would
# carry a broadcast only to its own worker's sockets, silently (wireview.W006).
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [f"{REDIS_URL}/0"], "capacity": 1500, "expiry": 10},
    },
}

# The rate limits count here. Redis, so every worker counts the same visitor once.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"{REDIS_URL}/1",
        "KEY_PREFIX": "demo",
    },
}

# SQLite on a volume, shared by the app's workers and the janitor: WAL and a busy timeout, as
# docs/DEPLOYMENT.md's single-server recipe says. The demo's writes are a chat line or a vote
# at a time; a database server would be one more thing to run for no load it has to carry.
DATA_DIR = Path(_env("DEMO_DATA_DIR", str(BASE_DIR / ".demo-data")))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "demo.sqlite3",
        # The examples were written on testproj, which has it: chat_lv4 publishes after the
        # request's transaction commits
        "ATOMIC_REQUESTS": True,
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# Stored sessions, not signed cookies: a guest's logout has to end the session on the server
# (docs/DEPLOYMENT.md, "세션 백엔드가 로그아웃의 의미를 정한다")
SESSION_ENGINE = "django.contrib.sessions.backends.db"
# A guest account lives a day at most (the janitor deletes it), so its session needs no more
SESSION_COOKIE_AGE = 24 * 3600

WIREVIEW = {
    "BOOST_PAGES": True,
    "DEBUG_SYNC_TRANSITIONS": False,
    "DEBUG_RENDER_QUERIES": False,
    "DEBUG_RENDER_QUERIES_DIR": False,
    "AUTO_GENERATE_STUBS": False,
    # The models the examples subscribe to, as testproj names them
    "AUTO_BROADCAST": AutoBroadcast(
        model=True,
        model_pk=True,
        related=True,
        m2m=True,
        senders={
            ("dashboard", "Activity"): "__all__",
            ("dashboard", "Stat"): "__all__",
            ("notifications", "Notification"): "__all__",
            ("poll", "Option"): "__all__",
            ("quiz", "Submission"): "__all__",
            ("rating", "Product"): "__all__",
            ("rating", "Rating"): ("product",),
            ("todo", "Item"): "__all__",
        },
    ),
}

# What a visitor may send. demo/limits.py enforces them over HTTP and over the WebSocket.
DEMO_LIMITS = {
    # The longest text a field may carry, in characters: a chat line, a room name, a todo
    "TEXT_MAX": int(_env("DEMO_TEXT_MAX", "500")),
    # The largest WebSocket frame a page may send, in bytes. A join carries signed state, so
    # this is far above TEXT_MAX
    "FRAME_MAX": int(_env("DEMO_FRAME_MAX", str(64 * 1024))),
    # Events per socket: a bucket of BURST that refills EVENTS_PER_SECOND a second
    "EVENT_BURST": int(_env("DEMO_EVENT_BURST", "30")),
    "EVENTS_PER_SECOND": float(_env("DEMO_EVENTS_PER_SECOND", "5")),
    # Per visitor (IP) per minute: form posts, and new sockets. An address can be a whole
    # meetup's Wi-Fi or a carrier's NAT, so these stop a script, not a crowd
    "POSTS_PER_MINUTE": int(_env("DEMO_POSTS_PER_MINUTE", "60")),
    "SOCKETS_PER_MINUTE": int(_env("DEMO_SOCKETS_PER_MINUTE", "300")),
    # Guest accounts per visitor (IP) per hour
    "GUESTS_PER_HOUR": int(_env("DEMO_GUESTS_PER_HOUR", "60")),
}

# The janitor (manage.py demo_janitor): when the demo's data goes back to its seed each day,
# in the server's TIME_ZONE, and how long a guest account lives
DEMO_RESET_AT = _env("DEMO_RESET_AT", "04:00")
DEMO_GUEST_MAX_AGE_HOURS = int(_env("DEMO_GUEST_MAX_AGE_HOURS", "12"))

AUTH_PASSWORD_VALIDATORS = []  # guests have no password

LANGUAGE_CODE = "en-us"
TIME_ZONE = _env("DEMO_TIME_ZONE", "Asia/Seoul")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
# Collected into the image at build time; Caddy (the web service) serves a copy of it
STATIC_ROOT = BASE_DIR / "staticfiles"

# Behind Cloudflare and Caddy. Caddy sets X-Forwarded-Proto from what reached it, and
# cloudflared reaches it over plain HTTP, so Caddy is told to pass on Cloudflare's own
# header instead (demo/Caddyfile). Nothing reaches the app except through Caddy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
# The healthchecks ask over plain HTTP from inside the network
SECURE_REDIRECT_EXEMPT = [r"^health/$", r"^ready/$"]
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
SECURE_HSTS_SECONDS = int(_env("DJANGO_HSTS_SECONDS", str(30 * 24 * 3600)))
# One host, not the zone: itda.work's other names are not this project's to pin
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

SILENCED_SYSTEM_CHECKS = [
    # security.W005 (HSTS includeSubDomains) and W021 (preload): the demo is one name under
    # itda.work, and pinning HTTPS for the whole zone is not its decision to make.
    "security.W005",
    "security.W021",
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        # stdout: docker compose logs collects it, and the json-file driver rotates it (compose.yaml)
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "wireview": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "demo": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
