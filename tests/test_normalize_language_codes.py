import unittest

from core.translation_service import SUPPORTED_LANGUAGE_CODES, canonical_language_code
from scripts.normalize_language_codes import plan_doc, plan_languages, plan_translations


class CanonicalCodeTests(unittest.TestCase):
    def test_table(self):
        expected = {"fr-FR": "fr", "tr-TR": "tr", "ja-JP": "ja", "es-ES": "es", "es-419": "es-LA", "zh-CN": "zh", "zh-Hans": "zh",
                    "zh-TW": "zh-TW", "zh-Hant": "zh-TW", "iw-IL": "he", "he": "he", "no-NO": "no", "nb": "no", "fa-IR": "fa",
                    "pt-BR": "pt", "en": "en", "ar": "ar", "am": "am", "sr": "sr"}
        for old, new in expected.items():
            self.assertEqual(canonical_language_code(old), new, old)

    def test_generation_uses_short_codes_only(self):
        self.assertTrue(all(len(c) <= 3 or c in ("zh-TW", "es-LA") for c in SUPPORTED_LANGUAGE_CODES), SUPPORTED_LANGUAGE_CODES)
        self.assertEqual(len(SUPPORTED_LANGUAGE_CODES), len(set(SUPPORTED_LANGUAGE_CODES)))
        self.assertNotIn("fr-FR", SUPPORTED_LANGUAGE_CODES)


class PlanTests(unittest.TestCase):
    def test_rename_without_twin(self):
        new, stats = plan_translations({"fr-FR": {"question": "q"}, "am": {"question": "a"}})
        self.assertEqual(new, {"fr": {"question": "q"}, "am": {"question": "a"}})
        self.assertEqual(stats, {"renamed": 1, "merged": 0, "conflicts": 0})

    def test_twin_keeps_short_and_fills_missing(self):
        new, stats = plan_translations({
            "fr-FR": {"question": "long q", "hint": "from long", "options": {"A": "a", "B": "b"}},
            "fr": {"question": "short q", "hint": "", "options": {"A": "A!", "B": ""}},
        })
        self.assertEqual(new["fr"], {"question": "short q", "hint": "from long", "options": {"A": "A!", "B": "b"}})
        self.assertEqual(stats, {"renamed": 0, "merged": 1, "conflicts": 1})

    def test_plan_doc_sets_deletes_and_languages_and_payload(self):
        data = {"supported_languages": ["en", "tr-TR", "tr"], "translations": {"en": {"question": "q"}, "tr-TR": {"question": "t"}},
                "payload": {"supported_languages": ["fr-FR"], "translations": {"fr-FR": {"question": "f"}}}}
        plan = plan_doc(data)
        self.assertEqual(plan["set"][("supported_languages",)], ["en", "tr"])
        self.assertEqual(plan["set"][("translations", "tr")], {"question": "t"})
        self.assertIn(("translations", "tr-TR"), plan["delete"])
        self.assertEqual(plan["set"][("payload", "supported_languages")], ["fr"])
        self.assertIn(("payload", "translations", "fr-FR"), plan["delete"])

    def test_clean_doc_needs_nothing(self):
        self.assertEqual(plan_doc({"supported_languages": ["en", "tr"], "translations": {"en": {}, "tr": {}}}), {})
        self.assertEqual(plan_languages(["iw-IL", "he", "en"]), ["he", "en"])


if __name__ == "__main__":
    unittest.main()
