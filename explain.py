#!/usr/bin/env python3
"""Build Russian word-explanation cards from the SMARTool data with Claude.

For a native kid who is learning Russian "like a foreigner": the card shows a
word and its plain-Russian explanation (no translation), with an example
sentence as a hint. This is the batch driver that produces one explanation per
lemma; a later generate step turns claude_data/ into questionary XML.

Source: miron/data-rus-eng/SMARTool_data_A1..B2.csv. Rows are grouped by
"Target language lemma"; every "Target language example sentence" for the lemma
is collected. That (lemma + its example sentences) is all the model gets.

Model step (mirrors oxford/fix_overrides.py): a batch of lemmas is baked into
one prompt and Claude is called as a pure function (tools off, JSON out). For
each lemma it returns a short simple Russian explanation and one example --
picked from the provided sentences, or its own if those are weak.

Cache (mirrors oxford/translate.py): one JSON file per word under claude_data/,
named with the URL-quoted word, written atomically. A cached word is skipped,
so the run is fully resumable -- just re-run it.

Usage: explain.py [BATCH] [MAX_BATCHES]
  BATCH        lemmas per claude call (default 200; cost is ~fixed per call, so
               bigger batches mean fewer calls)
  MAX_BATCHES  stop after N batches; 0 = all (default 1, i.e. a dry run)
"""

import csv
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data-rus-eng")
CACHE_DIR = os.path.join(HERE, "claude_data")

LEMMA_COL = "Target language lemma"
EXAMPLE_COL = "Target language example sentence"

# SMARTool marks removed entries with numeric placeholder lemmas like
# "1305 deleted" / "2412-2414 deleted". Real Russian lemmas carry no digits.
JUNK_LEMMA = re.compile(r"\d")

MODEL = "claude-opus-5"
# One-shot, no tools: the model can't read/write/loop, it just answers.
CLAUDE_BASE = ["claude", "-p",
               "--model", MODEL,
               "--output-format", "json",
               "--disallowedTools",
               "Read,Edit,Write,Bash,Glob,Grep,WebFetch,WebSearch,Task"]

# Examples shown per lemma in the prompt -- enough to pick a good one without
# bloating the batch.
MAX_EXAMPLES = 6

RULES = """\
Ты помогаешь ребёнку, который учит русский язык как иностранный: он понимает
простые слова, но незнакомые слова, формы и значения даются ему тяжело. Для
каждого слова дай короткое простое объяснение НА РУССКОМ языке (без перевода на
другой язык) — так, чтобы ребёнок понял смысл. Правила объяснения:
- 1–2 коротких простых предложения, простыми словами.
- по возможности не используй само объясняемое слово и однокоренные слова.
- объясняй основное, самое обычное значение слова.

Ещё выбери ОДИН пример употребления: возьми самый простой и естественный из
предложенных ниже; если все они плохие или слишком сложные — придумай свой
короткий естественный пример.

Пометь слова про секс и репродукцию, которые взрослый пока не хочет объяснять
ребёнку. Поставь "adult": true для слов про секс, половые органы и про то, как
зачинают, предотвращают или прерывают беременность (например: секс, половой акт,
эрекция, член, вагина, зачатие, аборт, контрацепция, презерватив, изнасилование).
Поставь "adult": false для обычных слов про семью и рождение детей, которые
ребёнок и так понимает (например: беременность, роды, родить, младенец, мама,
папа, семья), и для всех остальных слов. Не помечай слово просто за то, что тема
кажется «взрослой» или деликатной — только секс и репродукция в указанном смысле.

ВЫВОД: один JSON-объект и больше ничего (без пояснений, без ``` ). Ключи — это
данные тебе слова ровно в том же написании. Каждое слово → объект с полями:
  "explanation" — объяснение на русском,
  "example"     — одно предложение-пример,
  "adult"       — true/false (см. выше).
Пример формата:
{"трамвай": {"explanation": "Городской транспорт на рельсах. Возит людей по улицам.", "example": "Он сел в трамвай и поехал домой.", "adult": false}}
"""


