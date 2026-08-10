#!/usr/bin/env python3
"""Fetch the official Zlatoust lexical minimum from ros-edu.ru.

The /basic-dictionary page loads its data from a JSON endpoint (POST /380,
action=getPublications), 40 records per page. This pulls the whole set
(level_id=0 = all levels) and caches it, so it can later be merged into our
deck data as a more authoritative source than SMARTool (all parts of speech,
stress marks, grammar notes, per-word CEFR levels).

Each record: {id, word_rus (with stress ́), word_addition (grammar/forms),
word_eng (gloss), level_ids ("1, 2, ..."), categories (theme ids)}.
Levels: 1=A1, 2=A2, 3=B1, 4=B2 (5/6=C1/C2 are not published here).

Design (mirrors oxford/translate.py):
- Cache one file per page under rosedu_cache/; a cached page is skipped, so the
  run is fully resumable.
- Politeness: a short random sleep between real fetches (never on cache hits).
- Ban/backoff: HTTP error or a non-JSON/unsuccessful body triggers backoff that
  starts at 30s and doubles up to 10min, retrying the SAME page.
- Atomic writes (temp + rename) so an interrupt can't corrupt a cache file.
- After fetching, combines all pages into rosedu_words.json (deduped by id).
"""

import glob
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "rosedu_cache")
COMBINED = os.path.join(HERE, "rosedu_words.json")

ENDPOINT = "https://www.ros-edu.ru/380"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
PER_PAGE = 40

MIN_SLEEP = 0.5
MAX_SLEEP = 1.5
BAN_SLEEP_START = 30.0
BAN_SLEEP_MAX = 10 * 60.0


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def page_path(page):
    return os.path.join(CACHE_DIR, f"page_{page:04d}.json")


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


class Banned(Exception):
    """Raised when the server errors or returns an unusable body."""


def fetch_page(page):
    """Fetch one page's parsed JSON. Raises Banned on any unusable response."""
    body = urllib.parse.urlencode({
        "action": "getPublications",
        "collection_id": 0,
        "page": page,
        "level_id": 0,
        "category_id": "",
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body, headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as ex:
        raise Banned(f"HTTP {ex.code}") from ex
    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        raise Banned("non-JSON response") from ex
    if not data.get("success"):
        raise Banned(f"success=false: {data.get('message', '')[:100]}")
    return data


def fetch_with_backoff(page):
    sleep = BAN_SLEEP_START
    while True:
        try:
            return fetch_page(page)
        except (Banned, urllib.error.URLError) as ex:
            log(f"  problem on page {page} ({ex}); sleeping {int(sleep)}s before retry")
            time.sleep(sleep)
            sleep = min(sleep * 2, BAN_SLEEP_MAX)


def combine():
    """Merge all cached pages into COMBINED (list of records, deduped by id)."""
    by_id = {}
    for path in sorted(glob.glob(os.path.join(CACHE_DIR, "page_*.json"))):
        with open(path, encoding="utf-8") as f:
            for rec in json.load(f).get("data", []):
                by_id[rec["id"]] = rec
    records = sorted(by_id.values(), key=lambda r: int(r["id"]))
    write_atomic(COMBINED, json.dumps(records, ensure_ascii=False, indent=1) + "\n")
    return len(records)


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    first = fetch_page(1)
    write_atomic(page_path(1), json.dumps(first, ensure_ascii=False))
    count = first.get("count", 0)
    pages = -(-count // PER_PAGE)
    log(f"{count} words, {pages} pages (page 1 fetched)")

    fetched = 1
    for page in range(2, pages + 1):
        if os.path.exists(page_path(page)):
            log(f"page {page}/{pages} [cached]")
            continue
        log(f"fetching page {page}/{pages} ...")
        data = fetch_with_backoff(page)
        write_atomic(page_path(page), json.dumps(data, ensure_ascii=False))
        fetched += 1
        time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))

    total = combine()
    log(f"done: {fetched} pages fetched this run; {total} words -> {os.path.basename(COMBINED)}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted; re-run to resume from cache")
        sys.exit(130)
