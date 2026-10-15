import unittest

from scripts.backfill_v110_skill_fields import (
    build_updates, check_support, estimate, missing_fields, pick_source_language, valid_order,
)

FULL = {
    "question": "q", "options": {k: k for k in "ABCD"}, "hint": "h", "keyword": "k", "distractor_trap_note": "t",
    "simplified_question": "s", "clarified_options": {k: k for k in "ABCD"},
    "binary_fallback": {"statement": "x", "is_true": False},
}


def doc(**over):
    base = {"correct_answer": "A", "weakest_distractor_index": 1, "strongest_distractor_index": 2,
            "option_difficulty_order": [0, 1, 2, 3], "translations": {"en": dict(FULL), "tr": dict(FULL)}}
    base.update(over)
    return base


class BackfillPureTests(unittest.TestCase):
    def test_complete_doc_has_no_gaps(self):
        self.assertEqual(missing_fields(doc()), {"root": [], "locales": {}})

    def test_detects_root_and_locale_gaps(self):
        tr = dict(FULL, hint="", binary_fallback={"statement": "x"})
        gaps = missing_fields(doc(weakest_distractor_index=None, option_difficulty_order=None, translations={"en": FULL, "tr": tr}))
        self.assertEqual(gaps["root"], ["weakest_distractor_index", "option_difficulty_order"])
        self.assertEqual(gaps["locales"], {"tr": ["hint", "binary_fallback"]})

    def test_valid_order(self):
        self.assertTrue(valid_order([3, 1, 0, 2]))
        self.assertFalse(valid_order([0, 0, 1, 2]))
        self.assertFalse(valid_order(None))

    def test_build_updates_only_missing_and_valid(self):
        gaps = {"root": ["strongest_distractor_index", "option_difficulty_order"], "locales": {"tr": ["hint", "keyword"]}}
        updates = build_updates(gaps, {"strongest_distractor_index": 2, "option_difficulty_order": [0, 0, 1, 2]},
                                {"tr": {"hint": "yeni", "keyword": " ", "question": "must not be written"}})
        self.assertEqual(updates, {("strongest_distractor_index",): 2, ("translations", "tr", "hint"): "yeni"})

    def test_check_support_rejects_answer_or_equal_indices(self):
        check_support("A", {"weakest_distractor_index": 1, "strongest_distractor_index": 2}, FULL, "en")
        for weakest, strongest in ((0, 2), (2, 2)):
            with self.assertRaises(ValueError):
                check_support("A", {"weakest_distractor_index": weakest, "strongest_distractor_index": strongest}, FULL, "en")

    def test_source_language_and_estimate(self):
        self.assertEqual(pick_source_language(doc()), "en")
        self.assertIsNone(pick_source_language({"translations": {"en": {"question": ""}}}))
        gaps = missing_fields(doc(translations={"en": {**FULL, "hint": ""}, "tr": {**FULL, "hint": ""}}))
        self.assertEqual(estimate([gaps]), {"docs": 1, "generation_calls": 1, "translation_calls": 1, "locale_fields": 2})


if __name__ == "__main__":
    unittest.main()
