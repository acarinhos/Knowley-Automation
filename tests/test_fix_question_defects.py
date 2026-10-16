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



class RetranslateTests(unittest.TestCase):
    def two_locale(self, de_options):
        d = doc({"A": "Paris", "B": "Rome", "C": "Madrid", "D": "Berlin"})
        d["translations"]["de"] = {"question": "Q?", "options": de_options}
        return d

    def test_targets_only_broken_non_en_locales_when_en_is_clean(self):
        from scripts.fix_question_defects import retranslate_record, retranslate_targets

        broken = self.two_locale({"A": "Paris", "B": "Paris", "C": "Madrid", "D": "Berlin"})
        self.assertEqual(retranslate_targets(broken), ["de"])
        self.assertEqual(retranslate_record("d", broken)["locales"]["de"]["options"]["B"], "Paris")
        self.assertEqual(retranslate_targets(self.two_locale({"A": "Paris", "B": "Rom", "C": "Madrid", "D": "Berlin"})), [])
        en_broken = doc(DUP)
        en_broken["translations"]["de"] = {"options": {"A": "x1", "B": "x1", "C": "y2", "D": "z3"}}
        self.assertEqual(retranslate_targets(en_broken), [])

    def test_validate_retranslation(self):
        from scripts.fix_question_defects import validate_retranslation

        broken = self.two_locale({"A": "Paris", "B": "Paris", "C": "Madrid", "D": "Berlin"})
        good = {"de": {"A": "Paris", "B": "Rom", "C": "Madrid", "D": "Berlin"}}
        self.assertEqual(len(validate_retranslation(broken, good)), 4)
        for bad in ({"de": {**good["de"], "B": "Paris"}}, {"de": {"A": "x"}}, {"fr": good["de"]}, {"de": {**good["de"], "C": ""}}):
            with self.assertRaises(ValueError):
                validate_retranslation(broken, bad)

    def test_digit_mismatch_rejected(self):
        from scripts.fix_question_defects import validate_retranslation

        d = doc({"A": "30", "B": "35", "C": "33", "D": "36"})
        d["translations"]["tr"] = {"options": {"A": "33", "B": "35", "C": "33", "D": "36"}}
        with self.assertRaises(ValueError):
            validate_retranslation(d, {"tr": {"A": "30", "B": "35", "C": "34", "D": "36"}})
        self.assertEqual(len(validate_retranslation(d, {"tr": {"A": "30", "B": "35", "C": "33", "D": "36"}})), 4)


if __name__ == "__main__":
    unittest.main()


class ResetIndicesTests(unittest.TestCase):
    def test_reset_conflicting_indices_nulls_index_fields(self):
        d = doc(DUP, option_difficulty_order=[0, 1, 2, 3])  # weakest index 1 == B
        updates = validate_rewrite(d, {"en": {"B": "K2"}}, reset_conflicting=True)
        self.assertEqual(updates[("translations", "en", "options", "B")], "K2")
        for field in ("weakest_distractor_index", "strongest_distractor_index", "option_difficulty_order"):
            self.assertIsNone(updates[(field,)])
            self.assertIn((field,), updates)

    def test_no_reset_without_conflict_and_nulls_count_as_missing(self):
        d = doc(DUP, weakest_distractor_index=3, strongest_distractor_index=2)
        self.assertEqual(set(validate_rewrite(d, {"en": {"B": "K2"}}, reset_conflicting=True)), {("translations", "en", "options", "B")})
        from scripts.backfill_v110_skill_fields import missing_fields

        gaps = missing_fields({"weakest_distractor_index": None, "strongest_distractor_index": None, "option_difficulty_order": None, "translations": {}})
        self.assertEqual(gaps["root"], ["weakest_distractor_index", "strongest_distractor_index", "option_difficulty_order"])


class DeactivateDefectsTests(unittest.TestCase):
    def test_reason_priority_and_mismatch_exclusion(self):
        from scripts.fix_question_defects import defect_reason, deactivation_update

        self.assertEqual(defect_reason(["empty_or_tiny_option", "duplicate_options"]), "defect_duplicate_options")
        self.assertEqual(defect_reason(["incomplete_explanation"]), "defect_incomplete_explanation")
        self.assertEqual(defect_reason(["manual_review"]), "defect_manual_review")
        self.assertIsNone(defect_reason(["locale_option_order_mismatch"]))
        self.assertIsNone(defect_reason(["locale_option_order_mismatch", "empty_or_tiny_option"]))
        self.assertIsNone(defect_reason([]))
        self.assertEqual(deactivation_update("T", "defect_x"), {"is_active": False, "deactivated_reason": "defect_x", "deactivated_at": "T"})
