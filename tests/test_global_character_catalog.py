"""Alias (copyright-safe naming) and catalogue invariants for the global character data."""

import json
import re
import unittest
from pathlib import Path

from core.character_catalog import CHARACTER_LOCALES, load_global_character_catalog
from scripts.build_global_character_catalog import rename_text, slug_id
from scripts.export_global_character_runtime_map import RULES
from scripts.generate_global_character_aliases import KEEP, TOKEN, fold

DATA = Path(__file__).resolve().parents[1] / "data"
SOURCE = json.loads((DATA / "global_character_source.json").read_text(encoding="utf-8"))
ALIASES = json.loads((DATA / "global_character_aliases.json").read_text(encoding="utf-8"))
CHARACTERS = load_global_character_catalog()
# Generic words that merely contain/equal an original name token (Turkish "Böyle", "max_selections", song title...).
FALSE_POSITIVES = {"temel", "iron", "jean", "monet", "boyle", "sandwich"}


def visible_texts(character):
    yield from (character.name, character.title, character.role, character.category_name, character.sub_category_name)
    for translation in character.translations.values():
        yield from translation.values()
    for skill in character.skills:
        yield from (skill.name, skill.source_description, str(skill.model_extra.get("source_sheet_text", "")))
        yield json.dumps([effect.model_dump() for effect in skill.effects], ensure_ascii=False)


class AliasTests(unittest.TestCase):
    def test_every_character_has_alias_and_ids_are_stable(self):
        by_id = {c.id: c for c in CHARACTERS}
        self.assertEqual(len(CHARACTERS), len(SOURCE))
        for row in SOURCE:
            sub = row["subcategory"] or row["category"]
            raw_id = "gl_" + "_".join(p for p in (slug_id(row["category"]), slug_id(sub), slug_id(row["name"])) if p)
            self.assertIn(raw_id, by_id)
            self.assertEqual(ALIASES["characters"][raw_id]["original"], row["name"].strip())
            self.assertTrue(all(s.skill_id == f"sk_{raw_id}_l{s.skill_slot}" for s in by_id[raw_id].skills))

    def test_aliases_are_unique_anagrams_that_differ_from_the_original(self):
        seen = set()
        for cid, entry in ALIASES["characters"].items():
            original, alias = entry["original"], entry["alias"]
            self.assertNotEqual(original, alias, cid)
            self.assertNotIn(alias, seen, f"alias collision: {alias}")
            seen.add(alias)
            o_tokens, a_tokens = TOKEN.findall(original), TOKEN.findall(alias)
            self.assertEqual(len(o_tokens), len(a_tokens), cid)
            for o, a in zip(o_tokens, a_tokens):
                fo, fa = fold(o), fold(a)
                self.assertEqual(sorted(fo), sorted(fa), f"{o} -> {a}")
                if fo not in KEEP and len(fo) >= 3:
                    self.assertNotEqual(fo, fa, f"{o} unchanged")
                    ratio = sum(c in "aeiouy" for c in fa) / len(fa)
                    self.assertTrue(0.15 <= ratio <= 0.75, f"{a} unpronounceable ratio")
                    self.assertIsNone(re.search(r"[^aeiouy]{4}|[aeiouy]{4}", fa), a)

    def test_catalog_uses_aliases_and_hides_original_names(self):
        for character in CHARACTERS:
            entry = ALIASES["characters"][character.id]
            self.assertEqual(character.name, entry["alias"])
            self.assertTrue(all(t["name"] == entry["alias"] for t in character.translations.values()))
        text = fold("\n".join(t for c in CHARACTERS for t in visible_texts(c)))
        for entry in ALIASES["characters"].values():
            for token in TOKEN.findall(entry["original"]):
                f = fold(token)
                if len(f) >= 4 and f not in KEEP and f not in FALSE_POSITIVES:
                    self.assertIsNone(re.search(rf"(?<![a-z]){f}(?![a-z])", text), f"original name token still visible: {token}")

    def test_rename_text_maps_whole_words_only(self):
        self.assertEqual(rename_text("Pascal Kalkanı, Pascalian", {"Pascal": "Scalap"}), "Scalap Kalkanı, Pascalian")


class CatalogTests(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(sum(len(c.skills) for c in CHARACTERS), 3 * len(CHARACTERS))
        self.assertTrue(all(set(CHARACTER_LOCALES) <= set(c.translations) for c in CHARACTERS))
        self.assertTrue(all(not c.is_active and c.region_code == "GL" for c in CHARACTERS))

    def test_every_effect_type_has_a_runtime_rule(self):
        used = {e.type for c in CHARACTERS for s in c.skills for e in s.effects}
        self.assertFalse(used - set(RULES), used - set(RULES))


if __name__ == "__main__":
    unittest.main()
