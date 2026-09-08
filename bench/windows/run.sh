#!/usr/bin/env bash
# Windows benchmark lane: run bench/ inside the Parallels lab guest (win11-parlab) from macOS.
# Needs the windows-parallels-lab skill (pmlab.sh) and a running lab clone.
#
#   bench/windows/run.sh stage       # archive HEAD, fetch nats-server, push the guest scripts
#   bench/windows/run.sh provision   # C:\bench: uv, Python 3.12 (arm64 + x64), venvs, nats-server
#   bench/windows/run.sh run         # start seq.ps1 detached, wait for ALL-DONE.txt, print progress
#   bench/windows/run.sh collect     # copy results into bench/results as win11-parlab-*.json
#
# Start/stop and snapshots of the VM stay manual (pmlab_start, pmlab_snapshot, pmlab_stop).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PMLAB_SH="${PMLAB_SH:-$HOME/.claude/skills/windows-parallels-lab/scripts/pmlab.sh}"
NATS_VERSION="${NATS_VERSION:-v2.14.6}"
# shellcheck source=/dev/null
source "$PMLAB_SH"
SHARE="$PMLAB_SHARE_DIR"

stage() {
  mkdir -p "$SHARE/out"
  git -C "$ROOT" archive --format=zip -o "$SHARE/wireview.zip" HEAD
  curl -sL -o "$SHARE/nats-server-windows-arm64.zip" \
    "https://github.com/nats-io/nats-server/releases/download/$NATS_VERSION/nats-server-$NATS_VERSION-windows-arm64.zip"
  for f in setup seq all; do pmlab_push "$ROOT/bench/windows/$f.ps1" "bench-$f.ps1"; done
  echo "staged: $(git -C "$ROOT" rev-parse --short=7 HEAD)"
}
provision() { [ "$(pmlab_state)" = running ] || { echo "VM not running (pmlab_start first)" >&2; exit 1; }; PMLAB_EXEC_TIMEOUT=1800 pmlab_runps bench-setup.ps1; }
run() {
  [ "$(pmlab_state)" = running ] || { echo "VM not running (pmlab_start first)" >&2; exit 1; }
  pmlab_runps bench-all.ps1
  until [ -f "$SHARE/out/ALL-DONE.txt" ]; do sleep 20; done
  tr -d '\r' < "$SHARE/out/progress.txt"
}
collect() { python3 "$ROOT/bench/windows/collect.py" "$SHARE/out" "$ROOT/bench/results" "$(git -C "$ROOT" rev-parse --short=7 HEAD)"; }

case "${1:-}" in
  stage|provision|run|collect) "$1" ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
