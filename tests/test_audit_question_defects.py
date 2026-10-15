import unittest

from scripts.audit_question_defects import detect


def doc(**over):
    opts = {"A": "Paris", "B": "Rome", "C": "Madrid", "D": "Berlin"}
    base = {"correct_answer": "A", "correct_option": "A", "weakest_distractor_index": 1, "strongest_distractor_index": 2,
            "translations": {"en": {"question": "What is the capital of France?", "options": opts,
                                    "explanation": "Paris is the capital of France."}}}
    base.update(over)
    return base


def kinds(data):
    return {d["type"] for d in detect(data)}


class DefectTests(unittest.TestCase):
    def test_clean_doc(self):
        self.assertEqual(detect(doc()), [])

    def test_duplicate_and_tiny_options(self):
        en = doc()["translations"]["en"]
        en["options"] = {"A": "Paris", "B": " paris ", "C": "", "D": "x"}
        found = kinds(doc(translations={"en": en}))
        self.assertTrue({"duplicate_options", "empty_or_tiny_option"} <= found)

    def test_answer_and_option_keys(self):
        self.assertIn("bad_correct_answer", kinds(doc(correct_answer="E")))
        self.assertIn("bad_correct_answer", kinds(doc(correct_option="B")))
        en = doc()["translations"]["en"]
        en["options"] = {"A": "a1", "B": "b1", "C": "c1", "1": "d1"}
        self.assertIn("bad_option_keys", kinds(doc(translations={"en": en})))

    def test_distractor_indices(self):
        self.assertIn("bad_distractor_index", kinds(doc(weakest_distractor_index=0)))
        self.assertIn("bad_distractor_index", kinds(doc(weakest_distractor_index=2)))
        self.assertIn("bad_distractor_index", kinds(doc(strongest_distractor_index=7)))

    def test_short_question_and_incomplete_explanation(self):
        en = doc()["translations"]["en"]
        en.update(question="Why?", explanation="Paris is the capital and")
        self.assertTrue({"empty_or_tiny_question", "incomplete_explanation"} <= kinds(doc(translations={"en": en})))

    def test_locale_option_order_mismatch(self):
        de = {"question": "Was ist die Hauptstadt von Frankreich?", "options": {"A": "Rome", "B": "Paris", "C": "Madrid", "D": "Berlin"},
              "explanation": "Paris."}
        d = doc()
        d["translations"]["de"] = de
        self.assertIn("locale_option_order_mismatch", kinds(d))


if __name__ == "__main__":
    unittest.main()
