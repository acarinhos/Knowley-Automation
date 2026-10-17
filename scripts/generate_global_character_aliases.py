"""Generate close-to-original anagram aliases for global character names (copyright-safe display names).

Rules (per name token, i.e. letter run; camelCase segments count as separate tokens):
- tokens with <= 3 letters and particles/titles (KEEP) stay as they are;
- otherwise ONE small change keeps the letters: swap of two nearby letters or reversal of 2-3 letters,
  the first letter stays where possible ("Nikola Tesla" -> "Niko..."-style, same rhythm and look);
- the result is pronounceable (no consonant/vowel run longer than the original's, at least 2 allowed),
  differs from the original, never equals another original or alias token, and is stable: the same original
  token gets the same alias everywhere (skill terms use the same map).

Usage:
    python -m scripts.generate_global_character_aliases               # keep reviewed aliases, add new ones
    python -m scripts.generate_global_character_aliases --regenerate  # ignore existing aliases, rebuild all
"""

from __future__ import annotations

import argparse
import json
import math
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
# Real names/brands inside skill texts (folded original token) that must follow the same alias rule.
SKILL_TERMS = ("jarvis", "turing", "sonic", "hawking", "pascal", "joule", "kelvin", "nobel", "boyle", "faraday",
               "avogadro", "listerine")
BLOCKED = {"anal", "anus", "rape", "nazi", "slut", "porn", "cock", "dick", "cunt", "shit", "fuck", "piss", "tits", "boob"}
VOWELS = set("aeiouy")
TOKEN = re.compile(r"[^\W\d_]+")
CAMEL = re.compile(r"[A-ZÀ-Þİ][^A-ZÀ-Þİ]*|[^A-ZÀ-Þİ]+")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "I"))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def segments(token: str) -> list[str]:
    return CAMEL.findall(token) if token != token.upper() or len(token) < 2 else [token]


def max_run(word: str, vowels: bool) -> int:
    pattern = r"[aeiouy]+" if vowels else r"[^aeiouy]+"
    return max((len(m.group()) for m in re.finditer(pattern, word)), default=0)


def trigram_model(words: list[str]) -> Counter:
    model: Counter = Counter()
    for word in words:
        padded = f"^^{word}$"
        for i in range(len(padded) - 2):
            model[padded[i:i + 3]] += 1
    return model


def fluency(word: str, model: Counter) -> float:
    padded = f"^^{word}$"
    return sum(math.log(model[padded[i:i + 3]] + 0.02) for i in range(len(padded) - 2))


def variants(word: str, start: int) -> set[str]:
    """Words one small move away: swap of two letters at most 2 apart, or reversal of 2-3 letters (from `start`)."""
    n, out = len(word), set()
    for i in range(start, n):
        for j in range(i + 1, min(n, i + 3)):
            letters = list(word)
            letters[i], letters[j] = letters[j], letters[i]
            out.add("".join(letters))
    for i in range(start, n - 2):
        out.add(word[:i] + word[i:i + 3][::-1] + word[i + 3:])
    out.discard(word)
    return out


def pronounceable(candidate: str, original: str) -> bool:
    return (max_run(candidate, False) <= max(2, max_run(original, False))
            and max_run(candidate, True) <= max(2, max_run(original, True))
            and not re.search(r"(.)\1\1", candidate))


def similar_alias(word: str, model: Counter, taken: set[str], attempt: int = 0, force: bool = False) -> str:
    """Best small-change anagram of `word` (first letter kept when possible); `attempt` skips earlier winners.

    Short words (<= 3 letters) and KEEP words stay unless `force` (used when a whole name would otherwise be unchanged)."""
    if word in KEEP or (len(word) <= 3 and not force):
        return word
    for start in (1, 0):  # keep the first letter; only move it when nothing else is pronounceable
        pool = [c for c in variants(word, start) if c not in taken and c not in BLOCKED and pronounceable(c, word)]
        if pool:
            ranked = sorted(pool, key=lambda c: (-fluency(c, model), c))
            return ranked[min(attempt, len(ranked) - 1)]
    raise ValueError(f"No pronounceable small change for {word!r}")


def recase(original: str, alias: str) -> str:
    if original.isupper() and len(original) > 1:
        return alias.upper()
    return alias.capitalize() if original[:1].isupper() else alias


