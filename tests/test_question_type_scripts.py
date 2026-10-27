import copy
import unittest

from core.generator import GeneratedQuestion, normalize_blank_marker
from scripts.audit_local_languages import missing_languages, usable
from scripts.audit_question_defects import detect
from scripts.backfill_local_languages import pick_source, plan_update, validate_language
from scripts.backfill_v110_skill_fields import apply_result, missing_fields, usable_en
from scripts.fix_question_defects import defect_reason, defective_options, plan_reorder, retranslate_targets
from tests.test_question_types import payload


def tf_doc(**over):
    en = {"question": "Jupiter is the largest planet.", "options": {"A": "True", "B": "False"}, "explanation": "Jupiter is the largest planet.",
          "hint": "h", "keyword": "k", "distractor_trap_note": "n", "simplified_question": "Jupiter is biggest.",
          "binary_fallback": {"statement": "Jupiter is the largest planet.", "is_true": True},
          "clarified_options": {"A": "True", "B": "False"}, "filter_tag": "t"}
    fr = copy.deepcopy(en)
    fr["options"] = {"A": "Vrai", "B": "Faux"}
    data = {"question_type": "true_false", "correct_answer": "A", "weakest_distractor_index": None, "strongest_distractor_index": None,
            "option_difficulty_order": None, "translations": {"en": en, "fr": fr}}
    data.update(over)
    return data


def mc_doc():
    data = tf_doc(question_type="multiple_choice", weakest_distractor_index=1, strongest_distractor_index=2)
    data["translations"]["en"]["options"] = {"A": "Jupiter", "B": "Mars", "C": "Venus", "D": "Earth"}
    data["translations"]["fr"]["options"] = {"A": "Jupiter", "B": "Mars", "C": "Venus", "D": "Terre"}
    return data


class AuditTests(unittest.TestCase):
    def test_true_false_clean_and_mc_unchanged(self):
        self.assertEqual(detect(tf_doc()), [])
        self.assertEqual(detect(mc_doc()), [])
        broken = mc_doc()
        del broken["translations"]["en"]["options"]["D"]
        self.assertIn("bad_option_keys", {d["type"] for d in detect(broken)})
        no_type = mc_doc()
        del no_type["question_type"]
        del no_type["translations"]["en"]["options"]["D"]
        self.assertIn("bad_option_keys", {d["type"] for d in detect(no_type)})

    def test_true_false_still_rejects_bad_answer(self):
        self.assertIn("bad_correct_answer", {d["type"] for d in detect(tf_doc(correct_answer="C"))})

    def test_fix_script_does_not_flag_true_false(self):
        data = tf_doc()
        self.assertEqual(defective_options(data), {})
        self.assertEqual(plan_reorder(data), {})
        self.assertEqual(retranslate_targets(data), [])
        self.assertIsNone(defect_reason([d["type"] for d in detect(data)]))


class LocalLanguageTests(unittest.TestCase):
    def test_usable_and_missing(self):
        data = tf_doc()
        self.assertEqual(missing_languages(data, ["en", "fr"]), [])
        self.assertEqual(missing_languages(data, ["en", "de"]), ["de"])
        self.assertFalse(usable(data["translations"]["en"]))  # default A-D unchanged
        self.assertEqual(pick_source(data), "en")

    def test_validate_language_true_false(self):
        source = tf_doc()["translations"]["en"]
        out = validate_language(source, {"question": "q", "explanation": "e", "filter_tag": "t", "hint": "h", "keyword": "k",
                                         "distractor_trap_note": "n", "simplified_question": "s", "options": {"A": "Wahr", "B": "Falsch"},
                                         "clarified_options": {"A": "Wahr", "B": "Falsch"},
                                         "binary_fallback": {"statement": "x", "is_true": True}}, "AB")
        self.assertEqual(out["options"], {"A": "Wahr", "B": "Falsch"})
        updates = plan_update(tf_doc(), ["de"], {"de": {
            "question": "q", "explanation": "e", "filter_tag": "t", "hint": "h", "keyword": "k", "distractor_trap_note": "n",
            "simplified_question": "s", "options": {"A": "Wahr", "B": "Falsch"}, "clarified_options": {"A": "Wahr", "B": "Falsch"},
            "binary_fallback": {"statement": "x", "is_true": True}}})
        self.assertEqual(updates[("translations", "de", "options", "B")], "Falsch")
        with self.assertRaises(ValueError):
            validate_language(source, {"options": {"A": "Wahr", "B": "Wahr"}}, "AB")


class V110BackfillTests(unittest.TestCase):
    def test_true_false_needs_no_indices_or_cd(self):
        data = tf_doc()
        self.assertEqual(missing_fields(data), {"root": [], "locales": {}})
        self.assertTrue(usable_en(data["translations"]["en"], "AB"))
        self.assertFalse(usable_en(data["translations"]["en"]))

    def test_multiple_choice_still_requires_indices(self):
        data = mc_doc()
        data["weakest_distractor_index"] = None
        self.assertIn("weakest_distractor_index", missing_fields(data)["root"])

    def test_apply_result_true_false_strips_cd(self):
        data = tf_doc()
        del data["translations"]["en"]["hint"]
        updates = apply_result(data, {"hint": "new", "clarified_options": {"A": "T", "B": "F", "C": "x", "D": "y"}})
        self.assertEqual(updates, {("translations", "en", "hint"): "new"})


class GeneratorBlankTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_blank_marker("is ______ big"), "is _____ big")
        self.assertEqual(normalize_blank_marker("is _ _ _ big"), "is _____ big")
        self.assertEqual(normalize_blank_marker("is __ big"), "is __ big")

    def test_validation_requires_canonical_marker_and_simplified(self):
        GeneratedQuestion.model_validate(payload("fill_blank"))
        for bad in ("The largest is ______.", "The largest is ___."):
            data = payload("fill_blank")
            data["translations"]["en"]["question"] = bad
            with self.assertRaises(ValueError):
                GeneratedQuestion.model_validate(data)
        data = payload("fill_blank")
        data["translations"]["en"]["simplified_question"] = "The largest is Jupiter."
        with self.assertRaises(ValueError):
            GeneratedQuestion.model_validate(data)

    def test_multiple_choice_missing_cd_rejected(self):
        data = payload()
        del data["translations"]["en"]["options"]["D"]
        with self.assertRaises(ValueError):
            GeneratedQuestion.model_validate(data)


if __name__ == "__main__":
    unittest.main()
