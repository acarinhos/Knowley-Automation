import unittest

from scripts.fix_question_defects import (
    defective_options, deactivation_update, plan_reorder, rewrite_record, summarize, validate_rewrite,
)


def doc(options, **over):
    base = {"correct_answer": "A", "weakest_distractor_index": 1, "strongest_distractor_index": 2,
            "translations": {"en": {"question": "Q?", "options": options, "explanation": "e"}}}
    base.update(over)
    return base


DUP = {"A": "Rushmore", "B": "Rushmore", "C": "Everest", "D": "Fuji"}


class RewriteTests(unittest.TestCase):
    def test_duplicate_keeps_correct_and_flags_other(self):
        self.assertEqual(defective_options(doc(DUP))["en"], {"letters": ["B"], "blocked": None})

    def test_legit_repeats_are_ignored(self):
        self.assertEqual(defective_options(doc({"A": "5", "B": "5", "C": "7", "D": "9"})), {})
        self.assertEqual(defective_options(doc({"A": "酸", "B": "酸", "C": "碱", "D": "盐"})), {})

    def test_empty_correct_option_is_blocked(self):
        info = defective_options(doc({"A": "", "B": "x1", "C": "y2", "D": "z3"}))["en"]
        self.assertIsNotNone(info["blocked"])
        self.assertIsNone(rewrite_record("d", doc({"A": "", "B": "x1", "C": "y2", "D": "z3"})))

    def test_validate_rewrite_accepts_valid_and_rejects_unsafe(self):
        d = doc(DUP, weakest_distractor_index=3, strongest_distractor_index=2)
        self.assertEqual(validate_rewrite(d, {"en": {"B": "K2"}}), {("translations", "en", "options", "B"): "K2"})
        for bad in ({"en": {"B": "Everest"}}, {"en": {"B": ""}}, {"en": {"A": "X"}}, {"en": {"C": "X"}}, {"fr": {"B": "x"}}):
            with self.assertRaises(ValueError):
                validate_rewrite(d, bad)

    def test_rewrite_of_referenced_distractor_is_skipped(self):
        with self.assertRaises(ValueError):
            validate_rewrite(doc(DUP), {"en": {"B": "K2"}})  # weakest index 1 == B


class OrderTests(unittest.TestCase):
    def test_unambiguous_reorder(self):
        d = doc({"A": "Paris", "B": "Rome", "C": "Madrid", "D": "Berlin"})
        d["translations"]["de"] = {"options": {"A": "Rome", "B": "Paris", "C": "Madrid", "D": "Berlin"}}
        move = plan_reorder(d)["de"]
        self.assertEqual(move["options"], {"B": "Rome", "A": "Paris", "C": "Madrid", "D": "Berlin"})

    def test_uncertain_when_translated(self):
        d = doc({"A": "Paris", "B": "Rome", "C": "Madrid", "D": "Berlin"})
        d["translations"]["de"] = {"options": {"A": "Rome", "B": "Paris", "C": "Madrid", "D": "Hauptstadt"}}
        self.assertIsNone(plan_reorder(d)["de"]["options"])

    def test_aligned_locale_untouched(self):
        d = doc({"A": "Paris", "B": "Rome", "C": "Madrid", "D": "Berlin"})
        d["translations"]["de"] = {"options": dict(d["translations"]["en"]["options"])}
        self.assertEqual(plan_reorder(d), {})


class MiscTests(unittest.TestCase):
    def test_deactivation_and_summary(self):
        self.assertEqual(deactivation_update("T"), {"is_active": False, "deactivated_reason": "suspect_answer_key", "deactivated_at": "T"})
        self.assertEqual(summarize({"a": "written", "b": "skipped: x", "c": "skipped: y"}), {"written": 1, "skipped": 2})


if __name__ == "__main__":
    unittest.main()
