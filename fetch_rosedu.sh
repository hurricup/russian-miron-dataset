#!/usr/bin/env bash
# Wrapper for fetch_rosedu.py -- pulls the Zlatoust lexical minimum into
# rosedu_cache/ and combines it into rosedu_words.json.
# Safe to interrupt (Ctrl-C) and re-run: it resumes from the cache.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found -- install it first" >&2
  exit 1
fi

exec python3 "$HERE/fetch_rosedu.py" "$@"