def load_source():
    """lemma -> [example sentences], merged across all SMARTool CSV files.

    Lemmas may repeat across levels/rows; examples are collected in first-seen
    order and de-duplicated. Rows without a lemma or example are ignored.
    """
    lemmas = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "SMARTool_data_*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                lemma = (row.get(LEMMA_COL) or "").strip()
                example = (row.get(EXAMPLE_COL) or "").strip()
                if not lemma or JUNK_LEMMA.search(lemma):
                    continue
                bucket = lemmas.setdefault(lemma, [])
                if example and example not in bucket:
                    bucket.append(example)
    return lemmas


# Characters illegal in a path segment on Windows (the strictest of the three);
# this is a superset of macOS (`/`, `:`) and Linux (`/`). Control chars and a
# trailing dot/space (also rejected by Windows) are handled separately. Anything
# else -- including Cyrillic -- is kept as-is so filenames stay readable.
_FORBIDDEN = set('<>:"/\\|?*')


def safe_filename(word):
    """Percent-encode only path-hostile characters, keeping the word readable."""
    out = []
    for ch in word:
        if ch in _FORBIDDEN or ord(ch) < 32:
            out.append("%%%02X" % ord(ch))
        else:
            out.append(ch)
    name = "".join(out)
    if name and name[-1] in " .":  # Windows forbids a trailing dot or space
        name = name[:-1] + "%%%02X" % ord(name[-1])
    return name + ".json"


def cache_path(word):
    return os.path.join(CACHE_DIR, safe_filename(word))


def todo_words(lemmas):
    return sorted(w for w in lemmas if not os.path.exists(cache_path(w)))


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def build_prompt(words, lemmas):
    lines = [RULES, "", "=== СЛОВА ==="]
    for w in words:
        lines.append(f"\n## {w}")
        examples = lemmas.get(w, [])[:MAX_EXAMPLES]
        if examples:
            lines.append("примеры:")
            lines += [f"  - {ex}" for ex in examples]
        else:
            lines.append("примеры: (нет)")
    return "\n".join(lines)


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if "\n" in text else text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            return json.loads(text[i:j + 1])
        raise


def call_claude(prompt):
    """Run one claude call; return (result_obj, cost_usd)."""
    # Prompt goes on stdin: --disallowedTools is variadic and would otherwise
    # swallow a positional prompt argument.
    proc = subprocess.run(CLAUDE_BASE, input=prompt, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"claude failed: {proc.stderr.strip()[:400]}")
    envelope = json.loads(proc.stdout)
    cost = envelope.get("total_cost_usd", 0) or 0
    text = envelope.get("result", "") if isinstance(envelope, dict) else ""
    return extract_json(text), cost


def write_results(obj, batch_words):
    """Write one cache file per returned word. Returns the words written."""
    written = []
    allowed = set(batch_words)
    for word, spec in obj.items():
        if word not in allowed:
            print(f"  ! model returned unrequested word {word!r}, skipping")
            continue
        if not isinstance(spec, dict):
            continue
        explanation = (spec.get("explanation") or "").strip()
        example = (spec.get("example") or "").strip()
        if not explanation:
            print(f"  ! no explanation for {word!r}, skipping")
            continue
        payload = {"word": word, "explanation": explanation, "example": example,
                   "adult": bool(spec.get("adult"))}
        write_atomic(cache_path(word),
                     json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        written.append(word)
    return written


def main():
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    max_batches = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    lemmas = load_source()
    words = todo_words(lemmas)
    os.makedirs(CACHE_DIR, exist_ok=True)

    total = len(words)
    batches = [words[i:i + batch] for i in range(0, total, batch)]
    if max_batches:
        batches = batches[:max_batches]
    print(f"{len(lemmas)} lemmas total, {total} without explanations; "
          f"running {len(batches)} of {-(-total // batch) if total else 0} batches "
          f"(batch={batch}, model={MODEL})")

    total_cost = 0.0
    all_written = []
    for n, bw in enumerate(batches, 1):
        print(f"\n>>> batch {n}/{len(batches)} ({len(bw)} words): {bw[0]}..{bw[-1]}")
        obj, cost = call_claude(build_prompt(bw, lemmas))
        written = write_results(obj, bw)
        total_cost += cost
        all_written += written
        print(f"  wrote {len(written)} explanations; batch cost ${cost:.4f}")

    print(f"\ntotal: {len(all_written)} explanations written, ${total_cost:.4f}")
    if max_batches:
        print("(dry run: pass a 2nd arg of 0 to process all batches)")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted; re-run to resume from cache")
        sys.exit(130)
