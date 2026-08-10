#!/usr/bin/env bash
# Wrapper for generate.py -- builds russian_<level>.xml from the CSV levels and
# the claude_data/ explanations. Re-runnable; only processed words are included.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found -- install it first" >&2
  exit 1
fi

exec python3 "$HERE/generate.py" "$@"
