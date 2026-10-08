#!/usr/bin/env bash
# Windows benchmark lane on a real machine reached over SSH (OpenSSH server, PowerShell as its shell).
# Unlike run.sh (Parallels guest under emulation) this is a native x64 Windows box.
#
#   bench/windows/ssh.sh stage       # archive HEAD, fetch nats-server, copy them and the scripts to C:\bench
#   bench/windows/ssh.sh provision   # uv, Python, one venv (daphne, uvicorn, granian, channels-nats)
#   bench/windows/ssh.sh run         # ssh-seq.ps1 in the foreground (the SSH session ends its children)
#   bench/windows/ssh.sh run '<bench.servers args>[;<more>]'   # only these steps
#   bench/windows/ssh.sh collect     # copy the bench.servers results into bench/results
#
# WIN_HOST picks the machine (default allieus-macbook-2017-win10). Keep the machine on AC power
# and, on a laptop, the lid open: ssh-seq.ps1 holds off sleep only while it runs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOST="${WIN_HOST:-allieus-macbook-2017-win10}"
NATS_VERSION="${NATS_VERSION:-v2.14.6}"
GRANIAN_VERSION="${GRANIAN_VERSION:-$(sed -n 's/^GRANIAN_VERSION ?= //p' "$ROOT/Makefile")}"
SSH=(ssh -o BatchMode=yes -o ServerAliveInterval=30 "$HOST")

ps() { "${SSH[@]}" "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $1"; }

stage() {
  local tmp sha
  tmp="$(mktemp -d)"
  sha="$(git -C "$ROOT" rev-parse --short=7 HEAD)"
  git -C "$ROOT" archive --format=zip -o "$tmp/wireview.zip" HEAD
  curl -sfL -o "$tmp/nats-server.zip" \
    "https://github.com/nats-io/nats-server/releases/download/$NATS_VERSION/nats-server-$NATS_VERSION-windows-amd64.zip"
  echo "$sha" > "$tmp/commit.txt"
  # The same versions as uv.lock on the host, so both machines measure the same stack.
  { uv export --project "$ROOT" --frozen --all-extras --no-hashes --no-emit-project; echo "granian==$GRANIAN_VERSION"; } \
    > "$tmp/requirements.txt"
  "${SSH[@]}" "New-Item -ItemType Directory -Force -Path C:\\bench | Out-Null"
  scp -q -o BatchMode=yes "$tmp/wireview.zip" "$tmp/nats-server.zip" "$tmp/commit.txt" "$tmp/requirements.txt" \
    "$ROOT/bench/windows/ssh-setup.ps1" "$ROOT/bench/windows/ssh-seq.ps1" "$HOST:C:/bench/"
  rm -rf "$tmp"
  echo "staged: $sha"
}
provision() { ps 'C:\bench\ssh-setup.ps1'; }
run() {
  local steps="${1:-}"
  if [ -n "$steps" ]; then
    "${SSH[@]}" "\$env:BENCH_STEPS='$steps'; powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\\bench\\ssh-seq.ps1"
  else
    ps 'C:\bench\ssh-seq.ps1'
  fi
}
collect() {
  local tmp
  tmp="$(mktemp -d)"
  scp -q -o BatchMode=yes "$HOST:C:/bench/wireview/bench/results/*-servers-*.json" "$tmp/"
  for f in "$tmp"/*.json; do cp "$f" "$ROOT/bench/results/" && echo "wrote bench/results/$(basename "$f")"; done
  rm -rf "$tmp"
}

case "${1:-}" in
  stage|provision|run|collect) "$@" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
