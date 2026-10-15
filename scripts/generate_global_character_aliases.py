"""Draft anagram aliases for global character names (copyright-safe display names).

Writes the draft to data/global_character_aliases.json; after generation the file is the
source of truth and is hand-reviewed (OVERRIDES below only seeds known-bad drafts).

Rules: every name token (letter run) is rearranged into a pronounceable anagram of itself
(accents folded to ASCII); particles/generic titles in KEEP stay; camelCase segments are
rearranged separately; digits/punctuation/spacing stay. Identical original tokens always
map to the same alias token, so a person's first name stays consistent across characters.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "global_character_source.json"
LOCALES = ROOT / "data" / "global_character_locales.json"
OUTPUT = ROOT / "data" / "global_character_aliases.json"
KEEP = {"de", "da", "di", "van", "von", "der", "of", "the", "la", "le", "el", "al", "dr", "st",
        "lord", "kral", "buyuk", "baba", "bin", "ibn"}
# Hand-picked replacements for drafts that read poorly (folded original token -> alias token).
OVERRIDES = {
    "john": "honj", "pitt": "tipt", "jinx": "xinj", "musk": "smuk", "bill": "libl", "henry": "heryn",
    "marx": "xmar", "max": "xam", "alan": "lana", "ali": "lia", "kant": "nakt", "hume": "mehu",
    "croft": "forct", "momo": "moom", "zuckerberg": "burgerzeck",
}
# Non-name tokens inside skill texts that are real names/brands: folded original -> extra alias.
EXTRA_SKILL_TERMS = {"listerine": "nelisteri"}
SKILL_TERMS = ("jarvis", "turing", "sonic", "hawking", "pascal", "joule", "kelvin", "nobel", "boyle", "faraday", "avogadro")
VOWELS = set("aeiouy")
TOKEN = re.compile(r"[^\W\d_]+")
CAMEL = re.compile(r"[A-Z\u00c0-\u00de\u0130][^A-Z\u00c0-\u00de\u0130]*|[^A-Z\u00c0-\u00de\u0130]+")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "I"))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def segments(token: str) -> list[str]:
    return CAMEL.findall(token) if token != token.upper() or len(token) < 2 else [token]


def trigram_model(words: list[str]) -> Counter:
    model: Counter = Counter()
    for word in words:
        padded = f"^^{word}$"
        for i in range(len(padded) - 2):
            model[padded[i:i + 3]] += 1
    return model


def score(word: str, original: str, model: Counter) -> float:
    padded = f"^^{word}$"
    total = sum(math.log(model[padded[i:i + 3]] + 0.02) for i in range(len(padded) - 2))
    runs = max((len(m.group()) for m in re.finditer(r"[^aeiouy]+", word)), default=0)
    vruns = max((len(m.group()) for m in re.finditer(r"[aeiouy]+", word)), default=0)
    same = sum(a == b for a, b in zip(word, original))
    ratio = sum(c in VOWELS for c in word) / len(word)
    return total - 6 * max(0, runs - 2) - 6 * max(0, vruns - 2) - 4 * same - 8 * abs(ratio - 0.42)


def anagram(word: str, model: Counter, taken: set[str], seed: int) -> str:
    if word in KEEP or len(word) < 3:
        return word
    rng = random.Random(f"{seed}:{word}")
    letters = list(word)
    candidates = set(itertools.permutations(letters)) if len(word) <= 6 else set()
    for _ in range(2500 if len(word) > 6 else 0):
        rng.shuffle(letters)
        candidates.add(tuple(letters))
    ranked = sorted(("".join(c) for c in candidates), key=lambda w: -score(w, word, model))
    for choice in ranked:
        if choice != word and choice not in taken:
            return choice
    raise ValueError(f"No anagram for {word!r}")


def recase(original: str, alias: str) -> str:
    if original.isupper() and len(original) > 1:
        return alias.upper()
    return alias.capitalize() if original[:1].isupper() else alias


def main() -> int:
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    corpus = [fold(t) for row in rows for t in TOKEN.findall(row["name"])]
    corpus += [fold(t) for row in rows for skill in row["skills"] for t in TOKEN.findall(skill)]
    # Latin-script words from the 35 locale texts give a multilingual phonotactics model.
    locales = json.loads(LOCALES.read_text(encoding="utf-8"))
    corpus += [w for t in locales.values() for text in t.values() for w in map(fold, TOKEN.findall(text)) if w.isascii() and len(w) > 2]
    model = trigram_model(corpus)
    originals = {fold(t) for row in rows for t in TOKEN.findall(row["name"])}
    token_map: dict[str, str] = dict(OVERRIDES)
    taken: set[str] = set(originals)

    def rewrite(name: str, avoid: set[str]) -> str:
        def replace(match: re.Match) -> str:
            token = match.group()
            parts = []
            for seg in segments(token):
                key = fold(seg)
                if key not in token_map:
                    token_map[key] = anagram(key, model, taken | avoid, 0)
                    taken.add(token_map[key])
                parts.append(recase(seg, token_map[key]) if key not in KEEP else seg)
            return "".join(parts)
        return TOKEN.sub(replace, name)

    aliases: dict[str, dict[str, str]] = {}
    used: set[str] = set()
    from scripts.build_global_character_catalog import slug_id  # noqa: PLC0415

    for row in rows:
        name = row["name"].strip()
        raw_id = "gl_" + "_".join(p for p in (slug_id(row["category"]), slug_id(row["subcategory"] or row["category"]), slug_id(name)) if p)
        alias = rewrite(name, set())
        if alias in used:  # same person listed twice: pick a different arrangement of the first token
            token = TOKEN.search(name).group()
            alt = anagram(fold(token), model, taken | {fold(t) for t in TOKEN.findall(alias)}, 1)
            alias = alias.replace(TOKEN.search(alias).group(), recase(token, alt), 1)
            taken.add(alt)
        used.add(alias)
        if raw_id.endswith("terminator_t_800"):
            alias = alias.replace("T-800", "T-008")
        aliases[raw_id] = {"original": name, "alias": alias}
    skill_terms = {k.capitalize(): token_map[k].capitalize() for k in SKILL_TERMS}
    skill_terms.update({k.capitalize(): v.capitalize() for k, v in EXTRA_SKILL_TERMS.items()})
    OUTPUT.write_text(json.dumps({"schema_version": "1.0.0", "characters": aliases, "skill_terms": skill_terms}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(aliases)} aliases ({len(token_map)} distinct tokens).")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
