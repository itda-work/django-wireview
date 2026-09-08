#!/usr/bin/env bash
# Run the E2E suite on the requested channel layer.
#
# On the NATS layer this needs a nats-server. It uses one already running on NATS_URL,
# and otherwise starts a throwaway server on a free port and stops it afterwards, so
# `make test-e2e` works without any setup beyond the binary.
#
#   tests/e2e.sh [pytest args...]
#   WIREVIEW_TEST_LAYER=redis tests/e2e.sh      # channels_redis on REDIS_URL
#   WIREVIEW_TEST_LAYER=memory tests/e2e.sh     # no broker (single process, which E2E is)
set -euo pipefail
cd "$(dirname "$0")/.."

LAYER="${WIREVIEW_TEST_LAYER:-nats}"
export WIREVIEW_TEST_LAYER="$LAYER"
export DJANGO_ALLOW_ASYNC_UNSAFE=1

listening() { nc -z 127.0.0.1 "$1" >/dev/null 2>&1; }

free_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

wait_for_port() {
  for _ in $(seq 1 100); do
    listening "$1" && return 0
    sleep 0.2
  done
  echo "tests/e2e.sh: nothing came up on port $1" >&2
  return 1
}

find_nats() {
  for candidate in "${NATS_SERVER:-}" nats-server "$HOME/go/bin/nats-server"; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 && { command -v "$candidate"; return 0; }
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

broker_pid=""
cleanup() { [ -n "$broker_pid" ] && kill "$broker_pid" 2>/dev/null || true; }
trap cleanup EXIT

case "$LAYER" in
  nats)
    default_url="nats://127.0.0.1:4222"
    if [ -n "${NATS_URL:-}" ] || listening 4222; then
      export NATS_URL="${NATS_URL:-$default_url}"
      echo "tests/e2e.sh: using the nats-server already on $NATS_URL"
    else
      binary="$(find_nats)" || {
        cat >&2 <<'MSG'
tests/e2e.sh: the nats layer needs a nats-server and none was found.

  install it   brew install nats-server   (or: go install github.com/nats-io/nats-server/v2@latest)
  point at one NATS_URL=nats://host:4222 make test-e2e
  or skip it   make test-e2e LAYER=memory     # E2E runs in one process, so this passes too
MSG
        exit 1
      }
      port="$(free_port)"
      "$binary" -a 127.0.0.1 -p "$port" >/dev/null 2>&1 &
      broker_pid=$!
      wait_for_port "$port"
      export NATS_URL="nats://127.0.0.1:$port"
      echo "tests/e2e.sh: started $binary on $NATS_URL"
    fi
    ;;
  redis)
    export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379}"
    listening "${REDIS_URL##*:}" || {
      echo "tests/e2e.sh: the redis layer needs a redis-server on $REDIS_URL" >&2
      exit 1
    }
    ;;
  memory) ;;
  *)
    echo "tests/e2e.sh: WIREVIEW_TEST_LAYER must be nats, redis or memory, not '$LAYER'" >&2
    exit 1
    ;;
esac

# Not exec: that would replace this shell and lose the EXIT trap, leaving the
# throwaway nats-server running after the suite finishes.
uv run pytest tests/ -m e2e -v "$@"
