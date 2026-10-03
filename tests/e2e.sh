#!/usr/bin/env bash
# Run the E2E suite on the requested channel layer.
#
# The NATS and Redis layers need a broker. Each uses one already running (NATS_URL,
# REDIS_URL), and otherwise starts a throwaway server on a free port and stops it when
# the script ends, whether the suite passes, fails or is interrupted. `make test-e2e`
# works without any setup beyond the binary. A server that was already running is
# never stopped.
#
#   tests/e2e.sh [pytest args...]                # every E2E suite under tests/ and examples/
#   tests/e2e.sh tests/test_streams_e2e.py -x   # only the paths given (file, directory or path::test)
#   WIREVIEW_TEST_LAYER=redis tests/e2e.sh      # channels_redis
#   WIREVIEW_TEST_LAYER=memory tests/e2e.sh     # no broker (single process, which E2E is)
set -euo pipefail
cd "$(dirname "$0")/.."

LAYER="${WIREVIEW_TEST_LAYER:-nats}"
export WIREVIEW_TEST_LAYER="$LAYER"

listening() { nc -z "${2:-127.0.0.1}" "$1" >/dev/null 2>&1; }

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

find_redis() {
  # REDIS_SERVER, when set, is the only candidate: falling back to another binary would
  # start a server the caller did not ask for.
  if [ -n "${REDIS_SERVER:-}" ]; then
    command -v "$REDIS_SERVER" >/dev/null 2>&1 && { command -v "$REDIS_SERVER"; return 0; }
    return 1
  fi
  for candidate in redis-server /opt/homebrew/bin/redis-server /usr/local/bin/redis-server; do
    command -v "$candidate" >/dev/null 2>&1 && { command -v "$candidate"; return 0; }
  done
  return 1
}

# A port that accepts connections is not yet a redis that answers: it may still be
# loading. PING asks the server itself, so this needs no redis-cli.
redis_answers() {
  python3 -c 'import socket, sys
with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=1) as s:
    s.sendall(b"PING\r\n")
    sys.exit(0 if s.recv(16).startswith(b"+PONG") else 1)' "$1" "$2" >/dev/null 2>&1
}

broker_pid=""
broker_dir=""
cleanup() {
  if [ -n "$broker_pid" ]; then
    kill "$broker_pid" 2>/dev/null || true
    # The broker is this shell's child, so wait returns once it is gone: a server still
    # shutting down when the script returns is one left running.
    wait "$broker_pid" 2>/dev/null || true
  fi
  if [ -n "$broker_dir" ]; then rm -rf "$broker_dir"; fi
}
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
    url="${REDIS_URL:-redis://127.0.0.1:6379}"
    # Parsed, not cut at the last colon: redis://host:6379/0 names a database after the
    # port, and the host need not be this machine.
    read -r redis_host redis_port < <(python3 -c 'import sys, urllib.parse as u
url = u.urlsplit(sys.argv[1])
print(url.hostname or "127.0.0.1", url.port or 6379)' "$url")
    if listening "$redis_port" "$redis_host"; then
      export REDIS_URL="$url"
      echo "tests/e2e.sh: using the redis-server already on $REDIS_URL"
    else
      if [ -n "${REDIS_URL:-}" ]; then
        case "$redis_host" in
          127.0.0.1 | localhost | ::1) ;;
          *)
            echo "tests/e2e.sh: nothing answers on $REDIS_URL, and a redis-server is started only on this machine" >&2
            exit 1
            ;;
        esac
      fi
      binary="$(find_redis)" || {
        cat >&2 <<'MSG'
tests/e2e.sh: the redis layer needs a redis-server and none was found.

  install it   brew install redis   (or: apt install redis-server)
  point at one REDIS_URL=redis://host:6379 make test-e2e LAYER=redis
  or skip it   make test-e2e LAYER=memory     # E2E runs in one process, so this passes too
MSG
        if [ -n "${REDIS_SERVER:-}" ]; then echo "  REDIS_SERVER=$REDIS_SERVER is not an executable" >&2; fi
        exit 1
      }
      if [ -n "${REDIS_URL:-}" ]; then
        # The URL the caller named, database number included; only its server is missing.
        if [ "$redis_host" = localhost ]; then redis_host=127.0.0.1; fi
      else
        # Not 6379 even when it is free: a run elsewhere that looks there would take this
        # throwaway for a server of its own and lose it when this script ends.
        redis_host=127.0.0.1
        redis_port="$(free_port)"
        url="redis://127.0.0.1:$redis_port"
      fi
      broker_dir="$(mktemp -d "${TMPDIR:-/tmp}/wireview-e2e-redis.XXXXXX")"
      "$binary" --bind "$redis_host" --port "$redis_port" --save '' --appendonly no \
        --dir "$broker_dir" --logfile "$broker_dir/redis.log" >/dev/null 2>&1 &
      broker_pid=$!
      for _ in $(seq 1 150); do
        redis_answers "$redis_host" "$redis_port" && break
        if ! kill -0 "$broker_pid" 2>/dev/null; then
          echo "tests/e2e.sh: $binary exited before it answered on port $redis_port:" >&2
          cat "$broker_dir/redis.log" >&2 || true
          exit 1
        fi
        sleep 0.1
      done
      redis_answers "$redis_host" "$redis_port" || {
        echo "tests/e2e.sh: $binary did not answer on port $redis_port:" >&2
        cat "$broker_dir/redis.log" >&2 || true
        exit 1
      }
      export REDIS_URL="$url"
      echo "tests/e2e.sh: started $binary (pid $broker_pid) on $REDIS_URL"
    fi
    ;;
  memory) ;;
  *)
    echo "tests/e2e.sh: WIREVIEW_TEST_LAYER must be nats, redis or memory, not '$LAYER'" >&2
    exit 1
    ;;
esac

# The default paths only when none is given: pytest collects every path it gets,
# so a file passed next to them ran the whole E2E suite. A path is an argument
# that exists or names a test (path::name); the value of an option is neither,
# even when it spells a directory (`-k tests`).
#
# Only the options listed take a value here. The value of another one that
# names an existing path (`--cov wireview`, `--junitxml docs`) drops the
# defaults, and that is harmless: pyproject.toml sets no `testpaths`, so pytest
# collects from the rootdir, and `-m e2e` picks the same E2E suites there as
# under `tests examples`. Listing every pytest and plugin option would not end.
paths=(tests examples)
skip_value=""
for arg in "$@"; do
  if [ -n "$skip_value" ]; then
    skip_value=""
    continue
  fi
  case "$arg" in
    -k | -m | -o | -p | -c | -W | --deselect | --ignore | --rootdir | --basetemp) skip_value=1 ;;
    -*) ;;
    *::*) paths=() ;;
    *) [ -e "$arg" ] && paths=() ;;
  esac
done

# Not exec: that would replace this shell and lose the EXIT trap, leaving the
# throwaway broker running after the suite finishes.
uv run pytest ${paths[@]+"${paths[@]}"} -m e2e -v "$@"
