"""
Settings of the public demo (wireview-demo.itda.work): the fourteen examples in one project.

Everything a deployment differs in comes from the environment (demo/.env.example lists it).
The demo runs behind a Caddy container (demo/compose.yaml) and, in front of it, either Cloudflare
Tunnel or, in direct mode, the host's own Caddy (demo/Caddyfile.direct). TLS ends at the one in
front, the container serves /static/ and passes the rest to uvicorn, so Django sees plain HTTP and
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


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


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
        # socket_timeout=None: redis-py 8 times a read out after 5 s by default, and channels_redis
        # blocks 5 s on BZPOPMIN (django/channels_redis#422, #225)
        "CONFIG": {"hosts": [{"address": f"{REDIS_URL}/0", "socket_timeout": None}], "capacity": 1500, "expiry": 10},
    },
}

# The rate limits count here: a Redis of its own (compose.yaml's "limits"), so every worker counts
# the same visitor once and the channel layer's traffic never evicts a count. That Redis evicts
# nothing; full, it refuses writes and the limits refuse visitors (demo/limits.py, LimiterDown).
LIMITS_REDIS_URL = _env("LIMITS_REDIS_URL", f"{REDIS_URL}/1")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": LIMITS_REDIS_URL,
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
# (docs/DEPLOYMENT.md, "세션 백엔드가 로그아웃의 의미를 정한다"). The demo's store is the
# database one with a budget for new rows (demo/sessions.py)
SESSION_ENGINE = "demo.sessions"
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
# A visitor is an address (an IPv6 one by its /64). An address can be a whole meetup's Wi-Fi
# or a carrier's NAT, so the per-visitor numbers stop a script, not a crowd. Every one of them
# is the operator's to change in .env (demo/.env.example).
DEMO_LIMITS = {
    # The longest text a field may carry, in characters: a chat line, a room name, a todo, a
    # search. HTTP fields past it are refused; WebSocket texts are cut to it
    "TEXT_MAX": _int("DEMO_TEXT_MAX", 500),
    # The largest WebSocket frame a page may send, in UTF-8 bytes. A join carries signed state,
    # so this is far above TEXT_MAX
    "FRAME_MAX": _int("DEMO_FRAME_MAX", 64 * 1024),
    # Per socket, buckets of BURST that refill so many a second: every frame; frames the server
    # does not read (not JSON, an unknown command); events; first joins of a page's components
    # (a state this socket has not joined); renders without a click (a join of a state it has
    # joined, params_changed, navigated)
    "FRAME_BURST": _int("DEMO_FRAME_BURST", 120),
    "FRAMES_PER_SECOND": _float("DEMO_FRAMES_PER_SECOND", 20),
    "JUNK_BURST": _int("DEMO_JUNK_BURST", 5),
    "JUNK_PER_SECOND": _float("DEMO_JUNK_PER_SECOND", 0.2),
    "EVENT_BURST": _int("DEMO_EVENT_BURST", 30),
    "EVENTS_PER_SECOND": _float("DEMO_EVENTS_PER_SECOND", 5),
    "JOIN_BURST": _int("DEMO_JOIN_BURST", 60),
    "JOINS_PER_SECOND": _float("DEMO_JOINS_PER_SECOND", 5),
    "RENDER_BURST": _int("DEMO_RENDER_BURST", 20),
    "RENDERS_PER_SECOND": _float("DEMO_RENDERS_PER_SECOND", 2),
    # Per visitor, over all their sockets, in a window of VISITOR_WINDOW seconds: frames,
    # events, and joins and renders together
    "VISITOR_WINDOW": _int("DEMO_VISITOR_WINDOW", 10),
    "VISITOR_FRAMES": _int("DEMO_VISITOR_FRAMES", 600),
    "VISITOR_EVENTS": _int("DEMO_VISITOR_EVENTS", 100),
    "VISITOR_RENDERS": _int("DEMO_VISITOR_RENDERS", 300),
    # Sockets: new ones per visitor per minute, open at once per visitor, open at once in all
    "SOCKETS_PER_MINUTE": _int("DEMO_SOCKETS_PER_MINUTE", 300),
    "OPEN_SOCKETS_PER_VISITOR": _int("DEMO_OPEN_SOCKETS_PER_VISITOR", 40),
    "OPEN_SOCKETS_TOTAL": _int("DEMO_OPEN_SOCKETS_TOTAL", 2000),
    # HTTP per visitor per minute: every request Django answers (Caddy answers /static/), and
    # form posts among them
    "REQUESTS_PER_MINUTE": _int("DEMO_REQUESTS_PER_MINUTE", 300),
    "POSTS_PER_MINUTE": _int("DEMO_POSTS_PER_MINUTE", 60),
    # Stored rows: new sessions per visitor per hour and in all; guest accounts per visitor per
    # hour and in all (demo/sessions.py, demo/guests.py)
    "SESSIONS_PER_HOUR": _int("DEMO_SESSIONS_PER_HOUR", 60),
    "SESSIONS_MAX": _int("DEMO_SESSIONS_MAX", 20_000),
    "GUESTS_PER_HOUR": _int("DEMO_GUESTS_PER_HOUR", 60),
    "GUESTS_MAX": _int("DEMO_GUESTS_MAX", 5_000),
}

# What the demo keeps of what visitors write (demo/retention.py): the newest messages of each
# chat room (and submissions of each quiz), and the rows any one of those tables may hold -- a
# new row past it is refused. Full to the brim -- 20,000 lines of 500 emoji in each of the four
# chats, as many notifications -- that is some 200 MB; the demo's own use is a few
DEMO_KEEP_PER_ROOM = _int("DEMO_KEEP_PER_ROOM", 200)
DEMO_ROWS_MAX = _int("DEMO_ROWS_MAX", 20_000)

# The disk's last guard: the database file (with its WAL) past this many megabytes, the janitor
# resets the demo early -- every room, guest and session -- and compacts the file
# (demo_janitor). The budgets above keep it far below; reaching it is an ERROR in the log
DEMO_DB_MAX_MB = _int("DEMO_DB_MAX_MB", 1024)

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
STATIC_ROOT = Path(_env("DEMO_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
# Content-hashed names ({% static %} -> wireview/wireview.min.<hash>.js): Caddy marks them
# immutable and Cloudflare keeps them at its edge (demo/Caddyfile). A visitor in Korea reaches
# the tunnel through a far edge, so a file that has to come back to the origin costs seconds.
# Without DEBUG the names come from staticfiles.json, which collectstatic writes into the image
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}
# Each example's screenshot (#213, examples/<name>/media/<name>.webp) for the list's cards, as
# static/demo/media/<name>.webp (demo/catalog.py). The image carries the webp files only
STATICFILES_DIRS = [
    ("demo/media", media) for media in sorted((BASE_DIR / "examples").glob("*/media")) if media.is_dir()
]

# Behind Cloudflare (or the host's Caddy) and Caddy. The container's Caddy is reached over plain
# HTTP from a private address, so it passes on the X-Forwarded-Proto the one in front wrote
# (demo/Caddyfile). Nothing reaches the app except through Caddy.
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
        # Not a line per 4xx: a script the limits refuse would fill the log with them. The limits
        # log what they refuse once per socket or request, at INFO (demo.limits)
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "wireview": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "demo": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
