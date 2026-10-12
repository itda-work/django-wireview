# The demo's two images, built from the repository root (compose.yaml's build context is "..").
#
#   app  Python, the wireview package of this checkout, the examples and the demo project.
#        uvicorn serves HTTP and the WebSocket.
#   web  Caddy with a copy of the app image's collected static files (docs/DEPLOYMENT.md,
#        "컨테이너 배포": the front image is built from the app image, never a shared volume).
#
# Both are built for the machine's own platform; `docker buildx build --platform linux/amd64`
# builds the other one (demo/README.md).

# --- the client bundle (wireview.min.js is a build artifact, gitignored) ---------------------
FROM node:22-slim AS js
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY esbuild.conf.js ./
COPY wireview/static/wireview ./wireview/static/wireview
RUN npm run build

# --- Python dependencies, pinned by the repository's uv.lock ---------------------------------
FROM python:3.13-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY demo/requirements.txt ./demo/requirements.txt
# The lock's versions as constraints: the demo runs what the test suite ran
RUN uv export --frozen --no-hashes --all-extras --no-emit-project --no-header -o constraints.txt \
 && uv pip install --system --no-cache -c constraints.txt -r demo/requirements.txt

# --- app -------------------------------------------------------------------------------------
FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=demo.settings \
    DEMO_DATA_DIR=/data
COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin/uvicorn /usr/local/bin/uvicorn
WORKDIR /app
COPY wireview ./wireview
COPY --from=js /src/wireview/static/wireview/wireview.min.js ./wireview/static/wireview/wireview.min.js
COPY examples ./examples
COPY demo ./demo
# collectstatic imports the settings, which want a secret: a throwaway one for this line only
RUN DJANGO_SECRET_KEY=collectstatic python demo/manage.py collectstatic --noinput -v0 \
 && useradd --system --uid 10001 --home-dir /app demo \
 && mkdir /data && chown demo /data
USER demo
VOLUME /data
EXPOSE 8000
# Migrate and reset to the seed on every start (a restart is also a reset), then serve.
# Caddy logs each request, so uvicorn does not. Workers share Redis (channel layer, rate limits) and the SQLite file in /data.
CMD ["sh", "-c", "python demo/manage.py migrate --noinput -v0 && python demo/manage.py demo_reset && exec uvicorn demo.asgi:application --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --loop uvloop --ws-per-message-deflate false --ws-max-size 1048576 --timeout-graceful-shutdown 20 --no-server-header --no-access-log"]

# --- web -------------------------------------------------------------------------------------
FROM caddy:2.10-alpine AS web
COPY --from=app /app/staticfiles /srv/static
COPY demo/Caddyfile /etc/caddy/Caddyfile
