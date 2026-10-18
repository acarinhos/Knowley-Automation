import unittest

from core.sheet_algorithm import get_sheet_algorithm_repository
from scripts.audit_local_languages import canon, missing_languages
from scripts.backfill_local_languages import export_record, plan_update, validate_language

SRC = {"question": "Q?", "options": {k: f"o{k}" for k in "ABCD"}, "explanation": "e", "filter_tag": "t", "hint": "h",
       "keyword": "k", "distractor_trap_note": "n", "simplified_question": "s",
       "clarified_options": {k: f"c{k}" for k in "ABCD"}, "binary_fallback": {"statement": "st", "is_true": False}}
FR = {"question": "Q fr?", "options": {k: f"fo{k}" for k in "ABCD"}, "explanation": "ef", "filter_tag": "tf", "hint": "hf",
      "keyword": "kf", "distractor_trap_note": "nf", "simplified_question": "sf",
      "clarified_options": {k: f"fc{k}" for k in "ABCD"}, "binary_fallback": {"statement": "sf2", "is_true": False}}


def doc():
    return {"correct_answer": "A", "scope": "local", "supported_languages": ["en"], "translations": {"en": dict(SRC)}}


class LanguageRuleTests(unittest.TestCase):
    def test_question_languages_are_country_languages_plus_en(self):
        repo = get_sheet_algorithm_repository()
        self.assertEqual(repo.question_languages("Türkiye"), ["tr", "en"])
        self.assertEqual(repo.question_languages("ABD"), ["en"])
        self.assertEqual(repo.question_languages("Kanada"), ["en", "fr"])
        self.assertEqual(repo.question_languages("Nowhere"), ["en"])

    def test_code_equivalents(self):
        for a, b in (("tr", "tr-TR"), ("zh-Hans", "zh-CN"), ("he", "iw-IL"), ("nb", "no-NO"), ("fa", "fa-IR"), ("fr", "fr"), ("es-419", "es-LA")):
            self.assertEqual(canon(a), canon(b))
        self.assertNotEqual(canon("es-419"), canon("es-ES"))
        self.assertEqual(canon("zh-Hant"), "zh-TW")

    def test_missing_languages_uses_equivalent_keys_and_ignores_extras(self):
        d = doc()
        d["translations"]["fr"] = dict(FR)  # stored as `fr`, Sheets says fr-FR
        d["translations"]["de"] = dict(FR)  # extra language is fine
        self.assertEqual(missing_languages(d, ["en", "fr"]), [])
        self.assertEqual(missing_languages(doc(), ["en", "fr"]), ["fr"])


class BackfillTests(unittest.TestCase):
    def test_export_record(self):
        rec = export_record("d1", doc(), "Fransa", ["fr"])
        self.assertEqual((rec["source_language"], rec["missing_languages"]), ("en", ["fr"]))
        self.assertIsNone(export_record("d2", {"translations": {}}, "Fransa", ["fr"]))

    def test_plan_update_adds_language_merge_only(self):
        updates = plan_update(doc(), ["fr"], {"fr": FR})
        self.assertEqual(updates[("translations", "fr", "question")], "Q fr?")
        self.assertEqual(updates[("translations", "fr", "options", "B")], "foB")
        self.assertIs(updates[("translations", "fr", "binary_fallback", "is_true")], False)
        self.assertEqual(updates[("supported_languages",)], ["en", "fr"])
        self.assertFalse([k for k in updates if k[:2] == ("translations", "en")])

    def test_plan_update_never_overwrites_existing_fields(self):
        d = doc()
        d["translations"]["fr"] = {"question": "keep me", "options": {"A": "a", "B": "", "C": "", "D": ""}}
        updates = plan_update(d, ["fr"], {"fr": FR})
        self.assertNotIn(("translations", "fr", "question"), updates)
        self.assertNotIn(("translations", "fr", "options", "A"), updates)
        self.assertIn(("translations", "fr", "options", "B"), updates)

    def test_validation_rejects_bad_results(self):
        bad_cases = [
            {**FR, "options": {**FR["options"], "B": FR["options"]["A"]}},
            {**FR, "binary_fallback": {"statement": "x", "is_true": True}},
            {**FR, "hint": ""},
            {**FR, "options": {"A": "a"}},
        ]
        for bad in bad_cases:
            with self.assertRaises(ValueError):
                validate_language(SRC, bad)
        with self.assertRaises(ValueError):
            plan_update(doc(), ["fr"], {"de-DE": FR})  # not a missing language
        self.assertEqual(validate_language(SRC, FR)["binary_fallback"]["is_true"], False)


if __name__ == "__main__":
    unittest.main()
