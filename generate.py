#!/usr/bin/env python3
"""Generate russian_<level> questionary XMLs from SMARTool + claude_data/.

Joins two sources by lemma:
- CEFR level comes from the SMARTool CSVs (a lemma's lowest level across its
  rows), see explain.py for the source layout.
- The card body comes from claude_data/ (produced by explain.py): a plain-Russian
  explanation and an example sentence.

Card model:
- question   = the lemma (Russian word)
- answer     = the explanation
- answerNote = the example sentence (a hint under the answer)

Only PROCESSED words (those with a claude_data/ file) are included, so this is
safe to run against a partial cache and re-run as more words are explained.
Words flagged "adult": true are excluded. One file per level into the app assets:
  id    = russian-<level>    (e.g. russian-a1)
  title = Russian (<LEVEL>)  (e.g. Russian (A1))

Output: app/src/main/assets/xml/russian_<level>.xml
"""

import csv
import glob
import json
import os
from collections import defaultdict
from xml.sax.saxutils import escape

import explain

OUT_DIR = os.path.join(explain.HERE, "..", "app", "src", "main", "assets", "xml")

LEVELS = ["A1", "A2", "B1", "B2"]
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}
LEVEL_COL = "Level"


def load_levels():
    """lemma -> lowest CEFR level string among its rows (unknown levels ignored)."""
    best = {}
    for path in sorted(glob.glob(os.path.join(explain.DATA_DIR, "SMARTool_data_*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                lemma = (row.get(explain.LEMMA_COL) or "").strip()
                if not lemma or explain.JUNK_LEMMA.search(lemma):
                    continue
                rank = LEVEL_RANK.get((row.get(LEVEL_COL) or "").strip().upper())
                if rank is None:
                    continue
                if best.get(lemma) is None or rank < best[lemma]:
                    best[lemma] = rank
    return best


def load_cards():
    """Processed, non-adult cards from claude_data/ (skips empty explanations)."""
    cards, adult = [], 0
    for path in glob.glob(os.path.join(explain.CACHE_DIR, "*.json")):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("adult"):
            adult += 1
            continue
        if (d.get("explanation") or "").strip():
            cards.append(d)
    return cards, adult


def no_pipe(text):
    """The app treats '|' as a variant separator (incl. in notes), so a stray pipe
    in free-form text would split the card or break note alignment. Neutralize it."""
    return text.replace("|", "/")


def render(level, cards):
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<questionary>",
        f"    <id>russian-{level.lower()}</id>",
        f"    <title>Russian ({level})</title>",
        "    <cards>",
    ]
    for c in cards:
        lines.append("        <card>")
        lines.append(f"            <question>{escape(no_pipe(c['word']))}</question>")
        lines.append(f"            <answer>{escape(no_pipe(c['explanation'].strip()))}</answer>")
        example = no_pipe((c.get("example") or "").strip())
        if example:
            lines.append(f"            <answerNote>{escape(example)}</answerNote>")
        lines.append("        </card>")
    lines += ["    </cards>", "</questionary>", ""]
    return "\n".join(lines)


def main():
    levels = load_levels()
    cards, adult = load_cards()
    os.makedirs(OUT_DIR, exist_ok=True)

    by_level = defaultdict(list)
    no_level = 0
    for c in cards:
        rank = levels.get(c["word"])
        if rank is None:
            no_level += 1
            continue
        by_level[rank].append(c)

    placed = 0
    for level in LEVELS:
        bucket = sorted(by_level.get(LEVEL_RANK[level], []), key=lambda c: c["word"].lower())
        path = os.path.join(OUT_DIR, f"russian_{level.lower()}.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render(level, bucket))
        placed += len(bucket)
        print(f"russian_{level.lower()}.xml: {len(bucket)} cards")

    print(f"placed {placed} non-adult cards; excluded {adult} adult; "
          f"{no_level} processed words had no known CEFR level")


if __name__ == "__main__":
    main()
