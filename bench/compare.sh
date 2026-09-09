#!/usr/bin/env bash
# Benchmark a past commit in a throwaway worktree, then the current tree, and compare.
#   make bench-compare BASE=997ee59      # or: ./bench/compare.sh 997ee59 [--skip-ws]
set -euo pipefail

BASE="${1:?usage: compare.sh <git-ref> [bench.run args...]}"; shift || true
ROOT="$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short=7 "$BASE")"
WT="$(mktemp -d)/wireview-$SHA"
OUT="$ROOT/bench/results"
mkdir -p "$OUT"

echo ">>> benchmarking $BASE ($SHA) in $WT"
git worktree add --detach --quiet "$WT" "$BASE"
trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
# The bench itself comes from the current tree. The worktree already has a bench/
# directory, and `cp -R src dst` onto an existing directory would nest it as
# dst/bench, silently running the base commit's own bench code instead.
rm -rf "$WT/bench"
cp -R "$ROOT/bench" "$WT/bench"
rm -rf "$WT/bench/results" "$WT/bench/.data"
( cd "$WT" && uv sync --all-extras --quiet && uv run python -m bench.run --out "$OUT/$SHA.json" "$@" )

echo
echo ">>> benchmarking current tree ($(git rev-parse --short=7 HEAD))"
HEAD_SHA="$(git rev-parse --short=7 HEAD)"
[ -z "$(git status --porcelain -- ':!bench/results')" ] || HEAD_SHA="$HEAD_SHA-dirty"
( cd "$ROOT" && uv run python -m bench.run --out "$OUT/$HEAD_SHA.json" "$@" )

echo
echo ">>> comparison"
( cd "$ROOT" && uv run python -m bench.compare "$OUT/$SHA.json" "$OUT/$HEAD_SHA.json" )
