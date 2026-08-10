#!/usr/bin/env bash
# Wrapper for explain.py -- builds Russian word explanations into claude_data/.
# Safe to interrupt (Ctrl-C) and re-run: it resumes from the cache.
# Requires the `claude` CLI on PATH.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for cmd in python3 claude; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$cmd not found -- install it first" >&2
    exit 1
  fi
done

exec python3 "$HERE/explain.py" "$@"