def build_model(rows: list[dict]) -> Counter:
    corpus = [fold(t) for row in rows for t in TOKEN.findall(row["name"])]
    corpus += [fold(t) for row in rows for skill in row["skills"] for t in TOKEN.findall(skill)]
    locales = json.loads(LOCALES.read_text(encoding="utf-8"))
    corpus += [w for t in locales.values() for text in t.values() for w in map(fold, TOKEN.findall(text))
               if w.isascii() and len(w) > 2]
    return trigram_model(corpus)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regenerate", action="store_true", help="Ignore the existing aliases and rebuild all of them.")
    args = parser.parse_args(argv)
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    model = build_model(rows)
    originals = {fold(t) for row in rows for t in TOKEN.findall(row["name"])} | set(SKILL_TERMS)
    token_map: dict[str, str] = {}
    taken: set[str] = set(originals)
    existing = {} if args.regenerate or not OUTPUT.exists() else json.loads(OUTPUT.read_text(encoding="utf-8"))["characters"]

    def alias_token(key: str, attempt: int = 0) -> str:
        if attempt == 0 and key in token_map:
            return token_map[key]
        value = similar_alias(key, model, taken, attempt)
        if attempt == 0:
            token_map[key] = value
        taken.add(value)
        return value

    def rewrite(name: str) -> str:
        def replace(match: re.Match) -> str:
            parts = []
            for seg in segments(match.group()):
                key = fold(seg)
                parts.append(seg if key in KEEP or len(key) <= 3 else recase(seg, alias_token(key)))
            return "".join(parts)
        return TOKEN.sub(replace, name)

    from scripts.build_global_character_catalog import slug_id  # noqa: PLC0415

    # Reserve the aliases that stay (non-regenerate mode) so that new tokens cannot collide with them.
    for row in rows:
        previous = existing.get(
            "gl_" + "_".join(p for p in (slug_id(row["category"]), slug_id(row["subcategory"] or row["category"]), slug_id(row["name"].strip())) if p))
        if previous and previous["original"] == row["name"].strip():
            for orig, alias in zip(TOKEN.findall(previous["original"]), TOKEN.findall(previous["alias"])):
                for o, a in zip(segments(orig), segments(alias)):
                    if fold(o) not in KEEP and len(fold(o)) > 3:
                        token_map.setdefault(fold(o), fold(a))
                        taken.add(fold(a))

    aliases: dict[str, dict[str, str]] = {}
    used: set[str] = set()
    for row in rows:
        name = row["name"].strip()
        raw_id = "gl_" + "_".join(p for p in (slug_id(row["category"]), slug_id(row["subcategory"] or row["category"]), slug_id(name)) if p)
        previous = existing.get(raw_id)
        alias = previous["alias"] if previous and previous["original"] == name else rewrite(name)
        if alias in used:  # the same person listed twice: use the next-best arrangement of the first long token
            first = next(t for t in TOKEN.findall(name) if len(fold(t)) > 3 and fold(t) not in KEEP)
            alternative = alias_token(fold(first), attempt=1)
            alias = alias.replace(next(t for t in TOKEN.findall(alias) if t[:1].lower() == recase(first, alternative)[:1].lower()),
                                  recase(first, alternative), 1)
        if alias == name:  # every token was short (e.g. "Pac-Man"): move the longest one so the name is never unchanged
            segs = [(len(fold(seg)), seg) for tok in TOKEN.findall(name) for seg in segments(tok) if fold(seg) not in KEEP and len(fold(seg)) >= 3]
            _, longest = max(segs)
            changed = recase(longest, similar_alias(fold(longest), model, taken, force=True))
            taken.add(fold(changed))
            alias = name.replace(longest, changed, 1)
        if raw_id.endswith("terminator_t_800"):
            alias = alias.replace("T-800", "T-080")
        used.add(alias)
        aliases[raw_id] = {"original": name, "alias": alias}
    skill_terms = {k.capitalize(): alias_token(k).capitalize() for k in SKILL_TERMS}
    OUTPUT.write_text(json.dumps({"schema_version": "1.0.0", "characters": aliases, "skill_terms": skill_terms},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(aliases)} aliases ({len(token_map)} distinct tokens).")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
