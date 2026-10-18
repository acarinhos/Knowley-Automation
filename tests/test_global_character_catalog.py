"""Alias (copyright-safe naming) and catalogue invariants for the global character data."""

import json
import re
import unittest
from pathlib import Path

from core.character_catalog import CHARACTER_LOCALES, load_global_character_catalog
from scripts.build_global_character_catalog import rename_text, slug_id
from scripts.export_global_character_runtime_map import RULES
from scripts.sync_global_character_aliases_firestore import FALSE_POSITIVES, original_name_hits, planned_update
from scripts.generate_global_character_aliases import BLOCKED, KEEP, TOKEN, fold, max_run

DATA = Path(__file__).resolve().parents[1] / "data"
SOURCE: list = []
ALIASES: dict = {}
CHARACTERS: list = []


def setUpModule():
    global SOURCE, ALIASES, CHARACTERS
    SOURCE = json.loads((DATA / "global_character_source.json").read_text(encoding="utf-8"))
    ALIASES = json.loads((DATA / "global_character_aliases.json").read_text(encoding="utf-8"))
    CHARACTERS = load_global_character_catalog()



def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


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
        tokens_checked = [0, 0]
        for cid, entry in ALIASES["characters"].items():
            original, alias = entry["original"], entry["alias"]
            self.assertNotEqual(original, alias, cid)
            self.assertNotIn(alias, seen, f"alias collision: {alias}")
            seen.add(alias)
            o_tokens, a_tokens = TOKEN.findall(original), TOKEN.findall(alias)
            self.assertEqual(len(o_tokens), len(a_tokens), cid)
            for o, a in zip(o_tokens, a_tokens):
                fo, fa = fold(o), fold(a)
                self.assertEqual(sorted(fo), sorted(fa), f"{o} -> {a}")  # same letters
                if fo in KEEP or len(fo) < 4:
                    continue
                self.assertNotEqual(fo, fa, f"{o} unchanged")
                similarity = 1 - levenshtein(fo, fa) / len(fo)
                self.assertGreaterEqual(similarity, 0.5, f"{o} -> {a} is too far from the original")
                self.assertLessEqual(max_run(fa, False), max(2, max_run(fo, False)), f"{a}: consonant pile-up")
                self.assertLessEqual(max_run(fa, True), max(2, max_run(fo, True)), f"{a}: vowel pile-up")
                self.assertIsNone(re.search(r"(.)\1\1", fa), a)
                self.assertNotIn(fa, BLOCKED)
                tokens_checked[0] += 1
                tokens_checked[1] += fo[0] == fa[0]
        self.assertGreaterEqual(tokens_checked[1] / tokens_checked[0], 0.9)  # first letter and rhythm are kept

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


class SyncPlanTests(unittest.TestCase):
    def expected(self):
        return {
            "name": "Alias", "title": "T", "role": "R",
            "translations": {"en": {"name": "Alias", "title": "T", "role": "R"}},
            "skills": [{"skill_id": "s1", "name": "Sivraj Taraması", "source_description": "d", "source_sheet_text": "Sivraj Taraması: d",
                        "effects": [{"type": "manual_review", "params": {"source_text": "Sivraj Taraması: d"}}]}],
        }

    def current(self):
        return {
            "name": "Jarvis", "title": "T", "role": "R", "is_active": False, "created_at": "x", "rarity": "unassigned",
            "translations": {"en": {"name": "Jarvis", "title": "T", "role": "R", "extra": 1}},
            "skills": [{"skill_id": "s1", "name": "Jarvis Taraması", "source_description": "d", "source_sheet_text": "Jarvis Taraması: d",
                        "daily_limits": {"lvl1": 3}, "effects": [{"type": "manual_review", "params": {"source_text": "Jarvis Taraması: d", "k": 1}}]}],
        }

    def test_updates_only_allowed_fields(self):
        update = planned_update(self.current(), self.expected())
        self.assertEqual(set(update), {"name", "translations", "skills"})
        self.assertEqual(update["translations"]["en"], {"name": "Alias", "title": "T", "role": "R", "extra": 1})
        skill = update["skills"][0]
        self.assertEqual(skill["name"], "Sivraj Taraması")
        self.assertEqual(skill["daily_limits"], {"lvl1": 3})
        self.assertEqual(skill["effects"][0]["params"], {"source_text": "Sivraj Taraması: d", "k": 1})

    def test_no_update_when_current_and_idempotent(self):
        current = self.current()
        current.update(planned_update(current, self.expected()))
        self.assertEqual(planned_update(current, self.expected()), {})

    def test_skill_shape_mismatch_refused(self):
        expected = self.expected()
        expected["skills"][0]["skill_id"] = "other"
        with self.assertRaises(SystemExit):
            planned_update(self.current(), expected)

    def test_original_name_hits(self):
        self.assertEqual(original_name_hits(self.current(), ["Jarvis"]), ["jarvis"])
        self.assertEqual(original_name_hits({"name": "Sivraj"}, ["Jarvis"]), [])


class CatalogTests(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(sum(len(c.skills) for c in CHARACTERS), 3 * len(CHARACTERS))
        self.assertTrue(all(set(CHARACTER_LOCALES) <= set(c.translations) for c in CHARACTERS))
        self.assertTrue(all(c.is_active and c.region_code == "GL" for c in CHARACTERS))
        self.assertTrue(all(c.model_extra is None or "rarity" not in c.model_extra for c in CHARACTERS))
        self.assertTrue(all(c.unlock_threshold == 50 and c.level_thresholds == {"lvl1": 50, "lvl2": 100, "lvl3": 200} for c in CHARACTERS))
        self.assertTrue(all(c.avatar_url == c.avatar_urls["lvl1"] and c.avatar_url.endswith(f"/avatars/{c.id}/lvl1.png") for c in CHARACTERS))
        self.assertTrue(all(c.schema_version == "1.2.0" for c in CHARACTERS))

    def test_every_effect_type_has_a_runtime_rule(self):
        used = {e.type for c in CHARACTERS for s in c.skills for e in s.effects}
        self.assertFalse(used - set(RULES), used - set(RULES))


if __name__ == "__main__":
    unittest.main()
